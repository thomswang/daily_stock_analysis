# -*- coding: utf-8 -*-
"""
===================================
走势预测模型 训练入口（CLI）
===================================

把"训练"变成由你掌控的离线任务，与预测请求解耦。

用法示例：
  # 用指定股票训练（逗号分隔）
  python train_model.py --symbols 600519,000001,00700

  # 用自选股列表（.env 的 STOCK_LIST）训练
  python train_model.py --from-watchlist

  # 纯用本地缓存训练，不联网（适合盘后已缓存的场景）
  python train_model.py --from-watchlist --no-refresh

  # 每日定时训练（后台常驻，18:30 触发；Ctrl+C 退出）
  python train_model.py --from-watchlist --schedule 18:30

  # 查看已训练的模型版本
  python train_model.py --list

  # 回滚：把某个历史版本设为激活
  python train_model.py --activate 3

训练产物存入 prediction_models 表并标记激活版本，预测接口会自动加载。
⚠️ 仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import os
import sys

# ── 环境 bootstrap（与 main.py 对齐：加载 .env、可选代理）──
from src.config import setup_env  # noqa: E402

setup_env()
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    _proxy = f"http://{os.getenv('PROXY_HOST', '127.0.0.1')}:{os.getenv('PROXY_PORT', '10809')}"
    os.environ["http_proxy"] = _proxy
    os.environ["https_proxy"] = _proxy

import argparse  # noqa: E402
import logging  # noqa: E402
from typing import List, Optional  # noqa: E402

logger = logging.getLogger("train_model")


def _force_utf8_stdout() -> None:
    """Windows 控制台默认 GBK，重配为 UTF-8 避免打印非 ASCII 时崩溃。"""
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


def _resolve_symbols(args: argparse.Namespace) -> List[str]:
    """解析训练股票列表：--symbols > --from-watchlist > --all。"""
    if args.symbols:
        return [c.strip() for c in args.symbols.split(",") if c.strip()]
    if args.from_watchlist:
        from src.config import get_config

        config = get_config()
        try:
            config.refresh_stock_list()
        except Exception:  # noqa: BLE001
            pass
        codes = list(getattr(config, "stock_list", []) or [])
        if not codes:
            raise SystemExit("自选股列表为空：请在 .env 配置 STOCK_LIST，或改用 --symbols/--all")
        return codes
    if getattr(args, "all", False):
        # 复用回填工具的全市场代码清单加载器（读 stocks.index.json）
        from src.services.backfill import CodeListLoader

        codes = CodeListLoader.load_all_cn_codes(index_path=args.index_path)
        if args.limit:
            codes = codes[: args.limit]
        if not codes:
            raise SystemExit("未能载入全市场代码清单，请检查 stocks.index.json 或用 --symbols")
        return codes
    raise SystemExit("请通过 --symbols / --from-watchlist / --all 指定训练股票")


def _print_summary(summary: dict) -> None:
    print("\n===== 训练完成 =====")
    print(f"模型:     {summary['model_name']} @ {summary['version']}  (id={summary['model_id']})")
    print(f"激活:     {'是' if summary['is_active'] else '否'}")
    _lm = summary.get('label_mode', 'absolute')
    _algo = summary.get('algorithm', 'logistic_regression_gd')
    _lm_txt = {'relative': '跑赢大盘(相对)', 'cross_section': '横截面强势前50%(市场中性)'}.get(_lm, '绝对涨跌')
    print(f"标签口径: {_lm_txt}")
    print(f"算法:     {'LightGBM(梯度提升树)' if _algo == 'lightgbm_gbdt' else '逻辑回归'}")
    print(f"股票数:   {summary['symbol_count']}")
    print(f"总样本:   {summary['total_samples']}  (训练 {summary['train_samples']} / 验证 {summary['valid_samples']})")
    print(f"训练准确率: {_fmt_pct(summary.get('train_accuracy'))}")
    print(f"验证准确率: {_fmt_pct(summary.get('valid_accuracy'))}")
    print(f"基线(猜多数): {_fmt_pct(summary.get('baseline_accuracy'))}")
    print(f"样本日期:  {summary.get('train_start_date')} ~ {summary.get('train_end_date')}")
    print(f"耗时:     {summary.get('elapsed_sec')}s")
    print("====================\n")


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "N/A"


def _run_training(args: argparse.Namespace) -> dict:
    from src.services.model_training_service import ModelTrainingService

    symbols = _resolve_symbols(args)
    logger.info("准备训练：%d 只股票", len(symbols))
    service = ModelTrainingService()
    summary = service.train(
        symbols,
        lookback_days=args.lookback,
        model_name=args.name,
        epochs=args.epochs,
        lr=args.lr,
        horizon=args.horizon,
        threshold=args.threshold,
        set_active=not args.no_active,
        refresh=not args.no_refresh,
        notes=args.notes,
        label_mode=args.label_mode,
        algorithm=args.algorithm,
        train_end=args.train_end,
    )
    _print_summary(summary)
    return summary


def _list_models(args: argparse.Namespace) -> None:
    from src.repositories.prediction_model_repo import PredictionModelRepository

    # 默认列出全部；显式传入非默认 --name 时按名称过滤
    name_filter = args.name if args.name != "trend_lr" else None
    models = PredictionModelRepository().list_models(name=name_filter, limit=args.list_limit)
    if not models:
        print("暂无已训练的模型。先运行一次训练，例如：python train_model.py --from-watchlist")
        return
    print(f"\n{'id':>4}  {'name':<12} {'version':<16} {'active':<6} {'valid_acc':<10} {'samples':<9} created_at")
    print("-" * 88)
    for m in models:
        print(
            f"{m['id']:>4}  {m['name']:<12} {m['version']:<16} "
            f"{'* ON' if m['is_active'] else '':<6} "
            f"{_fmt_pct(m.get('valid_accuracy')):<10} "
            f"{(m.get('train_samples') or 0) + (m.get('valid_samples') or 0):<9} "
            f"{m.get('created_at')}"
        )
    print()


def _activate_model(model_id: int) -> None:
    from src.repositories.prediction_model_repo import PredictionModelRepository

    ok = PredictionModelRepository().set_active(model_id)
    if ok:
        print(f"已将模型 id={model_id} 设为激活版本。")
    else:
        raise SystemExit(f"未找到模型 id={model_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="走势预测模型训练入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--symbols", type=str, help="训练股票代码，逗号分隔，如 600519,000001")
    parser.add_argument("--from-watchlist", action="store_true", help="使用 .env 的 STOCK_LIST 作为训练股票")
    parser.add_argument("--all", action="store_true", help="全市场 A 股（读 stocks.index.json）")
    parser.add_argument("--index-path", type=str, default=None, help="stocks.index.json 路径（默认自动查找）")
    parser.add_argument("--limit", type=int, default=None, help="仅取前 N 只训练（试跑/控内存）")
    parser.add_argument("--lookback", type=int, default=500, help="每只股票回溯天数（默认 500）")
    parser.add_argument("--name", type=str, default="trend_lr", help="模型名（默认 trend_lr）")
    parser.add_argument("--epochs", type=int, default=400, help="训练轮数（默认 400）")
    parser.add_argument("--lr", type=float, default=0.3, help="学习率（默认 0.3）")
    parser.add_argument("--horizon", type=int, default=5, help="标签前瞻天数=预测未来N日方向（默认 5）")
    parser.add_argument("--threshold", type=float, default=0.0, help="记为看涨所需最小未来收益（默认 0=纯方向，如 0.02=需涨超2%%）")
    parser.add_argument("--label-mode", type=str, default="absolute",
                        choices=["absolute", "relative", "cross_section"],
                        help="标签口径：absolute=绝对涨跌(默认)；relative=是否跑赢大盘；"
                             "cross_section=当日横截面强势前50%%(市场中性/纯选股)")
    parser.add_argument("--algorithm", type=str, default="logistic", choices=["logistic", "lightgbm"],
                        help="模型：logistic=逻辑回归(默认)；lightgbm=梯度提升树(学非线性/交互)")
    parser.add_argument("--train-end", type=str, default=None, metavar="YYYY-MM-DD",
                        help="训练截止日：只用该日期之前的样本训练（留出近期做样本外回测）")
    parser.add_argument("--no-active", action="store_true", help="训练后不设为激活版本")
    parser.add_argument("--no-refresh", action="store_true", help="不联网，仅用本地缓存数据训练")
    parser.add_argument("--notes", type=str, default=None, help="模型备注")
    parser.add_argument("--schedule", type=str, default=None, metavar="HH:MM", help="每日定时训练时间，后台常驻")
    parser.add_argument("--no-run-immediately", action="store_true", help="定时模式下启动时不先训练一次")
    parser.add_argument("--list", action="store_true", help="列出已训练的模型版本")
    parser.add_argument("--list-limit", type=int, default=30, help="--list 显示的条数（默认 30）")
    parser.add_argument("--activate", type=int, default=None, metavar="ID", help="将指定 id 的模型设为激活版本")
    parser.add_argument("--debug", action="store_true", help="调试日志")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _force_utf8_stdout()
    _setup_logging(args.debug)

    if args.list:
        _list_models(args)
        return 0

    if args.activate is not None:
        _activate_model(args.activate)
        return 0

    if args.schedule:
        from src.scheduler import run_with_schedule

        symbols = _resolve_symbols(args)  # 提前校验参数

        def _task():
            try:
                _run_training(args)
            except Exception as exc:  # noqa: BLE001 - 定时任务单次失败不应终止调度
                logger.exception("定时训练失败: %s", exc)

        logger.info("进入定时训练模式：每日 %s（股票 %d 只）。Ctrl+C 退出。", args.schedule, len(symbols))
        run_with_schedule(
            task=_task,
            schedule_time=args.schedule,
            run_immediately=not args.no_run_immediately,
        )
        return 0

    _run_training(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
