# -*- coding: utf-8 -*-
"""
===================================
股价走势预测服务（轻量机器学习）
===================================

设计参考 invest_dojo/learn_ml 的教学流水线，落地为可直接运行的服务：

    取历史 K 线 → 构造技术因子(特征 X) → 打标签(次日涨/跌)
    → 逻辑回归 + 手写梯度下降训练 → 评估 → 预测次日方向
    → 依据方向与波动率推演未来 N 日价格路径（含置信带）

关键取舍：
1. **零重依赖**：只用 numpy + pandas（项目已装），不引入 sklearn/lightgbm，
   保证任何部署环境都能跑，训练过程完全透明可解释。
2. **可解释**：模型只有 (n_features + 1) 个参数，直接给出每个因子的贡献，
   方便前端展示"为什么看涨/看跌"。
3. **防未来函数**：标签用「次日收益」，特征只用当日及更早数据，训练/验证按
   时间顺序切分（前 80% 训练，后 20% 验证），不做随机打乱。

⚠️ 免责声明：本模块为技术演示，输出不构成任何投资建议。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# 特征名称（顺序与特征矩阵列一致），中文含义供前端展示
FEATURE_LABELS: Dict[str, Dict[str, str]] = {
    "ma5_dev": {"zh": "5日均线偏离度", "en": "MA5 deviation"},
    "ma10_dev": {"zh": "10日均线偏离度", "en": "MA10 deviation"},
    "prev_return": {"zh": "昨日涨跌幅", "en": "Prev-day return"},
    "momentum_5": {"zh": "5日动量", "en": "5-day momentum"},
    "volume_ratio": {"zh": "成交量比率", "en": "Volume ratio"},
    "rsi_14": {"zh": "RSI(14)", "en": "RSI(14)"},
    "macd_hist": {"zh": "MACD 柱", "en": "MACD histogram"},
}
FEATURE_ORDER = list(FEATURE_LABELS.keys())


class PredictionError(Exception):
    """预测流程可预期的业务错误（数据不足等），端点转 400。"""


@dataclass
class TinyLogisticModel:
    """世界上最简单的"模型"：逻辑回归。

        p(涨) = sigmoid(w · x + b)

    只有 n_features 个权重 + 1 个偏置需要学习，训练用手写批量梯度下降。
    """

    n_features: int
    weights: np.ndarray = field(default_factory=lambda: np.zeros(0))
    bias: float = 0.0
    mean: np.ndarray = field(default_factory=lambda: np.zeros(0))
    std: np.ndarray = field(default_factory=lambda: np.ones(0))

    def __post_init__(self) -> None:
        if self.weights.size == 0:
            self.weights = np.zeros(self.n_features)
        if self.mean.size == 0:
            self.mean = np.zeros(self.n_features)
        if self.std.size == 0:
            self.std = np.ones(self.n_features)

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        """输入原始特征（未标准化），返回涨的概率。支持单样本或批量。"""
        xs = self._standardize(np.atleast_2d(x))
        z = xs @ self.weights + self.bias
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def to_params(self) -> Dict[str, Any]:
        """序列化为可 JSON 存储的纯 Python 结构（供持久化）。"""
        return {
            "n_features": int(self.n_features),
            "weights": self.weights.astype(float).tolist(),
            "bias": float(self.bias),
            "mean": self.mean.astype(float).tolist(),
            "std": self.std.astype(float).tolist(),
        }

    @classmethod
    def from_params(cls, params: Dict[str, Any]) -> "TinyLogisticModel":
        """由持久化参数还原模型。"""
        return cls(
            n_features=int(params["n_features"]),
            weights=np.asarray(params["weights"], dtype=float),
            bias=float(params["bias"]),
            mean=np.asarray(params["mean"], dtype=float),
            std=np.asarray(params["std"], dtype=float),
        )


# ─────────────────────────────────────────────
# 第 1 步：特征工程
# ─────────────────────────────────────────────
def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return (dif - dea) * 2


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """由日线 DataFrame 构造技术因子矩阵。

    df 需包含列：date, open, high, low, close, volume
    返回：包含 FEATURE_ORDER 各列 + close + date 的 DataFrame（已 dropna）
    """
    data = df.copy()
    data = data.sort_values("date").reset_index(drop=True)
    close = data["close"].astype(float)
    volume = data["volume"].astype(float) if "volume" in data else pd.Series(np.nan, index=data.index)

    ma5 = close.rolling(5, min_periods=5).mean()
    ma10 = close.rolling(10, min_periods=10).mean()
    vol_ma5 = volume.rolling(5, min_periods=5).mean()

    feats = pd.DataFrame({"date": data["date"], "close": close})
    feats["ma5_dev"] = (close - ma5) / ma5
    feats["ma10_dev"] = (close - ma10) / ma10
    feats["prev_return"] = close.pct_change()
    feats["momentum_5"] = close.pct_change(periods=5)
    feats["volume_ratio"] = (volume / vol_ma5) - 1.0
    feats["rsi_14"] = _rsi(close, 14) / 100.0  # 归一到 ~0..1
    feats["macd_hist"] = _macd_hist(close) / close  # 相对价格归一

    feats = feats.replace([np.inf, -np.inf], np.nan)
    feats = feats.dropna(subset=FEATURE_ORDER + ["close"]).reset_index(drop=True)
    return feats


# ─────────────────────────────────────────────
# 第 2~3 步：切分 + 训练（手写梯度下降）
# ─────────────────────────────────────────────
def train_model(
    X: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int = 400,
    lr: float = 0.3,
    l2: float = 1e-3,
    train_ratio: float = 0.8,
) -> tuple[TinyLogisticModel, Dict[str, Any]]:
    """按时间顺序切分并训练逻辑回归，返回 (模型, 评估指标)。"""
    n = len(X)
    n_train = max(int(n * train_ratio), 1)
    X_train, X_valid = X[:n_train], X[n_train:]
    y_train, y_valid = y[:n_train], y[n_train:]

    # 用训练集统计量做标准化（避免信息泄露）
    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std == 0] = 1.0

    model = TinyLogisticModel(n_features=X.shape[1], mean=mean, std=std)
    Xs = (X_train - mean) / std
    m = len(Xs)

    for _ in range(epochs):
        z = Xs @ model.weights + model.bias
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        err = p - y_train
        grad_w = (Xs.T @ err) / m + l2 * model.weights
        grad_b = float(err.mean())
        model.weights -= lr * grad_w
        model.bias -= lr * grad_b

    def _accuracy(Xa: np.ndarray, ya: np.ndarray) -> Optional[float]:
        if len(Xa) == 0:
            return None
        preds = (model.predict_proba(Xa) >= 0.5).astype(int)
        return float((preds == ya).mean())

    # 基线：全部预测为「多数类」的准确率
    baseline = float(max(y_train.mean(), 1 - y_train.mean())) if len(y_train) else None

    metrics = {
        "train_accuracy": _accuracy(X_train, y_train),
        "valid_accuracy": _accuracy(X_valid, y_valid),
        "train_samples": int(len(X_train)),
        "valid_samples": int(len(X_valid)),
        "baseline_accuracy": baseline,
        "epochs": epochs,
        "learning_rate": lr,
    }
    return model, metrics


# ─────────────────────────────────────────────
# 第 4 步：未来价格路径推演
# ─────────────────────────────────────────────
def _future_trading_dates(last_date: str, horizon: int) -> List[str]:
    """从 last_date 起，生成 horizon 个交易日（跳过周末）。"""
    try:
        base = datetime.strptime(str(last_date)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        base = datetime.now()
    dates: List[str] = []
    cursor = base
    while len(dates) < horizon:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # 0-4 = 周一~周五
            dates.append(cursor.strftime("%Y-%m-%d"))
    return dates


def project_price_path(
    last_close: float,
    up_prob: float,
    daily_vol: float,
    horizon: int,
    last_date: str,
) -> tuple[List[Dict[str, Any]], float]:
    """依据预测概率与近期波动率推演价格路径，返回 (路径, 区间末期望收益%)。

    - 期望日收益 mu = (2p - 1) * daily_vol * k  （概率越极端、波动越大 → 漂移越强）
    - 中枢路径按 mu 复利推进
    - 上/下轨用波动率随时间 sqrt(t) 扩散，形成置信带
    """
    edge = (up_prob - 0.5) * 2.0  # ∈ [-1, 1]
    mu = edge * daily_vol * 0.8  # 单日期望收益
    path: List[Dict[str, Any]] = []
    dates = _future_trading_dates(last_date, horizon)
    for i in range(1, horizon + 1):
        center = last_close * ((1.0 + mu) ** i)
        band = daily_vol * np.sqrt(i) * last_close
        path.append(
            {
                "date": dates[i - 1],
                "day": i,
                "price": round(float(center), 4),
                "lower": round(float(max(center - band, 0.0)), 4),
                "upper": round(float(center + band), 4),
            }
        )
    expected_return_pct = round(((path[-1]["price"] / last_close) - 1.0) * 100, 2) if path else 0.0
    return path, expected_return_pct


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def _rows_to_df(rows: list) -> pd.DataFrame:
    """把 StockDaily ORM 行转换为特征工程所需的日线 DataFrame。"""
    records = [
        {
            "date": r.date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
        }
        for r in rows
    ]
    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def _load_cached_df(stock_code: str, lookback_days: int) -> pd.DataFrame:
    """从本地 stock_daily 表读取缓存的日线数据（复用主分析/回测已落的数据）。"""
    try:
        from datetime import date as _date

        from src.repositories.stock_repo import StockRepository

        repo = StockRepository()
        end = _date.today()
        # 多取一些日历日，保证 rolling/dropna 后仍有足够交易日样本
        start = end - timedelta(days=int((lookback_days + 90) * 1.6) + 30)
        rows = repo.get_range(stock_code, start, end)
        return _rows_to_df(rows) if rows else pd.DataFrame()
    except Exception as exc:  # noqa: BLE001 - 缓存读取失败不应中断预测
        logger.debug("读取 %s 的本地缓存失败，将走网络: %s", stock_code, exc)
        return pd.DataFrame()


def _is_cache_fresh(df: pd.DataFrame, max_stale_days: int = 4) -> bool:
    """判断缓存是否够新：最新一条数据距今不超过 max_stale_days 个自然日。

    盘后/周末场景下允许一定滞后；过期则触发联网增量刷新。
    """
    if df is None or df.empty:
        return False
    try:
        last = pd.to_datetime(df["date"].iloc[-1]).date()
    except Exception:  # noqa: BLE001
        return False
    return (datetime.now().date() - last).days <= max_stale_days


def _load_daily_df(
    stock_code: str,
    lookback_days: int,
    *,
    use_cache: bool = True,
    refresh: bool = True,
    resolve_name: bool = True,
) -> tuple[pd.DataFrame, Optional[str]]:
    """加载日线数据：读透缓存（read-through cache）策略。

    流程：
    1. 先查本地 stock_daily 缓存（与主分析/回测共享，命中即零联网）
    2. 缓存足够且够新 → 直接用
    3. 否则联网增量拉取，并写回 stock_daily 供下次复用
    4. 联网失败但缓存样本足够 → 降级用缓存；否则抛 PredictionError

    这样同一只票的反复预测不再每次联网，也与项目已有的数据缓存打通。
    """
    from data_provider.base import DataFetcherManager, DataFetchError

    min_rows = max(lookback_days // 2, 90)

    cached = _load_cached_df(stock_code, lookback_days) if use_cache else pd.DataFrame()
    if (
        not cached.empty
        and len(cached) >= min_rows
        and (not refresh or _is_cache_fresh(cached))
    ):
        logger.info("预测使用本地缓存数据: %s（%d 条，命中缓存免联网）", stock_code, len(cached))
        name = _safe_stock_name(stock_code) if resolve_name else None
        return cached, name

    manager = DataFetcherManager()
    try:
        # 多取一些，保证做完 rolling/dropna 后仍有足够样本
        df, source = manager.get_daily_data(stock_code, days=lookback_days + 60)
    except DataFetchError as exc:
        # 网络失败：若缓存尚可用则降级使用，否则抛业务错误
        if not cached.empty and len(cached) >= min_rows:
            logger.warning("联网获取 %s 失败，降级使用本地缓存（%d 条）", stock_code, len(cached))
            return cached, (_safe_stock_name(stock_code) if resolve_name else None)
        raise PredictionError(
            f"暂时无法获取 {stock_code} 的行情数据（数据源不可用或限流），请稍后重试或更换标的"
        ) from exc

    # 写回缓存供下次复用（失败不影响本次预测）
    if use_cache and df is not None and not df.empty:
        try:
            from src.repositories.stock_repo import StockRepository

            StockRepository().save_dataframe(df, stock_code, data_source=source or "prediction")
        except Exception as exc:  # noqa: BLE001
            logger.debug("回写 %s 缓存失败（忽略）: %s", stock_code, exc)

    return df, (_safe_stock_name(stock_code) if resolve_name else None)


def _safe_stock_name(stock_code: str) -> Optional[str]:
    """获取股票名称（失败返回 None，不影响预测）。"""
    try:
        from data_provider.base import DataFetcherManager

        return DataFetcherManager().get_stock_name(stock_code)
    except Exception:  # noqa: BLE001 - 名称是锦上添花
        return None


def _load_active_model(model_name: str) -> Optional[tuple[TinyLogisticModel, Dict[str, Any]]]:
    """尝试加载已持久化的激活模型；无或损坏则返回 None（退回实时训练）。"""
    try:
        from src.repositories.prediction_model_repo import PredictionModelRepository

        record = PredictionModelRepository().get_active(model_name)
    except Exception as exc:  # noqa: BLE001 - 加载失败不应阻断预测
        logger.debug("加载激活模型失败，退回实时训练: %s", exc)
        return None

    if not record:
        return None
    # 特征口径必须与当前一致，否则不可用
    if list(record.get("feature_names") or []) != FEATURE_ORDER:
        logger.warning(
            "激活模型 %s@%s 的特征集与当前不一致，退回实时训练",
            record.get("name"), record.get("version"),
        )
        return None
    try:
        model = TinyLogisticModel.from_params(record["params"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("激活模型参数损坏，退回实时训练: %s", exc)
        return None
    return model, record


def predict_stock(
    stock_code: str,
    *,
    lookback_days: int = 250,
    horizon_days: int = 5,
    history_points: int = 60,
    language: str = "zh",
    model_name: str = "trend_lr",
    use_saved_model: bool = True,
    persist: bool = True,
) -> Dict[str, Any]:
    """对单只股票执行完整预测流程，返回结构化结果字典。

    模型来源（model.source）：
    - "trained"：加载了已持久化的激活模型（由训练入口离线训练），直接推理
    - "on_the_fly"：库中无可用模型时，退回原有的"实时用该票历史训练"逻辑
    """
    if not stock_code or not stock_code.strip():
        raise PredictionError("股票代码不能为空")

    horizon_days = int(max(1, min(horizon_days, 20)))
    lookback_days = int(max(60, min(lookback_days, 800)))

    df, stock_name = _load_daily_df(stock_code, lookback_days)
    if df is None or df.empty:
        raise PredictionError(f"未获取到 {stock_code} 的历史行情数据")

    feats = build_features(df)
    if feats.empty:
        raise PredictionError(
            f"有效样本不足（仅 {len(feats)} 条），无法构造特征；请换个数据更全的标的或加大回溯天数"
        )

    # 标签：次日是否上涨（收盘价较当日上涨记 1）
    future_return = feats["close"].shift(-1) / feats["close"] - 1.0
    y_all = (future_return > 0).astype(int)

    # 最后一行没有"次日"标签，只能用于最终预测，不参与训练
    train_feats = feats.iloc[:-1]
    y = y_all.iloc[:-1].to_numpy()
    X = train_feats[FEATURE_ORDER].to_numpy(dtype=float)

    # 优先使用离线训练好的激活模型；否则退回实时训练
    loaded = _load_active_model(model_name) if use_saved_model else None
    if loaded is not None:
        model, record = loaded
        metrics = dict(record.get("metrics") or {})
        model_source = "trained"
        model_meta = {
            "source": "trained",
            "name": record.get("name"),
            "version": record.get("version"),
            "trained_at": record.get("created_at"),
            "trained_symbols": record.get("symbol_count"),
        }
    else:
        if len(train_feats) < 40:
            raise PredictionError(
                f"有效样本不足（仅 {len(feats)} 条），无法训练模型；请换个数据更全的标的或加大回溯天数"
            )
        model, metrics = train_model(X, y)
        model_source = "on_the_fly"
        model_meta = {"source": "on_the_fly", "name": None, "version": None}

    # 用最新一条特征做"次日"预测
    latest_row = feats.iloc[-1]
    latest_x = latest_row[FEATURE_ORDER].to_numpy(dtype=float)
    up_prob = float(model.predict_proba(latest_x)[0])
    direction = "up" if up_prob >= 0.5 else "down"
    confidence = round(abs(up_prob - 0.5) * 2.0, 4)  # 0..1

    # 因子贡献 = 标准化特征值 × 权重（正=推动上涨，负=推动下跌）
    standardized = (latest_x - model.mean) / model.std
    contributions = standardized * model.weights
    factors: List[Dict[str, Any]] = []
    for i, key in enumerate(FEATURE_ORDER):
        factors.append(
            {
                "key": key,
                "label": FEATURE_LABELS[key].get(language, FEATURE_LABELS[key]["zh"]),
                "value": round(float(latest_x[i]), 4),
                "weight": round(float(model.weights[i]), 4),
                "contribution": round(float(contributions[i]), 4),
            }
        )
    factors.sort(key=lambda f: abs(f["contribution"]), reverse=True)

    # 近期波动率（日收益标准差），给价格推演用
    daily_returns = feats["close"].pct_change().dropna()
    daily_vol = float(daily_returns.tail(60).std()) if len(daily_returns) else 0.02
    if not np.isfinite(daily_vol) or daily_vol <= 0:
        daily_vol = 0.02

    last_close = float(latest_row["close"])
    last_date = str(latest_row["date"])[:10]
    projected, expected_return_pct = project_price_path(
        last_close, up_prob, daily_vol, horizon_days, last_date
    )

    history = [
        {"date": str(r["date"])[:10], "close": round(float(r["close"]), 4)}
        for _, r in feats.tail(history_points).iterrows()
    ]

    # 补齐 ModelMetrics schema 必需字段（加载的历史模型可能缺省）
    metrics = {
        "train_accuracy": metrics.get("train_accuracy"),
        "valid_accuracy": metrics.get("valid_accuracy"),
        "train_samples": int(metrics.get("train_samples", 0) or 0),
        "valid_samples": int(metrics.get("valid_samples", 0) or 0),
        "baseline_accuracy": metrics.get("baseline_accuracy"),
        "epochs": int(metrics.get("epochs", 0) or 0),
        "learning_rate": float(metrics.get("learning_rate", 0.0) or 0.0),
    }

    result = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "as_of_date": last_date,
        "last_close": round(last_close, 4),
        "horizon_days": horizon_days,
        "direction": direction,
        "up_probability": round(up_prob, 4),
        "confidence": confidence,
        "expected_return_pct": expected_return_pct,
        "daily_volatility": round(daily_vol, 4),
        "history": history,
        "projected": projected,
        "factors": factors,
        "metrics": metrics,
        "model": {
            "algorithm": model_meta.get("algorithm", "logistic_regression_gd"),
            "feature_count": len(FEATURE_ORDER),
            "lookback_days": lookback_days,
            "trained_samples": metrics["train_samples"] + metrics["valid_samples"],
            "source": model_source,
            "version": model_meta.get("version"),
            "trained_at": model_meta.get("trained_at"),
        },
        "disclaimer": "本预测由轻量统计模型生成，仅供技术研究，不构成任何投资建议。",
    }

    if persist:
        _persist_prediction(result, model_meta)

    return result


def _persist_prediction(result: Dict[str, Any], model_meta: Dict[str, Any]) -> None:
    """把一次预测结果落库（失败仅记日志，绝不影响预测返回）。"""
    try:
        from datetime import date as _date

        from src.repositories.prediction_record_repo import PredictionRecordRepository

        try:
            as_of = _date.fromisoformat(str(result["as_of_date"])[:10])
        except (ValueError, TypeError):
            as_of = _date.today()

        PredictionRecordRepository().save({
            "code": str(result["stock_code"]).strip().upper(),
            "stock_name": result.get("stock_name"),
            "as_of_date": as_of,
            "horizon_days": result["horizon_days"],
            "direction": result["direction"],
            "up_probability": result["up_probability"],
            "confidence": result["confidence"],
            "expected_return_pct": result["expected_return_pct"],
            "last_close": result["last_close"],
            "model_source": result["model"].get("source"),
            "model_name": model_meta.get("name"),
            "model_version": result["model"].get("version"),
        })
    except Exception as exc:  # noqa: BLE001 - 落库失败不应影响预测
        logger.debug("预测结果落库失败（忽略）: %s", exc)
