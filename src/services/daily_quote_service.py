# -*- coding: utf-8 -*-
"""
行情截面回填（第 2 层：quote --date → stock_daily_quote）。

与 stock_daily（kline 时间序列）分表存储，解决 westock 两套接口的核心矛盾：
  - kline：一次拉 N 天 OHLCV（8 列），写入 stock_daily
  - quote --date：单日 40+ 列截面（含逐日 turnover_rate / float_shares），
    需按交易日循环，写入 stock_daily_quote

对齐 westock-data/test/index.html runDailyK()：并发分批调 quote --date。
训练读数时在 prediction_service 里 join 两表取 turnover_rate。
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class DailyQuoteService:
    """A 股 quote --date 截面回填 → stock_daily_quote。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.batch_size = int(os.getenv("WESTOCK_QUOTE_BATCH", "3"))
        self.sleep_batches = float(os.getenv("WESTOCK_QUOTE_SLEEP", "0.3"))

    def fetch_snapshots(
        self,
        code: str,
        *,
        start: date,
        end: date,
    ) -> List[Dict[str, Any]]:
        """拉取区间内 quote --date 截面（不写库）。"""
        from data_provider.westock_client import (
            WestockCliError,
            fetch_quote_snapshots_range,
            parse_quote_to_record,
        )

        try:
            pairs = fetch_quote_snapshots_range(
                code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                batch_size=self.batch_size,
                sleep_between_batches=self.sleep_batches,
            )
        except WestockCliError as exc:
            logger.warning("%s quote 截面拉取失败 [%s~%s]: %s", code, start, end, exc)
            return []

        records: List[Dict[str, Any]] = []
        for d_str, raw in pairs:
            parsed = parse_quote_to_record(raw)
            if parsed.get("turnover_rate") is None:
                continue
            row: Dict[str, Any] = {"date": date.fromisoformat(d_str[:10]), **parsed}
            records.append(row)
        return records

    def backfill_and_save(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
        data_source: str = "TencentQuote",
    ) -> int:
        """拉取 quote --date 并 upsert 到 stock_daily_quote。"""
        records = self.fetch_snapshots(code, start=start, end=end)
        if not records:
            return 0
        return self.db.save_daily_quote_data(
            records, code, data_source=data_source, overwrite=overwrite
        )
