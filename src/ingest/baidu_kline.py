# -*- coding: utf-8 -*-
"""百度股市通 K 线采集（含换手率/振幅/MA，无 failover）。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from data_provider.base import DataFetchError
from src.ingest.protocols import KlinePersistResult
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

BAIDU_KLINE_SOURCE = "BaiduFetcher"


class BaiduKlineIngestor:
    """仅走 BaiduFetcher，失败即抛错，不尝试其它平台。

    优势（vs TencentKlineIngestor）：
    - 直接 HTTP 请求，无 node subprocess 开销
    - 单次整段拉取（带 all=1 可回溯到上市日，如茅台 2001）
    - K 线自带换手率/振幅/MA，无需再走 quote --date 补换手率
    """

    source_name = BAIDU_KLINE_SOURCE

    def __init__(
        self,
        manager=None,
        *,
        db_manager: Optional[DatabaseManager] = None,
        ktype: str = "1",
        token_provider=None,
    ):
        self._manager = manager
        self._db = db_manager
        self._ktype = ktype
        self._token_provider = token_provider
        self._fetcher = None

    @property
    def fetcher(self):
        if self._fetcher is None:
            from data_provider.baidu_fetcher import BaiduFetcher

            self._fetcher = BaiduFetcher(token_provider=self._token_provider)
        return self._fetcher

    def close(self) -> None:
        """释放 token_provider 持有的浏览器资源（如由本 ingestor 创建）。"""
        if self._token_provider is not None:
            try:
                self._token_provider.close()
            except Exception:  # noqa: BLE001
                pass

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
        ktype: Optional[str] = None,
        overwrite: bool = True,
        full: bool = True,
    ) -> KlinePersistResult:
        """拉取百度 K 线并 upsert 到通用表 stock_daily_ohlcv（adj_type=qfq）。

        full: True 拉全量（all=1，回溯到上市日）；False 仅拉最近约 2000 行尾窗口
        （老票≈2018 起，新股=上市日起），用于已存有深历史、只需刷新近期数据的场景。
        """
        ktype = ktype or self._ktype
        # 单次请求同时拿到 K 线与财报披露事件（百度 reportData 随 K 线响应返回，
        # 零额外请求），财报解析/落库失败不影响 K 线主流程。
        df, reports = self.fetcher.fetch_kline_and_reports(
            code, start.isoformat(), end.isoformat(), ktype=ktype, full=full
        )
        if df is None or df.empty:
            return KlinePersistResult(
                rows_saved=0, source=self.source_name, rows_fetched=0, reports_saved=0
            )
        saved = self.db.save_ohlcv_kline(
            df, code, data_source=self.source_name, ktype=ktype, adj_type="qfq"
        )
        reports_saved: Optional[int] = None
        if reports:
            try:
                reports_saved = self.db.save_financial_reports(
                    code, reports, source=self.source_name
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("保存 %s 财报失败（K 线不受影响）: %s", code, exc)
        return KlinePersistResult(
            rows_saved=saved, source=self.source_name, rows_fetched=len(df),
            reports_saved=reports_saved,
        )
