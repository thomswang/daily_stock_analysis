# -*- coding: utf-8 -*-
"""
日线采集编排器（quote 截面落库）。

- quote --date → stock_daily_quote（不复权，40+ 字段，慢）
- K 线时间序列统一走 stock_daily_ohlcv（backfill.py baidu / westock-ohlcv）

旧 stock_daily_kline 表已下线，相关写入逻辑已移除。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

from data_provider.base import normalize_stock_code
from src.ingest.protocols import QuoteIngestor
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
    """A 股日线：westock quote --date 逐日落库（stock_daily_quote）。"""

    def __init__(
        self,
        repo: Optional[StockRepository] = None,
        *,
        quote: Optional[QuoteIngestor] = None,
        quote_enabled: bool = True,
    ):
        self.repo = repo or StockRepository()
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
        """拉取 [start, end] 每个工作日 quote --date 并落库。"""
        return self.ingest_quote(
            code, start=start, end=end, overwrite=quote_overwrite,
        )

    def ingest_quote(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> IngestResult:
        """按天循环 westock quote --date → stock_daily_quote。"""
        if not self.quote_enabled or not is_cn_a_share(code):
            return IngestResult(0, 0, "", None)
        quote_result = self.quote.backfill(
            code, start=start, end=end, overwrite=overwrite,
        )
        logger.info(
            "%s quote 采集完成 [%s~%s]: +%d 行",
            code, start, end, quote_result.rows_saved,
        )
        return IngestResult(
            kline_added=0,
            quote_added=quote_result.rows_saved,
            kline_source="",
            quote_source=quote_result.source,
        )
