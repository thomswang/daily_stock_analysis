# -*- coding: utf-8
"""Tests for westock kline client and kline table backfill."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from data_provider.westock_client import parse_kline_row
from src.repositories.stock_repo import StockRepository
from src.repositories.training_bars import TrainBarSource, load_training_bars_bulk
from src.storage import DatabaseManager


class TestWestockKlineParse(unittest.TestCase):
    def test_parse_kline_row_maps_last_and_exchange(self) -> None:
        row = parse_kline_row({
            "date": "2024-06-14",
            "open": 1.0,
            "last": 2.0,
            "high": 3.0,
            "low": 0.5,
            "volume": 100.0,
            "amount": 200.0,
            "exchange": "0.47",
        })
        self.assertEqual(row["close"], 2.0)
        self.assertAlmostEqual(float(row["turnover_rate"]), 0.47)
        self.assertEqual(row["date"], date(2024, 6, 14))
        self.assertEqual(row["adj_type"], "qfq")


class TestKlineStorage(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(db_url=f"sqlite:///{self._tmpdir.name}/kline.db")
        self.repo = StockRepository(self.db)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._tmpdir.cleanup()

    def test_save_and_load_kline_bulk(self) -> None:
        saved = self.db.save_daily_kline_data(
            [{
                "date": date(2024, 1, 2),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10000.0,
                "turnover_rate": 0.3,
            }],
            "600519",
            data_source="test",
        )
        self.assertEqual(saved, 1)
        cov = self.repo.get_kline_coverage("600519")
        self.assertEqual(cov["rows"], 1)
        frames = self.repo.load_kline_bulk(
            ["600519"], date(2024, 1, 1), date(2024, 1, 31),
        )
        self.assertIn("600519", frames)
        self.assertAlmostEqual(float(frames["600519"].iloc[0]["close"]), 10.5)

    @patch.dict(os.environ, {"TRAIN_BAR_SOURCE": "kline"})
    def test_training_bars_prefers_kline(self) -> None:
        self.db.save_daily_kline_data(
            [{
                "date": date(2024, 1, 2),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10000.0,
                "turnover_rate": 0.3,
            }],
            "600519",
            data_source="test",
        )
        out = load_training_bars_bulk(
            ["600519"],
            date(2024, 1, 1),
            date(2024, 1, 31),
            source=TrainBarSource.KLINE,
        )
        self.assertIn("600519", out)
        self.assertAlmostEqual(float(out["600519"].iloc[0]["close"]), 10.5)


if __name__ == "__main__":
    unittest.main()
