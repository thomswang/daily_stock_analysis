# -*- coding: utf-8 -*-
"""采集层协议：K 线（时间序列）与 quote（截面）分离。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

import pandas as pd


@dataclass(frozen=True)
class KlineFetchResult:
    df: pd.DataFrame
    source: str


@dataclass(frozen=True)
class QuoteFetchResult:
    rows_saved: int
    source: str


class KlineIngestor(Protocol):
    """第 1 层：一次拉 N 天 OHLCV → stock_daily。"""

    source_name: str

    def fetch(
        self,
        code: str,
        *,
        start: date,
        end: date,
    ) -> KlineFetchResult: ...


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
