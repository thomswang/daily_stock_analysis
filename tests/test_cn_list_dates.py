# -*- coding: utf-8 -*-
"""Tests for westock profile list date fetch and shard merge."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from data_provider.westock_client import fetch_profile_listed_dates
from scripts.fetch_cn_list_dates import shard_codes
from src.services.cn_list_date_store import CnListDateStore, merge_shard_files


def test_fetch_profile_listed_dates_batch() -> None:
    payload = {
        "data": [
            {
                "symbol": "sh600519",
                "data": {"name": "贵州茅台", "listedDate": "2001-08-27"},
            },
            {
                "symbol": "sz001335",
                "data": {"name": "信凯科技", "listedDate": "2025-04-15"},
            },
        ]
    }

    with patch("data_provider.westock_client.run_westock_raw", return_value=payload):
        rows = fetch_profile_listed_dates(["600519", "001335"])

    assert rows["600519"]["list_date"] == "2001-08-27"
    assert rows["001335"]["list_date"] == "2025-04-15"
    assert rows["600519"]["name"] == "贵州茅台"


def test_shard_codes_disjoint() -> None:
    codes = [f"{i:06d}" for i in range(10)]
    s0 = shard_codes(codes, 0, 3)
    s1 = shard_codes(codes, 1, 3)
    s2 = shard_codes(codes, 2, 3)
    assert len(s0) + len(s1) + len(s2) == 10
    assert set(s0) | set(s1) | set(s2) == set(codes)
    assert not (set(s0) & set(s1))


def test_merge_shard_files(tmp_path: Path) -> None:
    p0 = tmp_path / "a.shard0of2.json"
    p1 = tmp_path / "a.shard1of2.json"
    out = tmp_path / "merged.json"
    p0.write_text(json.dumps({
        "meta": {},
        "codes": {"600519": {"list_date": "2001-08-27", "name": "贵州茅台"}},
    }), encoding="utf-8")
    p1.write_text(json.dumps({
        "meta": {},
        "codes": {"001335": {"list_date": "2025-04-15", "name": "信凯科技"}},
    }), encoding="utf-8")

    stats = merge_shard_files(str(out), [str(p0), str(p1)])
    merged = CnListDateStore(str(out))
    assert stats["total_codes"] == 2
    assert merged.get("600519") == "2001-08-27"
    assert merged.get("001335") == "2025-04-15"
