# -*- coding: utf-8 -*-
"""
stock_rank_run.model_id 单测。

覆盖：
1. save_run(model_id=...) 落库后，get_run / list_runs 能回读到 model_id。
2. 旧库（stock_rank_run 缺 model_id 列）被 _ensure_rank_snapshot_schema 自动删重建，
   重建后列存在且可继续写入 model_id。
3. model_version 为 None 时兜底为 "unknown"（避免 NOT NULL 崩溃）。
"""
import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import inspect

from src.config import Config
from src.storage import DatabaseManager
from src.repositories.rank_snapshot_repo import RankSnapshotRepository


class TestRankSnapshotModelId(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()

    def _new_repo(self) -> RankSnapshotRepository:
        """基于独立内存库（自动跑 schema 迁移）创建 repo。"""
        db = DatabaseManager(db_url="sqlite:///:memory:")
        return RankSnapshotRepository(db_manager=db)

    def test_save_run_persists_model_id_and_round_trips(self) -> None:
        repo = self._new_repo()
        run_id = repo.save_run(
            model_id=123,
            model_name="trend_xsec",
            model_version="20260701_113000",
            as_of_date=date(2026, 7, 1),
            lookback_days=60,
            universe_size=4000,
            industry_count=20,
        )

        got = repo.get_run(run_id)
        self.assertIsNotNone(got)
        self.assertEqual(got["run_id"], run_id)
        self.assertEqual(got["model_id"], 123)
        self.assertEqual(got["model_name"], "trend_xsec")
        self.assertEqual(got["model_version"], "20260701_113000")

        runs = repo.list_runs()
        self.assertTrue(any(r["run_id"] == run_id and r["model_id"] == 123 for r in runs))

    def test_save_run_null_model_id_allowed(self) -> None:
        """model_id 可为 None（硬关联缺失时的兜底，列本身可空）。"""
        repo = self._new_repo()
        run_id = repo.save_run(
            model_id=None,
            model_name="trend_xsec",
            model_version="20260701_113000",
            as_of_date=date(2026, 7, 2),
        )
        got = repo.get_run(run_id)
        self.assertIsNone(got["model_id"])

    def test_model_version_falls_back_to_unknown(self) -> None:
        """model_version 为 None 时落库为 "unknown"，避免 NOT NULL 崩溃。"""
        repo = self._new_repo()
        run_id = repo.save_run(
            model_id=1,
            model_name="trend_xsec",
            model_version=None,  # 故意传 None
            as_of_date=date(2026, 7, 3),
        )
        got = repo.get_run(run_id)
        self.assertEqual(got["model_version"], "unknown")

    def test_legacy_schema_without_model_id_auto_rebuilds(self) -> None:
        """模拟旧库（stock_rank_run 缺 model_id 列），验证自动删重建。"""
        # ignore_cleanup_errors: Windows 下引擎连接可能仍持有文件锁，忽略残留清理错误
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db_path = os.path.join(temp_dir.name, "legacy_rank.sqlite")
        # 手工造一张「旧版」stock_rank_run（无 model_id 列）和 stock_rank_snapshot（无 run_id 列）
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE stock_rank_run ("
                "run_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "model_name TEXT NOT NULL, "
                "model_version TEXT NOT NULL, "
                "as_of_date TEXT)"
            )
            conn.execute(
                "CREATE TABLE stock_rank_snapshot ("
                "as_of_date TEXT, code TEXT, rank INTEGER, score REAL)"
            )
            conn.commit()

        # 启动时触发迁移检测
        db = DatabaseManager(db_url=f"sqlite:///{db_path}")
        repo = RankSnapshotRepository(db_manager=db)

        # 重建后两张表应含新列
        inspector = inspect(db._engine)
        run_cols = {c["name"] for c in inspector.get_columns("stock_rank_run")}
        snap_cols = {c["name"] for c in inspector.get_columns("stock_rank_snapshot")}
        self.assertIn("model_id", run_cols)
        self.assertIn("run_id", snap_cols)

        # 重建后仍可正常写入并回读 model_id
        run_id = repo.save_run(
            model_id=456,
            model_name="trend_xsec",
            model_version="20260705_090000",
            as_of_date=date(2026, 7, 5),
        )
        got = repo.get_run(run_id)
        self.assertEqual(got["model_id"], 456)
        # 关闭引擎释放文件锁，否则 Windows 下 temp_dir.cleanup() 会因占用失败
        db._engine.dispose()
        temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
