# -*- coding: utf-8
"""westock kline 整段 → stock_daily_kline 回填。"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from data_provider.westock_fields import DEFAULT_KLINE_ADJ

from .runner import run_backfill_job
from .segment_planner import is_no_data_error, plan_segments
from .segment_planner import iso as _iso

logger = logging.getLogger(__name__)

DEFAULT_START_DATE = "2010-01-01"
DEFAULT_PROGRESS_PATH = os.path.join("data", "kline_backfill_progress.json")


class KlineBackfillService:
    """kline 整段回填（TencentFetcher，HTTP 直连 fqkline API）。"""

    dataset = "kline"

    def __init__(self, db_manager=None):
        from src.repositories.stock_repo import StockRepository

        self.repo = StockRepository(db_manager)
        self._ingest = None

    @property
    def ingest(self):
        if self._ingest is None:
            from src.ingest.tencent_kline import TencentKlineIngestor
            self._ingest = TencentKlineIngestor(db_manager=self.repo.db)
        return self._ingest

    def run(
        self,
        codes: List[str],
        *,
        start_date: str = DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        mode: str = "full",
        sleep: float = 0.0,
        retry: int = 1,
        fresh_days: int = 4,
        force: bool = False,
        retry_failed: bool = False,
        limit: Optional[int] = None,
        progress_path: str = DEFAULT_PROGRESS_PATH,
        log_every: int = 1,
        stop_check: Optional[Callable[[], bool]] = None,
        adj: str = DEFAULT_KLINE_ADJ,
    ) -> Dict[str, Any]:
        return run_backfill_job(
            dataset=self.dataset,
            rows_key="kline_rows",
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
            meta_extra={"adj_type": adj},
            start_log=(
                "开始 kline 回填：%d 只，区间 %s ~ %s，分段=%s，限流=%.2fs，force=%s"
            ),
            finish_log=(
                "kline 回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，"
                "新增 kline 行 %d，台账：%s"
            ),
            process_kwargs={"adj": adj},
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
        adj: str = DEFAULT_KLINE_ADJ,
        **_: Any,
    ) -> Dict[str, Any]:
        from src.services.quote_backfill_planner import resolve_effective_start

        coverage = self.repo.get_kline_coverage(code, adj_type=adj)
        effective_start, start_reason = resolve_effective_start(
            code, start_d, end_d, list_date=list_date, force=force,
        )
        if effective_start is None:
            return {
                "action": "empty",
                "error": "区间内无 kline 数据",
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

        total_kline = 0
        got_any = False
        for seg_start, seg_end in segments:
            result, err = self._ingest_with_retry(code, seg_start, seg_end, retry=retry)
            if sleep > 0:
                time.sleep(sleep)
            if err is not None:
                if got_any:
                    logger.warning(
                        "%s kline 分段 %s~%s 失败：%s（已保留其余段）",
                        code, seg_start, seg_end, err,
                    )
                    continue
                if is_no_data_error(err):
                    return {"action": "empty", "error": err}
                return {"action": "failed", "error": err}
            if result is not None:
                total_kline += int(result.rows_saved)
                got_any = True

        if not got_any:
            return {"action": "empty"}

        cov2 = self.repo.get_kline_coverage(code, adj_type=adj)
        return {
            "action": "fetched",
            "added": total_kline,
            "kline_rows": total_kline,
            "first": cov2.get("first"),
            "last": cov2.get("last"),
            "rows": cov2.get("rows"),
            "source": "TencentFetcher",
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
        """返回 (result, err)。err 为 None 表示成功；err 命中 no_data 才判 empty。

        关键：**只有** ``rows_fetched == 0`` 才代表 westock 接口确实返回空
        （该票该区间确无数据）；``rows_fetched > 0 but rows_saved == 0`` 说明
        拉到了但落库 0 行（如瞬时限流后重试、全部 upsert 冲突），此时不可判 empty，
        必须回落成可重试错误——历史上把这两种情况混起来标终态导致大量票误判。
        """
        last_err = None
        for attempt in range(retry + 1):
            try:
                result = self.ingest.backfill(code, start=seg_start, end=seg_end)
                # 接口真的返回空 → 上层通过 no_data_error 判 empty 终态
                if result.rows_fetched == 0:
                    last_err = "kline 返回空"
                # 拉到但没保存：可能被限流/去重/事务冲突，判为可重试瞬时错误
                elif result.rows_saved == 0:
                    last_err = (
                        f"kline 拉到 {result.rows_fetched} 条但落库 0 行"
                        f"（疑似瞬时问题）"
                    )
                else:
                    return result, None
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
        return None, last_err
