# -*- coding: utf-8 -*-
"""
============================================================
行情截面回填服务（TurnoverBackfillService / stock_daily_quote）
============================================================

背景：
    stock_daily 存 kline 时间序列（OHLCV）；换手率等截面字段在
    stock_daily_quote，由 westock ``quote --date`` 逐日拉取。

本服务补漏：找出 stock_daily 已有、但 stock_daily_quote 缺失或
turnover_rate 为空的 (code, date)，调用 DailyQuoteService 回填。

特点：
    - 只补空/缺失：天然幂等、可反复运行。
    - 断点续传：可选 --progress JSON 记录已完成 code。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select

from src.storage import DatabaseManager, StockDaily, StockDailyQuote

logger = logging.getLogger(__name__)

_Target = Tuple[date, date, int]


class TurnoverBackfillService:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def find_targets(
        self,
        *,
        codes: Optional[List[str]] = None,
        recompute_approx: bool = False,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> Dict[str, _Target]:
        """返回 {code: (min_date, max_date, rows)} 待补 quote 截面的票。

        recompute_approx 保留 CLI 兼容：True 时纳入已有 quote 但可能需重拉的票。
        """
        del recompute_approx  # 新架构下 quote 表即权威源，重拉由 overwrite 控制
        sd = StockDaily
        sq = StockDailyQuote
        missing = or_(sq.id.is_(None), sq.turnover_rate.is_(None))
        conds = [missing]
        if codes:
            conds.append(sd.code.in_(codes))
        if start is not None:
            conds.append(sd.date >= start)
        if end is not None:
            conds.append(sd.date <= end)

        stmt = (
            select(
                sd.code,
                func.min(sd.date),
                func.max(sd.date),
                func.count(),
            )
            .select_from(sd)
            .outerjoin(sq, and_(sd.code == sq.code, sd.date == sq.date))
            .where(and_(*conds))
            .group_by(sd.code)
            .order_by(sd.code)
        )
        with self.db.get_session() as session:
            rows = session.execute(stmt).all()
        return {r[0]: (r[1], r[2], int(r[3])) for r in rows}

    def backfill(
        self,
        *,
        codes: Optional[List[str]] = None,
        sleep: float = 0.5,
        retry: int = 2,
        limit: Optional[int] = None,
        progress_path: Optional[str] = None,
        recompute_approx: bool = False,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> Dict[str, Any]:
        from src.services.daily_quote_service import DailyQuoteService

        targets = self.find_targets(
            codes=codes, recompute_approx=recompute_approx, start=start, end=end
        )
        all_codes = list(targets.keys())
        if limit is not None:
            all_codes = all_codes[: max(0, int(limit))]

        ledger = _Ledger(progress_path) if progress_path else None
        done_set = ledger.done_codes() if ledger else set()
        quote_svc = DailyQuoteService(db_manager=self.db)

        stats = {
            "total": len(all_codes),
            "updated_codes": 0,
            "updated_rows": 0,
            "skipped_done": 0,
            "empty": 0,
            "failed": 0,
        }

        for idx, code in enumerate(all_codes, start=1):
            if code in done_set:
                stats["skipped_done"] += 1
                continue

            min_d, max_d, null_rows = targets[code]
            try:
                updated = quote_svc.backfill_and_save(
                    code,
                    start=min_d,
                    end=max_d,
                    overwrite=bool(recompute_approx),
                )
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                logger.warning("[%d/%d] %s quote 截面拉取失败：%s",
                               idx, len(all_codes), code, exc)
                if ledger:
                    ledger.mark(code, status="failed", error=str(exc))
                if sleep > 0:
                    time.sleep(sleep)
                continue

            if updated <= 0:
                stats["empty"] += 1
                logger.info("[%d/%d] %s 无 quote 数据，跳过", idx, len(all_codes), code)
                if ledger:
                    ledger.mark(code, status="empty")
            else:
                stats["updated_rows"] += updated
                stats["updated_codes"] += 1
                logger.info("[%d/%d] %s 补 quote 截面 %d/%d 行",
                            idx, len(all_codes), code, updated, null_rows)
                if ledger:
                    ledger.mark(code, status="done", updated=updated)

            if sleep > 0:
                time.sleep(sleep)

        logger.info(
            "quote 截面回填完成：目标 %d 票，成功 %d 票 / %d 行，空 %d，失败 %d，跳过 %d",
            stats["total"], stats["updated_codes"], stats["updated_rows"],
            stats["empty"], stats["failed"], stats["skipped_done"],
        )
        return stats


class _Ledger:
    """轻量断点续传：JSON 记录每个 code 的状态。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.data: Dict[str, Any] = {"meta": {}, "codes": {}}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.setdefault("codes", {})
            except Exception:  # noqa: BLE001
                self.data = {"meta": {}, "codes": {}}

    def done_codes(self) -> set:
        return {
            c for c, v in self.data.get("codes", {}).items()
            if isinstance(v, dict) and v.get("status") == "done"
        }

    def mark(self, code: str, *, status: str, updated: int = 0, error: Optional[str] = None) -> None:
        self.data.setdefault("codes", {})[code] = {
            "status": status,
            "updated": updated,
            "error": error,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("进度写入失败：%s", exc)
