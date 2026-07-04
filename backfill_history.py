# -*- coding: utf-8 -*-
"""
=========================================
全历史日线数据回填 入口（CLI）
=========================================

数据写入单表 stock_daily_quote（westock quote --date，与 test/index.html 日K全字段一致）：
  按每个工作日循环请求，一天一行、40+ 字段。
  回填前需有 data/cache/cn_list_dates.json（python scripts/fetch_cn_list_dates.py）。

────────────────────────────────────────────────────────────
一、单进程常用命令
────────────────────────────────────────────────────────────

  大区间自动用 cn_list_dates.json 上市日裁剪起点（先运行 scripts/fetch_cn_list_dates.py）。

  # 首次：批量拉全市场上市日（约 260 批，可中断续传）
  python scripts/fetch_cn_list_dates.py

  # 全 A 股，从 2010 拉到今天（可随时 Ctrl+C 中断，重跑自动续传）
  python backfill_history.py --all --start 2010-01-01

  # 先拿前 20 只试跑
  python backfill_history.py --all --limit 20

  # 只补自选股 / 指定代码
  python backfill_history.py --from-watchlist
  python backfill_history.py --symbols 600519,000001

  # 渐进式建库：先拉近两年，以后把 start 往前推
  python backfill_history.py --all --mode smart --start 2024-07-01

  # 日常增量：只补每只票缺的最新一段
  python backfill_history.py --all --mode incremental

  # 多进程按年份分片（每进程独立 --progress）
  python backfill_history.py --all --mode range --start 2020-01-01 --end 2022-12-31 \\
    --progress data/quote_2020_2022.json --sleep 0.3

  # 环境变量 WESTOCK_DATA_DIR 需指向 westock-data 目录

⚠ 数据仅供技术研究，不构成任何投资建议。
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

logger = logging.getLogger("backfill_history")


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


def _resolve_codes(args: argparse.Namespace, service) -> List[str]:
    if args.symbols:
        return [c.strip().upper() for c in args.symbols.split(",") if c.strip()]
    if args.codes_file:
        return _read_codes_file(args.codes_file)
    if args.from_watchlist:
        from src.config import get_config

        config = get_config()
        try:
            config.refresh_stock_list()
        except Exception:  # noqa: BLE001
            pass
        codes = list(getattr(config, "stock_list", []) or [])
        if not codes:
            raise SystemExit("自选股列表为空：请在 .env 配置 STOCK_LIST，或改用 --all/--symbols")
        return codes
    if args.all:
        return service.load_all_cn_codes(index_path=args.index_path)
    raise SystemExit("请指定代码来源：--all / --from-watchlist / --symbols / --codes-file")


def _read_codes_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise SystemExit(f"代码文件不存在：{path}")
    codes: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            for tok in line.replace(",", " ").split():
                tok = tok.strip().upper()
                if tok:
                    codes.append(tok)
    if not codes:
        raise SystemExit(f"代码文件为空：{path}")
    return codes


def _print_progress_status(progress_path: str) -> None:
    from src.services.history_backfill_service import ProgressLedger

    ledger = ProgressLedger(progress_path)
    meta = ledger.data.get("meta", {})
    summary = ledger.summary()
    print("\n===== 回填进度台账 =====")
    print(f"文件:     {progress_path}")
    print(
        f"区间:     {meta.get('start_date')} ~ {meta.get('end_date')}  "
        f"模式={meta.get('mode')}"
    )
    print(f"计划总数: {meta.get('total')}   最后更新: {meta.get('updated_at')}")
    print("-" * 30)
    if not summary:
        print("（暂无记录，尚未运行过回填）")
    else:
        for st, n in sorted(summary.items()):
            print(f"  {st:<10} {n}")
    print("========================\n")


def _run_backfill(args: argparse.Namespace) -> dict:
    from src.services.history_backfill_service import HistoryBackfillService

    service = HistoryBackfillService()
    codes = _resolve_codes(args, service)
    logger.info("准备回填：%d 只股票", len(codes))
    return service.run(
        codes,
        start_date=args.start,
        end_date=args.end,
        mode=args.mode,
        sleep=args.sleep,
        retry=args.retry,
        fresh_days=args.fresh_days,
        force=args.force,
        retry_failed=args.retry_failed,
        limit=args.limit,
        progress_path=args.progress,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="全历史日线数据回填入口（支持分层、分进程并行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # 代码来源（四选一）
    parser.add_argument("--all", action="store_true", help="全部 A 股（读 stocks.index.json）")
    parser.add_argument("--from-watchlist", action="store_true", help="使用 .env 的 STOCK_LIST")
    parser.add_argument("--symbols", type=str, help="指定代码，逗号分隔")
    parser.add_argument("--codes-file", type=str, help="从文件读代码（每行一个或逗号分隔）")
    parser.add_argument("--index-path", type=str, default=None, help="stocks.index.json 路径（默认自动查找）")
    # 区间、模式与分层
    parser.add_argument("--start", type=str, default="2010-01-01", help="回填起始日（默认 2010-01-01）")
    parser.add_argument("--end", type=str, default=None, help="回填结束日（默认今天）")
    parser.add_argument(
        "--mode", type=str,
        choices=["full", "incremental", "smart", "range"], default="full",
        help="full=整段拉；incremental=只补最新缺口；smart=按覆盖补缺口；range=精确区间（多进程分片）",
    )
    # 续传/容错/限流
    parser.add_argument("--fresh-days", type=int, default=4, help="DB 最新日期距今≤该天数则视为已最新并跳过（默认 4）")
    parser.add_argument("--force", action="store_true", help="忽略已最新判断，强制按 start 重拉")
    parser.add_argument("--retry", type=int, default=1, help="单只失败重试次数（默认 1）")
    parser.add_argument("--retry-failed", action="store_true",
                        help="仅重试台账中 failed 的代码（不含 done/skipped/empty；empty=确定无数据如次新股）")
    parser.add_argument("--sleep", type=float, default=0.5, help="每次请求后的限流秒数（默认 0.5）")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 只（试跑）")
    # 台账与调度
    parser.add_argument("--progress", type=str, default=os.path.join("data", "backfill_progress.json"),
                        help="进度台账文件路径（多进程并行时每个进程须用不同路径）")
    parser.add_argument("--progress-status", action="store_true", help="只打印进度台账汇总后退出")
    parser.add_argument("--schedule", type=str, default=None, metavar="HH:MM", help="每日定时回填时间，后台常驻")
    parser.add_argument("--no-run-immediately", action="store_true", help="定时模式启动时不先跑一次")
    parser.add_argument("--debug", action="store_true", help="调试日志")
    return parser.parse_args()


def _print_summary(stats: dict) -> None:
    print("\n===== 回填完成 =====")
    print(f"计划总数: {stats['total']}")
    print(f"实际拉取: {stats['fetched']}")
    print(f"跳过(已最新): {stats['skipped']}")
    print(f"返回为空: {stats['empty']}")
    print(f"失败:     {stats['failed']}")
    print(f"新增 quote 行: {stats.get('quote_rows', stats.get('rows_added', 0))}")
    print("====================\n")


def main() -> int:
    _force_utf8_stdout()
    args = parse_args()
    _setup_logging(args.debug)

    if args.progress_status:
        _print_progress_status(args.progress)
        return 0

    if args.schedule:
        from src.scheduler import run_with_schedule

        def _task():
            try:
                _run_backfill(args)
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001 - 定时单次失败不终止调度
                logger.exception("定时回填失败: %s", exc)

        logger.info("进入定时回填模式：每日 %s。Ctrl+C 退出。", args.schedule)
        run_with_schedule(
            task=_task,
            schedule_time=args.schedule,
            run_immediately=not args.no_run_immediately,
        )
        return 0

    stats = _run_backfill(args)
    _print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
