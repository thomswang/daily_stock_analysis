# -*- coding: utf-8 -*-
"""采集层协议：quote（截面）与 K 线（时间序列，统一落 stock_daily_ohlcv）分离。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

import pandas as pd


@dataclass(frozen=True)
class KlinePersistResult:
    rows_saved: int
    source: str
    # 上游 fetch 拿到的原始行数（未去重/未落库前）。None 表示 ingestor 未记录。
    # 用于区分「接口真的返回 0 条（该票该区间确无数据）」vs「拉到了但落库 0 行
    # （例如 upsert 全冲突，或者被瞬时限流后重试）」——上层据此决定是否终态。
    rows_fetched: Optional[int] = None
    # 本次随 K 线搭车落库的财报披露事件行数（百度 vapi reportData）。
    # 与 rows_saved 解耦：财报解析失败不影响 K 线落库，故单独计数。
    reports_saved: Optional[int] = None


@dataclass(frozen=True)
class QuoteFetchResult:
    rows_saved: int
    source: str


class QuoteIngestor(Protocol):
    """第 2 层：按交易日循环 quote --date → stock_daily_quote。"""

    source_name: str

    def backfill(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> QuoteFetchResult: ...
