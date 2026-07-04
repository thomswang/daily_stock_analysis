# -*- coding: utf-8 -*-
"""
日线分层采集编排器。

架构：
    ┌─────────────────────────────────────────┐
    │ DailyIngestService（编排）               │
    └───────────┬─────────────┬───────────────┘
                │             │
    ┌───────────▼──────┐  ┌───▼────────────────────┐
    │ TencentKline      │  │ TencentQuote            │
    │ fqkline 时间序列   │  │ quote --date 单日截面    │
    │ → stock_daily     │  │ → stock_daily_quote     │
    └───────────────────┘  └─────────────────────────┘

核心矛盾（与 westock-data/test 一致）：
    - K 线：一次 N 天，8 列，适合 bulk
    - quote --date：一次 1 天，40+ 列，含逐日 turnover_rate / float_shares
    二者接口不同、不可混用，故分表 + 分采集器。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from data_provider.base import normalize_stock_code
from src.ingest.protocols import KlineIngestor, QuoteIngestor
from src.ingest.tencent_kline import TencentKlineIngestor
from src.ingest.tencent_quote import TencentQuoteIngestor
from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    kline_added: int
    quote_added: int
    kline_source: str
    quote_source: Optional[str]


def is_cn_a_share(code: str) -> bool:
    plain = normalize_stock_code(code)
    return plain.isdigit() and len(plain) == 6


class DailyIngestService:
    """腾讯专用日线采集：K 线 + quote 截面，无跨平台 failover。"""

    def __init__(
        self,
        repo: Optional[StockRepository] = None,
        *,
        kline: Optional[KlineIngestor] = None,
        quote: Optional[QuoteIngestor] = None,
        quote_enabled: bool = True,
    ):
        self.repo = repo or StockRepository()
        self.kline = kline or TencentKlineIngestor()
        self.quote = quote or TencentQuoteIngestor(db_manager=self.repo.db)
        self.quote_enabled = quote_enabled

    def ingest_range(
        self,
        code: str,
        *,
        start: date,
        end: date,
        quote_overwrite: bool = True,
    ) -> IngestResult:
        """拉取 [start, end] 并落库（K 线必做；A 股则补 quote 截面）。"""
        kline_result = self.kline.fetch(code, start=start, end=end)
        kline_added = self.repo.save_dataframe(
            kline_result.df, code, data_source=kline_result.source
        )

        quote_added = 0
        quote_source: Optional[str] = None
        if self.quote_enabled and is_cn_a_share(code):
            quote_result = self.quote.backfill(
                code, start=start, end=end, overwrite=quote_overwrite
            )
            quote_added = quote_result.rows_saved
            quote_source = quote_result.source

        logger.info(
            "%s 采集完成 [%s~%s]: K线+%d quote+%d",
            code, start, end, kline_added, quote_added,
        )
        return IngestResult(
            kline_added=kline_added,
            quote_added=quote_added,
            kline_source=kline_result.source,
            quote_source=quote_source,
        )

    def ingest_kline(
        self,
        code: str,
        *,
        start: date,
        end: date,
    ) -> IngestResult:
        """仅拉 K 线层（stock_daily）。"""
        kline_result = self.kline.fetch(code, start=start, end=end)
        added = self.repo.save_dataframe(
            kline_result.df, code, data_source=kline_result.source
        )
        return IngestResult(
            kline_added=added,
            quote_added=0,
            kline_source=kline_result.source,
            quote_source=None,
        )

    def ingest_quote(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> IngestResult:
        """仅拉 quote 截面层（stock_daily_quote）。"""
        if not self.quote_enabled or not is_cn_a_share(code):
            return IngestResult(0, 0, "", None)
        quote_result = self.quote.backfill(
            code, start=start, end=end, overwrite=overwrite
        )
        return IngestResult(
            kline_added=0,
            quote_added=quote_result.rows_saved,
            kline_source="",
            quote_source=quote_result.source,
        )
