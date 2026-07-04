# -*- coding: utf-8 -*-
"""顺序重算多个日期区间的换手率（单进程，抗后台外壳回收）。

背景：分批重算若用 bash 的 `&&` 串联，后台外壳一旦被回收，段与段之间的
链就断了（子 python 能存活，但下一段不会自动启动）。本脚本把多个区间放进
同一个 python 进程内顺序执行，只要该进程存活就会一路跑完，天然抗外壳回收。

每段调用 TurnoverBackfillService.backfill(recompute_approx=True)：
  - 用新浪逐日历史流通股本重算换手率，覆盖腾讯现算的近似错值；
  - 各段独立进度文件，断点续传、可反复运行；
  - 数据库为 WAL + busy_timeout + 写重试，与其它写进程并发安全（自动串行）。

用法：
  python -u scripts/recompute_turnover_batches.py >> data/turnover_recompute.log 2>&1
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

# 允许从 scripts/ 目录直接运行：把项目根加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import setup_env  # noqa: E402

setup_env()

from src.services.turnover_backfill_service import TurnoverBackfillService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("recompute_batches")

# 待处理区间（2018-2020 由先前进程负责，这里接力后续三段）
RANGES = [
    ("2020-07-01", "2022-06-30", "data/turnover_recompute_2020-2022.json"),
    ("2022-07-01", "2024-06-30", "data/turnover_recompute_2022-2024.json"),
    ("2024-07-01", "2026-06-30", "data/turnover_recompute_2024-2026.json"),
]


def _d(s: str):
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    svc = TurnoverBackfillService()
    for start, end, prog in RANGES:
        log.info("==== 开始区间 %s ~ %s ====", start, end)
        stats = svc.backfill(
            recompute_approx=True,
            start=_d(start),
            end=_d(end),
            progress_path=prog,
            sleep=0.3,
        )
        log.info("==== 完成区间 %s ~ %s：%s ====", start, end, stats)
    log.info("==== ALL RANGES DONE ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
