# -*- coding: utf-8 -*-
"""
===================================
走势预测模型训练服务
===================================

职责：把"训练"从预测请求链路里剥离出来，作为**可由用户掌控的离线任务**：
    命令行手动触发 / 定时触发 → 拉取(或复用缓存)多只股票日线
    → 构造技术因子 + 打标签(未来 N 日方向, 默认 5 日) → 汇聚成一个大样本集
    → 训练一个**全局**逻辑回归模型 → 持久化 + 版本化(prediction_models 表)
    → 标记为激活版本，供预测服务直接加载推理

设计取舍（参考 invest_dojo，但适配本项目 SQLite 单机规模）：
1. **一个全局模型**：跨多只股票汇聚样本训练，而非每票一个模型。这才是
   "训练一个走势预测模型"，样本更多、更稳健，也便于统一版本管理。
2. **复用现有基建**：特征工程直接复用 prediction_service.build_features；
   数据读取复用 _load_daily_df 的读透缓存（与主分析/回测共享 stock_daily）。
3. **模型参数入库**：模型极小（权重+偏置+标准化统计量），直接以 JSON 存 DB，
   省去 invest_dojo 的 MinIO/对象存储依赖。

⚠️ 训练产物仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.services.prediction_service import (
    DEFAULT_LABEL_HORIZON,
    DEFAULT_LABEL_THRESHOLD,
    FEATURE_ORDER,
    PredictionError,
    _align_market_close,
    _load_daily_df,
    build_features,
    load_market_df,
    make_forward_return,
    make_labels,
    make_labels_relative,
    preload_training_cache,
    train_model,
)

# 横截面排序：一个交易日至少要有这么多只股票，排名分强弱才有意义
MIN_NAMES_PER_DAY = 20
# 行业中性化：同日同行业内至少这么多只票，行业内排名才有意义
MIN_NAMES_PER_INDUSTRY_DAY = 5

logger = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "trend_lr"


class ModelTrainingError(Exception):
    """训练流程可预期的业务错误（有效样本不足等）。"""


class ModelTrainingService:
    """走势预测模型的离线训练与持久化。"""

    def __init__(self, db_manager=None):
        # 延迟导入 repo，避免与 storage 的循环依赖
        from src.repositories.prediction_model_repo import PredictionModelRepository

        self.repo = PredictionModelRepository(db_manager)

    def _collect_samples(
        self,
        symbols: List[str],
        lookback_days: int,
        *,
        refresh: bool,
        horizon: int = DEFAULT_LABEL_HORIZON,
        threshold: float = DEFAULT_LABEL_THRESHOLD,
        label_mode: str = "absolute",
        train_end: Optional[Any] = None,
    ) -> tuple[np.ndarray, np.ndarray, List[str], List[Any]]:
        """遍历股票，构造并汇聚 (X, y) 训练样本。

        标签口径（label_mode）：
        - "absolute"：未来 horizon 日绝对涨跌（默认，与旧逻辑一致）
        - "relative"：未来 horizon 日是否跑赢大盘（沪深300），剔除大盘 β、只考 alpha
        - "cross_section"：未来 horizon 日在**当日全市场横截面**里是否属强势前 50%
          （先收集连续远期收益，汇聚后按交易日横向排名分强弱；天然市场中性、
          类别均衡，基线恒 ~50%，超过即为纯选股能力）

        末 horizon 行无标签。

        Returns:
            (X, y, used_symbols, all_dates)
        """
        is_xsec = label_mode == "cross_section"
        market_df = load_market_df() if label_mode == "relative" else None
        # 横截面：加载行业归属做「行业中性」排名（同日同行业内比强弱）。
        ind_map: Dict[str, str] = {}
        if is_xsec:
            try:
                from src.repositories.stock_industry_repo import StockIndustryRepository
                ind_map = StockIndustryRepository().get_map()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[train] 行业映射加载失败，横截面退回全市场排名：%s", exc)
            logger.info(
                "[train] 横截面口径：%s（行业映射覆盖 %d 只）",
                "行业中性排名" if ind_map else "全市场排名（无行业数据）", len(ind_map),
            )
        X_parts: List[np.ndarray] = []
        y_parts: List[np.ndarray] = []       # absolute/relative：直接是 0/1 标签
        fwd_parts: List[np.ndarray] = []     # cross_section：连续远期收益，稍后横向排名
        ind_parts: List[str] = []            # cross_section：每行的行业（行业中性排名用）
        used_symbols: List[str] = []
        all_dates: List[Any] = []

        # 纯读本地库时：一次批量 JOIN 预加载，避免 5000×2 次 SQLite 往返
        df_cache: Dict[str, pd.DataFrame] = {}
        if not refresh:
            try:
                df_cache = preload_training_cache(symbols, lookback_days)
            except Exception as exc:  # noqa: BLE001 - 失败则退回逐票读取
                logger.warning("[train] 批量预读失败，退回逐票模式: %s", exc)

        for raw in symbols:
            code = (raw or "").strip()
            if not code:
                continue
            code_key = code.upper()
            try:
                if not refresh and code_key in df_cache:
                    df = df_cache[code_key]
                else:
                    df, _name = _load_daily_df(
                        code, lookback_days, use_cache=True, refresh=refresh,
                        resolve_name=False,
                    )
            except PredictionError as exc:
                logger.warning("[train] 跳过 %s：%s", code, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - 单票失败不应中断整体训练
                logger.warning("[train] 获取 %s 数据异常，跳过：%s", code, exc)
                continue

            if df is None or df.empty:
                logger.warning("[train] %s 无数据，跳过", code)
                continue

            # 大盘环境因子：传入指数日线（load_market_df 进程内缓存，只查一次库）
            feats = build_features(df, market_df=load_market_df())
            if len(feats) < max(30, horizon + 20):
                logger.info("[train] %s 有效样本过少(%d)，跳过", code, len(feats))
                continue

            usable = feats.iloc[:-horizon]
            X_i = usable[FEATURE_ORDER].to_numpy(dtype=float)
            d_i = usable["date"].tolist()

            if is_xsec:
                # 连续远期收益，末 horizon 行剔除；NaN 行按行剔除
                fwd_all = make_forward_return(feats["close"], horizon=horizon)
                v_i = fwd_all.iloc[:-horizon].to_numpy()
                valid = ~np.isnan(v_i)
                if not valid.all():
                    X_i, v_i = X_i[valid], v_i[valid]
                    d_i = [d for d, keep in zip(d_i, valid) if keep]
                if len(v_i) == 0:
                    continue
                fwd_parts.append(v_i)
                ind = ind_map.get((code or "").strip().upper(), "__UNK__") if ind_map else "__ALL__"
                ind_parts.extend([ind] * len(v_i))
            else:
                # 未来 horizon 日 0/1 标签；末 horizon 行无标签，剔除
                if label_mode == "relative":
                    mkt_close = _align_market_close(feats["date"], market_df)
                    y_all = make_labels_relative(
                        feats["close"], mkt_close, horizon=horizon, threshold=threshold,
                    )
                else:
                    y_all = make_labels(feats["close"], horizon=horizon, threshold=threshold)
                y_i = y_all.iloc[:-horizon].to_numpy()
                # 相对标签在大盘对不齐处会产生 NaN，需按行剔除（保持 X/y/date 对齐）
                valid = ~np.isnan(y_i)
                if not valid.all():
                    X_i, y_i = X_i[valid], y_i[valid]
                    d_i = [d for d, keep in zip(d_i, valid) if keep]
                if len(y_i) == 0:
                    logger.info("[train] %s 无有效标签，跳过", code)
                    continue
                y_parts.append(y_i)

            X_parts.append(X_i)
            all_dates.extend(d_i)
            used_symbols.append(code)

        if not X_parts:
            raise ModelTrainingError(
                "所有股票均无足够有效样本，无法训练；请检查股票代码或数据源可用性"
            )

        X = np.vstack(X_parts)

        # ── 训练截止日：只保留 date < train_end 的样本（留出近期做样本外回测）──
        if train_end is not None:
            cutoff = pd.Timestamp(train_end)
            dmask = (pd.to_datetime(pd.Series(all_dates), errors="coerce") < cutoff).to_numpy()
            n_before = len(all_dates)
            X = X[dmask]
            all_dates = [d for d, k in zip(all_dates, dmask) if k]
            if is_xsec:
                fwd_parts = [np.concatenate(fwd_parts)[dmask]]
                ind_parts = [v for v, k in zip(ind_parts, dmask) if k]
            else:
                y_parts = [np.concatenate(y_parts)[dmask]]
            logger.info("[train] 训练截止 %s：%d → %d 条", cutoff.date(), n_before, len(all_dates))

        if is_xsec:
            # ── 横截面排名：按远期收益在「同日(同行业)」内排名，前 50% 记 1 ──
            fwd = np.concatenate(fwd_parts)
            dser = pd.to_datetime(pd.Series(all_dates), errors="coerce")
            frame = pd.DataFrame({"d": dser.values, "ind": ind_parts, "fwd": fwd})
            neutralized = bool(ind_map)
            if neutralized:
                # 行业中性：同日同行业内排名；剔除该(日,行业)组票数过少者
                grp = frame.groupby(["d", "ind"])["fwd"]
                min_cnt = MIN_NAMES_PER_INDUSTRY_DAY
            else:
                grp = frame.groupby("d")["fwd"]
                min_cnt = MIN_NAMES_PER_DAY
            pct = grp.rank(pct=True, method="average").to_numpy()   # 组内分位 (0,1]
            cnt = grp.transform("count").to_numpy()                 # 组内样本数
            y = (pct > 0.5).astype(float)
            keep = cnt >= min_cnt
            if not keep.all():
                X, y = X[keep], y[keep]
                all_dates = [d for d, k in zip(all_dates, keep) if k]
            logger.info(
                "[train] 横截面标签(%s)：保留 %d 条，正样本占比 %.1f%%",
                "行业中性" if neutralized else "全市场",
                len(y), 100.0 * float(y.mean()) if len(y) else 0.0,
            )
            return X, y, used_symbols, all_dates

        y = np.concatenate(y_parts)
        return X, y, used_symbols, all_dates

    def train(
        self,
        symbols: List[str],
        *,
        lookback_days: int = 500,
        model_name: str = DEFAULT_MODEL_NAME,
        epochs: int = 400,
        lr: float = 0.3,
        l2: float = 1e-3,
        horizon: int = DEFAULT_LABEL_HORIZON,
        threshold: float = DEFAULT_LABEL_THRESHOLD,
        set_active: bool = True,
        refresh: bool = True,
        notes: Optional[str] = None,
        label_mode: str = "absolute",
        algorithm: str = "logistic",
        train_end: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """执行训练并持久化，返回训练摘要。

        Args:
            symbols: 参与训练的股票代码列表
            lookback_days: 每只股票的回溯天数
            model_name: 模型名（同名下按版本管理，新版本自动激活）
            epochs/lr/l2: 训练超参
            horizon: 标签前瞻天数（预测"未来 horizon 日"方向，默认 5，与预测/回测一致）
            threshold: 记为"看涨"所需的最小未来收益（默认 0=纯方向）
            set_active: 训练完成后是否设为激活版本（供预测使用）
            refresh: 是否联网刷新数据（False 则纯用本地缓存，适合离线补训）
            notes: 备注
            label_mode: "absolute"=绝对涨跌（默认）；"relative"=是否跑赢大盘（剔除大盘 β）

        Returns:
            训练摘要字典（版本、样本数、指标等）
        """
        if not symbols:
            raise ModelTrainingError("训练股票列表为空")

        _lm = str(label_mode).lower()
        label_mode = _lm if _lm in ("relative", "cross_section") else "absolute"
        algorithm = "lightgbm" if str(algorithm).lower() in ("lightgbm", "lgbm", "gbdt") else "logistic"
        lookback_days = int(max(120, min(lookback_days, 1200)))
        horizon = int(max(1, min(horizon, 20)))
        started = datetime.now()
        _lm_label = {
            "relative": "跑赢大盘", "cross_section": "横截面强势前50%",
        }.get(label_mode, "绝对涨跌")
        logger.info(
            "[train] 开始训练：模型=%s，股票=%d 只，回溯=%d 天，标签=未来%d日(%s)，联网刷新=%s",
            model_name, len(symbols), lookback_days, horizon, _lm_label, refresh,
        )

        X, y, used_symbols, all_dates = self._collect_samples(
            symbols, lookback_days, refresh=refresh,
            horizon=horizon, threshold=threshold, label_mode=label_mode,
            train_end=train_end,
        )

        logger.info(
            "[train] 样本汇聚完成：%d 条来自 %d 只股票，正样本占比 %.1f%%",
            len(X), len(used_symbols), 100.0 * float(y.mean()) if len(y) else 0.0,
        )

        # 传入与 X 行对齐的日期，启用“全局时序切分”（按日历切 train/valid，
        # 避免多股票堆叠时按行切退化成按股票切、时间段重叠而泄露）。
        dates_arr = pd.to_datetime(pd.Series(all_dates), errors="coerce").to_numpy()
        model, metrics = train_model(
            X, y, epochs=epochs, lr=lr, l2=l2, embargo=horizon, dates=dates_arr,
            algorithm=algorithm,
        )

        version = started.strftime("%Y%m%d_%H%M%S")
        # 统一归一到 date 再比较：样本可能同时来自缓存(datetime.date)与
        # 联网(pandas.Timestamp)，直接 min/max 会因类型混用报 TypeError。
        _norm_dates = [d for d in (_as_date(x) for x in all_dates) if d is not None]
        start_date = min(_norm_dates) if _norm_dates else None
        end_date = max(_norm_dates) if _norm_dates else None

        # 把标签口径写进 notes（相对模型的 up_probability 语义是“跑赢大盘概率”，
        # 供预测/展示层区分绝对涨跌 vs 相对超额）。
        _mode_tag = f"label_mode={label_mode}"
        notes = f"{_mode_tag}; {notes}" if notes else _mode_tag

        model_id = self.repo.save_model(
            name=model_name,
            version=version,
            algorithm=model.to_params().get("algorithm", "logistic_regression_gd"),
            params=model.to_params(),
            feature_names=list(FEATURE_ORDER),
            trained_symbols=used_symbols,
            train_start_date=_as_date(start_date),
            train_end_date=_as_date(end_date),
            horizon_days=horizon,
            metrics=metrics,
            set_active=set_active,
            notes=notes,
        )

        elapsed = (datetime.now() - started).total_seconds()
        summary = {
            "model_id": model_id,
            "model_name": model_name,
            "version": version,
            "is_active": set_active,
            "label_mode": label_mode,
            "algorithm": model.to_params().get("algorithm", "logistic_regression_gd"),
            "symbol_count": len(used_symbols),
            "trained_symbols": used_symbols,
            "total_samples": int(len(X)),
            "train_samples": metrics.get("train_samples"),
            "valid_samples": metrics.get("valid_samples"),
            "train_accuracy": metrics.get("train_accuracy"),
            "valid_accuracy": metrics.get("valid_accuracy"),
            "baseline_accuracy": metrics.get("baseline_accuracy"),
            "train_start_date": _iso(start_date),
            "train_end_date": _iso(end_date),
            "elapsed_sec": round(elapsed, 2),
        }
        logger.info(
            "[train] 训练完成：版本=%s，样本=%d，验证准确率=%s，基线=%s，耗时=%.1fs",
            version, len(X), metrics.get("valid_accuracy"),
            metrics.get("baseline_accuracy"), elapsed,
        )
        return summary


def _as_date(value):
    """把 date/datetime/字符串统一转成 date（供 ORM Date 列）。"""
    if value is None:
        return None
    if hasattr(value, "date") and not isinstance(value, str):
        try:
            return value.date() if hasattr(value, "hour") else value
        except Exception:  # noqa: BLE001
            return None
    try:
        import pandas as pd

        return pd.to_datetime(value).date()
    except Exception:  # noqa: BLE001
        return None


def _iso(value) -> Optional[str]:
    d = _as_date(value)
    return d.isoformat() if d else None
