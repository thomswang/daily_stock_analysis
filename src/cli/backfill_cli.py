# -*- coding: utf-8
"""回填 CLI 公共逻辑（quote / kline 子命令）。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Sequence

logger = logging.getLogger("backfill")


def bootstrap_env() -> None:
    from src.config import setup_env

    setup_env()
    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy = f"http://{os.getenv('PROXY_HOST', '127.0.0.1')}:{os.getenv('PROXY_PORT', '10809')}"
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy


def force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def read_codes_file(path: str) -> List[str]:
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


def resolve_codes(args: argparse.Namespace) -> List[str]:
    if args.symbols:
        return [c.strip().upper() for c in args.symbols.split(",") if c.strip()]
    if args.codes_file:
        return read_codes_file(args.codes_file)
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
        from src.services.backfill import CodeListLoader

        return CodeListLoader.load_all_cn_codes(index_path=args.index_path)
    raise SystemExit("请指定代码来源：--all / --from-watchlist / --symbols / --codes-file")


def add_code_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="全部 A 股（读 stocks.index.json）")
    parser.add_argument("--from-watchlist", action="store_true", help="使用 .env 的 STOCK_LIST")
    parser.add_argument("--symbols", type=str, help="指定代码，逗号分隔")
    parser.add_argument("--codes-file", type=str, help="从文件读代码（每行一个或逗号分隔）")
    parser.add_argument("--index-path", type=str, default=None, help="stocks.index.json 路径")


def add_run_args(parser: argparse.ArgumentParser, *, defaults: argparse.Namespace) -> None:
    parser.add_argument("--start", type=str, default=defaults.start, help="回填起始日")
    parser.add_argument("--end", type=str, default=None, help="回填结束日（默认今天）")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["full", "incremental", "smart", "range"],
        default=defaults.mode,
        help="full=整段；incremental=补最新；smart=按覆盖补缺口；range=精确区间（多进程分片）",
    )
    parser.add_argument("--fresh-days", type=int, default=defaults.fresh_days)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry", type=int, default=defaults.retry, help="单只失败重试次数")
    parser.add_argument("--retry-failed", action="store_true", help="仅重试台账中 failed 的代码")
    parser.add_argument("--sleep", type=float, default=defaults.sleep, help="限流秒数")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 只（试跑）")
    parser.add_argument("--progress", type=str, default=defaults.progress, help="进度台账 JSON 路径")
    parser.add_argument("--progress-status", action="store_true", help="只打印进度台账后退出")
    parser.add_argument("--debug", action="store_true")


def print_progress_status(progress_path: str, *, dataset: str) -> None:
    from src.services.backfill.ledger import ProgressLedger

    ledger = ProgressLedger(progress_path)
    meta = ledger.data.get("meta", {})
    summary = ledger.summary()
    if dataset == "quote":
        title = "quote 回填进度台账"
    elif dataset == "baidu":
        title = "baidu 回填进度台账"
    else:
        title = "kline 回填进度台账"
    print(f"\n===== {title} =====")
    print(f"文件:     {progress_path}")
    extra = f"  复权={meta.get('adj_type')}" if meta.get("adj_type") else ""
    print(
        f"区间:     {meta.get('start_date')} ~ {meta.get('end_date')}  "
        f"模式={meta.get('mode')}{extra}"
    )
    print(f"计划总数: {meta.get('total')}   最后更新: {meta.get('updated_at')}")
    print("-" * 30)
    if not summary:
        print("（暂无记录，尚未运行过回填）")
    else:
        for st, n in sorted(summary.items()):
            print(f"  {st:<10} {n}")
    print("========================\n")


def run_quote(args: argparse.Namespace) -> dict:
    from src.services.backfill import QuoteBackfillService

    codes = resolve_codes(args)
    logger.info("准备 quote 回填：%d 只股票", len(codes))
    return QuoteBackfillService().run(
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


def run_kline(args: argparse.Namespace) -> dict:
    from src.services.backfill import KlineBackfillService

    codes = resolve_codes(args)
    logger.info("准备 kline 回填：%d 只股票", len(codes))
    return KlineBackfillService().run(
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
        adj=args.adj,
    )


def run_baidu(args: argparse.Namespace) -> dict:
    from src.services.backfill import BaiduBackfillService

    codes = resolve_codes(args)
    logger.info("准备 baidu 回填：%d 只股票", len(codes))
    return BaiduBackfillService().run(
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
        ktype=args.ktype,
    )


def print_summary(stats: dict, *, dataset: str) -> None:
    if dataset == "quote":
        label = "quote"
        rows_key = "quote_rows"
    elif dataset == "baidu":
        label = "baidu"
        rows_key = "baidu_rows"
    else:
        label = "kline"
        rows_key = "kline_rows"
    print(f"\n===== {label} 回填完成 =====")
    print(f"计划总数: {stats['total']}")
    print(f"实际拉取: {stats['fetched']}")
    print(f"跳过(已最新): {stats['skipped']}")
    print(f"返回为空: {stats['empty']}")
    print(f"失败:     {stats['failed']}")
    print(f"新增 {label} 行: {stats.get(rows_key, stats.get('rows_added', 0))}")
    print("====================\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backfill",
        description="市场日线回填统一入口：quote（不复权截面）与 kline（复权 K 线）",
    )
    sub = parser.add_subparsers(dest="dataset", required=True)

    quote = sub.add_parser(
        "quote",
        help="westock quote --date → stock_daily_quote（慢，40+ 字段）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python backfill.py quote --all --mode range --start 2021-01-01 --end 2021-12-31 \\\n"
            "    --progress data/progress_2021.json --sleep 0.1 --retry 2\n"
        ),
    )
    add_code_source_args(quote)
    add_run_args(
        quote,
        defaults=argparse.Namespace(
            start="2010-01-01",
            mode="full",
            fresh_days=4,
            retry=1,
            sleep=0.1,
            progress=os.path.join("data", "backfill_progress.json"),
        ),
    )
    quote.add_argument("--schedule", type=str, default=None, metavar="HH:MM", help="每日定时回填")
    quote.add_argument("--no-run-immediately", action="store_true", help="定时模式启动时不先跑一次")

    kline = sub.add_parser(
        "kline",
        help="westock kline 整段 → stock_daily_kline（快，OHLCV）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python backfill.py kline --all --mode range --start 2021-01-01 --end 2022-12-31 \\\n"
            "    --progress data/kline_progress_2021_2022.json --retry 2 --adj qfq\n"
        ),
    )
    add_code_source_args(kline)
    add_run_args(
        kline,
        defaults=argparse.Namespace(
            start="2010-01-01",
            mode="full",
            fresh_days=4,
            retry=2,
            sleep=0.0,
            progress=os.path.join("data", "kline_backfill_progress.json"),
        ),
    )
    kline.add_argument(
        "--adj", type=str, default="qfq", choices=["qfq", "hfq", "bfq"],
        help="复权类型（默认 qfq 前复权）",
    )

    baidu = sub.add_parser(
        "baidu",
        help="百度股市通 K 线 → stock_daily_baidu（含换手率/振幅/MA，单表）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python backfill.py baidu --all --mode range --start 2015-01-01 --end 2026-07-03 \\\n"
            "    --progress data/baidu_progress.json --retry 3 --ktype 1\n"
        ),
    )
    add_code_source_args(baidu)
    add_run_args(
        baidu,
        defaults=argparse.Namespace(
            start="2010-01-01",
            mode="full",
            fresh_days=4,
            retry=2,
            sleep=0.0,
            progress=os.path.join("data", "baidu_backfill_progress.json"),
        ),
    )
    baidu.add_argument(
        "--ktype", type=str, default="1", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"],
        help="K 线类型（默认 1=日线；单表内以 ktype 区分，不分表）",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    bootstrap_env()
    force_utf8_stdout()

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    setup_logging(args.debug)

    if args.progress_status:
        print_progress_status(args.progress, dataset=args.dataset)
        return 0

    if args.dataset == "quote" and args.schedule:
        from src.scheduler import run_with_schedule

        def _task() -> None:
            try:
                stats = run_quote(args)
                print_summary(stats, dataset="quote")
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("定时 quote 回填失败: %s", exc)

        logger.info("进入定时 quote 回填：每日 %s。Ctrl+C 退出。", args.schedule)
        run_with_schedule(
            task=_task,
            schedule_time=args.schedule,
            run_immediately=not args.no_run_immediately,
        )
        return 0

    if args.dataset == "baidu":
        runner = run_baidu
    else:
        runner = run_quote if args.dataset == "quote" else run_kline
    stats = runner(args)
    print_summary(stats, dataset=args.dataset)
    return 0
