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
