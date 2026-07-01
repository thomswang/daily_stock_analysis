# -*- coding: utf-8 -*-
"""
=========================================
全历史日线数据回填服务（History Backfill）
=========================================

目标：把全市场（或指定股票）从很早的年份（默认 2010-01-01）到今天的日线数据
一次性灌入本地 stock_daily 缓存，支持**中断续传**——已经拉过的股票不再重复请求。

设计要点（与主分析/预测/训练解耦，复用同一套缓存）：
1. **代码清单**：默认读 stocks.index.json，筛选 A 股（country=CN & type=stock）。
2. **拉取**：DataFetcherManager.get_daily_data(code, start_date, end_date)（支持区间）。
3. **落库**：StockRepository.save_dataframe()（按 code+date 幂等 upsert）。
4. **断点续传**：DB 为真相源（get_coverage 查已存最早/最晚日期）+ JSON 进度台账
   记录每只票的状态/行数/错误，随时中断重跑自动跳过已完成的。
5. **两种模式**：
   - full：对每只票按 [start, today] 整段拉取（首次建库 / 修复历史缺口）。
   - incremental：只补每只票「已存最新日期之后」的缺口（日常维护，请求少）。
6. **容错/限流**：单只失败不影响整体（记录 error）；每次请求间 sleep 防封禁。

⚠️ 数据仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_START_DATE = "2010-01-01"
DEFAULT_PROGRESS_PATH = os.path.join("data", "backfill_progress.json")

# stocks.index.json 的候选位置（按优先级）
_INDEX_CANDIDATES = [
    os.path.join("data", "cache", "stocks.index.json"),
    os.path.join("static", "stocks.index.json"),
    os.path.join("apps", "dsa-web", "public", "stocks.index.json"),
]


class BackfillError(Exception):
    """回填流程可预期的业务错误（清单缺失等）。"""


# ─────────────────────────────────────────────
# 进度台账（断点续传的持久化）
# ─────────────────────────────────────────────
class ProgressLedger:
    """记录每只股票的回填状态，原子落盘，支持随时中断重跑。

    结构：
        {
          "meta": {"start_date": "...", "updated_at": "...", "total": N},
          "codes": { code: {status, first, last, rows, source, updated_at, error} }
        }
    """

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {"meta": {}, "codes": {}}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                self.data.setdefault("meta", {})
                self.data.setdefault("codes", {})
            except Exception as exc:  # noqa: BLE001 - 台账损坏不应中断，重建
                logger.warning("进度台账读取失败，将重建：%s", exc)
                self.data = {"meta": {}, "codes": {}}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # 原子替换，避免写一半损坏

    def get(self, code: str) -> Dict[str, Any]:
        return self.data["codes"].get(code, {})

    def update(self, code: str, **fields: Any) -> None:
        rec = self.data["codes"].get(code, {})
        rec.update(fields)
        rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.data["codes"][code] = rec

    def set_meta(self, **fields: Any) -> None:
        self.data["meta"].update(fields)
        self.data["meta"]["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for rec in self.data["codes"].values():
            st = rec.get("status", "unknown")
            counts[st] = counts.get(st, 0) + 1
        return counts


# ─────────────────────────────────────────────
# 回填服务
# ─────────────────────────────────────────────
class HistoryBackfillService:
    def __init__(self, db_manager=None):
        from src.repositories.stock_repo import StockRepository

        self.repo = StockRepository(db_manager)
        self._manager = None  # 延迟初始化 DataFetcherManager

    @property
    def manager(self):
        if self._manager is None:
            from data_provider.base import DataFetcherManager

            self._manager = DataFetcherManager()
        return self._manager

    # ---- 代码清单 ----
    def load_all_cn_codes(self, index_path: Optional[str] = None) -> List[str]:
        """从 stocks.index.json 读取全部 A 股代码（country=CN & type=stock & 已上市）。"""
        path = index_path or self._resolve_index_path()
        if not path:
            raise BackfillError(
                "未找到 stocks.index.json（尝试位置："
                + " / ".join(_INDEX_CANDIDATES)
                + "）；请用 --codes-file 指定，或先生成索引。"
            )
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)

        codes: List[str] = []
        for row in rows:
            # 结构：[ts_code, plain, name, pinyin, abbr, [aliases], country, type, listed, weight]
            if not isinstance(row, list) or len(row) < 8:
                continue
            ts_code = row[0]
            country = row[6] if len(row) > 6 else None
            sec_type = row[7] if len(row) > 7 else None
            listed = row[8] if len(row) > 8 else True
            if country == "CN" and sec_type == "stock" and listed and ts_code:
                codes.append(str(ts_code).strip().upper())
        # 去重保序
        seen = set()
        uniq = [c for c in codes if not (c in seen or seen.add(c))]
        logger.info("从 %s 载入 A 股代码 %d 只", path, len(uniq))
        return uniq

    def _resolve_index_path(self) -> Optional[str]:
        for cand in _INDEX_CANDIDATES:
            if os.path.exists(cand):
                return cand
        return None

    # ---- 主流程 ----
    def run(
        self,
        codes: List[str],
        *,
        start_date: str = DEFAULT_START_DATE,
        end_date: Optional[str] = None,
        mode: str = "full",
        sleep: float = 0.5,
        retry: int = 1,
        fresh_days: int = 4,
        force: bool = False,
        retry_failed: bool = False,
        limit: Optional[int] = None,
        progress_path: str = DEFAULT_PROGRESS_PATH,
        log_every: int = 1,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """执行回填。

        Args:
            codes: 待回填代码列表
            start_date/end_date: 回填区间（end 默认今天）
            mode: full（整段拉）| incremental（只补最新缺口）
            sleep: 每次实际请求后的限流秒数
            retry: 单只失败重试次数
            fresh_days: DB 最新日期距今 <= 该自然日数则视为“已最新”，跳过
            force: 忽略“已最新”判断，强制按 start 重新拉
            retry_failed: 仅处理台账中 status=failed/未完成 的代码
            limit: 仅处理前 N 只（试跑）
            progress_path: 进度台账路径
        """
        if mode not in ("full", "incremental"):
            raise BackfillError(f"未知模式：{mode}（应为 full 或 incremental）")

        end_date = end_date or date.today().isoformat()
        end_d = _parse_date(end_date)
        start_d = _parse_date(start_date)
        if start_d is None or end_d is None:
            raise BackfillError("start_date/end_date 格式应为 YYYY-MM-DD")

        ledger = ProgressLedger(progress_path)
        ledger.set_meta(start_date=start_date, end_date=end_date, mode=mode)

        # 仅重试失败的
        if retry_failed:
            codes = [
                c for c in codes
                if ledger.get(c).get("status") not in ("done", "skipped")
            ]
        if limit is not None:
            codes = codes[:limit]

        total = len(codes)
        ledger.set_meta(total=total)
        logger.info(
            "开始回填：%d 只，区间 %s ~ %s，模式=%s，限流=%.2fs，force=%s",
            total, start_date, end_date, mode, sleep, force,
        )

        stats = {
            "total": total, "fetched": 0, "skipped": 0,
            "failed": 0, "empty": 0, "rows_added": 0,
        }

        for i, raw in enumerate(codes, 1):
            if stop_check and stop_check():
                logger.warning("收到停止信号，已处理 %d/%d，安全退出。", i - 1, total)
                break

            code = (raw or "").strip().upper()
            if not code:
                continue

            try:
                result = self._backfill_one(
                    code, start_d=start_d, end_d=end_d, mode=mode,
                    retry=retry, fresh_days=fresh_days, force=force, sleep=sleep,
                )
            except Exception as exc:  # noqa: BLE001 - 单只异常不应中断整体
                logger.warning("[%d/%d] %s 回填异常：%s", i, total, code, exc)
                ledger.update(code, status="failed", error=str(exc))
                stats["failed"] += 1
                ledger.save()
                continue

            action = result["action"]
            if action == "skipped":
                stats["skipped"] += 1
                ledger.update(code, status="done", note="fresh",
                              last=_iso(result.get("last")), rows=result.get("rows"))
            elif action == "empty":
                stats["empty"] += 1
                ledger.update(code, status="empty", error="数据源返回空")
            elif action == "failed":
                stats["failed"] += 1
                ledger.update(code, status="failed", error=result.get("error"))
            else:  # fetched
                stats["fetched"] += 1
                stats["rows_added"] += result.get("added", 0)
                ledger.update(
                    code, status="done", error=None,
                    first=_iso(result.get("first")), last=_iso(result.get("last")),
                    rows=result.get("rows"), source=result.get("source"),
                )

            # 每只都落盘 → 随时中断可续
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
            "回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，新增行 %d，台账：%s",
            stats["fetched"], stats["skipped"], stats["failed"],
            stats["empty"], stats["rows_added"], progress_path,
        )
        return stats

    def _backfill_one(
        self, code: str, *, start_d: date, end_d: date, mode: str,
        retry: int, fresh_days: int, force: bool, sleep: float,
    ) -> Dict[str, Any]:
        """处理单只股票，返回 {action, ...}。action ∈ skipped/fetched/empty/failed。"""
        coverage = self.repo.get_coverage(code)
        last = coverage.get("last")

        # 判断是否已最新（DB 为准）
        if not force and last is not None:
            if (end_d - last).days <= fresh_days:
                return {"action": "skipped", "last": last, "rows": coverage.get("rows", 0)}

        # 决定拉取起始日
        if force or last is None or mode == "full":
            fetch_start = start_d
        else:  # incremental 且已有数据 → 从最新日期次日开始
            fetch_start = last + timedelta(days=1)

        if fetch_start > end_d:
            return {"action": "skipped", "last": last, "rows": coverage.get("rows", 0)}

        # 拉取（带重试）
        df, source, err = self._fetch_with_retry(
            code, fetch_start.isoformat(), end_d.isoformat(), retry=retry
        )
        # 只要实际发起了网络请求就限流
        if sleep > 0:
            time.sleep(sleep)

        if err is not None:
            return {"action": "failed", "error": err}
        if df is None or df.empty:
            return {"action": "empty"}

        added = self.repo.save_dataframe(df, code, data_source=source or "backfill")
        cov2 = self.repo.get_coverage(code)
        return {
            "action": "fetched", "added": int(added),
            "first": cov2.get("first"), "last": cov2.get("last"),
            "rows": cov2.get("rows"), "source": source,
        }

    def _fetch_with_retry(self, code: str, start: str, end: str, *, retry: int):
        """返回 (df, source, error_str)。error_str 为 None 表示成功。"""
        from data_provider.base import DataFetchError

        last_err = None
        for attempt in range(retry + 1):
            try:
                df, source = self.manager.get_daily_data(
                    code, start_date=start, end_date=end
                )
                return df, source, None
            except DataFetchError as exc:
                last_err = str(exc)
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))  # 退避
        return None, None, last_err


def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, str):
        return d[:10]
    try:
        return d.isoformat()
    except Exception:  # noqa: BLE001
        return None
