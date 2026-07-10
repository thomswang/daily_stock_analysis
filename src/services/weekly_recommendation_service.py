# -*- coding: utf-8 -*-
"""
===================================
周度选股推荐服务（实时收益 · 单页）
===================================

把「推荐榜单 + 实时收益回测 + 买卖时间窗口」合成一次返回，供前端
`/recommendations` 单页使用，消除原「推荐列表 / 收益回测」两个 Tab 的割裂。

设计要点：
1. 买卖时间窗口严格对齐模型训练口径：周一开盘买入、当周周五收盘卖出。
   - 周一~周五：当前所处交易周的周一即为买入日（已买入/买入当天）。
   - 周六/周日：下一笔买入是「下周一」，当前处于「待买入」状态（买入日尚未到，
     取不到最新趋势数据，故实时收益为 null）。
   这样与用户训练逻辑（周一买周五卖）一致，并天然实现时间一致性：
   买入日未到 → 不展示收益；买入日已到 → 按实际交易日计算 1/3/5 日收益。

2. 实时收益通过 TencentFetcher 拉取（单次上限 800 条、HTTP 直连、内置限流，
   速度快且不易被封），以买入日（周一）开盘价为成本基准，计算：
     - 1 日收益 = T+1 收盘 / 买入开盘 − 1
     - 3 日收益 = T+3 收盘 / 买入开盘 − 1
     - 当周收益 = T+4（周五）收盘 / 买入开盘 − 1
   收益是否可算由「实际返回的交易日行」决定（自动兼容停牌/节假日顺延）。

⚠️ 仅供技术研究，不构成投资建议。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from data_provider.base import DataFetchError, DataFetcherManager
from src.services.stock_ranking_service import StockRankingError, StockRankingService

logger = logging.getLogger(__name__)

TENCENT_SOURCE = "TencentFetcher"


@dataclass
class TradeWindow:
    """买入日（周一）/ 卖出日（周五）及状态，与模型训练口径一致。"""

    buy_date: str
    sell_date: str
    status: str  # "buy_today" | "holding" | "pending"
    status_label: str
    next_buy_date: str
    days_since_buy: int
    days_to_sell: int
    is_buy_reached: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_trade_window(today: Optional[date] = None) -> TradeWindow:
    """根据今天推算本周/下周买卖窗口。

    - 周一~周五：买入日 = 本周一（已买入或买入当天）。
    - 周六/周日：买入日 = 下周一（未来）→ 待买入，实时收益不可用。
    """
    today = today or date.today()
    weekday = today.weekday()  # 周一=0
    this_monday = today - timedelta(days=weekday)

    if weekday >= 5:  # 周六/周日：下一笔买入是下周一
        buy_date = this_monday + timedelta(days=7)
        status = "pending"
        status_label = "待买入（下周一开盘）"
        is_buy_reached = False
    else:
        buy_date = this_monday
        is_buy_reached = True
        status = "buy_today" if weekday == 0 else "holding"
        status_label = "本周一已买入" if weekday != 0 else "本周一买入"

    sell_date = buy_date + timedelta(days=4)  # 当周周五
    next_buy_date = buy_date + timedelta(days=7)
    return TradeWindow(
        buy_date=buy_date.isoformat(),
        sell_date=sell_date.isoformat(),
        status=status,
        status_label=status_label,
        next_buy_date=next_buy_date.isoformat(),
        days_since_buy=(today - buy_date).days,
        days_to_sell=(sell_date - today).days,
        is_buy_reached=is_buy_reached,
    )


def _fetch_tencent_kline(code: str, start: date, end: date) -> Optional[pd.DataFrame]:
    """用 TencentFetcher 拉取 [start, end] 的日线（快、不易被封）。"""
    try:
        mgr = DataFetcherManager()
        df, _ = mgr.get_daily_data(
            code,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            source=TENCENT_SOURCE,
        )
        if df is None or df.empty:
            return None
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.sort_values("date").reset_index(drop=True)
    except (DataFetchError, Exception) as exc:  # noqa: BLE001 - 单票失败不致命
        logger.warning("[weekly] TencentFetcher 拉取 %s 失败: %s", code, exc)
        return None


def _compute_live_return(df: pd.DataFrame, buy_date: date) -> Dict[str, Any]:
    """以买入日（周一）开盘价为成本，计算 1/3/5 日实时收益。

    收益是否可算取决于实际返回的交易日行（自动兼容停牌/节假日顺延）。
    """
    rows = df[df["date"] >= buy_date]
    if rows.empty:
        return {
            "available": False,
            "buy_date": None,
            "buy_price": None,
            "last_price": None,
            "return_1d_pct": None,
            "return_3d_pct": None,
            "return_wk_pct": None,
        }

    buy_idx = int(rows.index[0])
    real_buy_date = df.loc[buy_idx, "date"]
    open_price = float(df.loc[buy_idx, "open"])
    last_row = df.loc[df.index[-1]]
    last_price = float(last_row.get("close", last_row.get("last")))

    def _ret(n: int) -> Optional[float]:
        ti = buy_idx + n
        if ti >= len(df):
            return None
        close_price = float(df.loc[ti].get("close", df.loc[ti].get("last")))
        if open_price <= 0:
            return None
        return round((close_price / open_price - 1.0) * 100.0, 2)

    return {
        "available": True,
        "buy_date": real_buy_date.isoformat(),
        "buy_price": round(open_price, 2),
        "last_price": round(last_price, 2),
        "return_1d_pct": _ret(1),
        "return_3d_pct": _ret(3),
        "return_wk_pct": _ret(4),
    }


def build_weekly_recommendations(
    *,
    industry: Optional[str] = None,
    top_n: int = 20,
    industry_cap: Optional[int] = 3,
    as_of_date: Optional[date] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """构造单页推荐数据：榜单 + 买卖窗口 + 实时收益。

    Raises:
        StockRankingError: 无强弱榜快照时（需先 python rank_snapshot.py）。
    """
    ranking = StockRankingService()
    rec = ranking.get_recommendations(
        industry=industry, top_n=top_n, industry_cap=industry_cap, as_of_date=as_of_date
    )

    window = resolve_trade_window(today)
    buy_date = datetime.strptime(window.buy_date, "%Y-%m-%d").date()
    end_date = today or date.today()

    live_items: List[Dict[str, Any]] = []
    r1: List[float] = []
    r3: List[float] = []
    rw: List[float] = []

    for it in rec.get("items", []):
        code = (it.get("code") or "").strip().upper()
        live: Dict[str, Any] = {
            "code": code,
            "available": False,
            "buy_date": None,
            "buy_price": None,
            "last_price": None,
            "return_1d_pct": None,
            "return_3d_pct": None,
            "return_wk_pct": None,
            "note": None,
        }
        if window.is_buy_reached and code:
            df = _fetch_tencent_kline(code, buy_date, end_date)
            if df is not None and not df.empty:
                lr = _compute_live_return(df, buy_date)
                live.update(lr)
                if lr.get("return_1d_pct") is not None:
                    r1.append(lr["return_1d_pct"])
                if lr.get("return_3d_pct") is not None:
                    r3.append(lr["return_3d_pct"])
                if lr.get("return_wk_pct") is not None:
                    rw.append(lr["return_wk_pct"])
            else:
                live["note"] = "实时行情获取失败（可能停牌/未上市）"
        else:
            live["note"] = "买入日未到，暂无实时收益"
        live_items.append(live)

    def _avg(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    def _win(xs: List[float]) -> float:
        return round(sum(1 for x in xs if x > 0) / len(xs), 4) if xs else 0.0

    summary = {
        "total": len(live_items),
        "with_data": len(
            [l for l in live_items if l.get("available") and l.get("buy_price") is not None]
        ),
        "avg_1d_pct": _avg(r1),
        "avg_3d_pct": _avg(r3),
        "avg_wk_pct": _avg(rw),
        "win_rate_1d": _win(r1),
        "win_rate_3d": _win(r3),
        "win_rate_wk": _win(rw),
        "best_1d_pct": round(max(r1), 2) if r1 else 0.0,
        "worst_1d_pct": round(min(r1), 2) if r1 else 0.0,
    }

    return {
        "scope": rec.get("scope"),
        "industry": rec.get("industry"),
        "as_of_date": rec.get("as_of_date"),
        "universe_size": rec.get("universe_size"),
        "count": rec.get("count"),
        "industry_cap": rec.get("industry_cap"),
        "strategy": rec.get("strategy"),
        "items": rec.get("items", []),
        "trade_window": window.to_dict(),
        "live": live_items,
        "live_summary": summary,
        "data_source": TENCENT_SOURCE,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": rec.get("disclaimer"),
    }
