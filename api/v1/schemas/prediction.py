# -*- coding: utf-8 -*-
"""股价走势预测 API schemas。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PredictionRequest(BaseModel):
    code: str = Field(..., description="股票代码，如 600519、00700、AAPL")
    horizon_days: int = Field(5, ge=1, le=20, description="预测未来交易日数")
    lookback_days: int = Field(250, ge=60, le=800, description="训练回溯天数")
    language: Literal["zh", "en"] = Field("zh", description="因子标签语言")


class FactorContribution(BaseModel):
    key: str
    label: str
    value: float
    weight: float
    contribution: float = Field(..., description="标准化特征 × 权重，正=推动上涨")


class HistoryPoint(BaseModel):
    date: str
    close: float


class ProjectedPoint(BaseModel):
    date: str
    day: int
    price: float
    lower: float
    upper: float


class ModelMetrics(BaseModel):
    train_accuracy: Optional[float] = None
    valid_accuracy: Optional[float] = None
    train_samples: int
    valid_samples: int
    baseline_accuracy: Optional[float] = None
    epochs: int
    learning_rate: float


class ModelInfo(BaseModel):
    algorithm: str
    feature_count: int
    lookback_days: int
    trained_samples: int
    source: Optional[Literal["trained", "on_the_fly"]] = Field(
        None, description="模型来源：trained=加载离线训练的持久化模型；on_the_fly=实时训练"
    )
    version: Optional[str] = Field(None, description="持久化模型版本号（仅 trained）")
    trained_at: Optional[str] = Field(None, description="模型训练时间（仅 trained）")


class PredictionRecordItem(BaseModel):
    id: int
    code: str
    stock_name: Optional[str] = None
    as_of_date: Optional[str] = None
    horizon_days: int
    direction: str
    up_probability: Optional[float] = None
    confidence: Optional[float] = None
    expected_return_pct: Optional[float] = None
    last_close: Optional[float] = None
    model_source: Optional[str] = None
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    eval_status: str
    actual_close: Optional[float] = None
    actual_return_pct: Optional[float] = None
    actual_direction: Optional[str] = None
    is_correct: Optional[bool] = None
    evaluated_at: Optional[str] = None
    created_at: Optional[str] = None


class PredictionHistoryResponse(BaseModel):
    items: List[PredictionRecordItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class PredictionAccuracyResponse(BaseModel):
    total: int = 0
    pending: int = 0
    evaluated: int = 0
    correct: int = 0
    accuracy: Optional[float] = Field(None, description="方向命中率 0~1（已评估口径）")
    avg_expected_return_pct: Optional[float] = None
    avg_actual_return_pct: Optional[float] = None


class PredictionEvaluateRequest(BaseModel):
    refresh: bool = Field(True, description="评估前是否联网刷新缓存以补齐前向K线")
    limit: int = Field(500, ge=1, le=2000, description="单次最多评估的记录数")


class PredictionEvaluateResponse(BaseModel):
    processed: int = 0
    evaluated: int = 0
    insufficient: int = 0
    errors: int = 0


class PredictionBacktestRequest(BaseModel):
    code: str = Field(..., description="股票代码，如 600519、00700、AAPL")
    horizon_days: int = Field(5, ge=1, le=20, description="预测/评估的未来交易日数")
    lookback_days: int = Field(500, ge=120, le=1500, description="用于回测的历史回溯天数")
    retrain_every: int = Field(5, ge=1, le=60, description="每隔多少个交易日重训一次模型")
    min_train: int = Field(60, ge=30, le=500, description="首次预测前至少积累的样本数")
    threshold: float = Field(0.5, ge=0.05, le=0.95, description="判定看涨的概率阈值")
    allow_short: bool = Field(False, description="资金曲线是否允许做空（看跌时反向）")
    refresh: bool = Field(True, description="是否联网刷新缓存以补齐最新K线")
    start_date: Optional[str] = Field(None, description="回测评估起始日 YYYY-MM-DD（含）")
    end_date: Optional[str] = Field(None, description="回测评估结束日 YYYY-MM-DD（含）")


class BacktestPoint(BaseModel):
    date: str
    up_probability: float
    direction: Literal["up", "down"]
    actual_return_pct: float
    correct: bool


class EquityPoint(BaseModel):
    date: str
    strategy: float
    benchmark: float


class PredictionBacktestResponse(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    horizon_days: int
    lookback_days: int
    retrain_every: int
    threshold: float
    allow_short: bool
    start_date: str
    end_date: str
    n_predictions: int
    correct: int
    accuracy: float = Field(..., description="逐日方向命中率 0~1")
    baseline_accuracy: float = Field(..., description="基线：始终猜多数类的命中率")
    up_precision: Optional[float] = Field(None, description="预测看涨时的实际上涨占比")
    pred_up_count: int
    actual_up_ratio: float
    n_trades: int
    win_rate: Optional[float] = None
    strategy_return_pct: float
    benchmark_return_pct: float
    max_drawdown_pct: float
    equity_curve: List[EquityPoint] = Field(default_factory=list)
    points: List[BacktestPoint] = Field(default_factory=list)
    disclaimer: str


class RankRequest(BaseModel):
    codes: Optional[List[str]] = Field(
        None, description="待打分的股票代码列表；留空则使用 .env 的 STOCK_LIST 自选股"
    )
    lookback_days: int = Field(250, ge=60, le=800, description="每只票特征回溯天数")
    top_n: Optional[int] = Field(None, ge=1, le=500, description="只返回强弱分最高的前 N 只(留空=全部)")
    model_name: str = Field("trend_xsec", description="横截面模型名(须为已激活的 cross_section 模型)")


class RankItem(BaseModel):
    code: str
    stock_name: Optional[str] = None
    strength_score: float = Field(..., description="强弱分 0~1：属当日强势前50%的概率(越高越强)")
    rank: int = Field(..., description="在本批内的强弱名次(1=最强)")
    rank_pct: float = Field(..., description="强弱分位 0~1")
    suggested_weight: float = Field(..., description="概率加权多头建议权重(∑=1)")
    last_close: float
    as_of_date: str


class RankModelInfo(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    algorithm: str
    label_mode: str
    trained_at: Optional[str] = None


class RankResponse(BaseModel):
    as_of_date: Optional[str] = None
    model: RankModelInfo
    count: int
    scored_total: int = Field(..., description="实际完成打分的股票数")
    items: List[RankItem] = Field(default_factory=list)
    disclaimer: str


class PredictionResponse(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    as_of_date: str = Field(..., description="最新数据日期")
    last_close: float
    horizon_days: int
    direction: Literal["up", "down"]
    up_probability: float = Field(..., description="次日上涨概率 0~1")
    confidence: float = Field(..., description="置信度 0~1")
    expected_return_pct: float = Field(..., description="预测区间末期望收益%")
    daily_volatility: float
    history: List[HistoryPoint] = Field(default_factory=list)
    projected: List[ProjectedPoint] = Field(default_factory=list)
    factors: List[FactorContribution] = Field(default_factory=list)
    metrics: ModelMetrics
    model: ModelInfo
    disclaimer: str
