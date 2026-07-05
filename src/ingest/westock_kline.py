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
        # 拆分 fetch/save 两步：让上层能通过 rows_fetched 区分「接口真返回 0 条」
        # 和「拉到了但落库 0 行」（后者不应被判为该票的 empty 终态）。
        records = self._service.fetch_bars(code, start=start, end=end)
        if not records:
            return KlinePersistResult(
                rows_saved=0, source=self.source_name, rows_fetched=0
            )
        saved = self._service.db.save_daily_kline_data(
            records,
            code,
            data_source=self.source_name,
            adj_type=self._service.adj,
            overwrite=overwrite,
        )
        return KlinePersistResult(
            rows_saved=saved, source=self.source_name, rows_fetched=len(records)
        )
