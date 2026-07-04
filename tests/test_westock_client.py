# -*- coding: utf-8 -*-
"""Tests for westock quote --date client."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from data_provider.westock_client import (
    enum_weekday_dates,
    parse_quote_snapshot,
    to_westock_symbol,
)


def test_to_westock_symbol() -> None:
    assert to_westock_symbol("600519") == "sh600519"
    assert to_westock_symbol("600519.SH") == "sh600519"
    assert to_westock_symbol("000001.SZ") == "sz000001"
    assert to_westock_symbol("920748.BJ") == "bj920748"
    assert to_westock_symbol("HK00700") is None


def test_enum_weekday_dates_skips_weekends() -> None:
    dates = enum_weekday_dates("2026-06-25", "2026-06-29")
    assert dates == ["2026-06-25", "2026-06-26", "2026-06-29"]


def test_parse_quote_to_record() -> None:
    raw = {
        "turnover_rate": 0.39,
        "float_shares": 1250081601,
        "total_shares": 1250081601,
        "volume_ratio": 0.92,
        "range_pct": 2.24,
        "change": 4.42,
        "change_percent": 0.37,
        "pe_ratio": 18.32,
        "pb_ratio": 5.66,
        "total_market_cap": 1.5e12,
        "circulating_market_cap": 1.5e12,
        "prev_close": 1207.68,
        "inner_volume": 24565,
        "outer_volume": 23882,
    }
    rec = parse_quote_snapshot(raw, quote_date="2026-06-25")
    assert rec["turnover_rate"] == 0.39
    assert rec["float_shares"] == 1250081601.0
    assert rec["change"] == 4.42
    assert rec["date"].isoformat() == "2026-06-25"
    assert "lot" not in rec
    assert "pre_market_price" not in rec
    assert "raw_json" not in rec


def test_fetch_quote_snapshots_range_batches() -> None:
    from data_provider import westock_client

    calls: list[str] = []

    def fake_fetch(code: str, d: str):
        calls.append(d)
        return {"turnover_rate": 1.0, "float_shares": 100}

    with patch.object(westock_client, "fetch_quote_snapshot", side_effect=fake_fetch):
        pairs = westock_client.fetch_quote_snapshots_range(
            "600519",
            start_date="2026-06-25",
            end_date="2026-06-26",
            batch_size=2,
            sleep_between_batches=0,
        )

    assert len(pairs) == 2
    assert calls == ["2026-06-25", "2026-06-26"]
