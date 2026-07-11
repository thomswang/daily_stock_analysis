# -*- coding: utf-8 -*-
"""回填区间规划与错误分类（quote / kline 共用）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, List, Optional, Tuple

_Seg = Tuple[date, date]

_TRANSIENT_ERROR_MARKERS = (
    "CircuitOpen", "熔断", "Connection", "RemoteDisconnected", "Timeout",
    "timed out", "ProtocolError", "Max retries", "SSL", "ReadTimeout",
    "ConnectionError", "Proxy", "代理", "reset by peer", "aborted",
)
_NO_DATA_MARKERS = (
    "未查询到", "未获取到", "无数据", "没有数据", "暂无数据", "查询不到",
    "no data", "not found", "空日线",
    # 注意：不再包含裸「返回空」/「kline 返回空」/「quote 返回空」——
    # 因为并发风控 / CLI 瞬时崩坏也会让接口返回 []，此前把它当 empty 终态
    # 导致大量票被误判永久跳过（如 000531 明明 2023+ 有数据，凌晨批跑时接口回空
    # 就被标 empty，之后再跑也不重试）。现在这类情况一律走可重试 failed。
    # 真正的「未上市/已退市」应由 resolve_effective_start（cn_list_dates.json）
    # 前置拦截，effective_start is None 才判 empty，语义更清晰、误伤率更低。
)


def is_no_data_error(err: Optional[str]) -> bool:
    if not err:
        return False
    if any(m in err for m in _TRANSIENT_ERROR_MARKERS):
        return False
    return any(m in err for m in _NO_DATA_MARKERS)


def parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


def plan_segments(
    *,
    start_d: date,
    end_d: date,
    first: Optional[date],
    last: Optional[date],
    mode: str,
    fresh_days: int,
    force: bool,
    min_attempted: Optional[date] = None,
) -> List[_Seg]:
    """根据模式与 DB 覆盖，规划需要请求的区间段。

    跳过只应发生在"无新数据可拉"（last >= end_d）；任何 last < end_d 的票
    都会被安排去拉缺口，不再用 fresh_days 容忍"小缺口"——那会把还差几天
    的新数据也跳过，导致 bump --end 后缺失中间交易日 / daily incremental
    静默停更。
    fresh_days 参数保留仅为向后兼容（各调用方仍传入），已不再参与跳过判断。
    """
    if mode == "range" or force:
        return [(start_d, end_d)] if start_d <= end_d else []

    if first is None or last is None:
        if min_attempted is not None and start_d >= min_attempted:
            return []
        return [(start_d, end_d)] if start_d <= end_d else []

    if mode == "smart":
        segs: List[_Seg] = []
        front_boundary = first if min_attempted is None else min(first, min_attempted)
        if start_d < front_boundary:
            segs.append((start_d, front_boundary - timedelta(days=1)))
        if end_d > last:
            segs.append((last + timedelta(days=1), end_d))
        return segs

    if mode == "incremental":
        # 只要 [last+1, end_d] 还有缺口就拉；已完整覆盖（end_d 已在库内）才跳过
        if last >= end_d:
            return []
        seg_start = last + timedelta(days=1)
        return [(seg_start, end_d)] if seg_start <= end_d else []

    # full（默认）：确保 [start_d, end_d] 完整覆盖。
    # 跳过条件 = 后端已到 end_d（last >= end_d）且本地已存有数据（first is not None）。
    #
    # 为什么不再要求 start_d >= first：
    #   first 是「本数据源在当前模式下能返回的最早日」——百度尾窗（--no-full）只回
    #   最近约 2000 行（老票≈2018 起），全量源则回真实最早日。当 last 已到 end_d 时，
    #   [start_d, first-1] 这段更早的历史在当前模式下物理不可达，重复请求只会白白
    #   保存 0 条并浪费一次请求 + 限流（如 000001：last=end 但 first=2018-04-10，
    #   旧逻辑因 2010 < 2018 每轮必拉）。
    #   需要补齐深历史请显式用 --full（全量 all=1）或 --force。
    # 首跑时 first is None（见上方 first/last 分支），仍会正常探测一次，不会漏数据。
    # 注意：不再用 fresh_days 容忍"小缺口"——那会把还差几天的新数据也跳过。
    if last >= end_d and first is not None:
        return []
    return [(start_d, end_d)] if start_d <= end_d else []
