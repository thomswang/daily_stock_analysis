# -*- coding: utf-8 -*-
"""Quote 回填区间规划：用 cn_list_dates.json 上市日裁剪起点。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_effective_start(
    code: str,
    start_d: date,
    end_d: date,
    *,
    list_date: Optional[date] = None,
    force: bool = False,
) -> Tuple[Optional[date], str]:
    """计算实际应从哪一天开始拉 quote。

    有 list_date 时：effective_start = max(start, list_date)。
    无 list_date 时：从 start 线性拉（应运行 fetch_cn_list_dates.py 补齐 metadata）。
    """
    if start_d > end_d:
        return None, "invalid_range"

    if list_date is not None and not force:
        eff = max(start_d, list_date)
        if eff > end_d:
            return None, "list_date_past_end"
        if eff > start_d:
            logger.debug("%s 上市日 %s，起点 %s → %s", code, list_date, start_d, eff)
        return eff, "list_date"

    if list_date is None:
        logger.warning("%s 无上市日 metadata，从 %s 线性拉取", code, start_d)
    return start_d, "no_list_date"
