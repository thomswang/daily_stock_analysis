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
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# 特征名称（顺序与特征矩阵列一致），中文含义供前端展示。
# 分六大类：趋势 / 动量 / 摆动 / 波动率 / K线形态 / 量价，全部仅用 OHLCV 派生、
# 严格回看（防未来函数）、并做无量纲归一化。
FEATURE_LABELS: Dict[str, Dict[str, str]] = {
    # ── 趋势（均线结构）──
    "ma5_dev": {"zh": "5日均线偏离度", "en": "MA5 deviation"},
    "ma10_dev": {"zh": "10日均线偏离度", "en": "MA10 deviation"},
    "ma20_dev": {"zh": "20日均线偏离度", "en": "MA20 deviation"},
    "ma_trend": {"zh": "均线多空排列(MA5-MA20)", "en": "MA5-MA20 trend"},
    # ── 动量 ──
    "prev_return": {"zh": "昨日涨跌幅", "en": "Prev-day return"},
    "momentum_5": {"zh": "5日动量", "en": "5-day momentum"},
    "momentum_10": {"zh": "10日动量", "en": "10-day momentum"},
    "momentum_20": {"zh": "20日动量", "en": "20-day momentum"},
    # ── 摆动指标（超买超卖）──
    "rsi_14": {"zh": "RSI(14)", "en": "RSI(14)"},
    "stoch_k_14": {"zh": "随机指标%K(14)", "en": "Stochastic %K(14)"},
    "boll_b_20": {"zh": "布林带%B(20)", "en": "Bollinger %B(20)"},
    "macd_hist": {"zh": "MACD 柱", "en": "MACD histogram"},
    # ── 波动率 ──
    "volatility_20": {"zh": "20日波动率", "en": "20-day volatility"},
    "atr_14": {"zh": "真实波幅ATR(14)", "en": "ATR(14) / price"},
    "range_pct": {"zh": "当日振幅", "en": "Intraday range"},
    # ── K线形态 ──
    "body_ratio": {"zh": "K线实体占比", "en": "Candle body ratio"},
    "close_position": {"zh": "收盘位置(当日区间)", "en": "Close position in range"},
    "gap_open": {"zh": "跳空幅度", "en": "Opening gap"},
    # ── 量价 ──
    "volume_ratio": {"zh": "成交量比率", "en": "Volume ratio"},
    "volume_trend": {"zh": "量能趋势(5/20)", "en": "Volume trend (5/20)"},
    "pv_corr_10": {"zh": "10日量价相关性", "en": "10-day price-volume corr"},
    # ── 换手率（数据源直供，非 OHLCV 可派生，含流通盘信息）──
    "turnover_norm": {"zh": "换手率(绝对)", "en": "Turnover rate"},
    "turnover_rel": {"zh": "换手率相对20日均值", "en": "Turnover vs 20d avg"},
    # ── 大盘/环境（需传入指数日线 market_df；缺失时中性填 0）──
    # A 股个股短期方向很大程度由大盘 β 驱动，补上环境维度是提准确率的最大杠杆。
    "mkt_ma20_dev": {"zh": "大盘20日均线偏离", "en": "Index MA20 deviation"},
    "mkt_momentum_20": {"zh": "大盘20日动量", "en": "Index 20-day momentum"},
    "mkt_rsi_14": {"zh": "大盘RSI(14)", "en": "Index RSI(14)"},
    "mkt_volatility_20": {"zh": "大盘20日波动率", "en": "Index 20-day volatility"},
    "rel_strength_5": {"zh": "相对大盘强弱(5日)", "en": "Rel. strength vs index (5d)"},
    "rel_strength_20": {"zh": "相对大盘强弱(20日)", "en": "Rel. strength vs index (20d)"},
}
FEATURE_ORDER = list(FEATURE_LABELS.keys())

# 数据源/入参可能不提供的扩展特征：整列缺失时以 0(中性)填充，
# 避免这些列的 NaN 把整只股票的样本在 dropna 时清空
# （兼容无换手率的兜底源，以及未传入 market_df 的调用方）。
_MARKET_FEATURES = (
    "mkt_ma20_dev", "mkt_momentum_20", "mkt_rsi_14", "mkt_volatility_20",
    "rel_strength_5", "rel_strength_20",
)
_OPTIONAL_FEATURES = ("turnover_norm", "turnover_rel") + _MARKET_FEATURES

# 标签口径（默认）：预测"未来 N 日"方向，而非噪声很大的"次日"。
# N 日趋势信噪比更高；threshold 可要求"涨幅超过阈值"才记为看涨（默认 0=纯方向）。
DEFAULT_LABEL_HORIZON = 5
DEFAULT_LABEL_THRESHOLD = 0.0


def make_labels(
    close: pd.Series,
    horizon: int = DEFAULT_LABEL_HORIZON,
    threshold: float = DEFAULT_LABEL_THRESHOLD,
) -> pd.Series:
    """构造"未来 horizon 日方向"标签（防未来函数，末 horizon 行无标签=NaN）。

        未来收益 = close[t+horizon] / close[t] - 1
        label    = 1 if 未来收益 > threshold else 0

    Args:
        close: 收盘价序列（需按日期升序）
        horizon: 前瞻交易日数（>=1）
        threshold: 记为"看涨"所需的最小未来收益（0=只看方向）

    Returns:
        与 close 等长的 float Series；最后 horizon 行为 NaN（无法计算未来收益）。
    """
    horizon = int(max(1, horizon))
    fwd_return = close.shift(-horizon) / close - 1.0
    labels = (fwd_return > threshold).astype(float)
    labels[fwd_return.isna()] = np.nan  # 末 horizon 行无未来数据，不可用于训练
    return labels


def make_forward_return(
    close: pd.Series,
    horizon: int = DEFAULT_LABEL_HORIZON,
) -> pd.Series:
    """连续「未来 horizon 日收益率」= close[t+h]/close[t] − 1（末 horizon 行=NaN）。

    供横截面排序标签使用：先算连续远期收益，再在同一交易日横向排名分强弱。
    """
    horizon = int(max(1, horizon))
    return close.shift(-horizon) / close - 1.0


def make_labels_relative(
    close: pd.Series,
    market_close: pd.Series,
    horizon: int = DEFAULT_LABEL_HORIZON,
    threshold: float = DEFAULT_LABEL_THRESHOLD,
) -> pd.Series:
    """构造"未来 horizon 日是否跑赢大盘"标签（相对收益，剔除大盘 β）。

        个股未来收益 = close[t+h]/close[t] − 1
        大盘未来收益 = market_close[t+h]/market_close[t] − 1
        label = 1 if (个股未来收益 − 大盘未来收益) > threshold else 0

    相比"绝对涨跌"标签，相对收益标签只考"选股能力(alpha)"：跌市里所有票都跌，
    但预测"哪只跌得比大盘少"依然有意义，因此不会像绝对标签那样在下跌市系统性失效。
    market_close 须已按个股交易日对齐（等长、同索引）；对不齐处为 NaN → 标签 NaN。

    Args:
        close: 个股收盘价（按日期升序）
        market_close: 与 close 等长、按同一交易日对齐的大盘指数收盘价
        horizon: 前瞻交易日数（>=1）
        threshold: 记为"跑赢"所需的最小超额收益（0=只要跑赢即可）

    Returns:
        与 close 等长的 float Series；无法计算处为 NaN。
    """
    horizon = int(max(1, horizon))
    stock_fwd = close.shift(-horizon) / close - 1.0
    mkt = pd.to_numeric(pd.Series(np.asarray(market_close, dtype=float)), errors="coerce")
    mkt.index = close.index
    mkt_fwd = mkt.shift(-horizon) / mkt - 1.0
    excess = stock_fwd - mkt_fwd
    labels = (excess > threshold).astype(float)
    labels[excess.isna()] = np.nan  # 个股或大盘未来收益缺失 → 不可用于训练
    return labels


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
    algorithm: str = "logistic_regression_gd"

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
            "algorithm": "logistic_regression_gd",
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


@dataclass
class LightGBMModel:
    """梯度提升树模型（LightGBM）封装：与 TinyLogisticModel 同构（predict_proba /
    to_params / from_params），供 train_model / 预测 / 回测按 algorithm 分支复用。

    - 能学习特征非线性与交互（如"跌市里压低看涨""不同行业看不同因子"），
      是相对线性逻辑回归的能力升级。
    - 序列化用 Booster.model_to_string()（纯文本，JSON 可存）。
    """

    n_features: int
    booster: Any = None
    algorithm: str = "lightgbm_gbdt"

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        """输入原始特征（未标准化，树模型不需要标准化），返回涨/正类概率。"""
        arr = np.atleast_2d(np.asarray(x, dtype=float))
        return np.asarray(self.booster.predict(arr), dtype=float)

    def shap_contrib(self, x: np.ndarray) -> np.ndarray:
        """单样本各特征的 SHAP 贡献（有符号，长度=n_features，已去掉末尾 bias 项）。"""
        arr = np.atleast_2d(np.asarray(x, dtype=float))
        contrib = np.asarray(self.booster.predict(arr, pred_contrib=True), dtype=float)
        return contrib[0, : self.n_features]

    def feature_importance(self) -> np.ndarray:
        """全局特征重要度（gain）。"""
        try:
            return np.asarray(self.booster.feature_importance(importance_type="gain"), dtype=float)
        except Exception:  # noqa: BLE001
            return np.zeros(self.n_features)

    def to_params(self) -> Dict[str, Any]:
        return {
            "algorithm": "lightgbm_gbdt",
            "n_features": int(self.n_features),
            "booster": self.booster.model_to_string() if self.booster is not None else "",
        }

    @classmethod
    def from_params(cls, params: Dict[str, Any]) -> "LightGBMModel":
        import lightgbm as lgb

        booster = lgb.Booster(model_str=params["booster"]) if params.get("booster") else None
        return cls(n_features=int(params["n_features"]), booster=booster)


def model_from_params(params: Dict[str, Any]):
    """按 params['algorithm'] 还原对应模型（旧逻辑回归模型无该字段，默认逻辑回归）。"""
    algo = (params or {}).get("algorithm", "logistic_regression_gd")
    if algo == "lightgbm_gbdt":
        return LightGBMModel.from_params(params)
    return TinyLogisticModel.from_params(params)


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


def _align_market_close(dates: pd.Series, market_df: Optional[pd.DataFrame]) -> pd.Series:
    """把大盘指数收盘价按交易日对齐到个股的 date 序列，返回等长(同索引 0..n-1)的 Series。

    market_df=None/空/无 close 时返回全 NaN（调用方按中性 0 处理，行为向后兼容）。
    仅左连接、不前向填充：对不上的交易日留 NaN，避免用其它日的大盘值污染。
    """
    n = len(dates)
    empty = pd.Series(np.nan, index=range(n), dtype=float)
    if market_df is None or market_df.empty or "close" not in market_df.columns:
        return empty
    left = pd.DataFrame({"_d": pd.to_datetime(pd.Series(list(dates)), errors="coerce")})
    right = pd.DataFrame({
        "_d": pd.to_datetime(market_df["date"], errors="coerce"),
        "_mc": pd.to_numeric(market_df["close"], errors="coerce"),
    }).dropna(subset=["_d"]).drop_duplicates(subset=["_d"], keep="last")
    merged = left.merge(right, on="_d", how="left")
    return merged["_mc"].astype(float).reset_index(drop=True)


def build_features(df: pd.DataFrame, market_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """由日线 DataFrame 构造技术因子矩阵。

    df 需包含列：date, open, high, low, close, volume（可选 turnover_rate）
    market_df：可选的大盘指数日线（需含 date, close）。传入则派生"大盘环境/相对
        强弱"特征；不传则这些列整列缺失、按 _OPTIONAL_FEATURES 中性填 0（行为向后兼容）。
    返回：包含 FEATURE_ORDER 各列 + close + date 的 DataFrame（已 dropna）

    所有因子仅用当日及更早数据（rolling/shift 回看），保证防未来函数；
    并做无量纲归一化（比例/相对价格/0~1 区间），避免量纲干扰梯度下降。

    TODO(准确率优化 · 第二阶段：模型升级 LightGBM) —— 视第一阶段回测结果再定。
    若线性模型天花板明显（环境特征重训回测后仍不足），再引入 LightGBM（需加依赖，
    并在 train_model()/预测加载处按 algorithm 字段分支）。
    """
    data = df.copy()
    data = data.sort_values("date").reset_index(drop=True)
    close = data["close"].astype(float)
    open_ = data["open"].astype(float) if "open" in data else close
    high = data["high"].astype(float) if "high" in data else close
    low = data["low"].astype(float) if "low" in data else close
    volume = data["volume"].astype(float) if "volume" in data else pd.Series(np.nan, index=data.index)
    # 换手率（数据源直供，%）；缺列则整列 NaN，后续以 0 中性填充
    turnover = (
        pd.to_numeric(data["turnover_rate"], errors="coerce")
        if "turnover_rate" in data.columns
        else pd.Series(np.nan, index=data.index)
    )

    ret = close.pct_change()
    prev_close = close.shift(1)

    ma5 = close.rolling(5, min_periods=5).mean()
    ma10 = close.rolling(10, min_periods=10).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    vol_ma5 = volume.rolling(5, min_periods=5).mean()
    vol_ma20 = volume.rolling(20, min_periods=20).mean()

    # 当日区间/实体（防止除零：用 NaN 兜底，最后 dropna）
    hl = (high - low).replace(0, np.nan)

    # 随机指标 %K（价格在近 14 日高低区间的位置）
    low14 = low.rolling(14, min_periods=14).min()
    high14 = high.rolling(14, min_periods=14).max()
    stoch_range = (high14 - low14).replace(0, np.nan)

    # 布林带 %B（价格在 ±2σ 通道内的相对位置）
    boll_upper = ma20 + 2.0 * std20
    boll_lower = ma20 - 2.0 * std20
    boll_range = (boll_upper - boll_lower).replace(0, np.nan)

    # 真实波幅 ATR(14)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr14 = tr.rolling(14, min_periods=14).mean()

    feats = pd.DataFrame({"date": data["date"], "close": close})
    # ── 趋势 ──
    feats["ma5_dev"] = (close - ma5) / ma5
    feats["ma10_dev"] = (close - ma10) / ma10
    feats["ma20_dev"] = (close - ma20) / ma20
    feats["ma_trend"] = (ma5 - ma20) / ma20
    # ── 动量 ──
    feats["prev_return"] = ret
    feats["momentum_5"] = close.pct_change(periods=5)
    feats["momentum_10"] = close.pct_change(periods=10)
    feats["momentum_20"] = close.pct_change(periods=20)
    # ── 摆动指标 ──
    feats["rsi_14"] = _rsi(close, 14) / 100.0  # 归一到 ~0..1
    feats["stoch_k_14"] = (close - low14) / stoch_range
    feats["boll_b_20"] = (close - boll_lower) / boll_range
    feats["macd_hist"] = _macd_hist(close) / close  # 相对价格归一
    # ── 波动率 ──
    feats["volatility_20"] = ret.rolling(20, min_periods=20).std()
    feats["atr_14"] = atr14 / close
    feats["range_pct"] = (high - low) / close
    # ── K线形态 ──
    feats["body_ratio"] = (close - open_) / hl
    feats["close_position"] = (close - low) / hl
    feats["gap_open"] = (open_ - prev_close) / prev_close
    # ── 量价 ──
    feats["volume_ratio"] = (volume / vol_ma5) - 1.0
    feats["volume_trend"] = (vol_ma5 / vol_ma20) - 1.0
    feats["pv_corr_10"] = ret.rolling(10, min_periods=10).corr(volume.pct_change())
    # ── 换手率 ──
    turn_ma20 = turnover.rolling(20, min_periods=5).mean()
    feats["turnover_norm"] = turnover / 100.0                              # 绝对换手率(小数)
    feats["turnover_rel"] = (turnover / turn_ma20.replace(0, np.nan)) - 1.0  # 相对20日均值的活跃度

    # ── 大盘/环境（需 market_df；缺失则整列 NaN，后续中性填 0）──
    mkt_close = _align_market_close(data["date"], market_df)
    mkt_ret = mkt_close.pct_change(fill_method=None)
    mkt_ma20 = mkt_close.rolling(20, min_periods=20).mean()
    feats["mkt_ma20_dev"] = (mkt_close - mkt_ma20) / mkt_ma20
    feats["mkt_momentum_20"] = mkt_close.pct_change(periods=20, fill_method=None)
    feats["mkt_rsi_14"] = _rsi(mkt_close, 14) / 100.0
    feats["mkt_volatility_20"] = mkt_ret.rolling(20, min_periods=20).std()
    # 相对强弱：个股收益 − 大盘收益（>0 表示跑赢大盘）
    feats["rel_strength_5"] = (
        close.pct_change(periods=5, fill_method=None)
        - mkt_close.pct_change(periods=5, fill_method=None)
    )
    feats["rel_strength_20"] = (
        close.pct_change(periods=20, fill_method=None)
        - mkt_close.pct_change(periods=20, fill_method=None)
    )

    feats = feats.replace([np.inf, -np.inf], np.nan)
    # 可选特征（换手率）在数据源缺失时整列为 NaN：填 0 中性值，
    # 避免把无换手率的股票在下面 dropna 时整只清空。
    for _col in _OPTIONAL_FEATURES:
        if _col in feats.columns:
            feats[_col] = feats[_col].fillna(0.0)
    feats = feats.dropna(subset=FEATURE_ORDER + ["close"]).reset_index(drop=True)
    return feats


# ─────────────────────────────────────────────
# 第 2~3 步：切分 + 训练（手写梯度下降）
# ─────────────────────────────────────────────
def _fit_logistic(
    X_raw: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int,
    lr: float,
    l2: float,
    class_weight: bool,
) -> TinyLogisticModel:
    """在给定样本上拟合逻辑回归（手写批量梯度下降），返回已训练模型。

    class_weight=True 时按类别频率反比加权，纠正正/负样本不平衡带来的方向偏移
    （牛市正样本多、熊市负样本多，无权重会系统性偏向多数类）。
    标准化统计量取自传入样本本身（调用方负责保证无信息泄露）。
    """
    mean = X_raw.mean(axis=0)
    std = X_raw.std(axis=0)
    std[std == 0] = 1.0

    model = TinyLogisticModel(n_features=X_raw.shape[1], mean=mean, std=std)
    Xs = (X_raw - mean) / std
    m = len(Xs)
    if m == 0:
        return model

    # 样本权重：类别频率反比（均衡）；关闭则等权
    if class_weight:
        pos_rate = float(np.clip(y.mean(), 1e-6, 1 - 1e-6))
        w_pos = 0.5 / pos_rate
        w_neg = 0.5 / (1.0 - pos_rate)
        sw = np.where(y >= 0.5, w_pos, w_neg)
    else:
        sw = np.ones(m)
    sw_sum = float(sw.sum()) or 1.0

    for _ in range(epochs):
        z = Xs @ model.weights + model.bias
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        err = (p - y) * sw
        grad_w = (Xs.T @ err) / sw_sum + l2 * model.weights
        grad_b = float(err.sum() / sw_sum)
        model.weights -= lr * grad_w
        model.bias -= lr * grad_b
    return model


# LightGBM 默认超参：强正则 + 早停，适配金融数据高噪声、抑制过拟合。
# 设计取向：宁可欠拟合也别背题——降低树复杂度、提高叶最小样本、加 L1/L2 与列/行采样，
# n_estimators 只是上限，实际训练轮数由验证集早停决定（见 _fit_lightgbm）。
_GBM_DEFAULTS = dict(
    num_leaves=15,
    max_depth=5,
    learning_rate=0.02,
    n_estimators=3000,          # 仅上限；配合早停通常远用不满
    min_child_samples=800,
    subsample=0.7,
    colsample_bytree=0.6,
    reg_lambda=5.0,
    reg_alpha=1.0,
    min_split_gain=0.0,
    early_stopping_rounds=50,   # 验证集 logloss 连续 N 轮不改善即停
)


def _fit_lightgbm(
    X_raw: np.ndarray,
    y: np.ndarray,
    *,
    class_weight: bool,
    params: Optional[Dict[str, Any]] = None,
    X_valid: Optional[np.ndarray] = None,
    y_valid: Optional[np.ndarray] = None,
    num_boost_round: Optional[int] = None,
) -> "LightGBMModel":
    """在给定样本上训练 LightGBM 二分类模型，返回封装模型。

    class_weight=True 时用 scale_pos_weight=负/正 平衡类别（对齐逻辑回归口径）。
    树模型无需标准化，直接吃原始特征。

    早停/轮数控制：
    - 传入 (X_valid,y_valid) 且未指定 num_boost_round 时，用验证集做早停，
      训练到 logloss 不再改善为止（best_iteration 记录在 booster 上）。
    - 传入 num_boost_round 时，固定训练该轮数、不早停（用于「全量 refit 复用
      早停得到的最优轮数」，避免全量数据上又过拟合）。
    """
    import lightgbm as lgb

    hp = dict(_GBM_DEFAULTS)
    if params:
        hp.update(params)
    max_rounds = int(hp.pop("n_estimators", 3000))
    early = int(hp.pop("early_stopping_rounds", 0) or 0)

    lgb_params: Dict[str, Any] = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": int(hp["num_leaves"]),
        "max_depth": int(hp.get("max_depth", -1)),
        "learning_rate": float(hp["learning_rate"]),
        "min_child_samples": int(hp["min_child_samples"]),
        "bagging_fraction": float(hp["subsample"]),
        "bagging_freq": 1,
        "feature_fraction": float(hp["colsample_bytree"]),
        "lambda_l2": float(hp["reg_lambda"]),
        "lambda_l1": float(hp.get("reg_alpha", 0.0)),
        "min_split_gain": float(hp.get("min_split_gain", 0.0)),
        "verbosity": -1,
        "num_threads": 0,
    }
    if class_weight:
        pos = float(max(y.sum(), 1.0))
        neg = float(max(len(y) - y.sum(), 1.0))
        lgb_params["scale_pos_weight"] = neg / pos

    n_features = X_raw.shape[1] if X_raw.ndim == 2 else 0
    if len(X_raw) == 0:
        return LightGBMModel(n_features=n_features, booster=None)

    dtrain = lgb.Dataset(X_raw, label=y, free_raw_data=False)

    # 固定轮数模式（全量 refit 复用早停最优轮数）
    if num_boost_round is not None:
        booster = lgb.train(lgb_params, dtrain, num_boost_round=int(max(1, num_boost_round)))
        return LightGBMModel(n_features=n_features, booster=booster)

    # 早停模式（需验证集）
    use_early = early > 0 and X_valid is not None and len(X_valid) > 0
    if use_early:
        dvalid = lgb.Dataset(X_valid, label=y_valid, reference=dtrain, free_raw_data=False)
        booster = lgb.train(
            lgb_params, dtrain, num_boost_round=max_rounds,
            valid_sets=[dvalid],
            callbacks=[lgb.early_stopping(early, verbose=False), lgb.log_evaluation(0)],
        )
    else:
        booster = lgb.train(lgb_params, dtrain, num_boost_round=max_rounds)
    return LightGBMModel(n_features=n_features, booster=booster)


def _fit_model(
    algorithm: str,
    X: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int,
    lr: float,
    l2: float,
    class_weight: bool,
    gbm_params: Optional[Dict[str, Any]] = None,
    X_valid: Optional[np.ndarray] = None,
    y_valid: Optional[np.ndarray] = None,
    num_boost_round: Optional[int] = None,
):
    """按 algorithm 选择拟合器：'lightgbm' → 树模型；否则逻辑回归。

    lightgbm 分支支持早停(传 X_valid/y_valid)与固定轮数(传 num_boost_round)；
    逻辑回归分支忽略这些参数。
    """
    if algorithm == "lightgbm":
        return _fit_lightgbm(
            X, y, class_weight=class_weight, params=gbm_params,
            X_valid=X_valid, y_valid=y_valid, num_boost_round=num_boost_round,
        )
    return _fit_logistic(X, y, epochs=epochs, lr=lr, l2=l2, class_weight=class_weight)


def _time_split_indices(
    dates: np.ndarray, train_ratio: float, embargo: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按“日历日期”做全局时序切分，返回 (排序序, 训练掩码, 验证掩码)。

    多股票汇聚样本按股票堆叠时，行位置 ≠ 时间先后，直接按位置切会退化成“按股票
    切分”（train/valid 时间段重叠 → 泄露 → 验证准确率虚高）。这里改为：把所有样本
    按真实日期排序，取 train_ratio 分位处的日期为切点，切点之后为验证段；训练段再
    往前挖掉 embargo 个交易日，隔断“未来 N 日标签”跨越切点造成的重叠。
    """
    d = pd.to_datetime(pd.Series(dates), errors="coerce").to_numpy()
    order = np.argsort(d, kind="stable")
    d_sorted = d[order]
    uniq = np.unique(d_sorted[~pd.isna(d_sorted)])
    if len(uniq) < 2:
        # 无法按日期切分：退回“全部训练、无验证”
        keep = ~pd.isna(d_sorted)
        return order, keep, np.zeros(len(d_sorted), dtype=bool)
    cut_i = min(max(int(len(uniq) * train_ratio), 1), len(uniq) - 1)
    cutoff = uniq[cut_i]  # 第一个验证日
    emb_i = max(cut_i - int(max(0, embargo)), 0)
    train_hi = uniq[emb_i]  # 训练日期须严格早于此，隔出 embargo 缓冲带
    train_mask = d_sorted < train_hi
    valid_mask = d_sorted >= cutoff
    return order, train_mask, valid_mask


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int = 400,
    lr: float = 0.3,
    l2: float = 1e-3,
    train_ratio: float = 0.8,
    embargo: int = 0,
    class_weight: bool = True,
    refit_full: bool = True,
    dates: Optional[np.ndarray] = None,
    algorithm: str = "logistic",
    gbm_params: Optional[Dict[str, Any]] = None,
) -> tuple[Any, Dict[str, Any]]:
    """按时间顺序切分并训练逻辑回归，返回 (上线模型, 评估指标)。

    相比朴素时序切分，这里修正了几处影响真实准确率/指标可信度的问题：

    1. **全局时序切分（dates）**：当传入与 X 行对齐的 ``dates`` 时，按真实日历日期
       切分 train/valid（而非按行位置）。这对“多股票汇聚样本”至关重要——不传 dates
       时行位置=按股票堆叠，位置切分会退化成按股票切、时间段重叠而泄露，验证准确率
       虚高。传 dates 后得到诚实的“用过去预测未来”样本外指标。
    2. **purge/embargo 切分**：标签为「未来 N 日方向」按天滑动，切点附近的样本未来
       收益会跨进验证段造成泄露。故在 train 与 valid 之间挖掉 ``embargo``(=标签前瞻
       天数) 个交易日（有 dates 按日期挖，无 dates 按行挖）。
    3. **全量 refit 上线**：用切分评估拿到诚实指标后，最终模型改用**全部有标签
       样本**重新拟合（``refit_full``），确保上线模型吃到最新一段行情。
    4. **类别权重**：见 :func:`_fit_logistic`，纠正正/负样本不平衡的方向偏移。

    TODO(第二阶段·模型升级 LightGBM，下次续做): 若第一阶段(改标签+环境特征)
    回测后线性模型天花板明显，可在此新增 LightGBM 训练分支（需加 lightgbm 依赖，
    加依赖前先确认收益）。详见 build_features 顶部 TODO 的完整路线说明。
    """
    n = len(X)
    embargo = int(max(0, embargo))

    if dates is not None and len(dates) == n and n > 0:
        # —— 全局时序切分（按日历日期）——
        order, train_mask, valid_mask = _time_split_indices(
            np.asarray(dates), train_ratio, embargo,
        )
        X_ord, y_ord = X[order], y[order]
        X_train, y_train = X_ord[train_mask], y_ord[train_mask]
        X_valid, y_valid = X_ord[valid_mask], y_ord[valid_mask]
    else:
        # —— 按行位置切分（单票时序，行本就按日期升序）——
        n_train = max(int(n * train_ratio), 1)
        valid_start = min(n_train + embargo, n)  # 挖掉 embargo 行，隔断标签重叠
        X_train, y_train = X[:n_train], y[:n_train]
        X_valid, y_valid = X[valid_start:], y[valid_start:]

    # 评估用模型：仅在训练段拟合，验证段完全样本外（诚实估计泛化能力）
    # lightgbm 用验证段做早停，训练到不再改善即止（抑制过拟合）。
    eval_model = _fit_model(
        algorithm, X_train, y_train,
        epochs=epochs, lr=lr, l2=l2, class_weight=class_weight, gbm_params=gbm_params,
        X_valid=X_valid, y_valid=y_valid,
    )

    # 记录 lightgbm 早停得到的最优轮数，供全量 refit 复用（避免全量数据上再次过拟合）
    best_rounds: Optional[int] = None
    if algorithm == "lightgbm":
        booster = getattr(eval_model, "booster", None)
        bi = int(getattr(booster, "best_iteration", 0) or 0) if booster is not None else 0
        if bi > 0:
            best_rounds = bi

    def _accuracy(model: Any, Xa: np.ndarray, ya: np.ndarray) -> Optional[float]:
        if len(Xa) == 0:
            return None
        preds = (model.predict_proba(Xa) >= 0.5).astype(int)
        return float((preds == ya).mean())

    # 基线：全部预测为「多数类」的准确率（valid 段口径，用于判断模型是否真有增量）
    baseline = float(max(y_valid.mean(), 1 - y_valid.mean())) if len(y_valid) else (
        float(max(y_train.mean(), 1 - y_train.mean())) if len(y_train) else None
    )

    metrics = {
        "train_accuracy": _accuracy(eval_model, X_train, y_train),
        "valid_accuracy": _accuracy(eval_model, X_valid, y_valid),
        "train_samples": int(len(X_train)),
        "valid_samples": int(len(X_valid)),
        "baseline_accuracy": baseline,
        "epochs": epochs,
        "learning_rate": lr,
    }
    if best_rounds is not None:
        metrics["best_iteration"] = best_rounds

    # 上线模型：用全部有标签样本重训，吃到最新行情；否则退回评估模型。
    # lightgbm 复用早停得到的最优轮数(best_rounds)，全量数据上按固定轮数训练，不再过拟合。
    if refit_full and n > 0:
        final_model = _fit_model(
            algorithm, X, y,
            epochs=epochs, lr=lr, l2=l2, class_weight=class_weight, gbm_params=gbm_params,
            num_boost_round=best_rounds,
        )
    else:
        final_model = eval_model

    metrics["algorithm"] = "lightgbm_gbdt" if algorithm == "lightgbm" else "logistic_regression_gd"
    return final_model, metrics


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
def _rows_to_df(rows: list, quote_by_date: Optional[Dict[date, Any]] = None) -> pd.DataFrame:
    """把 StockDaily ORM 行转换为特征工程所需的日线 DataFrame。

    换手率**权威源**为 stock_daily_quote（quote --date 截面，逐日 40 列）；
    stock_daily 的同名列已 deprecated（各源 kline 口径差异大，会污染训练数据）。
    如需临时兼容旧库，设置环境变量 ``DSA_LEGACY_TURNOVER_FALLBACK=1`` 打开退回。
    """
    quote_by_date = quote_by_date or {}
    allow_legacy = os.getenv("DSA_LEGACY_TURNOVER_FALLBACK", "").lower() in ("1", "true", "yes")
    records = []
    for r in rows:
        q = quote_by_date.get(r.date)
        if q is not None:
            turnover = q.turnover_rate
        elif allow_legacy:
            turnover = getattr(r, "turnover_rate", None)
        else:
            turnover = None
        records.append({
            "date": r.date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "turnover_rate": turnover,
        })
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
        quote_rows = repo.get_quote_range(stock_code, start, end)
        quote_by_date = {r.date: r for r in quote_rows}
        return _rows_to_df(rows, quote_by_date) if rows else pd.DataFrame()
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


# 全市场 β 基准指数（沪深300）；个股环境特征统一以它为参照。
DEFAULT_MARKET_INDEX = "000300.SH"
_MARKET_DF_CACHE: Dict[str, pd.DataFrame] = {}


def load_market_df(index_code: str = DEFAULT_MARKET_INDEX) -> pd.DataFrame:
    """读取大盘指数日线（date, close）供环境特征使用；进程内缓存，避免逐票重复查库。

    数据来自本地 stock_daily（由 backfill_index.py 回填）。查不到则返回空 DataFrame，
    build_features 会据此把大盘特征中性填 0（不影响其余流程）。
    """
    if index_code in _MARKET_DF_CACHE:
        return _MARKET_DF_CACHE[index_code]
    df = pd.DataFrame()
    try:
        from datetime import date as _date

        from src.repositories.stock_repo import StockRepository

        rows = StockRepository().get_range(index_code, _date(2000, 1, 1), _date.today())
        if rows:
            df = pd.DataFrame([{"date": r.date, "close": r.close} for r in rows])
            df = df.sort_values("date").reset_index(drop=True)
    except Exception as exc:  # noqa: BLE001 - 缺指数不应中断预测/训练
        logger.debug("加载大盘指数 %s 失败（将中性处理）: %s", index_code, exc)
    if df.empty:
        logger.warning("大盘指数 %s 无本地数据，环境特征将中性填 0；建议先跑 backfill_index.py", index_code)
    _MARKET_DF_CACHE[index_code] = df
    return df


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
        model = model_from_params(record["params"])
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

    feats = build_features(df, market_df=load_market_df())
    if feats.empty:
        raise PredictionError(
            f"有效样本不足（仅 {len(feats)} 条），无法构造特征；请换个数据更全的标的或加大回溯天数"
        )

    # 标签：未来 horizon_days 日是否上涨（与下方价格路径推演的周期一致）
    # 相比"次日涨跌"，N 日趋势信噪比更高。末 horizon 行无未来数据、不参与训练。
    label_horizon = horizon_days
    y_series = make_labels(feats["close"], horizon=label_horizon)

    # 仅保留有标签的行用于训练（等价于剔除末尾 horizon 行）
    train_feats = feats.iloc[:-label_horizon] if label_horizon < len(feats) else feats.iloc[0:0]
    y = y_series.iloc[:-label_horizon].to_numpy() if label_horizon < len(feats) else np.zeros(0)
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
        model, metrics = train_model(X, y, embargo=label_horizon)
        model_source = "on_the_fly"
        model_meta = {"source": "on_the_fly", "name": None, "version": None}

    # 用最新一条特征做"次日"预测
    latest_row = feats.iloc[-1]
    latest_x = latest_row[FEATURE_ORDER].to_numpy(dtype=float)
    up_prob = float(model.predict_proba(latest_x)[0])
    direction = "up" if up_prob >= 0.5 else "down"
    confidence = round(abs(up_prob - 0.5) * 2.0, 4)  # 0..1

    # 因子贡献（正=推动上涨/正类，负=推动下跌）：
    # - 逻辑回归：标准化特征值 × 权重
    # - LightGBM：单样本 SHAP 贡献（有符号），weight 用全局 gain 重要度作参考
    if isinstance(model, LightGBMModel):
        contributions = model.shap_contrib(latest_x)
        importance = model.feature_importance()
        imp_sum = float(importance.sum()) or 1.0
        weights_ref = importance / imp_sum
    else:
        standardized = (latest_x - model.mean) / model.std
        contributions = standardized * model.weights
        weights_ref = model.weights
    factors: List[Dict[str, Any]] = []
    for i, key in enumerate(FEATURE_ORDER):
        factors.append(
            {
                "key": key,
                "label": FEATURE_LABELS[key].get(language, FEATURE_LABELS[key]["zh"]),
                "value": round(float(latest_x[i]), 4),
                "weight": round(float(weights_ref[i]), 4),
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
            "algorithm": getattr(model, "algorithm", "logistic_regression_gd"),
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


DEFAULT_RANK_MODEL = "trend_xsec"


def load_ranking_model(model_name: str = DEFAULT_RANK_MODEL):
    """加载已激活的横截面打分模型，返回 (model, record)；无则抛 PredictionError。"""
    loaded = _load_active_model(model_name)
    if loaded is None:
        raise PredictionError(
            f"未找到已激活的横截面模型 {model_name}；请先运行 "
            f"train_model.py --all --label-mode cross_section --algorithm lightgbm --name {model_name}"
        )
    return loaded


def score_codes(
    codes: List[str],
    *,
    model,
    market_df: Optional[pd.DataFrame] = None,
    lookback_days: int = 250,
    resolve_name: bool = True,
    refresh: bool = True,
) -> List[Dict[str, Any]]:
    """给一批股票逐票打「强弱分」(单票最新特征喂横截面模型)。

    纯打分、不排序不加权(排序/分位/权重由调用方按其票池口径计算)，供
    rank_stocks 与选股推荐服务共用。单票失败自动跳过、不中断整批。

    refresh=False 时仅用本地缓存(全市场扫描必用，避免上千次联网)。

    Returns: [{code, stock_name, strength_score, last_close, as_of_date}, ...]
    """
    if market_df is None:
        market_df = load_market_df()
    out: List[Dict[str, Any]] = []
    for raw in codes:
        code = (raw or "").strip()
        if not code:
            continue
        try:
            df, name = _load_daily_df(code, lookback_days, refresh=refresh, resolve_name=resolve_name)
            if df is None or df.empty:
                continue
            feats = build_features(df, market_df=market_df)
            if feats.empty:
                continue
            latest = feats.iloc[-1]
            x = latest[FEATURE_ORDER].to_numpy(dtype=float)
            out.append({
                "code": code,
                "stock_name": name,
                "strength_score": round(float(model.predict_proba(x)[0]), 4),
                "last_close": round(float(latest["close"]), 4),
                "as_of_date": str(latest["date"])[:10],
            })
        except PredictionError as exc:
            logger.info("[score] 跳过 %s：%s", code, exc)
        except Exception as exc:  # noqa: BLE001 - 单票失败不应中断整批
            logger.warning("[score] %s 打分异常，跳过：%s", code, exc)
    return out


def attach_ranking(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """在给定票池内计算横截面分位排名 + 概率加权多头建议权重(∑=1)，就地补字段并按强→弱排序。"""
    if not items:
        return items
    s = np.array([it["strength_score"] for it in items], dtype=float)
    pct = pd.Series(s).rank(pct=True, method="average").to_numpy()
    pos = np.clip(pct - pct.mean(), 0.0, None)
    w = pos / (pos.sum() or 1.0)
    order = np.argsort(-s)
    for rank_i, idx in enumerate(order, start=1):
        items[idx]["rank"] = rank_i
        items[idx]["rank_pct"] = round(float(pct[idx]), 4)
        items[idx]["suggested_weight"] = round(float(w[idx]), 4)
    return [items[i] for i in order]


def rank_stocks(
    codes: List[str],
    *,
    model_name: str = DEFAULT_RANK_MODEL,
    lookback_days: int = 250,
    top_n: Optional[int] = None,
    language: str = "zh",
) -> Dict[str, Any]:
    """横截面选股打分：用激活的横截面模型给一批股票打「强弱分」并排序。

    这是经 walk-forward / CPCV 验证、扣成本能跑赢市场的用法：模型对单票的绝对
    涨跌预测能力有限(≈天花板)，但对「同一时点谁比谁强」的横向排序有稳定 alpha
    (CPCV 28 条样本外路径 Rank IC 100% 为正)。故按用途拆分：
        - /predict  单票方向(绝对涨跌)——沿用旧口径，向后兼容
        - rank_stocks 横截面强弱打分+概率加权建议权重——本函数

    流程：逐票取最新特征 → 横截面模型输出「属当日强势前50%的概率」=强弱分 →
    在传入这批票内做分位排名 → 概率加权(去均值后取正、归一)给出多头建议权重。

    Args:
        codes: 待打分的股票代码列表
        model_name: 横截面模型名(默认 trend_xsec，须为已激活的 cross_section 模型)
        lookback_days: 每只票的回溯天数(用于构造特征)
        top_n: 只返回强弱分最高的前 N 只(None=全部返回)
        language: 预留(当前仅影响错误文案)

    Returns:
        {as_of_date, model:{...}, count, items:[{code, stock_name, strength_score,
         rank, rank_pct, suggested_weight, last_close, as_of_date}], disclaimer}
    """
    codes = [c.strip() for c in (codes or []) if c and c.strip()]
    if not codes:
        raise PredictionError("待打分的股票列表为空")

    model, record = load_ranking_model(model_name)
    scored = score_codes(codes, model=model, lookback_days=lookback_days)
    if not scored:
        raise PredictionError("所有股票均无足够数据完成打分；请检查代码或稍后重试")

    n = len(scored)
    items = attach_ranking(scored)
    if top_n is not None and top_n > 0:
        items = items[:top_n]

    as_of = max((it["as_of_date"] for it in scored), default=None)
    return {
        "as_of_date": as_of,
        "model": {
            "name": record.get("name"),
            "version": record.get("version"),
            "algorithm": getattr(model, "algorithm", "lightgbm_gbdt"),
            "label_mode": "cross_section",
            "trained_at": record.get("created_at"),
        },
        "count": len(items),
        "scored_total": n,
        "items": items,
        "disclaimer": "强弱分为横截面相对排序(非绝对涨跌概率)，仅供技术研究，不构成投资建议。",
    }


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
