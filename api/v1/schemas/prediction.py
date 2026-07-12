# -*- coding: utf-8 -*-
"""选股推荐 / 预测回测 API schemas。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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


class RecommendationItem(BaseModel):
    code: str
    stock_name: Optional[str] = None
    industry: Optional[str] = None
    strength_score: float = Field(..., description="强弱分 0~1(越高越强)")
    rank: int = Field(..., description="所选范围内名次(1=最强)")
    rank_in_industry: Optional[int] = Field(None, description="所属行业内名次(1=最强，生成时算好)")
    rank_pct: float = Field(..., description="强弱分位 0~1(所选范围内)")
    suggested_weight: float = Field(..., description="组合建议权重(返回清单内∑=1)")
    last_close: Optional[float] = None


class StrategyHint(BaseModel):
    """经回测选出的最优交易口径（供前端展示"怎么用这份清单"）。"""
    name: str = Field(..., description="口径名，如 双周·等权·行业≤3")
    rebalance: str = Field(..., description="调仓节奏，如 每2周(周一开盘)")
    weighting: str = Field(..., description="权重方式，如 等权")
    industry_cap: Optional[int] = Field(None, description="每个行业最多几只(已在生成时固定为前 20)")
    backtest: Optional[str] = Field(None, description="回测口径表现摘要")


class SnapshotRun(BaseModel):
    """一次强弱打分快照执行（不可变，永不覆盖）。"""
    run_id: int = Field(..., description="快照执行 ID（一次 rank_snapshot.py = 一个 run）")
    model_id: Optional[int] = Field(None, description="关联 prediction_models.id，唯一锁定训练产物")
    model_name: str = Field(..., description="模型名，如 trend_xsec")
    model_version: str = Field(..., description="模型版本/训练时间，如 20260706_171722")
    as_of_date: Optional[str] = Field(None, description="特征取数的行情日 YYYY-MM-DD")
    generated_at: Optional[str] = Field(None, description="本次执行真实时间(即'哪个时间预测的') ISO")
    lookback_days: Optional[int] = None
    universe_size: Optional[int] = Field(None, description="实际打分股票数")
    industry_count: Optional[int] = Field(None, description="覆盖行业数")
    note: Optional[str] = None


class RecommendationsResponse(BaseModel):
    run_id: int = Field(..., description="本次返回榜单所属的快照 run_id")
    model_id: Optional[int] = Field(None, description="关联 prediction_models.id，唯一锁定训练产物")
    model_name: str = Field(..., description="模型名")
    model_version: str = Field(..., description="模型版本")
    generated_at: Optional[str] = Field(None, description="快照生成时间 ISO")
    scope: str = Field(..., description="推荐范围：全市场 或 行业名")
    industry: Optional[str] = None
    as_of_date: Optional[str] = None
    universe_size: int = Field(..., description="所选范围内被打分的股票总数")
    count: int
    industry_cap: Optional[int] = Field(None, description="行业分散上限(生成时已固定为前 20，这里恒为 None)")
    strategy: Optional[StrategyHint] = Field(None, description="推荐的交易口径(回测最优)")
    items: List[RecommendationItem] = Field(default_factory=list)
    disclaimer: str


class SnapshotRunsResponse(BaseModel):
    """历史快照执行列表（最新在前），供前端「快照选择」下拉。"""
    count: int
    runs: List[SnapshotRun] = Field(default_factory=list)


# ================ 推荐回测 ================

class BacktestStockItem(BaseModel):
    """单只推荐票的实际回测收益明细（以推荐日所在周一开盘价为买入价）。"""

    code: str
    stock_name: Optional[str] = None
    industry: Optional[str] = None
    strength_score: float = Field(..., description="强弱分 0~1")
    rank: int = Field(..., description="清单内名次(1=最强)")

    buy_date: str = Field(..., description="实际买入日(周一或最近交易日) YYYY-MM-DD")
    price_source: str = Field(..., description="买入价来源: 集合竞价/开盘价/无数据")
    buy_price: Optional[float] = Field(None, description="买入价(默认周一开盘价)")
    auction_price: Optional[float] = Field(None, description="集合竞价价(暂等同于开盘价)")
    open_price: Optional[float] = Field(None, description="开盘价")

    return_1d_pct: Optional[float] = Field(None, description="1日(T+1收盘)相对买入价涨跌幅%")
    return_3d_pct: Optional[float] = Field(None, description="3日(T+3收盘)相对买入价涨跌幅%")
    return_wk_pct: Optional[float] = Field(None, description="当周收益(周一开盘买→当周周五收盘卖)相对买入价涨跌幅%")

    kline_judgment: str = Field(..., description="K线形态主判断")
    kline_secondary: str = Field(..., description="K线强度/确定性提示")
    volume_status: str = Field(..., description="成交量状态: 放量/温和放量/正常/缩量/异常")
    note: Optional[str] = Field(None, description="数据缺失/异常时的提示")


class BacktestSummary(BaseModel):
    """回测汇总统计。"""

    total: int = Field(..., description="参与回测的股票数")
    with_data: int = Field(..., description="有完整收益数据的股票数")
    avg_1d_pct: float = Field(..., description="平均1日收益%")
    avg_3d_pct: float = Field(..., description="平均3日收益%")
    avg_wk_pct: float = Field(..., description="平均当周收益%(周一开买→周五收卖)")
    win_rate_1d: float = Field(..., description="1日正收益占比 0~1")
    win_rate_3d: float = Field(..., description="3日正收益占比 0~1")
    win_rate_wk: float = Field(..., description="当周正收益占比 0~1")
    best_1d_pct: float = Field(..., description="最佳1日收益%")
    worst_1d_pct: float = Field(..., description="最差1日收益%")


class RecommendationBacktestResponse(BaseModel):
    """推荐回测完整响应。"""

    run_id: int = Field(..., description="回测所用榜单的快照 run_id")
    model_id: Optional[int] = Field(None, description="关联 prediction_models.id，唯一锁定训练产物")
    model_name: str = Field(..., description="模型名")
    model_version: str = Field(..., description="模型版本")
    generated_at: Optional[str] = Field(None, description="快照生成时间 ISO")
    as_of_date: Optional[str] = Field(None, description="快照日(推荐日)")
    buy_date: str = Field(..., description="理论买入日(周一) YYYY-MM-DD")
    actual_buy_date: str = Field(..., description="实际生效买入日(若周一休市则顺延) YYYY-MM-DD")
    strategy_note: str = Field(..., description="本次回测使用的交易口径说明")
    summary: BacktestSummary
    items: List[BacktestStockItem] = Field(default_factory=list)
    disclaimer: str


# ================ 周度推荐（单页：榜单 + 实时收益 + 买卖窗口） ================

class WeeklyLiveItem(BaseModel):
    """单只推荐票的实时收益明细（腾讯行情，周一开盘买入）。"""

    code: str
    available: bool = Field(False, description="是否已取到实时收益")
    buy_date: Optional[str] = Field(None, description="实际买入日(周一) YYYY-MM-DD")
    buy_price: Optional[float] = Field(None, description="买入价(周一开盘价)")
    last_price: Optional[float] = Field(None, description="最新价(腾讯实时收盘)")
    return_1d_pct: Optional[float] = Field(None, description="1日(T+1收盘)相对买入价涨跌幅%")
    return_3d_pct: Optional[float] = Field(None, description="3日(T+3收盘)相对买入价涨跌幅%")
    return_wk_pct: Optional[float] = Field(None, description="当周收益(周一开买→周五收卖)相对买入价涨跌幅%")
    note: Optional[str] = Field(None, description="数据缺失/未到买入日时的提示")


class WeeklyTradeWindow(BaseModel):
    """买卖时间窗口（与模型训练口径一致：周一买、周五卖）。"""

    buy_date: str = Field(..., description="买入日(周一) YYYY-MM-DD")
    sell_date: str = Field(..., description="卖出日(当周周五) YYYY-MM-DD")
    status: str = Field(..., description="buy_today(预测周周一买入)/holding(持有中·实时)/settled(预测周已收盘·已结算)/pending(待买入)")
    status_label: str = Field(..., description="状态中文标签")
    next_buy_date: str = Field(..., description="下一次买入日(下周一) YYYY-MM-DD")
    days_since_buy: int = Field(..., description="距预测买入日天数")
    days_to_sell: int = Field(..., description="距预测卖出日天数(已结算为负)")
    is_buy_reached: bool = Field(..., description="预测买入日是否已到(决定能否取收益)")
    is_settled: bool = Field(..., description="预测周是否已收盘(收益为当周实际值，非实时)")
    as_of_date: Optional[str] = Field(None, description="特征参考日(模型看到的最后一天行情)，非预测目标周")
    predict_week: str = Field(
        ...,
        description="预测目标周区间(买入周一~卖出周五)，与 as_of_date(特征日)区分，显式标明'预测的是哪一周'",
    )


class WeeklyLiveSummary(BaseModel):
    """实时收益汇总统计。"""

    total: int = Field(0, description="参与实时计算的股票数")
    with_data: int = Field(0, description="有完整实时收益数据的股票数")
    avg_1d_pct: float = Field(0.0, description="平均1日收益%")
    avg_3d_pct: float = Field(0.0, description="平均3日收益%")
    avg_wk_pct: float = Field(0.0, description="平均当周收益%")
    win_rate_1d: float = Field(0.0, description="1日正收益占比 0~1")
    win_rate_3d: float = Field(0.0, description="3日正收益占比 0~1")
    win_rate_wk: float = Field(0.0, description="当周正收益占比 0~1")
    best_1d_pct: float = Field(0.0, description="最佳1日收益%")
    worst_1d_pct: float = Field(0.0, description="最差1日收益%")


class WeeklyRecommendationResponse(BaseModel):
    """周度推荐单页完整响应：榜单 + 买卖窗口 + 实时收益。"""

    run_id: int = Field(..., description="本次返回榜单所属的快照 run_id")
    model_id: Optional[int] = Field(None, description="关联 prediction_models.id，唯一锁定训练产物")
    model_name: str = Field(..., description="模型名")
    model_version: str = Field(..., description="模型版本")
    generated_at: Optional[str] = Field(None, description="快照生成时间 ISO")
    scope: str = Field(..., description="推荐范围：全市场 或 行业名")
    industry: Optional[str] = None
    as_of_date: Optional[str] = None
    universe_size: int = Field(..., description="所选范围内被打分的股票总数")
    count: int
    industry_cap: Optional[int] = Field(None, description="行业分散上限(生成时固定为前 20)")
    strategy: Optional[StrategyHint] = Field(None, description="推荐的交易口径(回测最优)")
    items: List[RecommendationItem] = Field(default_factory=list, description="推荐榜单(强弱分等)")
    trade_window: WeeklyTradeWindow = Field(..., description="买卖时间窗口(周一买/周五卖)")
    live: List[WeeklyLiveItem] = Field(default_factory=list, description="与 items 同序的实时收益明细")
    live_summary: WeeklyLiveSummary = Field(..., description="实时收益汇总")
    data_source: Optional[str] = Field(None, description="实时行情数据源")
    fetched_at: Optional[str] = Field(None, description="实时行情拉取时间(ISO)")
    disclaimer: str


class IndustryOption(BaseModel):
    industry: str
    count: int = Field(..., description="该行业当日被打分的股票数")


class IndustriesResponse(BaseModel):
    run_id: Optional[int] = Field(None, description="所属快照 run_id")
    as_of_date: Optional[str] = None
    count: int
    industries: List[IndustryOption] = Field(default_factory=list)
