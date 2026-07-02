# -*- coding: utf-8 -*-
"""
=========================================
大盘指数日线回填 入口（CLI）
=========================================

把 A 股主要宽基指数（上证/沪深300/中证500/中证1000/深成/创业板）的历史日线
灌入本地 stock_daily 缓存，作为预测建模的「大盘环境/相对强弱」特征来源。

用法：
  # 回填全部默认宽基指数（akshare 新浪源，无需 token）
  python backfill_index.py

  # 只回填指定指数
  python backfill_index.py --symbols 000300.SH,399006.SZ

  # 指定区间（默认全历史）
  python backfill_index.py --start 2018-01-01

  # 查看已知的默认指数清单
  python backfill_index.py --list

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

logger = logging.getLogger("backfill_index")


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


def _print_list() -> None:
    from src.services.index_backfill_service import DEFAULT_INDEXES

    print("\n===== 默认宽基指数清单 =====")
    for code, (name, sina) in DEFAULT_INDEXES.items():
        print(f"  {code:<12} {name:<8} (sina={sina})")
    print("============================\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="大盘指数日线回填入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--symbols", type=str, help="指定指数 canonical 代码，逗号分隔（默认全部）")
    parser.add_argument("--start", type=str, default=None, help="回填起始日 YYYY-MM-DD（默认全历史）")
    parser.add_argument("--end", type=str, default=None, help="回填结束日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--sleep", type=float, default=0.5, help="每个指数请求后的限流秒数（默认 0.5）")
    parser.add_argument("--retry", type=int, default=1, help="单个指数失败重试次数（默认 1）")
    parser.add_argument("--list", action="store_true", help="只打印默认指数清单后退出")
    parser.add_argument("--debug", action="store_true", help="调试日志")
    return parser.parse_args()


def _print_summary(stats: dict) -> None:
    print("\n===== 指数回填完成 =====")
    print(f"计划总数: {stats['total']}")
    print(f"实际拉取: {stats['fetched']}")
    print(f"返回为空: {stats['empty']}")
    print(f"失败:     {stats['failed']}")
    print(f"新增行数: {stats['rows_added']}")
    print("========================\n")


def main() -> int:
    _force_utf8_stdout()
    args = parse_args()
    _setup_logging(args.debug)

    if args.list:
        _print_list()
        return 0

    from src.services.index_backfill_service import IndexBackfillService

    codes: Optional[List[str]] = None
    if args.symbols:
        codes = [c.strip().upper() for c in args.symbols.split(",") if c.strip()]

    stats = IndexBackfillService().run(
        codes,
        start_date=args.start,
        end_date=args.end,
        sleep=args.sleep,
        retry=args.retry,
    )
    _print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
