# -*- coding: utf-8 -*-
"""
=========================================
走势预测 · 回测入口（CLI）
=========================================

对单只股票的走势预测模型做滚动步进(walk-forward)历史检验，严格防未来函数。
与「策略回测」(backtest_service) 无关，命名独立，互不影响。

用法示例：
  # 用本地缓存回测某只票（不联网）
  python predict_backtest.py --symbol 603363.SH --no-refresh

  # 指定评估区间与参数
  python predict_backtest.py --symbol 600519 --start 2024-01-01 --end 2025-01-01 \
      --horizon 5 --lookback 500 --retrain-every 5 --threshold 0.5

  # 允许做空构建资金曲线
  python predict_backtest.py --symbol 000001 --allow-short

⚠️ 回测过往表现不代表未来收益，不构成任何投资建议。
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
from typing import Optional  # noqa: E402

logger = logging.getLogger("predict_backtest")


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.2f}%" if isinstance(v, (int, float)) else "N/A"


def _print_report(r: dict) -> None:
    print("\n===== 预测回测报告 =====")
    print(f"标的:       {r['stock_code']}  {r.get('stock_name') or ''}")
    _mode = r.get("model_mode", "per_stock")
    _lm = r.get("label_mode", "absolute")
    print(f"模型模式:   {'全局模型(样本内)' if _mode == 'global' else '单票滚动重训(样本外)'}")
    print(f"检验口径:   {'跑赢大盘(相对/市场中性)' if _lm == 'relative' else '绝对涨跌'}")
    print(f"评估区间:   {r['start_date']} ~ {r['end_date']}")
    print(f"参数:       horizon={r['horizon_days']}  lookback={r['lookback_days']}  "
          f"retrain_every={r['retrain_every']}  threshold={r['threshold']}  allow_short={r['allow_short']}")
    print("-" * 40)
    _rel = r.get("label_mode") == "relative"
    _up_word = "跑赢" if _rel else "上涨"
    print(f"逐日预测数: {r['n_predictions']}  (命中 {r['correct']})")
    print(f"方向命中率: {_fmt_pct(r['accuracy'])}   基线(猜多数): {_fmt_pct(r['baseline_accuracy'])}")
    print(f"{_up_word}精确率: {_fmt_pct(r['up_precision'])}   实际{_up_word}占比: {_fmt_pct(r['actual_up_ratio'])}")
    print("-" * 40)
    print(f"非重叠交易: {r['n_trades']} 笔   胜率: {_fmt_pct(r['win_rate'])}")
    print(f"策略收益:   {r['strategy_return_pct']}%   买入持有: {r['benchmark_return_pct']}%")
    print(f"最大回撤:   {r['max_drawdown_pct']}%")
    print("========================\n")
    verdict = "跑赢基线 ✅" if r["accuracy"] > r["baseline_accuracy"] else "未跑赢基线（模型方向判断弱于始终猜多数类）"
    print(f"结论: {verdict}")
    print("⚠️ 过往表现不代表未来收益，不构成任何投资建议。\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="走势预测回测入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--symbol", type=str, required=True, help="股票代码，如 603363.SH / 600519 / 00700")
    p.add_argument("--start", type=str, default=None, help="评估起始日 YYYY-MM-DD（含）")
    p.add_argument("--end", type=str, default=None, help="评估结束日 YYYY-MM-DD（含）")
    p.add_argument("--horizon", type=int, default=5, help="未来交易日数（默认 5）")
    p.add_argument("--lookback", type=int, default=500, help="历史回溯天数（默认 500）")
    p.add_argument("--retrain-every", type=int, default=5, help="每隔多少交易日重训（默认 5）")
    p.add_argument("--min-train", type=int, default=60, help="首次预测前最少样本数（默认 60）")
    p.add_argument("--threshold", type=float, default=0.5, help="看涨概率阈值（默认 0.5）")
    p.add_argument("--allow-short", action="store_true", help="资金曲线允许做空")
    p.add_argument("--no-refresh", action="store_true", help="不联网，仅用本地缓存")
    p.add_argument("--global-model", action="store_true",
                   help="用当前激活的全局模型逐日打分(检验线上模型;样本内偏乐观),否则单票滚动重训")
    p.add_argument("--model-name", type=str, default="trend_lr", help="全局模型名(默认 trend_lr)")
    p.add_argument("--label-mode", type=str, default="absolute", choices=["absolute", "relative"],
                   help="检验口径：absolute=绝对涨跌(默认)；relative=是否跑赢大盘(超额收益,市场中性)")
    p.add_argument("--algorithm", type=str, default="logistic", choices=["logistic", "lightgbm"],
                   help="per_stock 模式的模型：logistic(默认)/lightgbm；global 模式以激活模型为准")
    p.add_argument("--debug", action="store_true", help="调试日志")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _force_utf8_stdout()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    from src.services.prediction_backtest_service import PredictionBacktestService
    from src.services.prediction_service import PredictionError

    try:
        result = PredictionBacktestService().run(
            args.symbol,
            start_date=args.start,
            end_date=args.end,
            horizon_days=args.horizon,
            lookback_days=args.lookback,
            retrain_every=args.retrain_every,
            min_train=args.min_train,
            threshold=args.threshold,
            allow_short=args.allow_short,
            refresh=not args.no_refresh,
            use_global_model=args.global_model,
            model_name=args.model_name,
            label_mode=args.label_mode,
            algorithm=args.algorithm,
        )
    except PredictionError as exc:
        raise SystemExit(f"回测失败：{exc}")

    _print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
