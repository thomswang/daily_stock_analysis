# -*- coding: utf-8
"""westock quote --date → stock_daily_quote 回填。"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from .runner import run_backfill_job
from .segment_planner import is_no_data_error, plan_segments
from .segment_planner import iso as _iso

logger = logging.getLogger(__name__)

DEFAULT_START_DATE = "2010-01-01"
DEFAULT_PROGRESS_PATH = os.path.join("data", "backfill_progress.json")


class QuoteBackfillService:
    """quote 逐日截面回填。"""

    dataset = "quote"

    def __init__(self, db_manager=None):
        from src.repositories.stock_repo import StockRepository

        self.repo = StockRepository(db_manager)
        self._ingest = None

    @property
    def ingest(self):
        if self._ingest is None:
            from src.ingest import DailyIngestService

            self._ingest = DailyIngestService(self.repo)
        return self._ingest

    def run(
        self,
        codes: List[str],
        *,
        start_date: str = DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        mode: str = "full",
        sleep: float = 0.1,
        retry: int = 1,
        fresh_days: int = 4,
        force: bool = False,
        retry_failed: bool = False,
        limit: Optional[int] = None,
        progress_path: str = DEFAULT_PROGRESS_PATH,
        log_every: int = 1,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        return run_backfill_job(
            dataset=self.dataset,
            rows_key="quote_rows",
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
        )

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
        **_: Any,
    ) -> Dict[str, Any]:
        from src.services.quote_backfill_planner import resolve_effective_start

        coverage = self.repo.get_quote_coverage(code)
        effective_start, start_reason = resolve_effective_start(
            code, start_d, end_d, list_date=list_date, force=force,
        )
        if effective_start is None:
            return {
                "action": "empty",
                "error": "区间内无 quote 数据",
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

        total_quote = 0
        got_any = False
        for seg_start, seg_end in segments:
            result, err = self._ingest_with_retry(code, seg_start, seg_end, retry=retry)
            if sleep > 0:
                time.sleep(sleep)
            if err is not None:
                if got_any:
                    logger.warning(
                        "%s quote 分段 %s~%s 失败：%s（已保留其余段）",
                        code, seg_start, seg_end, err,
                    )
                    continue
                if is_no_data_error(err):
                    return {"action": "empty", "error": err}
                return {"action": "failed", "error": err}
            if result is not None:
                total_quote += int(result.quote_added)
                got_any = True

        if not got_any:
            return {"action": "empty"}

        cov2 = self.repo.get_quote_coverage(code)
        return {
            "action": "fetched",
            "added": total_quote,
            "quote_rows": total_quote,
            "first": cov2.get("first"),
            "last": cov2.get("last"),
            "rows": cov2.get("rows"),
            "source": "TencentQuote",
            "start_reason": start_reason,
            "effective_start": _iso(effective_start),
        }

    def _ingest_with_retry(
        self,
        code: str,
        seg_start: date,
        seg_end: date,
        *,
        retry: int,
    ):
        last_err = None
        for attempt in range(retry + 1):
            try:
                result = self.ingest.ingest_quote(code, start=seg_start, end=seg_end)
                return result, None
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
        return None, last_err
