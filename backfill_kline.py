# -*- coding: utf-8
"""
=========================================
全历史 kline 回填入口（CLI）
=========================================

数据写入 stock_daily_kline（westock kline 整段，默认前复权 qfq）：
  每只股票每个区间 1 次 node 请求，约 8 列 OHLCV。

与 backfill_history.py（quote 逐日 40+ 字段）并行，台账/进度文件分开。

示例：

  export WESTOCK_DATA_DIR=e:/analysis/westock-data

  # 全 A 股，2020 至今
  python backfill_kline.py --all --start 2020-01-01

  # 多进程按年分片（每进程独立 progress）
  python backfill_kline.py --all --mode range --start 2021-01-01 --end 2021-12-31 \\
    --progress data/kline_progress_2021.json

  # 试跑 20 只
  python backfill_kline.py --all --limit 20 --start 2024-01-01

⚠ 数据仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import os
import sys

from src.config import setup_env  # noqa: E402

setup_env()

import argparse  # noqa: E402
import logging  # noqa: E402
from typing import List, Optional  # noqa: E402

logger = logging.getLogger("backfill_kline")


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
    from src.services.backfill.ledger import ProgressLedger

    ledger = ProgressLedger(progress_path)
    meta = ledger.data.get("meta", {})
    summary = ledger.summary()
    print("\n===== kline 回填进度台账 =====")
    print(f"文件:     {progress_path}")
    print(
        f"区间:     {meta.get('start_date')} ~ {meta.get('end_date')}  "
        f"模式={meta.get('mode')}  复权={meta.get('adj_type', 'qfq')}"
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
    from src.services.kline_backfill_service import KlineBackfillService

    service = KlineBackfillService()
    codes = _resolve_codes(args, service)
    logger.info("准备 kline 回填：%d 只股票", len(codes))
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
        adj=args.adj,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="全历史 kline 回填（stock_daily_kline，westock kline 整段）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--all", action="store_true", help="全部 A 股（读 stocks.index.json）")
    parser.add_argument("--from-watchlist", action="store_true", help="使用 .env 的 STOCK_LIST")
    parser.add_argument("--symbols", type=str, help="指定代码，逗号分隔")
    parser.add_argument("--codes-file", type=str, help="从文件读代码")
    parser.add_argument("--index-path", type=str, default=None, help="stocks.index.json 路径")
    parser.add_argument("--start", type=str, default="2010-01-01", help="回填起始日")
    parser.add_argument("--end", type=str, default=None, help="回填结束日（默认今天）")
    parser.add_argument(
        "--mode", type=str,
        choices=["full", "incremental", "smart", "range"], default="full",
    )
    parser.add_argument("--fresh-days", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry", type=int, default=2, help="单只失败重试次数（默认 2）")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.05, help="每只股票请求后 sleep（默认 0.05）")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 只（试跑）")
    parser.add_argument(
        "--progress", type=str,
        default=os.path.join("data", "kline_backfill_progress.json"),
        help="进度台账（多进程须用不同路径，如 data/kline_progress_2021.json）",
    )
    parser.add_argument(
        "--adj", type=str, default="qfq", choices=["qfq", "hfq", "bfq"],
        help="复权类型（默认 qfq 前复权）",
    )
    parser.add_argument("--progress-status", action="store_true", help="只打印进度台账")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def _print_summary(stats: dict) -> None:
    print("\n===== kline 回填完成 =====")
    print(f"计划总数: {stats['total']}")
    print(f"实际拉取: {stats['fetched']}")
    print(f"跳过(已最新): {stats['skipped']}")
    print(f"返回为空: {stats['empty']}")
    print(f"失败:     {stats['failed']}")
    print(f"新增 kline 行: {stats.get('kline_rows', stats.get('rows_added', 0))}")
    print("========================\n")


def main() -> int:
    _force_utf8_stdout()
    args = parse_args()
    _setup_logging(args.debug)

    if args.progress_status:
        _print_progress_status(args.progress)
        return 0

    stats = _run_backfill(args)
    _print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
