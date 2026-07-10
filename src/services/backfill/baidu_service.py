# -*- coding: utf-8 -*-
"""百度股市通 K 线 → 通用表 stock_daily_ohlcv（adj_type=qfq）回填。"""

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
DEFAULT_PROGRESS_PATH = os.path.join("data", "baidu_backfill_progress.json")


def _default_token_provider():
    """延迟导入并创建默认的百度 acs-token 自动获取器（浏览器懒启动）。"""
    from data_provider.baidu_token_provider import BaiduTokenProvider

    return BaiduTokenProvider()


def _resolve_need_full(full_mode: str, local_first, effective_start) -> bool:
    """根据 full_mode 决定百度请求是否带 all=1（全量）。

    - auto：本地无数据或有效起点早于本地 first → 全量；否则尾窗口。
    - full：永远全量（无视本地覆盖，用于补齐/修复深历史）。
    - tail：永远尾窗口（仅最近约 2000 行，传输量最小）。
    """
    if full_mode == "full":
        return True
    if full_mode == "tail":
        return False
    # auto
    return (local_first is None) or (effective_start < local_first)


class BaiduBackfillService:
    """百度 K 线整段回填（BaiduFetcher，HTTP 直连 vapi/v1/getquotation）。

    默认自动创建 :class:`BaiduTokenProvider`（懒启动浏览器，按需刷新 acs-token），
    无需手动粘贴 token。也可通过 ``token_provider`` 传入自定义实例。
    """

    dataset = "baidu"

    def __init__(self, db_manager=None, token_provider=None):
        from src.repositories.stock_repo import StockRepository

        self.repo = StockRepository(db_manager)
        # 未显式传入则默认创建一个（浏览器懒启动，仅在首次请求时拉起）
        self._token_provider = token_provider or _default_token_provider()
        self._owns_provider = token_provider is None
        self._ingest = None

    @property
    def ingest(self):
        if self._ingest is None:
            from src.ingest.baidu_kline import BaiduKlineIngestor

            self._ingest = BaiduKlineIngestor(
                db_manager=self.repo.db, token_provider=self._token_provider
            )
        return self._ingest

    def close(self) -> None:
        """释放 token_provider 持有的浏览器资源（仅当由本服务内部创建时）。

        不清空 ``_token_provider`` 引用：provider 对象在下次 ``get_token()`` 时会
        按需重新拉起浏览器，从而支持服务被多次 ``run()`` 复用。
        """
        if self._owns_provider and self._token_provider is not None:
            try:
                self._token_provider.close()
            except Exception:  # noqa: BLE001
                pass

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
        ktype: str = "1",
        full_mode: str = "auto",
    ) -> Dict[str, Any]:
        logger.info(
            "baidu 回填：分段策略(mode)=%s，全量策略(full_mode)=%s",
            mode, full_mode,
        )
        try:
            return run_backfill_job(
                dataset=self.dataset,
                rows_key="baidu_rows",
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
                meta_extra={"ktype": ktype},
                start_log=(
                    "开始 baidu kline 回填：%d 只，区间 %s ~ %s，分段=%s，"
                    "限流=%.2fs，force=%s"
                ),
                finish_log=(
                    "baidu 回填结束：拉取 %d / 跳过 %d / 失败 %d / 空 %d，"
                    "新增 baidu 行 %d，台账：%s"
                ),
                process_kwargs={"ktype": ktype, "full_mode": full_mode},
                # 百度 vapi 强依赖 acs-token，IP 被风控时会统一返回空 403；
                # 连续命中即熔断，避免 --all 全量轰炸把 IP 打进黑名单（本次根因）。
                # 百度纯数字请求在健康 IP 下几乎不会 403，故连续 3 只即判为限流/封锁，
                # 宁可早停也不要继续轰炸延长惩罚窗口。
                fail_fast_on_error_substr="403",
                fail_fast_consecutive=3,
            )
        finally:
            # 仅在内部创建的 provider 才负责关闭浏览器，避免影响调用方持有的实例
            self.close()

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
        ktype: str = "1",
        full_mode: str = "auto",
        **_: Any,
    ) -> Dict[str, Any]:
        from src.services.quote_backfill_planner import resolve_effective_start

        coverage = self.repo.get_ohlcv_coverage(code, ktype=ktype, adj_type="qfq")
        effective_start, start_reason = resolve_effective_start(
            code, start_d, end_d, list_date=list_date, force=force,
        )
        if effective_start is None:
            return {
                "action": "empty",
                "error": "区间内无 baidu 数据",
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

        # 全量 / 尾窗口策略（控制百度请求是否带 all=1）：
        # - full_mode="auto"（默认）：本地无数据，或有效起点早于本地已存 first → 全量；
        #   否则本地已持有深历史，仅刷新近期尾窗口（不带 all=1，传输量显著减少）。
        # - full_mode="full"：强制全量（all=1），无视本地覆盖（用于补齐/修复深历史）。
        # - full_mode="tail"：强制尾窗口（不带 all=1），仅取百度最近约 2000 行
        #   （老票≈2018 起；新股=上市日起），传输量最小。
        # 注意：此处的 full 与 --mode 的 "full" 是两回事——--mode 控制「分段策略」，
        # 本开关控制「单段是否带 all=1」。百度接口忽略 start/end，区间靠本地裁剪。
        local_first = coverage.get("first")
        need_full = _resolve_need_full(full_mode, local_first, effective_start)

        total_rows = 0
        total_reports = 0
        got_any = False
        for seg_start, seg_end in segments:
            result, err = self._ingest_with_retry(
                code, seg_start, seg_end, retry=retry, ktype=ktype, full=need_full
            )
            if sleep > 0:
                jittered_sleep(sleep)
            if err is not None:
                if got_any:
                    logger.warning(
                        "%s baidu 分段 %s~%s 失败：%s（已保留其余段）",
                        code, seg_start, seg_end, err,
                    )
                    continue
                if is_no_data_error(err):
                    return {"action": "empty", "error": err}
                return {"action": "failed", "error": err}
            if result is not None:
                total_rows += int(result.rows_saved)
                if result.reports_saved:
                    total_reports += int(result.reports_saved)
                got_any = True

        if not got_any:
            return {"action": "empty"}

        cov2 = self.repo.get_ohlcv_coverage(code, ktype=ktype, adj_type="qfq")
        first2 = cov2.get("first")
        if (
            full_mode == "tail"
            and effective_start is not None
            and first2 is not None
            and first2 > effective_start
        ):
            logger.warning(
                "%s 尾窗口仅覆盖到 %s（早于该日的请求起点 %s 百度不返回，需 full 模式才能补齐）",
                code, first2, effective_start,
            )
        return {
            "action": "fetched",
            "added": total_rows,
            "baidu_rows": total_rows,
            "baidu_reports": total_reports,
            "first": cov2.get("first"),
            "last": cov2.get("last"),
            "rows": cov2.get("rows"),
            "source": "BaiduFetcher",
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
        ktype: str = "1",
        full: bool = True,
    ):
        """返回 (result, err)。err 为 None 表示成功；err 命中 no_data 才判 empty。

        百度落库是 upsert（按 code+date+ktype+adj_type+data_source 冲突则更新），
        ``save_ohlcv_kline``
        返回的是「新增」行数——重复回填时新增为 0 但数据已正确覆盖写入，属正常成功。
        因此判定成功只看 ``rows_fetched > 0``（已取到数据且已 upsert 落库）；
        仅 ``rows_fetched == 0`` 才代表接口确实无数据。

        full: 是否拉全量（all=1）。已存有深历史时由调用方置 False，仅刷新尾窗口。
        """
        last_err = None
        for attempt in range(retry + 1):
            try:
                result = self.ingest.backfill(
                    code, start=seg_start, end=seg_end, ktype=ktype, full=full
                )
                if result.rows_fetched == 0:
                    last_err = "baidu 返回空"
                else:
                    return result, None
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.5 * (attempt + 1))
        return None, last_err
