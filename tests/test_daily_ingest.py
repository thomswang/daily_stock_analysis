# -*- coding: utf-8 -*-
"""DailyIngestService 与 quote 单表采集测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from src.ingest.service import DailyIngestService, IngestResult
from src.ingest.protocols import QuoteFetchResult


def test_ingest_quote_delegates_to_westock() -> None:
    quote = MagicMock()
    quote.backfill.return_value = QuoteFetchResult(rows_saved=3, source="TencentQuote")
    quote.source_name = "TencentQuote"

    repo = MagicMock()
    svc = DailyIngestService(repo=repo, quote=quote)
    result = svc.ingest_quote("600519", start=date(2026, 6, 25), end=date(2026, 6, 27))

    quote.backfill.assert_called_once()
    assert result.quote_added == 3
    assert result.kline_added == 0
    assert result.quote_source == "TencentQuote"


def test_ingest_range_is_quote_only() -> None:
    quote = MagicMock()
    quote.backfill.return_value = QuoteFetchResult(rows_saved=1, source="TencentQuote")

    svc = DailyIngestService(repo=MagicMock(), quote=quote)
    result = svc.ingest_range("600519", start=date(2026, 7, 3), end=date(2026, 7, 3))

    quote.backfill.assert_called_once()
    assert result.quote_added == 1


def test_ingest_kline_deprecated_routes_to_quote() -> None:
    quote = MagicMock()
    quote.backfill.return_value = QuoteFetchResult(rows_saved=2, source="TencentQuote")

    svc = DailyIngestService(repo=MagicMock(), quote=quote)
    result = svc.ingest_kline("600519", start=date(2026, 7, 1), end=date(2026, 7, 2))

    quote.backfill.assert_called_once()
    assert result.quote_added == 2
