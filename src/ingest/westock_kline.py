# -*- coding: utf-8 -*-
"""westock kline 采集（整段 day K → stock_daily_kline）。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from src.ingest.protocols import KlinePersistResult
from src.services.daily_kline_service import DailyKlineService
from src.storage import DatabaseManager

WESTOCK_KLINE_SOURCE = "WestockKline"


class WestockKlineIngestor:
    """一次 westock kline 请求整段区间，写入 stock_daily_kline。"""

    source_name = WESTOCK_KLINE_SOURCE

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        kline_service: Optional[DailyKlineService] = None,
    ):
        self._service = kline_service or DailyKlineService(db_manager=db_manager)

    def backfill(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> KlinePersistResult:
        saved = self._service.backfill_and_save(
            code,
            start=start,
            end=end,
            overwrite=overwrite,
            data_source=self.source_name,
        )
        return KlinePersistResult(rows_saved=saved, source=self.source_name)
