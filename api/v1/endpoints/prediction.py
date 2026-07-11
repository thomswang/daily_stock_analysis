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
from datetime import date as _date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.prediction import (
    PredictionAccuracyResponse,
    PredictionBacktestRequest,
    PredictionBacktestResponse,
    PredictionEvaluateRequest,
    PredictionEvaluateResponse,
    IndustriesResponse,
    PredictionHistoryResponse,
    PredictionRequest,
    PredictionResponse,
    RankRequest,
    RankResponse,
    RecommendationsResponse,
    RecommendationBacktestResponse,
    BacktestStockItem,
    BacktestSummary,
    SnapshotRunsResponse,
    WeeklyRecommendationResponse,
)
from src.repositories.training_bars import load_training_bar_df
from src.services.prediction_service import PredictionError, predict_stock, rank_stocks

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


@router.post(
    "/rank",
    response_model=RankResponse,
    responses={
        200: {"description": "打分完成"},
        400: {"description": "数据不足或参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="横截面选股打分/排序",
    description=(
        "用已激活的横截面模型(trend_xsec)给一批股票打「强弱分」并按概率加权排序。"
        "相比单票涨跌预测，横截面排序经 walk-forward / CPCV 验证有稳定 alpha，"
        "扣成本后能跑赢等权基准。强弱分为相对排序(非绝对涨跌概率)，不构成投资建议。"
    ),
)
def prediction_rank(request: RankRequest) -> RankResponse:
    """对一批股票做横截面强弱打分与排序（同步 def，FastAPI 自动放入线程池）。"""
    codes = request.codes
    if not codes:
        # 未指定则回退到 .env 自选股列表
        try:
            from src.config import get_config

            cfg = get_config()
            try:
                cfg.refresh_stock_list()
            except Exception:  # noqa: BLE001
                pass
            codes = list(getattr(cfg, "stock_list", []) or [])
        except Exception:  # noqa: BLE001
            codes = []
    if not codes:
        raise HTTPException(
            status_code=400,
            detail={"error": "empty_codes", "message": "未提供股票代码，且自选股列表为空"},
        )
    try:
        result = rank_stocks(
            codes,
            model_name=request.model_name,
            lookback_days=request.lookback_days,
            top_n=request.top_n,
        )
        return RankResponse(**result)
    except PredictionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "rank_failed", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"选股打分失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"选股打分失败: {str(exc)}"},
        )


@router.get(
    "/recommendations/runs",
    response_model=SnapshotRunsResponse,
    summary="历史快照执行列表",
    description=(
        "返回历史强弱打分快照执行(run)列表（最新在前），每项含模型名/版本/生成时间/行情日。"
        "前端「快照选择」下拉用它来切换查看不同模型、不同时间生成的榜单。"
    ),
)
def prediction_recommendation_runs(
    limit: int = Query(50, ge=1, le=200, description="返回最近 N 个 run"),
) -> SnapshotRunsResponse:
    from src.services.stock_ranking_service import StockRankingService

    return SnapshotRunsResponse(**StockRankingService().list_runs(limit=limit))


@router.get(
    "/recommendations",
    response_model=RecommendationsResponse,
    responses={
        200: {"description": "查询成功"},
        400: {"description": "暂无快照或参数错误", "model": ErrorResponse},
    },
    summary="选股推荐（横截面强弱榜）",
    description=(
        "系统主动推荐：读取某次快照(run)的全市场强弱打分，返回最强的前 N 只及等权建议权重。"
        "不传 run_id=最新一次快照；传 run_id=查看历史上某次模型/某次时间生成的榜单（可回溯对比）。\n"
        "不传 industry=全市场榜（按强弱取前 N，N≤20）；传 industry=该行业内前 20。\n"
        "强弱为相对排序，不构成投资建议。"
    ),
)
def prediction_recommendations(
    run_id: Optional[int] = Query(None, description="快照 run_id；留空=最新一次"),
    industry: Optional[str] = Query(None, description="按行业筛选；留空=全市场"),
    top_n: int = Query(20, ge=1, le=20, description="返回前 N 强（每行业最多 20，不支持更多）"),
) -> RecommendationsResponse:
    from src.services.stock_ranking_service import StockRankingError, StockRankingService

    try:
        result = StockRankingService().get_recommendations(
            run_id=run_id, industry=industry, top_n=top_n
        )
        return RecommendationsResponse(**result)
    except StockRankingError as exc:
        raise HTTPException(status_code=400, detail={"error": "no_snapshot", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.error(f"选股推荐失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"选股推荐失败: {str(exc)}"},
        )


@router.get(
    "/recommendations/weekly",
    response_model=WeeklyRecommendationResponse,
    responses={
        200: {"description": "查询成功"},
        400: {"description": "暂无快照或参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="周度选股推荐（实时收益 · 单页）",
    description=(
        "推荐榜单 + 买卖时间窗口(周一买/周五卖) + 实时收益(腾讯行情) 合一返回，"
        "供前端单页展示（不再分「推荐列表 / 收益回测」两个 Tab）。\n"
        "时间一致性：买入日(周一)未到(如周六日，下周一才买)→ 实时收益为 null(待买入)；"
        "买入日已到→ 以周一开盘价为成本基准，实时计算 1/3/5 日收益。\n"
        "实时数据通过 TencentFetcher 拉取（快、不易被封）。强弱为相对排序，不构成投资建议。"
    ),
)
def prediction_recommendations_weekly(
    run_id: Optional[int] = Query(None, description="快照 run_id；留空=最新一次"),
    industry: Optional[str] = Query(None, description="按行业筛选；留空=全市场"),
    top_n: int = Query(20, ge=1, le=20, description="返回前 N 强（每行业最多 20）"),
) -> WeeklyRecommendationResponse:
    from src.services.weekly_recommendation_service import (
        StockRankingError,
        build_weekly_recommendations,
    )

    try:
        payload = build_weekly_recommendations(
            run_id=run_id, industry=industry, top_n=top_n
        )
        return WeeklyRecommendationResponse(**payload)
    except StockRankingError as exc:
        raise HTTPException(status_code=400, detail={"error": "no_snapshot", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.error(f"周度推荐失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"周度推荐失败: {str(exc)}"},
        )


@router.get(
    "/industries",
    response_model=IndustriesResponse,
    summary="可选行业清单",
    description="某次快照(run)覆盖的行业清单及各行业股票数，供行业筛选下拉。",
)
def prediction_industries(
    run_id: Optional[int] = Query(None, description="快照 run_id；留空=最新一次"),
) -> IndustriesResponse:
    from src.services.stock_ranking_service import StockRankingService

    return IndustriesResponse(**StockRankingService().list_industries(run_id=run_id))


# ─────────────────────────────────────────────────────────────
# 推荐回测：基于周一开盘价模拟买入，计算实际 1/3/5 日收益
# ─────────────────────────────────────────────────────────────

@router.get(
    "/recommendations/backtest",
    response_model=RecommendationBacktestResponse,
    responses={
        200: {"description": "回测完成"},
        400: {"description": "暂无快照或参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="推荐收益回测",
    description=(
        "读取当日推荐清单，模拟「推荐日所在周一的开盘价」买入后 1/3/5 个交易日的"
        "实际涨跌幅。用于评估推荐清单的实盘表现参考(仅供研究，不构成投资建议)。"
        "若周一休市则顺延到最近的有效交易日。"
    ),
)
def prediction_recommendations_backtest(
    run_id: Optional[int] = Query(None, description="快照 run_id；留空=最新一次"),
    industry: Optional[str] = Query(None, description="按行业筛选；留空=全市场"),
    top_n: int = Query(20, ge=1, le=20, description="取前 N 强(每行业最多 20)"),
) -> RecommendationBacktestResponse:
    """
    业务流程：
        1) 拿某次快照(run)的推荐清单(全市场/行业)
        2) 找快照日所在周一的实际买入日(顺延到最近的有效交易日)
        3) 逐票读 kline 拿到 [买入日, 买入日+10] 的日线
        4) 模拟周一开盘价买入，计算 1/3/5 日 (T+N 收盘) 相对买入价涨跌幅
        5) 叠加 K 线形态 + 量能状态作为辅助信号
    """
    import pandas as pd

    from src.services.stock_ranking_service import StockRankingError, StockRankingService
    from src.repositories.stock_repo import StockRepository

    ranking = StockRankingService()
    stock_repo = StockRepository()

    # 1. 拿推荐清单 (复刻 StockRankingService.get_recommendations 的过滤逻辑，但只取 items)
    try:
        rec = ranking.get_recommendations(run_id=run_id, industry=industry, top_n=top_n)
    except StockRankingError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_snapshot", "message": str(exc)},
        )

    as_of_date_str = rec.get("as_of_date")
    as_of_date = None
    if as_of_date_str:
        try:
            as_of_date = datetime.strptime(as_of_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            as_of_date = None
    if as_of_date is None:
        as_of_date = datetime.now().date()

    # 2. 找该周的实际买入日（周一 → 顺延到最近的有效交易日）
    days_since_monday = as_of_date.weekday()  # Mon=0
    monday = as_of_date - timedelta(days=days_since_monday)
    actual_buy_date = _next_trading_day(stock_repo, monday, max_lookback=7)
    end_window = actual_buy_date + timedelta(days=10)  # 多取几天，覆盖周末/休市

    strategy_note = (
        f"快照日={as_of_date.isoformat()} (周{['一','二','三','四','五','六','日'][as_of_date.weekday()]})"
        f"，理论买入日=周一 {monday.isoformat()}"
        f"，实际买入日(顺延后)={actual_buy_date.isoformat()}"
        f"；执行口径=买入日开盘(集合竞价)买入→当周周五收盘卖出(与训练标签/cross_section一致)"
    )

    # 3-5. 逐票回测
    items: List[BacktestStockItem] = []
    returns_1d, returns_3d, returns_wk = [], [], []
    for it in rec.get("items", []):
        code = (it.get("code") or "").strip().upper()
        if not code:
            continue

        # 统一经训练/预测数据网关取数（默认 stock_daily_ohlcv，不回退），与训练口径一致
        kdf = load_training_bar_df(code, actual_buy_date, end_window)
        if kdf is None or kdf.empty:
            items.append(BacktestStockItem(
                code=code,
                stock_name=it.get("stock_name"),
                industry=it.get("industry"),
                strength_score=float(it.get("strength_score") or 0),
                rank=int(it.get("rank") or 0),
                buy_date=actual_buy_date.isoformat(),
                price_source="无数据",
                buy_price=None, auction_price=None, open_price=None,
                return_1d_pct=None, return_3d_pct=None, return_wk_pct=None,
                kline_judgment="不确定", kline_secondary="待观察",
                volume_status="异常",
                note="本地无K线数据(可能未回填或停牌)",
            ))
            continue

        kdf = kdf.sort_values("date").reset_index(drop=True)
        kdf["date"] = pd.to_datetime(kdf["date"]).dt.date

        # 找到实际买入日对应的行（若该日无K线则顺延到下一个有效交易日）
        buy_row = kdf[kdf["date"] >= actual_buy_date]
        if buy_row.empty:
            items.append(BacktestStockItem(
                code=code, stock_name=it.get("stock_name"), industry=it.get("industry"),
                strength_score=float(it.get("strength_score") or 0),
                rank=int(it.get("rank") or 0),
                buy_date=actual_buy_date.isoformat(),
                price_source="无数据", buy_price=None, auction_price=None, open_price=None,
                return_1d_pct=None, return_3d_pct=None, return_wk_pct=None,
                kline_judgment="不确定", kline_secondary="待观察",
                volume_status="异常", note="买入日及之后无K线数据",
            ))
            continue

        buy_idx = int(buy_row.index[0])
        real_buy_date = kdf.loc[buy_idx, "date"]
        open_price = float(kdf.loc[buy_idx, "open"])
        auction_price = open_price  # 集合竞价 ≈ 开盘价
        buy_price = open_price
        buy_price_src = "集合竞价"

        # 1/3 日收益率 T+1/T+3 收盘；当周收益=买入日(周一)开盘→当周周五收盘(T+4)
        # 与训练标签 make_weekly_open_close_return 的"下周一开买、当周周五收卖"完全对齐。
        ret_1d = _pct_return_at(kdf, buy_idx, 1)
        ret_3d = _pct_return_at(kdf, buy_idx, 3)
        ret_wk = _pct_return_at(kdf, buy_idx, 4)

        kline_primary, kline_secondary = _analyze_kline(kdf, buy_idx)
        volume_status = _analyze_volume(kdf, buy_idx)

        if ret_1d is not None:
            returns_1d.append(ret_1d)
        if ret_3d is not None:
            returns_3d.append(ret_3d)
        if ret_wk is not None:
            returns_wk.append(ret_wk)

        items.append(BacktestStockItem(
            code=code, stock_name=it.get("stock_name"), industry=it.get("industry"),
            strength_score=float(it.get("strength_score") or 0),
            rank=int(it.get("rank") or 0),
            buy_date=real_buy_date.isoformat(),
            price_source=buy_price_src,
            buy_price=round(buy_price, 2),
            auction_price=round(auction_price, 2),
            open_price=round(open_price, 2),
            return_1d_pct=round(ret_1d, 2) if ret_1d is not None else None,
            return_3d_pct=round(ret_3d, 2) if ret_3d is not None else None,
            return_wk_pct=round(ret_wk, 2) if ret_wk is not None else None,
            kline_judgment=kline_primary, kline_secondary=kline_secondary,
            volume_status=volume_status,
        ))

    def _avg(xs): return round(sum(xs) / len(xs), 2) if xs else 0.0
    def _win(xs): return round(sum(1 for x in xs if x > 0) / len(xs), 4) if xs else 0.0
    summary = BacktestSummary(
        total=len(items),
        with_data=len([i for i in items if i.buy_price is not None]),
        avg_1d_pct=_avg(returns_1d),
        avg_3d_pct=_avg(returns_3d),
        avg_wk_pct=_avg(returns_wk),
        win_rate_1d=_win(returns_1d),
        win_rate_3d=_win(returns_3d),
        win_rate_wk=_win(returns_wk),
        best_1d_pct=round(max(returns_1d), 2) if returns_1d else 0.0,
        worst_1d_pct=round(min(returns_1d), 2) if returns_1d else 0.0,
    )

    return RecommendationBacktestResponse(
        run_id=rec.get("run_id"),
        model_id=rec.get("model_id"),
        model_name=rec.get("model_name"),
        model_version=rec.get("model_version"),
        generated_at=rec.get("generated_at"),
        as_of_date=as_of_date.isoformat() if as_of_date else None,
        buy_date=monday.isoformat(),
        actual_buy_date=actual_buy_date.isoformat(),
        strategy_note=strategy_note,
        summary=summary,
        items=items,
        disclaimer=(
            "回测模拟基于历史K线，假设周一开盘价(集合竞价)成交，未考虑滑点/手续费/"
            "停牌/涨跌停不可买入等。仅供研究参考，不构成投资建议。"
        ),
    )


def _next_trading_day(stock_repo, target_date, max_lookback: int = 7):
    """在 [target_date, target_date+max_lookback] 内找第一个有K线数据的交易日。

    用沪深300(000300.SH)作为交易日历探针；通常 K 线数据存在即代表是交易日。
    """
    probe_code = "000300.SH"
    end = target_date + timedelta(days=max_lookback)
    try:
        # 交易日历探针同样走网关，保持与训练/预测口径一致
        df = load_training_bar_df(probe_code, target_date, end)
        if df is not None and not df.empty:
            import pandas as pd
            df = df.sort_values("date")
            df["date"] = pd.to_datetime(df["date"]).dt.date
            for _, row in df.iterrows():
                if row["date"] >= target_date:
                    return row["date"]
    except Exception:  # noqa: BLE001 - 探针失败不致命
        pass
    return target_date  # 退而求其次


def _pct_return_at(kdf, buy_idx: int, days: int):
    """以 T+days 收盘相对买入价 (kdf.loc[buy_idx, 'open']) 的涨跌幅%。"""
    target_idx = buy_idx + days
    if target_idx >= len(kdf):
        return None
    buy_open = float(kdf.loc[buy_idx, "open"])
    sell_close = float(kdf.loc[target_idx, "close"])
    if buy_open <= 0:
        return None
    return (sell_close / buy_open - 1.0) * 100.0


def _analyze_kline(kdf, idx: int):
    """单根 K 线形态 + 趋势/强度提示。返回 (primary, secondary)。"""
    import numpy as np

    if idx >= len(kdf) or idx < 0:
        return ("不确定", "待观察")

    row = kdf.iloc[idx]
    open_p = float(row["open"]); close_p = float(row["close"])
    high_p = float(row["high"]); low_p = float(row["low"])

    body = abs(close_p - open_p)
    rng = max(high_p - low_p, 1e-9)
    upper = high_p - max(open_p, close_p)
    lower = min(open_p, close_p) - low_p
    body_ratio = body / rng

    # 趋势：相对前 5 日（含当日）MA5
    ma_trend = "中性"
    if idx >= 4:
        ma5 = float(kdf.iloc[idx - 4: idx + 1]["close"].mean())
        if close_p > ma5 * 1.02: ma_trend = "偏强"
        elif close_p < ma5 * 0.98: ma_trend = "偏弱"

    is_bullish = close_p > open_p
    is_doji = body_ratio < 0.15
    has_long_upper = upper > body * 1.5
    has_long_lower = lower > body * 1.5

    if is_doji:
        if has_long_upper and ma_trend == "偏强":
            primary = "趋势修复观察"
        elif has_long_lower and ma_trend == "偏弱":
            primary = "底部震荡"
        else:
            primary = "方向选择中"
    elif is_bullish:
        if body_ratio > 0.6:
            primary = "均线修复转强" if ma_trend != "偏弱" else "强势反弹"
        else:
            primary = "趋势修复观察" if ma_trend != "偏弱" else "震荡修复"
    else:
        if body_ratio > 0.5:
            primary = "回调确认"
        else:
            primary = "蓄势整理" if ma_trend != "偏弱" else "弱势延续"

    if is_doji:
        secondary = "不确定"
    elif ma_trend == "偏强" and is_bullish and body_ratio > 0.5:
        secondary = "偏延续"
    elif ma_trend == "偏弱" and (not is_bullish) and body_ratio > 0.5:
        secondary = "偏延续"
    elif ma_trend in ("偏强", "偏弱") and body_ratio < 0.3:
        secondary = "需观察"
    elif ma_trend == "中性":
        secondary = "需观察"
    else:
        secondary = "需观察"
    return (primary, secondary)


def _analyze_volume(kdf, idx: int):
    """成交量相对前 5 日均值的活跃度。"""
    import numpy as np

    if idx >= len(kdf) or idx < 0:
        return "异常"

    cur = float(kdf.iloc[idx].get("volume", 0) or 0)
    if idx >= 5:
        prev = [float(kdf.iloc[i].get("volume", 0) or 0) for i in range(idx - 5, idx)]
        avg = sum(prev) / len(prev) if prev else 0
        if avg > 0:
            r = cur / avg
            if r > 1.8: return "放量"
            if r > 1.3: return "温和放量"
            if r < 0.6: return "缩量"
            return "正常"
    return "正常"


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
    "/backtest",
    response_model=PredictionBacktestResponse,
    responses={
        200: {"description": "回测完成"},
        400: {"description": "数据不足或参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="走势预测回测",
    description=(
        "对单只股票做滚动步进(walk-forward)回测：每隔若干交易日仅用当时可见的历史"
        "数据重训并预测方向，严格防未来函数，给出逐日方向命中率与策略资金曲线。"
        "仅供技术研究，不构成投资建议。"
    ),
)
def prediction_backtest(request: PredictionBacktestRequest) -> PredictionBacktestResponse:
    from src.services.prediction_backtest_service import PredictionBacktestService

    try:
        result = PredictionBacktestService().run(
            request.code.strip(),
            start_date=request.start_date,
            end_date=request.end_date,
            horizon_days=request.horizon_days,
            lookback_days=request.lookback_days,
            retrain_every=request.retrain_every,
            min_train=request.min_train,
            threshold=request.threshold,
            allow_short=request.allow_short,
            refresh=request.refresh,
        )
        return PredictionBacktestResponse(**result)
    except PredictionError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "backtest_failed", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"预测回测失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"预测回测失败: {str(exc)}"},
        )


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
