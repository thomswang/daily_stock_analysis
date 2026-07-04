# -*- coding: utf-8 -*-
"""训练批量读库：JOIN 预加载测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from src.repositories.stock_repo import StockRepository, compute_training_date_range
from src.storage import DatabaseManager


class TestTrainingBulkLoad(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "bulk.db")
        self.db = DatabaseManager(db_url=f"sqlite:///{self.db_path}")
        self.repo = StockRepository(self.db)

        kline_a = pd.DataFrame([
            {"date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10.5,
             "volume": 1000, "amount": 10000, "pct_chg": 1.0},
            {"date": "2024-01-03", "open": 10.5, "high": 11, "low": 10, "close": 10.8,
             "volume": 1100, "amount": 11000, "pct_chg": 2.0},
        ])
        kline_b = pd.DataFrame([
            {"date": "2024-01-02", "open": 20, "high": 21, "low": 19, "close": 20.5,
             "volume": 2000, "amount": 20000, "pct_chg": 0.5},
        ])
        self.db.save_daily_data(kline_a, "600519", data_source="test")
        self.db.save_daily_data(kline_b, "000001", data_source="test")
        self.db.save_daily_quote_data(
            [{"date": date(2024, 1, 2), "turnover_rate": 0.3, "float_shares": 1e9}],
            "600519",
            data_source="test",
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        self._tmpdir.cleanup()

    def test_load_merged_bulk_returns_per_code_frames(self) -> None:
        out = self.repo.load_merged_bulk(
            ["600519", "000001"],
            date(2024, 1, 1),
            date(2024, 1, 31),
        )
        self.assertIn("600519", out)
        self.assertIn("000001", out)
        self.assertEqual(len(out["600519"]), 2)
        self.assertEqual(len(out["000001"]), 1)
        self.assertAlmostEqual(float(out["600519"].iloc[0]["turnover_rate"]), 0.3)

    def test_load_merged_df_single_code(self) -> None:
        df = self.repo.load_merged_df("600519", date(2024, 1, 1), date(2024, 1, 31))
        self.assertEqual(len(df), 2)

    def test_compute_training_date_range(self) -> None:
        start, end = compute_training_date_range(250, end_date=date(2024, 6, 1))
        self.assertLess(start, end)
        self.assertEqual(end, date(2024, 6, 1))


if __name__ == "__main__":
    unittest.main()
