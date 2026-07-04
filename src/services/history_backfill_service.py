# -*- coding: utf-8 -*-
"""
=========================================
全历史日线 quote 回填服务（History Backfill）
=========================================

目标：把全市场（或指定股票）从指定起始日到今天的 westock quote --date 数据
灌入 stock_daily_quote 单表，支持**中断续传**。

设计要点：
1. **代码清单**：stocks.index.json，A 股（country=CN & type=stock）。
2. **采集**：DailyIngestService → quote --date 逐工作日。
3. **上市前优化**：`cn_list_dates.json` 静态上市日裁剪起点（`max(start, list_date)`）。
4. **断点续传**：DB 覆盖 + JSON 进度台账。
5. **模式**：full / incremental / smart / range（见 _plan_segments）。

⚠️ 数据仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from data_provider.base import normalize_stock_code

logger = logging.getLogger(__name__)

# 单个待拉取区间段：(起始日, 结束日)，闭区间
_Seg = Tuple[date, date]

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


# 瞬时/环境类故障信号：出现任一则判为“可重试失败”，绝不当成“无数据”。
_TRANSIENT_ERROR_MARKERS = (
    "CircuitOpen", "熔断", "Connection", "RemoteDisconnected", "Timeout",
    "timed out", "ProtocolError", "Max retries", "SSL", "ReadTimeout",
    "ConnectionError", "Proxy", "代理", "reset by peer", "aborted",
)
# “确定无数据”信号：数据源明确回“查不到该票数据”（如次新股在请求区间尚未上市）。
_NO_DATA_MARKERS = (
    "未查询到", "未获取到", "无数据", "没有数据", "暂无数据", "查询不到",
    "no data", "not found", "返回空", "空日线",
)


def _is_no_data_error(err: Optional[str]) -> bool:
    """聚合错误是否表示“该票在请求区间确定无数据”（终态，不必重试）。

    判定原则（保守）：仅当错误里出现“无数据”类信号、且**不含任何**“熔断/网络/超时”
    等瞬时故障信号时才判为无数据。若两类信号混杂（例如部分源熔断、部分源查无数据），
    则无法确认有数据的源是否真的没有数据，故保守判为可重试失败，交由下次重试自愈——
    届时若数据源健康仍统一回“查无数据”，才会被正确标记为 empty。
    """
    if not err:
        return False
    if any(m in err for m in _TRANSIENT_ERROR_MARKERS):
        return False
    return any(m in err for m in _NO_DATA_MARKERS)


# ─────────────────────────────────────────────
# 进度台账（断点续传的持久化）
# ─────────────────────────────────────────────
class ProgressLedger:
    """记录每只股票的回填状态，原子落盘，支持随时中断重跑。

    结构：
        {
          "meta": {"start_date": "...", "updated_at": "...", "total": N},
          "codes": {
            code: {
              status, first, last, rows, source, updated_at, error,
              min_start,   # 历史上最早请求过的 start（水位线）
            }
          }
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
        self._ingest = None

    @property
    def ingest(self):
        if self._ingest is None:
            from src.ingest import DailyIngestService

            self._ingest = DailyIngestService(self.repo)
        return self._ingest

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

    def load_cn_name_map(self, index_path: Optional[str] = None) -> Dict[str, str]:
        """从 stocks.index.json 读取「代码 -> 名称」映射（A 股、已上市）。

        供选股推荐等场景廉价补齐股票名（免逐票联网）。索引缺失时返回空 dict。
        """
        path = index_path or self._resolve_index_path()
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception as exc:  # noqa: BLE001 - 名称是锦上添花
            logger.debug("读取 stocks.index.json 名称映射失败：%s", exc)
            return {}
        name_map: Dict[str, str] = {}
        for row in rows:
            # 结构：[ts_code, plain, name, pinyin, abbr, [aliases], country, type, listed, weight]
            if not isinstance(row, list) or len(row) < 8:
                continue
            ts_code, name = row[0], row[2] if len(row) > 2 else None
            country = row[6] if len(row) > 6 else None
            sec_type = row[7] if len(row) > 7 else None
            if country == "CN" and sec_type == "stock" and ts_code and name:
                name_map[str(ts_code).strip().upper()] = str(name).strip()
        return name_map

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
        """执行 quote --date 回填。

        Args:
            codes: 待回填代码列表
            start_date/end_date: 回填区间（end 默认今天）
            mode: full | incremental | smart | range（见 _plan_segments）
            sleep: 每次实际请求后的限流秒数
            retry: 单只失败重试次数
            fresh_days: DB 最新日期距今 <= 该自然日数则视为“已最新”，跳过
            force: 忽略“已最新”判断，强制重拉（并复查已标记 empty 的票）
            retry_failed: 仅处理台账中 status=failed 的代码
            limit: 仅处理前 N 只（试跑）
            progress_path: 进度台账路径
        """
        if mode not in ("full", "incremental", "smart", "range"):
            raise BackfillError(f"未知模式：{mode}（应为 full / incremental / smart / range）")

        end_date = end_date or date.today().isoformat()
        end_d = _parse_date(end_date)
        start_d = _parse_date(start_date)
        if start_d is None or end_d is None:
            raise BackfillError("start_date/end_date 格式应为 YYYY-MM-DD")

        ledger = ProgressLedger(progress_path)
        ledger.set_meta(start_date=start_date, end_date=end_date, mode=mode)

        from src.services.cn_list_date_store import load_list_date_map

        list_dates = load_list_date_map()
        if list_dates:
            logger.info("已加载上市日 metadata：%d 只", len(list_dates))
        else:
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
        logger.info(
            "开始回填：%d 只，区间 %s ~ %s，模式=%s，限流=%.2fs，force=%s",
            total, start_date, end_date, mode, sleep, force,
        )

        stats = {
            "total": total, "fetched": 0, "skipped": 0,
            "failed": 0, "empty": 0, "rows_added": 0,
            "quote_rows": 0,
        }

        for i, raw in enumerate(codes, 1):
            if stop_check and stop_check():
                logger.warning("收到停止信号，已处理 %d/%d，安全退出。", i - 1, total)
                break

            code = (raw or "").strip().upper()
            if not code:
                continue

            # 该票历史上已请求过的最早起始日（水位线）：用于避免反复探测“史前”空区间
            prev_min = _parse_date(ledger.get(code).get("min_start") or "")
            # 非失败结束后，把本次 start 并入水位线（失败不并入，保证重试仍会补齐）
            new_min = start_d if prev_min is None else min(prev_min, start_d)

            try:
                plain = normalize_stock_code(code)
                result = self._backfill_one(
                    code, start_d=start_d, end_d=end_d, mode=mode,
                    retry=retry, fresh_days=fresh_days, force=force, sleep=sleep,
                    min_attempted=prev_min,
                    list_date=list_dates.get(plain),
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
                ledger.update(code, status="done", note="fresh", min_start=_iso(new_min),
                              last=_iso(result.get("last")), rows=result.get("rows"))
            elif action == "empty":
                stats["empty"] += 1
                ledger.update(
                    code, status="empty", min_start=_iso(new_min),
                    error=result.get("error") or "数据源返回空",
                )
            elif action == "failed":
                stats["failed"] += 1
                ledger.update(code, status="failed", error=result.get("error"))
            else:  # fetched
                stats["fetched"] += 1
                stats["rows_added"] += result.get("added", 0)
                stats["quote_rows"] += result.get("quote_rows", 0)
                ledger.update(
                    code, status="done", error=None, min_start=_iso(new_min),
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
            "回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，"
            "新增 quote 行 %d，台账：%s",
            stats["fetched"], stats["skipped"], stats["failed"],
            stats["empty"], stats["quote_rows"], progress_path,
        )
        return stats

    def _backfill_one(
        self, code: str, *, start_d: date, end_d: date, mode: str,
        retry: int, fresh_days: int, force: bool, sleep: float,
        min_attempted: Optional[date] = None,
        list_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """处理单只股票，返回 {action, ...}。action ∈ skipped/fetched/empty/failed。"""
        return self._backfill_quote(
            code, start_d=start_d, end_d=end_d, mode=mode,
            retry=retry, fresh_days=fresh_days, force=force, sleep=sleep,
            min_attempted=min_attempted,
            list_date=list_date,
        )

    def _backfill_quote(
        self, code: str, *, start_d: date, end_d: date, mode: str,
        retry: int, fresh_days: int, force: bool, sleep: float,
        min_attempted: Optional[date] = None,
        list_date: Optional[date] = None,
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

        segments = self._plan_segments(
            start_d=effective_start, end_d=end_d,
            first=coverage.get("first"), last=coverage.get("last"),
            mode=mode, fresh_days=fresh_days, force=force,
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
            result, err = self._ingest_quote_with_retry(code, seg_start, seg_end, retry=retry)
            if sleep > 0:
                time.sleep(sleep)
            if err is not None:
                if got_any:
                    logger.warning("%s quote 分段 %s~%s 失败：%s（已保留其余段）", code, seg_start, seg_end, err)
                    continue
                if _is_no_data_error(err):
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
            "first": cov2.get("first"),
            "last": cov2.get("last"),
            "rows": cov2.get("rows"),
            "source": "TencentQuote",
            "quote_rows": total_quote,
            "start_reason": start_reason,
            "effective_start": _iso(effective_start),
        }

    @staticmethod
    def _plan_segments(
        *, start_d: date, end_d: date, first: Optional[date], last: Optional[date],
        mode: str, fresh_days: int, force: bool, min_attempted: Optional[date] = None,
    ) -> List["_Seg"]:
        """根据模式与 DB 已有覆盖，规划需要请求的区间段。

        - full：整段 [start, end]（force 或数据太旧时；否则若已最新则空）。
        - incremental：只补 [last+1, end] 的往后缺口。
        - smart：按 DB 覆盖计算前后缺口——start<first 补前段、end>last 补后段，
                 从而支持“先拉近段、后补更早历史”而不重复请求。
        - range：精确拉 [start, end]，不做“已最新”跳过（多进程按时间段分片专用）。

        min_attempted：该票历史上已请求过的最早起始日（水位线）。若本次 start 不早于
        水位线，说明 [start, first) 这段“史前”区间此前已探测过且确认无更多数据（否则
        first 早就前移了），无需再空请求——次新股/上市前区间因此只会被探测一次。
        """
        # range / force：精确区间，不做新鲜度跳过
        if mode == "range" or force:
            return [(start_d, end_d)] if start_d <= end_d else []

        # DB 无数据 → 直接整段；但若此前已按更早/相同起点探测过仍无数据，则不再空请求
        if first is None or last is None:
            if min_attempted is not None and start_d >= min_attempted:
                return []
            return [(start_d, end_d)] if start_d <= end_d else []

        if mode == "smart":
            segs: List["_Seg"] = []
            # 前向缺口只取“尚未探测过”的更早一段：边界取 first 与水位线中的较早者
            front_boundary = first if min_attempted is None else min(first, min_attempted)
            if start_d < front_boundary:
                segs.append((start_d, front_boundary - timedelta(days=1)))  # 往前补历史
            if end_d > last:
                segs.append((last + timedelta(days=1), end_d))     # 往后补增量
            return segs

        if mode == "incremental":
            # 已最新则不动
            if (end_d - last).days <= fresh_days:
                return []
            seg_start = last + timedelta(days=1)
            return [(seg_start, end_d)] if seg_start <= end_d else []

        # full：数据够新则跳过，否则整段
        if (end_d - last).days <= fresh_days and start_d >= first:
            return []
        return [(start_d, end_d)] if start_d <= end_d else []

    def _ingest_quote_with_retry(
        self, code: str, seg_start: date, seg_end: date, *, retry: int,
    ):
        """返回 (IngestResult|None, error_str)。"""
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
