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

from src.services.prediction_service import (
    DEFAULT_LABEL_HORIZON,
    DEFAULT_LABEL_THRESHOLD,
    FEATURE_ORDER,
    PredictionError,
    _load_daily_df,
    build_features,
    make_labels,
    train_model,
)

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
    ) -> tuple[np.ndarray, np.ndarray, List[str], List[Any]]:
        """遍历股票，构造并汇聚 (X, y) 训练样本。

        标签口径：未来 horizon 日方向（与预测/回测一致），末 horizon 行无标签。

        Returns:
            (X, y, used_symbols, all_dates)
        """
        X_parts: List[np.ndarray] = []
        y_parts: List[np.ndarray] = []
        used_symbols: List[str] = []
        all_dates: List[Any] = []

        for raw in symbols:
            code = (raw or "").strip()
            if not code:
                continue
            try:
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

            # TODO(第一阶段②·下次续做): 待指数日线回填后，这里改为
            #   feats = build_features(df, market_df=<缓存的指数日线>)
            #   以引入大盘/板块环境因子（详见 prediction_service.build_features 顶部 TODO）。
            feats = build_features(df)
            if len(feats) < max(30, horizon + 20):
                logger.info("[train] %s 有效样本过少(%d)，跳过", code, len(feats))
                continue

            # 未来 horizon 日方向标签；末 horizon 行无标签，剔除
            y_all = make_labels(feats["close"], horizon=horizon, threshold=threshold)
            usable = feats.iloc[:-horizon]
            X_parts.append(usable[FEATURE_ORDER].to_numpy(dtype=float))
            y_parts.append(y_all.iloc[:-horizon].to_numpy())
            all_dates.extend(usable["date"].tolist())
            used_symbols.append(code)
            logger.info("[train] %s 贡献样本 %d 条", code, len(usable))

        if not X_parts:
            raise ModelTrainingError(
                "所有股票均无足够有效样本，无法训练；请检查股票代码或数据源可用性"
            )

        X = np.vstack(X_parts)
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

        Returns:
            训练摘要字典（版本、样本数、指标等）
        """
        if not symbols:
            raise ModelTrainingError("训练股票列表为空")

        lookback_days = int(max(120, min(lookback_days, 1200)))
        horizon = int(max(1, min(horizon, 20)))
        started = datetime.now()
        logger.info(
            "[train] 开始训练：模型=%s，股票=%d 只，回溯=%d 天，标签=未来%d日，联网刷新=%s",
            model_name, len(symbols), lookback_days, horizon, refresh,
        )

        X, y, used_symbols, all_dates = self._collect_samples(
            symbols, lookback_days, refresh=refresh,
            horizon=horizon, threshold=threshold,
        )

        logger.info(
            "[train] 样本汇聚完成：%d 条来自 %d 只股票，正样本占比 %.1f%%",
            len(X), len(used_symbols), 100.0 * float(y.mean()) if len(y) else 0.0,
        )

        model, metrics = train_model(X, y, epochs=epochs, lr=lr, l2=l2, embargo=horizon)

        version = started.strftime("%Y%m%d_%H%M%S")
        start_date = min(all_dates) if all_dates else None
        end_date = max(all_dates) if all_dates else None

        model_id = self.repo.save_model(
            name=model_name,
            version=version,
            algorithm="logistic_regression_gd",
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
