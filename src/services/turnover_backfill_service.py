# -*- coding: utf-8 -*-
"""
============================================================
换手率回填服务（TurnoverBackfillService）
============================================================

背景：
    全市场历史 OHLCV 回填以腾讯（TencentFetcher, P0）为主源，速度快、稳定，
    但腾讯 K 线接口不提供换手率，导致 stock_daily.turnover_rate 大面积为空。

方案：
    腾讯负责快速拉 OHLCV；本服务作为“第 2 层”专门补换手率——用新浪
    (ak.stock_zh_a_daily) 拉逐日 `成交量 / 流通股本`，按
        换手率(%) = 成交量 ÷ 流通股本 × 100
    计算（口径已多源交叉验证），只更新 turnover_rate 为空的行。
    顺带用 OHLC 回看补 change_amount / amplitude（仅当当前为空，coalesce 保护）。

特点：
    - 只补空：where turnover_rate IS NULL，天然幂等、可反复运行。
    - 新浪源：逐日提供 outstanding_share，历史股本变动也能还原。
    - 断点续传：可选 --progress JSON 记录已完成 code。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import and_, func, select, update

from src.storage import DatabaseManager, StockDaily

logger = logging.getLogger(__name__)

# code -> (min_null_date, max_null_date, null_rows)
_Target = Tuple[date, date, int]


def _sina_symbol(stored_code: str) -> Optional[str]:
    """把库内代码（如 600519.SH / 000001.SZ / 830799.BJ）转成新浪符号 sh600519。"""
    text = (stored_code or "").strip().upper()
    if not text:
        return None
    base, _, suffix = text.partition(".")
    if not (base.isdigit() and len(base) == 6):
        return None  # 只处理 A 股 6 位代码，港股/美股跳过
    if suffix == "SH":
        return f"sh{base}"
    if suffix == "SZ":
        return f"sz{base}"
    if suffix == "BJ":
        return f"bj{base}"
    # 无后缀时按代码段推断
    if base.startswith(("6", "9", "5")):
        return f"sh{base}"
    if base.startswith(("4", "8")):
        return f"bj{base}"
    return f"sz{base}"


class TurnoverBackfillService:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ---------------------------------------------------------------- 目标发现
    def find_targets(self, *, codes: Optional[List[str]] = None) -> Dict[str, _Target]:
        """返回 {code: (min_null_date, max_null_date, null_rows)}，只含 turnover_rate 为空的票。"""
        cond = StockDaily.turnover_rate.is_(None)
        if codes:
            cond = and_(cond, StockDaily.code.in_(codes))
        stmt = (
            select(
                StockDaily.code,
                func.min(StockDaily.date),
                func.max(StockDaily.date),
                func.count(),
            )
            .where(cond)
            .group_by(StockDaily.code)
            .order_by(StockDaily.code)
        )
        with self.db.get_session() as session:
            rows = session.execute(stmt).all()
        return {r[0]: (r[1], r[2], int(r[3])) for r in rows}

    # ------------------------------------------------------------------ 主流程
    def backfill(
        self,
        *,
        codes: Optional[List[str]] = None,
        sleep: float = 0.5,
        retry: int = 2,
        limit: Optional[int] = None,
        progress_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        targets = self.find_targets(codes=codes)
        all_codes = list(targets.keys())
        if limit is not None:
            all_codes = all_codes[: max(0, int(limit))]

        ledger = _Ledger(progress_path) if progress_path else None
        done_set = ledger.done_codes() if ledger else set()

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
                per_date = self._fetch_sina_metrics(
                    code, start=min_d, end=max_d, retry=retry
                )
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                logger.warning("[%d/%d] %s 新浪拉取失败：%s",
                               idx, len(all_codes), code, exc)
                if ledger:
                    ledger.mark(code, status="failed", error=str(exc))
                if sleep > 0:
                    time.sleep(sleep)
                continue

            if not per_date:
                stats["empty"] += 1
                logger.info("[%d/%d] %s 新浪无数据，跳过", idx, len(all_codes), code)
                if ledger:
                    ledger.mark(code, status="empty")
                if sleep > 0:
                    time.sleep(sleep)
                continue

            updated = self._apply_updates(code, per_date)
            stats["updated_rows"] += updated
            if updated > 0:
                stats["updated_codes"] += 1
            logger.info("[%d/%d] %s 补换手率 %d/%d 行",
                        idx, len(all_codes), code, updated, null_rows)
            if ledger:
                ledger.mark(code, status="done", updated=updated)

            if sleep > 0:
                time.sleep(sleep)

        logger.info(
            "换手率回填完成：目标 %d 票，成功 %d 票 / %d 行，空 %d，失败 %d，跳过(已完成) %d",
            stats["total"], stats["updated_codes"], stats["updated_rows"],
            stats["empty"], stats["failed"], stats["skipped_done"],
        )
        return stats

    # ------------------------------------------------------------- 新浪取数计算
    def _fetch_sina_metrics(
        self, code: str, *, start: date, end: date, retry: int
    ) -> Dict[date, Dict[str, Optional[float]]]:
        symbol = _sina_symbol(code)
        if not symbol:
            return {}

        import akshare as ak

        last_err: Optional[Exception] = None
        df: Optional[pd.DataFrame] = None
        for attempt in range(retry + 1):
            try:
                df = ak.stock_zh_a_daily(
                    symbol=symbol,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq",
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < retry:
                    time.sleep(0.8 * (attempt + 1))
        if df is None:
            raise last_err or RuntimeError("sina 返回 None")
        if df.empty:
            return {}

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        vol = pd.to_numeric(df.get("volume"), errors="coerce")
        os_share = pd.to_numeric(df.get("outstanding_share"), errors="coerce")
        turnover_col = pd.to_numeric(df.get("turnover"), errors="coerce")

        # 换手率(%)：优先 成交量/流通股本×100；缺流通股本退回 turnover×100
        turn = (vol / os_share * 100).where(os_share > 0)
        turn = turn.fillna(turnover_col * 100)

        close = pd.to_numeric(df.get("close"), errors="coerce")
        high = pd.to_numeric(df.get("high"), errors="coerce")
        low = pd.to_numeric(df.get("low"), errors="coerce")
        prev = close.shift(1)
        change_amount = close - prev
        amplitude = (high - low) / prev * 100

        out: Dict[date, Dict[str, Optional[float]]] = {}
        for i, d in enumerate(df["date"].tolist()):
            out[d] = {
                "turnover_rate": _num(turn.iloc[i]),
                "change_amount": _num(change_amount.iloc[i]),
                "amplitude": _num(amplitude.iloc[i]),
            }
        return out

    # ------------------------------------------------------------------ 写库
    def _apply_updates(
        self, code: str, per_date: Dict[date, Dict[str, Optional[float]]]
    ) -> int:
        params = []
        for d, vals in per_date.items():
            tr = vals.get("turnover_rate")
            if tr is None:
                continue  # 换手率都算不出就没必要更新
            params.append({
                "c": code,
                "d": d,
                "tr": tr,
                "ca": vals.get("change_amount"),
                "amp": vals.get("amplitude"),
            })
        if not params:
            return 0

        def _write(session) -> int:
            total = 0
            # 只补 turnover_rate 为空的行；change_amount/amplitude 用 coalesce 保护已有值
            for p in params:
                res = session.execute(
                    update(StockDaily)
                    .where(
                        and_(
                            StockDaily.code == p["c"],
                            StockDaily.date == p["d"],
                            StockDaily.turnover_rate.is_(None),
                        )
                    )
                    .values(
                        turnover_rate=p["tr"],
                        change_amount=func.coalesce(StockDaily.change_amount, p["ca"]),
                        amplitude=func.coalesce(StockDaily.amplitude, p["amp"]),
                        updated_at=datetime.now(),
                    )
                )
                total += int(res.rowcount or 0)
            return total

        return self.db._run_write_transaction("turnover_backfill", _write)


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
