# -*- coding: utf-8 -*-
"""回填限流工具：带随机抖动的休眠。

固定节奏的 sleep 易被数据源风控识别为爬虫（如百度按 IP 限流）。在基础
间隔上下浮动一定比例后休眠，可打散请求节拍、降低被封概率。
"""

from __future__ import annotations

import random
import time
from typing import Callable

# 默认抖动幅度 ±30%（相对基础间隔）
DEFAULT_JITTER = 0.3


def jittered_sleep(
    base: float,
    jitter: float = DEFAULT_JITTER,
    *,
    clock: Callable[[float], None] = time.sleep,
) -> float:
    """在 ``base`` 上下浮动 ±``jitter`` 比例后休眠，返回实际休眠秒数。

    - ``base <= 0`` 视为不限流，直接返回 0 且不休眠；
    - ``jitter`` 取绝对值并钳制到 ``[0, 1]``，避免非法比例导致负间隔；
    - 实际间隔在 ``[base*(1-jitter), base*(1+jitter)]`` 间均匀随机。

    ``clock`` 可注入（测试时替换为 fake），默认 ``time.sleep``。
    """
    if base <= 0:
        return 0.0
    jitter = max(0.0, min(1.0, abs(jitter)))
    duration = random.uniform(base * (1 - jitter), base * (1 + jitter))
    clock(duration)
    return duration
