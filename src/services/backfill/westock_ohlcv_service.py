# -*- coding: utf-8 -*-
"""westock kline(qfq) → 通用表 stock_daily_ohlcv 每日增量回填。

与百度历史段写入同一张 stock_daily_ohlcv（adj_type='qfq'），按 data_source='Westock'
独立记录覆盖度，与百度段互不干扰；两源 qfq 价在 (code, date) 上无缝拼接。

注意：westock quote --date（不复权截面）不写本时间序列表——其价格是不复权口径，
若直接写入会与百度 qfq 出现 7~8% 断崖。本服务只走 westock kline(qfq)。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from .runner import run_backfill_job
from .segment_planner import is_no_data_error, plan_segments
from .segment_planner import iso as _iso
from .throttle import jittered_sleep

logger = logging.getLogger(__name__)

DEFAULT_START_DATE = "2010-01-01"
DEFAULT_PROGRESS_PATH = os.path.join("data", "westock_ohlcv_backfill_progress.json")


class WestockOhlcvBackfillService:
    """westock kline(qfq) 每日增量回填（WestockOhlcvIngestor → stock_daily_ohlcv）。"""

    dataset = "westock_ohlcv"

    def __init__(self, db_manager=None):
        from src.repositories.stock_repo import StockRepository

        self.repo = StockRepository(db_manager)
        self._ingest = None

    @property
    def ingest(self):
        if self._ingest is None:
            from src.ingest.westock_ohlcv import WestockOhlcvIngestor

            self._ingest = WestockOhlcvIngestor(
                db_manager=self.repo.db, adj="qfq"
            )
        return self._ingest

    def run(
        self,
        codes: List[str],
        *,
        start_date: str = DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        mode: str = "incremental",
        sleep: float = 0.0,
        retry: int = 1,
        fresh_days: int = 4,
        force: bool = False,
        retry_failed: bool = False,
        limit: Optional[int] = None,
        progress_path: str = DEFAULT_PROGRESS_PATH,
        log_every: int = 1,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        try:
            return run_backfill_job(
                dataset=self.dataset,
                rows_key="ohlcv_rows",
                codes=codes,
                process_code=self._process_code,
                progress_path=progress_path,
                start_date=start_date,
                end_date=end_date,
                mode=mode,
                sleep=sleep,
                retry=retry,
                fresh_days=fresh_days,
                force=force,
                retry_failed=retry_failed,
                limit=limit,
                log_every=log_every,
                stop_check=stop_check,
                meta_extra={"adj_type": "qfq", "data_source": "Westock"},
                start_log=(
                    "开始 westock ohlcv 回填：%d 只，区间 %s ~ %s，分段=%s，"
                    "限流=%.2fs，force=%s"
                ),
                finish_log=(
                    "westock ohlcv 回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，"
                    "新增 ohlcv 行 %d，台账：%s"
                ),
                process_kwargs={"data_source": "Westock"},
            )
        finally:
            pass

    def _process_code(
        self,
        code: str,
        *,
        start_d: date,
        end_d: date,
        mode: str,
        retry: int,
        fresh_days: int,
        force: bool,
        sleep: float,
        min_attempted: Optional[date] = None,
        list_date: Optional[date] = None,
        data_source: str = "Westock",
        **_: Any,
    ) -> Dict[str, Any]:
        from src.services.quote_backfill_planner import resolve_effective_start

        coverage = self.repo.get_ohlcv_coverage(
            code, adj_type="qfq", data_source=data_source
        )
        effective_start, start_reason = resolve_effective_start(
            code, start_d, end_d, list_date=list_date, force=force,
        )
        if effective_start is None:
            return {
                "action": "empty",
                "error": "区间内无 westock kline 数据",
                "start_reason": start_reason,
            }

        segments = plan_segments(
            start_d=effective_start,
            end_d=end_d,
            first=coverage.get("first"),
            last=coverage.get("last"),
            mode=mode,
            fresh_days=fresh_days,
            force=force,
            min_attempted=None if force else min_attempted,
        )
        if not segments:
            return {
                "action": "skipped",
                "last": coverage.get("last"),
                "rows": coverage.get("rows", 0),
            }

        # westock kline 单次即可拉整段（与 baidu 不同，无需 all=1 参数），
        # 故 local_first 仅用于决定是否整段还是增量尾窗口（此处统一全量拉取，
        # 由 upsert 覆盖；westock 限额宽松，整段拉取更简单可靠）。
        total_rows = 0
        got_any = False
        for seg_start, seg_end in segments:
            result, err = self._ingest_with_retry(
                code, seg_start, seg_end, retry=retry
            )
            if sleep > 0:
                jittered_sleep(sleep)
            if err is not None:
                if got_any:
                    logger.warning(
                        "%s westock ohlcv 分段 %s~%s 失败：%s（已保留其余段）",
                        code, seg_start, seg_end, err,
                    )
                    continue
                if is_no_data_error(err):
                    return {"action": "empty", "error": err}
                return {"action": "failed", "error": err}
            if result is not None:
                total_rows += int(result.rows_saved)
                got_any = True

        if not got_any:
            return {"action": "empty"}

        cov2 = self.repo.get_ohlcv_coverage(
            code, adj_type="qfq", data_source=data_source
        )
        return {
            "action": "fetched",
            "added": total_rows,
            "ohlcv_rows": total_rows,
            "first": cov2.get("first"),
            "last": cov2.get("last"),
            "rows": cov2.get("rows"),
            "source": "Westock",
            "start_reason": start_reason,
            "effective_start": _iso(effective_start),
        }

    def _ingest_with_retry(
        self,
        code: str,
        seg_start: date,
        seg_end: date,
        *,
        retry: int = 1,
    ):
        """返回 (result, err)。err 为 None 表示成功；err 命中 no_data 才判 empty。"""
        last_err = None
        for attempt in range(retry + 1):
            try:
                result = self.ingest.backfill(code, start=seg_start, end=seg_end)
                if result.rows_fetched == 0:
                    last_err = "westock kline 返回空"
                else:
                    return result, None
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
        return None, last_err
