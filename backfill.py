#!/usr/bin/env python
# -*- coding: utf-8
"""
市场日线回填统一入口。

  python backfill.py quote ...   → stock_daily_quote（westock quote --date）
  python backfill.py kline ...   → stock_daily_kline（westock kline 整段）

详见：执行quote.md / 执行kline.md
"""

from __future__ import annotations

import sys

from src.cli.backfill_cli import main

if __name__ == "__main__":
    sys.exit(main())
