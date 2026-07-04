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
    "no data", "not found", "返回空", "空日线",
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
