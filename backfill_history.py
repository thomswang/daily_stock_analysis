# -*- coding: utf-8 -*-
"""
=========================================
全历史日线数据回填 入口（CLI）
=========================================

数据写入两张表（腾讯专用，无跨平台 failover）：
  - stock_daily        ← Tencent fqkline（OHLCV 时间序列）
  - stock_daily_quote  ← Tencent quote --date（截面：换手率、流通股本等）

默认 --layer all 一次跑 K 线 + quote；也可分层、分进程并行以提高效率。

────────────────────────────────────────────────────────────
一、单进程常用命令
────────────────────────────────────────────────────────────

  # 全 A 股，从 2010 拉到今天（可随时 Ctrl+C 中断，重跑自动续传）
  python backfill_history.py --all --start 2010-01-01

  # 先拿前 20 只试跑
  python backfill_history.py --all --limit 20

  # 只补自选股 / 指定代码
  python backfill_history.py --from-watchlist
  python backfill_history.py --symbols 600519,000001,00700

  # 从文件读代码（每行一个，或逗号分隔）
  python backfill_history.py --codes-file my_codes.txt

  # 渐进式建库（推荐）：先拉近两年，以后把 start 往前推
  python backfill_history.py --all --mode smart --start 2024-07-01
  python backfill_history.py --all --mode smart --start 2020-07-01

  # 日常增量：只补每只票缺的最新一段
  python backfill_history.py --all --mode incremental

  # 只重试之前失败的
  python backfill_history.py --all --retry-failed

  # 查看进度台账
  python backfill_history.py --progress-status

  # 每日定时增量（后台常驻）
  python backfill_history.py --all --mode incremental --schedule 17:30

────────────────────────────────────────────────────────────
二、分层回填（K 线与 quote 解耦）
────────────────────────────────────────────────────────────

  # 只拉 K 线 → stock_daily
  python backfill_history.py --all --layer kline --start 2010-01-01

  # 只拉 quote 截面（换手率等）→ stock_daily_quote
  python backfill_history.py --all --layer quote --start 2010-01-01

  # 两层都拉（默认，与不加 --layer 等价）
  python backfill_history.py --all --layer all --start 2010-01-01

  典型流程：先开进程 A 快速灌 K 线，再开进程 B 慢慢补 quote（或两者同时跑）。

────────────────────────────────────────────────────────────
三、多进程并行（按时间段分片）
────────────────────────────────────────────────────────────

  用 --mode range 精确拉 [start, end]，不受 DB 已有数据“已最新”影响；
  每个进程必须用**独立** --progress 台账，避免互相覆盖断点记录。

  【示例 1】两个终端并行拉 K 线，各负责一段年份：

    # 终端 A
    python backfill_history.py --all --layer kline --mode range \\
      --start 2018-01-01 --end 2019-12-31 \\
      --progress data/kline_2018_2019.json

    # 终端 B
    python backfill_history.py --all --layer kline --mode range \\
      --start 2020-01-01 --end 2021-12-31 \\
      --progress data/kline_2020_2021.json

  【示例 2】K 线与 quote 同时跑（写不同表，效率最高）：

    # 终端 A：K 线全区间
    python backfill_history.py --all --layer kline --mode range \\
      --start 2018-01-01 --end 2024-12-31 \\
      --progress data/kline_full.json

    # 终端 B：quote 2018-2020
    python backfill_history.py --all --layer quote --mode range \\
      --start 2018-01-01 --end 2020-12-31 \\
      --progress data/quote_2018_2020.json --sleep 0.3

    # 终端 C：quote 2021-2024
    python backfill_history.py --all --layer quote --mode range \\
      --start 2021-01-01 --end 2024-12-31 \\
      --progress data/quote_2021_2024.json --sleep 0.3

  【示例 3】按股票代码拆分（把全市场 codes 拆成两个文件）：

    python backfill_history.py --codes-file codes_a.txt --layer kline \\
      --mode range --start 2010-01-01 --progress data/kline_batch_a.json

    python backfill_history.py --codes-file codes_b.txt --layer kline \\
      --mode range --start 2010-01-01 --progress data/kline_batch_b.json

────────────────────────────────────────────────────────────
四、模式说明
────────────────────────────────────────────────────────────

  full         整段拉；若 DB 已够新则跳过（适合单进程首次建库）
  incremental  只补 [last+1, end] 最新缺口（日常维护）
  smart        按 DB 覆盖自动补前后缺口（渐进式扩历史）
  range        精确拉 [start, end]，不跳过（多进程分片专用）

────────────────────────────────────────────────────────────
五、并行注意事项
────────────────────────────────────────────────────────────

  1. 每个进程独立 --progress 文件（必须）
  2. K 线进程 + quote 进程可并行（不同表，推荐）
  3. 同表多进程写 SQLite 可能偶发 database is locked；时间段/代码不重叠时
     通常可接受，或降低并发数 / 换 PostgreSQL
  4. quote 比 K 线慢很多（逐日请求），--sleep 可调小至 0.2~0.3
  5. 环境变量 WESTOCK_DATA_DIR 需指向 westock-data 目录（quote 层依赖）

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
        f"模式={meta.get('mode')}  layer={meta.get('layer', 'all')}"
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
    logger.info("准备回填：%d 只股票，layer=%s", len(codes), args.layer)
    return service.run(
        codes,
        start_date=args.start,
        end_date=args.end,
        mode=args.mode,
        layer=args.layer,
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
    parser.add_argument(
        "--layer", type=str, choices=["all", "kline", "quote"], default="all",
        help="all=K线+quote；kline=仅 stock_daily；quote=仅 stock_daily_quote",
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


def _print_summary(stats: dict, layer: str = "all") -> None:
    print("\n===== 回填完成 =====")
    print(f"layer:    {layer}")
    print(f"计划总数: {stats['total']}")
    print(f"实际拉取: {stats['fetched']}")
    print(f"跳过(已最新): {stats['skipped']}")
    print(f"返回为空: {stats['empty']}")
    print(f"失败:     {stats['failed']}")
    if layer in ("all", "kline"):
        print(f"新增 K 线行数: {stats['rows_added']}")
    if layer in ("all", "quote"):
        print(f"quote 截面: {stats.get('quote_rows', 0)} 行")
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
    _print_summary(stats, layer=args.layer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
