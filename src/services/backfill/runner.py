# -*- coding: utf-8
"""回填主循环（quote / kline 共用 orchestration）。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from data_provider.base import normalize_stock_code

from .code_list import BackfillError
from .ledger import ProgressLedger
from .segment_planner import iso as _iso
from .segment_planner import parse_date

logger = logging.getLogger(__name__)

ProcessCodeFn = Callable[..., Dict[str, Any]]


def run_backfill_job(
    *,
    dataset: str,
    rows_key: str,
    codes: List[str],
    process_code: ProcessCodeFn,
    progress_path: str,
    start_date: str,
    end_date: Optional[str],
    mode: str,
    sleep: float,
    retry: int,
    fresh_days: int,
    force: bool,
    retry_failed: bool,
    limit: Optional[int],
    log_every: int,
    stop_check: Optional[Callable[[], bool]],
    meta_extra: Optional[Dict[str, Any]] = None,
    start_log: Optional[str] = None,
    finish_log: Optional[str] = None,
    process_kwargs: Optional[Dict[str, Any]] = None,
    fail_fast_on_error_substr: Optional[str] = None,
    fail_fast_consecutive: int = 3,
) -> Dict[str, Any]:
    """执行全市场/批量回填主循环。"""
    if mode not in ("full", "incremental", "smart", "range"):
        raise BackfillError(f"未知模式：{mode}")

    end_date = end_date or date.today().isoformat()
    end_d = parse_date(end_date)
    start_d = parse_date(start_date)
    if start_d is None or end_d is None:
        raise BackfillError("start_date/end_date 格式应为 YYYY-MM-DD")

    ledger = ProgressLedger(progress_path)
    meta = {
        "start_date": start_date,
        "end_date": end_date,
        "mode": mode,
        "dataset": dataset,
    }
    if meta_extra:
        meta.update(meta_extra)
    ledger.set_meta(**meta)

    from src.services.cn_list_date_store import load_list_date_map

    list_dates = load_list_date_map()
    if list_dates:
        logger.info("已加载上市日 metadata：%d 只", len(list_dates))
    elif dataset == "quote":
        logger.warning(
            "未找到 cn_list_dates.json，将依赖探测兜底；"
            "建议先运行: python scripts/fetch_cn_list_dates.py"
        )

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
    if start_log:
        logger.info(start_log, total, start_date, end_date, mode, sleep, force)
    else:
        logger.info(
            "开始 %s 回填：%d 只，区间 %s ~ %s，分段=%s，限流=%.2fs，force=%s",
            dataset, total, start_date, end_date, mode, sleep, force,
        )

    stats: Dict[str, Any] = {
        "total": total,
        "fetched": 0,
        "skipped": 0,
        "failed": 0,
        "empty": 0,
        "rows_added": 0,
        rows_key: 0,
    }
    extra = process_kwargs or {}

    consecutive_fail = 0
    for i, raw in enumerate(codes, 1):
        if stop_check and stop_check():
            logger.warning("收到停止信号，已处理 %d/%d，安全退出。", i - 1, total)
            break

        code = (raw or "").strip().upper()
        if not code:
            continue

        prev_min = parse_date(ledger.get(code).get("min_start") or "")
        new_min = start_d if prev_min is None else min(prev_min, start_d)

        is_failure = False
        fail_text = ""
        action = "failed"
        try:
            plain = normalize_stock_code(code)
            result = process_code(
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
                **extra,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%d/%d] %s %s 回填异常：%s", i, total, code, dataset, exc)
            ledger.update(code, status="failed", error=str(exc))
            stats["failed"] += 1
            is_failure = True
            fail_text = str(exc)
            action = "failed"
            ledger.save()
        else:
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
                is_failure = True
                fail_text = result.get("error") or ""
            else:
                stats["fetched"] += 1
                stats["rows_added"] += result.get("added", 0)
                stats[rows_key] += result.get(rows_key, result.get("added", 0))
                ledger.update(
                    code, status="done", error=None, min_start=_iso(new_min),
                    first=_iso(result.get("first")), last=_iso(result.get("last")),
                    rows=result.get("rows"), source=result.get("source"),
                )

            ledger.save()

        # ── 熔断：连续命中同一类错误（如百度 403 / IP 限流）即中止，避免继续轰炸 ──
        if is_failure:
            consecutive_fail += 1
            if (
                fail_fast_on_error_substr
                and fail_fast_on_error_substr in fail_text
                and consecutive_fail >= fail_fast_consecutive
            ):
                logger.error(
                    "连续 %d 只失败且命中 '%s'（疑似数据源限流/IP 封锁），"
                    "触发熔断提前退出，避免继续轰炸被风控。请排查网络/账号风控后重试。",
                    consecutive_fail, fail_fast_on_error_substr,
                )
                break
        else:
            consecutive_fail = 0


        if i % max(log_every, 1) == 0 or i == total:
            logger.info(
                "[%d/%d] %s → %s | 已拉 %d 跳过 %d 失败 %d 空 %d 新增行 %d",
                i, total, code, action,
                stats["fetched"], stats["skipped"], stats["failed"],
                stats["empty"], stats["rows_added"],
            )

    ledger.set_meta(finished_at=datetime.now().isoformat(timespec="seconds"))
    ledger.save()
    if finish_log:
        logger.info(
            finish_log,
            stats["fetched"], stats["skipped"], stats["failed"],
            stats["empty"], stats[rows_key], progress_path,
        )
    else:
        logger.info(
            "%s 回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，"
            "新增行 %d，台账：%s",
            dataset,
            stats["fetched"], stats["skipped"], stats["failed"],
            stats["empty"], stats[rows_key], progress_path,
        )
    return stats
