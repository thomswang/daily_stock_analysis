# -*- coding: utf-8 -*-
"""腾讯行情截面采集（westock quote --date → stock_quote_snapshot）。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from src.ingest.protocols import QuoteFetchResult
from src.services.daily_quote_service import DailyQuoteService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# westock quote --date 底层即腾讯 stock_quote_snapshot / stock_quote_history
TENCENT_QUOTE_SOURCE = "TencentQuote"


class TencentQuoteIngestor:
    """按交易日循环 quote --date，写入 stock_daily_quote。"""

    source_name = TENCENT_QUOTE_SOURCE

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        quote_service: Optional[DailyQuoteService] = None,
    ):
        self._quote_service = quote_service or DailyQuoteService(db_manager=db_manager)

    def backfill(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> QuoteFetchResult:
        saved = self._quote_service.backfill_and_save(
            code,
            start=start,
            end=end,
            overwrite=overwrite,
            data_source=self.source_name,
        )
        return QuoteFetchResult(rows_saved=saved, source=self.source_name)
