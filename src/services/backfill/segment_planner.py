# -*- coding: utf-8 -*-
"""回填区间规划与错误分类（quote / kline 共用）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, List, Optional, Tuple

_Seg = Tuple[date, date]

# westock CLI 单次 kline 请求上限约 250 条（交易日）。
# 拆成 6 个月子段 ≈ 125 交易日，留足余量。
_MAX_SEGMENT_DAYS = 180

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
    """根据模式与 DB 覆盖，规划需要请求的区间段。"""
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
        if (end_d - last).days <= fresh_days:
            return []
        seg_start = last + timedelta(days=1)
        return [(seg_start, end_d)] if seg_start <= end_d else []

    if (end_d - last).days <= fresh_days and start_d >= first:
        return []
    return [(start_d, end_d)] if start_d <= end_d else []


def split_segments_by_max_days(
    segments: List[_Seg],
    max_days: int = _MAX_SEGMENT_DAYS,
) -> List[_Seg]:
    """将超过 max_days 的段拆成连续子段，避免 westock CLI 250 行截断。

    拆分策略：按日历天数等分，每段不超过 max_days 天。
    6 个月 ≈ 180 天 ≈ 125 交易日 < 250 行上限。
    """
    out: List[_Seg] = []
    for start, end in segments:
        span = (end - start).days
        if span <= max_days:
            out.append((start, end))
            continue
        cursor = start
        while cursor <= end:
            seg_end = min(cursor + timedelta(days=max_days - 1), end)
            out.append((cursor, seg_end))
            cursor = seg_end + timedelta(days=1)
    return out
