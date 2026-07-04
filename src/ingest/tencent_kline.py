# -*- coding: utf-8 -*-
"""腾讯 K 线采集（fqkline 时间序列，无 failover）。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data_provider.base import DataFetcherManager, DataFetchError
from src.ingest.protocols import KlineFetchResult

logger = logging.getLogger(__name__)

TENCENT_KLINE_SOURCE = "TencentFetcher"


class TencentKlineIngestor:
    """仅走 TencentFetcher，失败即抛错，不尝试其它平台。"""

    source_name = TENCENT_KLINE_SOURCE

    def __init__(self, manager: Optional[DataFetcherManager] = None):
        self._manager = manager

    @property
    def manager(self) -> DataFetcherManager:
        if self._manager is None:
            self._manager = DataFetcherManager()
        return self._manager

    def fetch(
        self,
        code: str,
        *,
        start: date,
        end: date,
    ) -> KlineFetchResult:
        df, source = self.manager.get_daily_data(
            code,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            source=self.source_name,
        )
        if df is None or df.empty:
            raise DataFetchError(
                f"{self.source_name} 返回空 K 线: {code} [{start}~{end}]"
            )
        return KlineFetchResult(df=df, source=source)
