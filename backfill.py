#!/usr/bin/env python
# -*- coding: utf-8
"""
市场日线回填统一入口。

  python backfill.py baidu ...          → stock_daily_ohlcv（百度股市通 K 线，前复权）
  python backfill.py westock-ohlcv ...  → stock_daily_ohlcv（westock kline 每日增量续写）
  python backfill.py quote ...          → stock_daily_quote（westock quote --date 截面）

详见：执行ohlcv.md / 执行quote.md
"""

from __future__ import annotations

import sys

from src.cli.backfill_cli import main

if __name__ == "__main__":
    sys.exit(main())
