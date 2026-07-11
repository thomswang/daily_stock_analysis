# -*- coding: utf-8 -*-
"""
===================================
选股强弱榜 预计算入口（CLI）
===================================

扫描全市场，用已激活的横截面模型(trend_xsec)给每只票打「强弱分」，落库为当日
快照(stock_rank_snapshot)。之后 /api/v1/prediction/recommendations 秒级读取，
支持全市场榜与行业榜。

用法示例：
  # 全市场预计算一次（纯本地缓存，不联网）
  python rank_snapshot.py

  # 试跑：仅前 300 只
  python rank_snapshot.py --limit 300

  # 每日定时预计算（后台常驻，17:30 触发；Ctrl+C 退出）
  python rank_snapshot.py --schedule 17:30

前置：先训练并激活横截面模型：
  python train_model.py --all --label-mode cross_section --algorithm lightgbm --name trend_xsec
⚠️ 仅供技术研究，不构成任何投资建议。
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

logger = logging.getLogger("rank_snapshot")


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _run(args: argparse.Namespace) -> dict:
    from src.services.stock_ranking_service import StockRankingService

    summary = StockRankingService().compute_snapshot(
        model_name=args.name,
        model_id=args.model_id,
        lookback_days=args.lookback,
        limit=args.limit,
    )
    print("\n===== 强弱榜预计算完成（已登记为不可变 run） =====")
    print(f"run_id:   {summary['run_id']}  ← 后续可用 --run-id 回溯本次榜单")
    print(f"打分日:   {summary['as_of_date']}")
    print(f"模型:     {summary['model_name']} @ {summary['model_version']}")
    print(f"打分股票: {summary['scored']} 只")
    print(f"落库记录: {summary['written']} 条（每行业前 20）")
    print(f"行业覆盖: {summary['industries']} 个")
    print("================================================\n")
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="选股强弱榜预计算入口")
    p.add_argument("--name", type=str, default="trend_xsec", help="横截面模型名（默认 trend_xsec）")
    p.add_argument("--model-id", type=int, default=None, dest="model_id",
                   help="精确指定模型版本 id 来打分（覆盖 --name 的激活版；用 train_model.py --list 查 id）")
    p.add_argument("--lookback", type=int, default=250, help="每只票特征回溯天数（默认 250）")
    p.add_argument("--limit", type=int, default=None, help="仅打分前 N 只（试跑/控内存）")
    p.add_argument("--schedule", type=str, default=None, metavar="HH:MM", help="每日定时预计算时间，后台常驻")
    p.add_argument("--no-run-immediately", action="store_true", help="定时模式下启动时不先算一次")
    p.add_argument("--debug", action="store_true", help="调试日志")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _force_utf8_stdout()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.schedule:
        from src.scheduler import run_with_schedule

        def _task():
            try:
                _run(args)
            except Exception as exc:  # noqa: BLE001 - 单次失败不应终止调度
                logger.exception("定时预计算失败: %s", exc)

        logger.info("进入定时预计算模式：每日 %s。Ctrl+C 退出。", args.schedule)
        run_with_schedule(
            task=_task,
            schedule_time=args.schedule,
            run_immediately=not args.no_run_immediately,
        )
        return 0

    _run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
