# -*- coding: utf-8 -*-
"""日线数据分层采集（K 线时间序列 + quote 截面）。"""

from .service import DailyIngestService, IngestResult
from .tencent_kline import TencentKlineIngestor
from .tencent_quote import TencentQuoteIngestor

__all__ = [
    "DailyIngestService",
    "IngestResult",
    "TencentKlineIngestor",
    "TencentQuoteIngestor",
]
