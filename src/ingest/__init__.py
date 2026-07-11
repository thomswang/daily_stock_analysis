# -*- coding: utf-8 -*-
"""日线数据分层采集（quote 截面 + K 线时间序列，统一落 stock_daily_ohlcv）。

旧 stock_daily_kline 表已下线，Tencent/Westock kline 写入适配层已移除；
K 线时间序列统一经 stock_daily_ohlcv（backfill.py baidu / westock-ohlcv）。
"""

from .service import DailyIngestService, IngestResult
from .tencent_quote import TencentQuoteIngestor

__all__ = [
    "DailyIngestService",
    "IngestResult",
    "TencentQuoteIngestor",
]
