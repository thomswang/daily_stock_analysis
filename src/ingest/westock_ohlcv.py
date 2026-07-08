# -*- coding: utf-8 -*-
"""westock kline(qfq) 采集 → 通用表 stock_daily_ohlcv（与百度历史段同口径拼接）。

与 stock_daily_kline（腾讯表，按约束不可改动）完全解耦：本 ingestor 只写
stock_daily_ohlcv，承接「百度历史全量段 + westock 每日增量段」的同源 qfq K 线，
从而在 (code, date, adj_type='qfq') 上无缝拼接，无复权断崖。

关键口径处理（与上一轮 baidu vs westock 对齐验证一致）：
- volume：westock kline 返回单位为「手」，落库统一为「股」需 ×100（与百度一致）。
- ratio(涨跌幅)：westock kline 不返回该项，按前一日 qfq 收盘价链式推导；首行取
  库内该 code 在 adj_type='qfq' 下早于本批的最近收盘价作种子。preClose(昨收) 不落库
  （=前一日 close，可在特征工程阶段由 close 偏移得到，避免冗余维护）。
- amount / turnover_rate(→turnoverratio)：与百度口径一致，直接落库。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Optional

from data_provider.westock_client import (
    DEFAULT_KLINE_ADJ,
    fetch_kline_range,
    parse_kline_row,
)
from src.ingest.protocols import KlinePersistResult
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

WESTOCK_OHLCV_SOURCE = "Westock"
# westock kline 的 volume 单位为「手」，落库统一为「股」需 ×100（与百度一致）
_KLINE_VOLUME_TO_SHARES = 100


class WestockOhlcvIngestor:
    """westock kline(qfq) → stock_daily_ohlcv（源无关通用表）。"""

    source_name = WESTOCK_OHLCV_SOURCE

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        adj: str = DEFAULT_KLINE_ADJ,
    ):
        self._db = db_manager
        self._adj = adj

    @property
    def db(self) -> DatabaseManager:
        if self._db is None:
            self._db = DatabaseManager.get_instance()
        return self._db

    def backfill(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> KlinePersistResult:
        """拉取 westock kline 整段并 upsert 到 stock_daily_ohlcv（adj_type=qfq）。

        overwrite 仅作接口兼容（save_ohlcv_kline 始终 upsert），无实际分支差异。
        """
        raw = fetch_kline_range(
            code, start_date=start.isoformat(), end_date=end.isoformat(), adj=self._adj
        )
        if not raw:
            return KlinePersistResult(
                rows_saved=0, source=self.source_name, rows_fetched=0
            )
        df = self._build_df(code, raw)
        if df is None or len(df) == 0:
            return KlinePersistResult(
                rows_saved=0, source=self.source_name, rows_fetched=len(raw)
            )
        saved = self.db.save_ohlcv_kline(
            df, code, data_source=self.source_name, adj_type=self._adj
        )
        return KlinePersistResult(
            rows_saved=saved, source=self.source_name, rows_fetched=len(raw)
        )

    def _build_df(self, code: str, raw: List[dict]):
        """raw kline → ohlcv DataFrame（含 volume×100、派生 ratio、time）。"""
        import pandas as pd

        records = []
        for row in raw:
            rec = parse_kline_row(row, adj=self._adj)
            d = rec.get("date")
            if d is None:
                continue
            close = rec.get("close")
            vol = rec.get("volume")
            records.append(
                {
                    "date": d,
                    "open": rec.get("open"),
                    "high": rec.get("high"),
                    "low": rec.get("low"),
                    "close": close,
                    "volume": (vol * _KLINE_VOLUME_TO_SHARES) if vol is not None else None,
                    "amount": rec.get("amount"),
                    "turnoverratio": rec.get("turnover_rate"),
                    "ratio": None,
                    "time": d.isoformat(),
                }
            )
        if not records:
            return None
        records.sort(key=lambda r: r["date"])

        # 链式推导 ratio（qfq 口径：前一日 qfq 收盘价）；preClose 不落库，由 close 偏移可得
        prev_close = self._seed_prev_close(code, records[0]["date"])
        for rec in records:
            close = rec["close"]
            if prev_close is not None and close is not None and prev_close != 0:
                rec["ratio"] = (close - prev_close) / prev_close * 100.0
            if close is not None:
                prev_close = close

        return pd.DataFrame.from_records(records)

    def _seed_prev_close(self, code: str, first_date: date) -> Optional[float]:
        """取库内该 code 在 qfq 口径下、早于本批首日的收盘价作种子（百度/westock 均可）。"""
        try:
            seed_end = (first_date - timedelta(days=1)).isoformat()
            prior = self.db.get_ohlcv_kline(
                code, end_date=seed_end, adj_type=self._adj
            )
            if prior:
                return prior[-1].get("close")
        except Exception as exc:  # noqa: BLE001
            logger.debug("seed prev close 失败 %s: %s", code, exc)
        return None
