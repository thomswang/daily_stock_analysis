# -*- coding: utf-8 -*-
"""
westock kline 日线采集（整段拉取 → stock_daily_kline）。

与 quote 逐日截面分离：默认前复权 qfq，一次 node 请求整段区间。
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from data_provider.westock_fields import DEFAULT_KLINE_ADJ
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


class DailyKlineService:
    """A 股 kline 整段回填 → stock_daily_kline。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()
        self.adj = os.getenv("WESTOCK_KLINE_ADJ", DEFAULT_KLINE_ADJ)

    def fetch_bars(
        self,
        code: str,
        *,
        start: date,
        end: date,
        adj: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """拉取区间内 kline（不写库）。"""
        from data_provider.westock_client import (
            WestockCliError,
            fetch_kline_range,
            parse_kline_row,
        )

        adj_type = adj or self.adj
        try:
            rows = fetch_kline_range(
                code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                adj=adj_type,
            )
        except WestockCliError as exc:
            logger.warning("%s kline 拉取失败 [%s~%s]: %s", code, start, end, exc)
            raise

        records: List[Dict[str, Any]] = []
        for raw in rows:
            parsed = parse_kline_row(raw, adj=adj_type)
            if parsed.get("date") is None:
                continue
            records.append(parsed)
        return records

    def backfill_and_save(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
        data_source: str = "WestockKline",
        adj: Optional[str] = None,
    ) -> int:
        """拉取 kline 整段并 upsert 到 stock_daily_kline。"""
        adj_type = adj or self.adj
        records = self.fetch_bars(code, start=start, end=end, adj=adj_type)
        if not records:
            return 0
        return self.db.save_daily_kline_data(
            records,
            code,
            data_source=data_source,
            adj_type=adj_type,
            overwrite=overwrite,
        )
