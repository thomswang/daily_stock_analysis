# -*- coding: utf-8 -*-
"""
=========================================
换手率回填 入口（CLI）
=========================================

全市场 K 线回填以 Efinance/Tencent 等为主源（→ stock_daily）；
换手率等截面字段在 stock_daily_quote，由 westock ``quote --date`` 逐日拉取。
本脚本补漏：找出 K 线已有但 quote 截面缺失的行并重拉。

用法：
  # 先看有多少票/行需要补（不写库）
  python backfill_turnover.py --list

  # 全量回填（新浪源，限流 0.5s/票，带断点续传）
  python backfill_turnover.py --progress data/turnover_fill.json

  # 只补指定票
  python backfill_turnover.py --codes 600519.SH,000001.SZ

  # 先小批量试跑
  python backfill_turnover.py --limit 20

⚠️ 建议等全市场 OHLCV 回填跑完后再执行，避免同时写库导致 SQLite 锁冲突。
⚠️ 数据仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import os
import sys

from src.config import setup_env  # noqa: E402

setup_env()
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    _proxy = f"http://{os.getenv('PROXY_HOST', '127.0.0.1')}:{os.getenv('PROXY_PORT', '10809')}"
    os.environ["http_proxy"] = _proxy
    os.environ["https_proxy"] = _proxy

import argparse  # noqa: E402
import logging  # noqa: E402
from typing import List, Optional  # noqa: E402

logger = logging.getLogger("backfill_turnover")


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="换手率回填入口（新浪源，只补空）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--codes", type=str, help="指定股票 canonical 代码，逗号分隔（默认全部缺失票）")
    parser.add_argument("--sleep", type=float, default=0.5, help="每只票请求后的限流秒数（默认 0.5）")
    parser.add_argument("--retry", type=int, default=2, help="单只票失败重试次数（默认 2）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少只票（试跑用）")
    parser.add_argument("--progress", type=str, default=None, help="断点续传进度文件路径（JSON）")
    parser.add_argument("--list", action="store_true", help="只统计需要补的票/行数后退出（不写库）")
    parser.add_argument("--recompute-approx", action="store_true",
                        help="重算近似源(腾讯现算)的换手率：用新浪逐日流通股本覆盖错值"
                             "（默认只补 NULL，不动已有值）")
    parser.add_argument("--start", type=str, default=None, help="只处理该日期起(含) YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="只处理该日期止(含) YYYY-MM-DD")
    parser.add_argument("--debug", action="store_true", help="调试日志")
    return parser.parse_args()


def _print_summary(stats: dict) -> None:
    print("\n===== 换手率回填完成 =====")
    print(f"目标票数:      {stats['total']}")
    print(f"成功票数:      {stats['updated_codes']}")
    print(f"补齐行数:      {stats['updated_rows']}")
    print(f"新浪无数据:    {stats['empty']}")
    print(f"失败:          {stats['failed']}")
    print(f"跳过(已完成):  {stats['skipped_done']}")
    print("==========================\n")


def main() -> int:
    _force_utf8_stdout()
    args = parse_args()
    _setup_logging(args.debug)

    from src.services.turnover_backfill_service import TurnoverBackfillService

    codes: Optional[List[str]] = None
    if args.codes:
        codes = [c.strip().upper() for c in args.codes.split(",") if c.strip()]

    from datetime import datetime as _dt

    def _pd(s: Optional[str]):
        return _dt.strptime(s, "%Y-%m-%d").date() if s else None

    start_d = _pd(args.start)
    end_d = _pd(args.end)

    service = TurnoverBackfillService()

    if args.list:
        targets = service.find_targets(
            codes=codes, recompute_approx=args.recompute_approx,
            start=start_d, end=end_d,
        )
        total_rows = sum(v[2] for v in targets.values())
        head = "换手率待重算统计" if args.recompute_approx else "换手率缺失统计"
        print(f"\n===== {head} =====")
        print(f"命中票数: {len(targets)}")
        print(f"命中行数: {total_rows}")
        if targets:
            sample = list(targets.items())[:10]
            print("样例(code: 起~止 缺失行数):")
            for code, (min_d, max_d, rows) in sample:
                print(f"  {code:<12} {min_d} ~ {max_d}  {rows} 行")
        print("==========================\n")
        return 0

    stats = service.backfill(
        codes=codes,
        sleep=args.sleep,
        retry=args.retry,
        limit=args.limit,
        progress_path=args.progress,
        recompute_approx=args.recompute_approx,
        start=start_d,
        end=end_d,
    )
    _print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
