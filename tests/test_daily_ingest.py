# -*- coding: utf-8 -*-
"""DailyIngestService 与腾讯锁定数据源测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from src.ingest.service import DailyIngestService, IngestResult


def test_ingest_kline_delegates_to_tencent_only() -> None:
    df = pd.DataFrame([{"date": "2026-06-25", "close": 100.0}])
    kline = MagicMock()
    kline.fetch.return_value = type("R", (), {"df": df, "source": "TencentFetcher"})()
    kline.source_name = "TencentFetcher"

    repo = MagicMock()
    repo.save_dataframe.return_value = 1

    svc = DailyIngestService(repo=repo, kline=kline, quote_enabled=False)
    result = svc.ingest_kline("600519", start=date(2026, 6, 25), end=date(2026, 6, 25))

    kline.fetch.assert_called_once()
    assert result.kline_added == 1
    assert result.kline_source == "TencentFetcher"


def test_get_daily_data_source_pin_skips_failover() -> None:
    from data_provider.base import DataFetcherManager, DataFetchError
    from data_provider.tencent_fetcher import TencentFetcher

    tencent = TencentFetcher()
    efinance = MagicMock()
    efinance.name = "EfinanceFetcher"
    efinance.priority = 0

    manager = DataFetcherManager(fetchers=[efinance, tencent])

    with patch.object(
        TencentFetcher,
        "get_daily_data",
        return_value=pd.DataFrame([{"date": "2026-06-25", "close": 1.0}]),
    ):
        df, source = manager.get_daily_data(
            "600519",
            start_date="2026-06-25",
            end_date="2026-06-25",
            source="TencentFetcher",
        )
    assert source == "TencentFetcher"
    assert not df.empty
    efinance.get_daily_data.assert_not_called()

    with patch.object(
        TencentFetcher,
        "get_daily_data",
        side_effect=DataFetchError("tencent down"),
    ):
        try:
            manager.get_daily_data(
                "600519",
                start_date="2026-06-25",
                end_date="2026-06-25",
                source="TencentFetcher",
            )
            raised = False
        except DataFetchError:
            raised = True
    assert raised
    efinance.get_daily_data.assert_not_called()
