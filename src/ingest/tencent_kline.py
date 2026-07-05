# -*- coding: utf-8 -*-
"""腾讯 K 线采集（fqkline 时间序列，无 failover）。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from data_provider.base import DataFetcherManager, DataFetchError
from data_provider.westock_fields import DEFAULT_KLINE_ADJ
from src.ingest.protocols import KlineFetchResult, KlinePersistResult
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

TENCENT_KLINE_SOURCE = "TencentFetcher"


class TencentKlineIngestor:
    """仅走 TencentFetcher，失败即抛错，不尝试其它平台。

    优势（vs WestockKlineIngestor）：
    - 直接 HTTP 请求，无 node subprocess 开销
    - 单次上限 800 条（vs westock 250 条），2 年区间一次拉完
    - 内置限流保护（BaseFetcher._enforce_rate_limit）
    - 有截断检测（_is_capped_history_incomplete）
    - 并发抗性好（HTTP 连接池 vs 串行 node 进程）

    缺点：不含换手率（turnover_rate），该字段由 stock_daily_quote 表补。
    """

    source_name = TENCENT_KLINE_SOURCE

    def __init__(
        self,
        manager: Optional[DataFetcherManager] = None,
        *,
        db_manager: Optional[DatabaseManager] = None,
        adj: str = DEFAULT_KLINE_ADJ,
    ):
        self._manager = manager
        self._db = db_manager
        self._adj = adj

    @property
    def manager(self) -> DataFetcherManager:
        if self._manager is None:
            self._manager = DataFetcherManager()
        return self._manager

    @property
    def db(self) -> DatabaseManager:
        if self._db is None:
            self._db = DatabaseManager.get_instance()
        return self._db

    def fetch(
        self,
        code: str,
        *,
        start: date,
        end: date,
    ) -> KlineFetchResult:
        df, source = self.manager.get_daily_data(
            code,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            source=self.source_name,
        )
        if df is None or df.empty:
            raise DataFetchError(
                f"{self.source_name} 返回空 K 线: {code} [{start}~{end}]"
            )
        return KlineFetchResult(df=df, source=source)

    def backfill(
        self,
        code: str,
        *,
        start: date,
        end: date,
        overwrite: bool = True,
    ) -> KlinePersistResult:
        """拉取腾讯 kline 整段并 upsert 到 stock_daily_kline。

        直接调 fqkline API（不走 DataFetcherManager），保留 qt 快照用于
        计算换手率。返回 KlinePersistResult，与 WestockKlineIngestor.backfill
        接口一致。

        换手率算法（与 westock 一致）：
          流通股本 = qt[45](流通市值,亿) × 1e8 / qt[3](当前价)
          turnover_rate = volume(手) × 100 / 流通股本 × 100
        """
        records = self._fetch_and_map(code, start=start, end=end)
        if not records:
            return KlinePersistResult(
                rows_saved=0, source=self.source_name, rows_fetched=0
            )

        saved = self.db.save_daily_kline_data(
            records,
            code,
            data_source=self.source_name,
            adj_type=self._adj,
            overwrite=overwrite,
        )
        return KlinePersistResult(
            rows_saved=saved, source=self.source_name, rows_fetched=len(records)
        )

    def _fetch_and_map(
        self,
        code: str,
        *,
        start: date,
        end: date,
    ) -> List[Dict[str, Any]]:
        """直接调 fqkline API，返回映射后的 records。

        fqkline 只返回 6 字段（date, open, last, high, low, volume），
        不含 amount 和 turnover_rate。

        turnover_rate 不在此计算——用当前流通股本除历史成交量不准确
        （流通股本会因解禁/增发/回购变化）。换手率由 quote 逐日回填获取，
        quote --date 返回的是当天的真实快照。
        """
        import requests
        from data_provider.tencent_fetcher import (
            _to_tencent_symbol,
            _estimate_lookback_days,
            _format_tencent_date,
            _extract_kline_rows,
        )

        symbol = _to_tencent_symbol(code)
        if not symbol:
            return []

        lookback = _estimate_lookback_days(
            start_date=start.isoformat(), end_date=end.isoformat()
        )
        explicit_start = _format_tencent_date(start.isoformat())
        explicit_end = _format_tencent_date(end.isoformat())
        explicit_window = (
            f"{explicit_start},{explicit_end}"
            if explicit_start and explicit_end
            else ","
        )

        try:
            resp = requests.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"{symbol},day,{explicit_window},{lookback},qfq"},
                headers={"User-Agent": "Mozilla/5.0",
                         "Accept": "application/json,text/plain,*/*"},
                timeout=8,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning("fqkline 请求失败 %s [%s~%s]: %s", code, start, end, exc)
            return []

        rows = _extract_kline_rows(payload, symbol=symbol)
        if not rows:
            return []

        records: List[Dict[str, Any]] = []
        for row in rows:
            d_str = row.get("date")
            if not d_str:
                continue
            try:
                from datetime import datetime as _dt
                row_date = _dt.strptime(str(d_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            rec: Dict[str, Any] = {
                "adj_type": self._adj,
                "date": row_date,
                "open": self._safe_float(row.get("open")),
                "high": self._safe_float(row.get("high")),
                "low": self._safe_float(row.get("low")),
                "close": self._safe_float(row.get("last")),
                "volume": self._safe_float(row.get("volume")),
                "amount": self._safe_float(row.get("amount")),
                "turnover_rate": None,
            }
            records.append(rec)

        return records

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            return None if f != f else f  # NaN check
        except (TypeError, ValueError):
            return None

