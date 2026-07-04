# -*- coding: utf-8
"""
全历史 kline 回填服务（westock kline 整段 → stock_daily_kline）。

与 HistoryBackfillService（quote 逐日）并行，共用台账/清单/区间规划组件。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from data_provider.base import normalize_stock_code
from data_provider.westock_fields import DEFAULT_KLINE_ADJ
from src.services.backfill.code_list import BackfillError, CodeListLoader
from src.services.backfill.ledger import ProgressLedger
from src.services.backfill.segment_planner import is_no_data_error, parse_date, plan_segments
from src.services.backfill.segment_planner import iso as _iso

logger = logging.getLogger(__name__)

DEFAULT_START_DATE = "2010-01-01"
DEFAULT_PROGRESS_PATH = os.path.join("data", "kline_backfill_progress.json")


class KlineBackfillService:
    """westock kline 整段回填 orchestrator。"""

    dataset = "kline"

    def __init__(self, db_manager=None):
        from src.repositories.stock_repo import StockRepository

        self.repo = StockRepository(db_manager)
        self._ingest = None

    @property
    def ingest(self):
        if self._ingest is None:
            from src.ingest.westock_kline import WestockKlineIngestor

            self._ingest = WestockKlineIngestor(db_manager=self.repo.db)
        return self._ingest

    def load_all_cn_codes(self, index_path: Optional[str] = None) -> List[str]:
        return CodeListLoader.load_all_cn_codes(index_path)

    def run(
        self,
        codes: List[str],
        *,
        start_date: str = DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        mode: str = "full",
        sleep: float = 0.05,
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
        if mode not in ("full", "incremental", "smart", "range"):
            raise BackfillError(f"未知模式：{mode}")

        end_date = end_date or date.today().isoformat()
        end_d = parse_date(end_date)
        start_d = parse_date(start_date)
        if start_d is None or end_d is None:
            raise BackfillError("start_date/end_date 格式应为 YYYY-MM-DD")

        ledger = ProgressLedger(progress_path)
        ledger.set_meta(
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            dataset=self.dataset,
            adj_type=adj,
        )

        from src.services.cn_list_date_store import load_list_date_map

        list_dates = load_list_date_map()
        if list_dates:
            logger.info("已加载上市日 metadata：%d 只", len(list_dates))

        if not force:
            codes = [c for c in codes if ledger.get(c).get("status") != "empty"]

        if retry_failed:
            codes = [
                c for c in codes
                if ledger.get(c).get("status") not in ("done", "skipped", "empty")
            ]
        if limit is not None:
            codes = codes[:limit]

        total = len(codes)
        ledger.set_meta(total=total)
        logger.info(
            "开始 kline 回填：%d 只，区间 %s ~ %s，模式=%s，复权=%s，限流=%.2fs",
            total, start_date, end_date, mode, adj, sleep,
        )

        stats = {
            "total": total,
            "fetched": 0,
            "skipped": 0,
            "failed": 0,
            "empty": 0,
            "rows_added": 0,
            "kline_rows": 0,
        }

        for i, raw in enumerate(codes, 1):
            if stop_check and stop_check():
                logger.warning("收到停止信号，已处理 %d/%d，安全退出。", i - 1, total)
                break

            code = (raw or "").strip().upper()
            if not code:
                continue

            prev_min = parse_date(ledger.get(code).get("min_start") or "")
            new_min = start_d if prev_min is None else min(prev_min, start_d)

            try:
                plain = normalize_stock_code(code)
                result = self._backfill_one(
                    code,
                    start_d=start_d,
                    end_d=end_d,
                    mode=mode,
                    retry=retry,
                    fresh_days=fresh_days,
                    force=force,
                    sleep=sleep,
                    min_attempted=prev_min,
                    list_date=list_dates.get(plain),
                    adj=adj,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%d/%d] %s kline 回填异常：%s", i, total, code, exc)
                ledger.update(code, status="failed", error=str(exc))
                stats["failed"] += 1
                ledger.save()
                continue

            action = result["action"]
            if action == "skipped":
                stats["skipped"] += 1
                ledger.update(
                    code, status="done", note="fresh", min_start=_iso(new_min),
                    last=_iso(result.get("last")), rows=result.get("rows"),
                )
            elif action == "empty":
                stats["empty"] += 1
                ledger.update(
                    code, status="empty", min_start=_iso(new_min),
                    error=result.get("error") or "数据源返回空",
                )
            elif action == "failed":
                stats["failed"] += 1
                ledger.update(code, status="failed", error=result.get("error"))
            else:
                stats["fetched"] += 1
                stats["rows_added"] += result.get("added", 0)
                stats["kline_rows"] += result.get("kline_rows", 0)
                ledger.update(
                    code, status="done", error=None, min_start=_iso(new_min),
                    first=_iso(result.get("first")), last=_iso(result.get("last")),
                    rows=result.get("rows"), source=result.get("source"),
                )

            ledger.save()

            if i % max(log_every, 1) == 0 or i == total:
                logger.info(
                    "[%d/%d] %s → %s | 已拉 %d 跳过 %d 失败 %d 空 %d 新增行 %d",
                    i, total, code, action,
                    stats["fetched"], stats["skipped"], stats["failed"],
                    stats["empty"], stats["rows_added"],
                )

        ledger.set_meta(finished_at=datetime.now().isoformat(timespec="seconds"))
        ledger.save()
        logger.info(
            "kline 回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，"
            "新增 kline 行 %d，台账：%s",
            stats["fetched"], stats["skipped"], stats["failed"],
            stats["empty"], stats["kline_rows"], progress_path,
        )
        return stats

    def _backfill_one(
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
            result, err = self._ingest_segment_with_retry(
                code, seg_start, seg_end, retry=retry,
            )
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
            "first": cov2.get("first"),
            "last": cov2.get("last"),
            "rows": cov2.get("rows"),
            "source": "WestockKline",
            "kline_rows": total_kline,
            "start_reason": start_reason,
            "effective_start": _iso(effective_start),
        }

    def _ingest_segment_with_retry(
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
                result = self.ingest.backfill(code, start=seg_start, end=seg_end)
                if result.rows_saved == 0:
                    last_err = "kline 返回空"
                else:
                    return result, None
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
        return None, last_err
