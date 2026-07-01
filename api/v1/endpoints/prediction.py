# -*- coding: utf-8 -*-
"""
===================================
股价走势预测接口
===================================

职责：
1. POST /api/v1/prediction/predict  对单只股票做轻量 ML 走势预测

流程见 src/services/prediction_service.py：
    取K线 → 技术因子 → 逻辑回归(梯度下降) → 预测次日方向 + 未来价格路径
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.prediction import (
    PredictionAccuracyResponse,
    PredictionEvaluateRequest,
    PredictionEvaluateResponse,
    PredictionHistoryResponse,
    PredictionRequest,
    PredictionResponse,
)
from src.services.prediction_service import PredictionError, predict_stock

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/predict",
    response_model=PredictionResponse,
    responses={
        200: {"description": "预测完成"},
        400: {"description": "数据不足或参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="预测股价走势",
    description=(
        "对指定股票拉取历史 K 线，构造技术因子并训练轻量逻辑回归模型，"
        "预测次日涨跌方向、概率与未来 N 日价格路径。仅供技术研究，不构成投资建议。"
    ),
)
def predict(request: PredictionRequest) -> PredictionResponse:
    """执行股价走势预测（同步 def，FastAPI 自动放入线程池执行）。"""
    try:
        result = predict_stock(
            request.code.strip(),
            lookback_days=request.lookback_days,
            horizon_days=request.horizon_days,
            language=request.language,
        )
        return PredictionResponse(**result)
    except PredictionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "prediction_failed", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"股价预测失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"股价预测失败: {str(exc)}"},
        )


@router.get(
    "/history",
    response_model=PredictionHistoryResponse,
    summary="历史预测记录",
    description="分页查询已落库的历史预测，可按股票代码 / 评估状态过滤。",
)
def prediction_history(
    code: Optional[str] = Query(None, description="按股票代码过滤"),
    status: Optional[str] = Query(None, description="评估状态：pending/evaluated/insufficient"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PredictionHistoryResponse:
    from src.repositories.prediction_record_repo import PredictionRecordRepository

    items, total = PredictionRecordRepository().list_records(
        code=code, status=status, limit=limit, offset=offset
    )
    return PredictionHistoryResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/accuracy",
    response_model=PredictionAccuracyResponse,
    summary="预测准确率统计",
    description="聚合历史预测的方向命中率、平均期望/实际收益等指标。",
)
def prediction_accuracy(
    code: Optional[str] = Query(None, description="按股票代码过滤"),
) -> PredictionAccuracyResponse:
    from src.repositories.prediction_record_repo import PredictionRecordRepository

    return PredictionAccuracyResponse(**PredictionRecordRepository().accuracy_stats(code=code))


@router.post(
    "/evaluate",
    response_model=PredictionEvaluateResponse,
    summary="回填评估历史预测",
    description="对到期的待评估预测，用真实行情回填实际涨跌与命中结果。",
)
def prediction_evaluate(request: PredictionEvaluateRequest) -> PredictionEvaluateResponse:
    from src.services.prediction_eval_service import PredictionEvalService

    try:
        stats = PredictionEvalService().evaluate_pending(
            refresh=request.refresh, limit=request.limit
        )
        return PredictionEvaluateResponse(**stats)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"预测评估失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"预测评估失败: {str(exc)}"},
        )
