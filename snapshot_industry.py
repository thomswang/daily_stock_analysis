# -*- coding: utf-8 -*-
"""
=========================================
个股行业归属快照 入口（CLI）
=========================================

把「个股 -> 所属行业」按今天日期快照存入 stock_industry 表（point-in-time）。
定期运行（如每周/每月）可积累行业归属历史，供预测建模按日期对齐、避免未来函数。

用法：
  # 抓一次全市场行业归属快照（akshare 东财行业板块，无需 token）
  python snapshot_industry.py

  # 只试抓前 5 个行业板块（试跑）
  python snapshot_industry.py --limit-boards 5

  # 指定快照日期（默认今天）
  python snapshot_industry.py --as-of 2026-07-02

  # 查看已有快照概览
  python snapshot_industry.py --status

  # 每周定时快照（后台常驻）
  python snapshot_industry.py --schedule 18:00

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
from datetime import date, datetime  # noqa: E402

logger = logging.getLogger("snapshot_industry")


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


def _print_status() -> None:
    from src.repositories.stock_industry_repo import StockIndustryRepository

    summary = StockIndustryRepository().summary()
    print("\n===== 行业归属快照概览 =====")
    print(f"快照次数:   {summary['snapshot_count']}")
    print(f"最新快照日: {summary['latest']}")
    print(f"覆盖股票:   {summary['latest_codes']}")
    print(f"行业数:     {summary['latest_industries']}")
    print("===========================\n")


def _run(args: argparse.Namespace) -> dict:
    from src.services.industry_snapshot_service import IndustrySnapshotService

    as_of: date = date.today()
    if args.as_of:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit("--as-of 格式应为 YYYY-MM-DD")

    return IndustrySnapshotService().run(
        as_of=as_of,
        sleep=args.sleep,
        limit_boards=args.limit_boards,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="个股行业归属快照入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--as-of", type=str, default=None, help="快照日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--limit-boards", type=int, default=None, help="仅抓前 N 个行业板块（试跑）")
    parser.add_argument("--sleep", type=float, default=0.3, help="每个板块请求后的限流秒数（默认 0.3）")
    parser.add_argument("--status", action="store_true", help="只打印快照概览后退出")
    parser.add_argument("--schedule", type=str, default=None, metavar="HH:MM", help="每日定时快照时间，后台常驻")
    parser.add_argument("--no-run-immediately", action="store_true", help="定时模式启动时不先跑一次")
    parser.add_argument("--debug", action="store_true", help="调试日志")
    return parser.parse_args()


def _print_summary(stats: dict) -> None:
    print("\n===== 行业快照完成 =====")
    print(f"快照日期: {stats['as_of_date']}")
    print(f"覆盖股票: {stats['codes']}")
    print(f"行业数:   {stats['industries']}")
    print(f"写入记录: {stats['written']}")
    print(f"数据来源: {stats['source']}")
    print("========================\n")


def main() -> int:
    _force_utf8_stdout()
    args = parse_args()
    _setup_logging(args.debug)

    if args.status:
        _print_status()
        return 0

    if args.schedule:
        from src.scheduler import run_with_schedule

        def _task():
            try:
                _run(args)
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("定时行业快照失败: %s", exc)

        logger.info("进入定时行业快照模式：每日 %s。Ctrl+C 退出。", args.schedule)
        run_with_schedule(
            task=_task,
            schedule_time=args.schedule,
            run_immediately=not args.no_run_immediately,
        )
        return 0

    stats = _run(args)
    _print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
