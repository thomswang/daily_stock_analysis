# -*- coding: utf-8 -*-
"""
=========================================
走势预测 · 回测模块（Prediction Backtest）
=========================================

与项目已有的「策略回测」(src/services/backtest_service.py / core/backtest_engine.py)
完全解耦：本模块只对 *走势预测模型* 做**滚动步进(walk-forward)**历史检验，回答一个
问题——「如果当初每隔几天就用当时能看到的数据训练并预测一次，方向到底准不准、
照着做能不能跑赢买入持有？」

核心原则：**严格防未来函数**
- 第 i 个交易日做预测时，训练集只允许使用「标签已经揭晓」的样本，即
  close[j+h] 必须 <= 当前时点 i（也就是 j <= i-h），绝不使用 i 之后的任何数据。
- 特征本身只用当日及更早数据（build_features 里全是 rolling 回看）。

产出：
- 逐日方向命中率 / 基线 / 上涨精确率
- 非重叠交易的资金曲线（策略 vs 买入持有）与最大回撤、胜率

⚠️ 仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.services.prediction_service import (
    FEATURE_ORDER,
    PredictionError,
    _align_market_close,
    _load_active_model,
    _load_daily_df,
    build_features,
    load_market_df,
    train_model,
)

logger = logging.getLogger(__name__)


class PredictionBacktestService:
    """对单只股票的走势预测做滚动步进回测。"""

    def run(
        self,
        symbol: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        horizon_days: int = 5,
        lookback_days: int = 500,
        retrain_every: int = 5,
        min_train: int = 60,
        threshold: float = 0.5,
        allow_short: bool = False,
        refresh: bool = True,
        use_global_model: bool = False,
        model_name: str = "trend_lr",
        label_mode: str = "absolute",
        algorithm: str = "logistic",
    ) -> Dict[str, Any]:
        if not symbol or not symbol.strip():
            raise PredictionError("股票代码不能为空")

        symbol = symbol.strip()
        horizon = int(max(1, min(horizon_days, 20)))
        lookback_days = int(max(120, min(lookback_days, 1500)))
        retrain_every = int(max(1, min(retrain_every, 60)))
        min_train = int(max(30, min(min_train, 500)))
        threshold = float(min(max(threshold, 0.05), 0.95))
        # relative：检验"是否跑赢大盘"(剔除大盘β)；absolute：绝对涨跌
        label_mode = "relative" if str(label_mode).lower() == "relative" else "absolute"
        algorithm = "lightgbm" if str(algorithm).lower() in ("lightgbm", "lgbm", "gbdt") else "logistic"

        # use_global_model=True：直接用当前“激活的全局模型”逐日打分（不重训），
        # 让回测检验的正是线上激活模型真正使用的模型；否则退回“单票滚动重训”。
        global_model = None
        if use_global_model:
            loaded = _load_active_model(model_name)
            if not loaded:
                raise PredictionError(
                    f"未找到激活的全局模型 {model_name}，无法用全局模型回测；"
                    "请先训练并激活模型，或改用单票滚动重训模式"
                )
            global_model, _rec = loaded

        df, stock_name = _load_daily_df(symbol, lookback_days, refresh=refresh)
        if df is None or df.empty:
            raise PredictionError(f"未获取到 {symbol} 的历史行情数据")

        feats = build_features(df, market_df=load_market_df())
        n = len(feats)
        if n < min_train + horizon + 10:
            raise PredictionError(
                f"有效样本不足（仅 {n} 条），无法回测；请加大回溯天数或更换数据更全的标的"
            )

        close = feats["close"].to_numpy(dtype=float)
        X_all = feats[FEATURE_ORDER].to_numpy(dtype=float)
        date_strs = pd.to_datetime(feats["date"]).dt.strftime("%Y-%m-%d").tolist()

        # 相对口径需要大盘收盘价对齐（沪深300）；absolute 则整段置 NaN 不参与
        if label_mode == "relative":
            mkt_close = _align_market_close(
                feats["date"], load_market_df()
            ).to_numpy(dtype=float)
            if np.isnan(mkt_close).all():
                raise PredictionError(
                    "relative 模式需要大盘指数数据但未加载到；请先跑 "
                    "`python backfill.py baidu --symbols \"000300\" --no-full --end <今天> --browser` 回填沪深300"
                )
        else:
            mkt_close = np.full(n, np.nan, dtype=float)

        def _fwd_target(idx: int) -> float:
            """第 idx 个评估点的"目标收益"：absolute=个股收益；relative=超额收益(个股−大盘)。"""
            stock_ret = close[idx + horizon] / close[idx] - 1.0
            if label_mode == "relative":
                mc0, mc1 = mkt_close[idx], mkt_close[idx + horizon]
                if np.isnan(mc0) or np.isnan(mc1) or mc0 <= 0:
                    return float("nan")
                return stock_ret - (mc1 / mc0 - 1.0)
            return stock_ret

        # 标签：absolute=未来 H 日上涨；relative=未来 H 日跑赢大盘。严格自洽方向检验
        labels = np.zeros(n, dtype=int)
        for j in range(n - horizon):
            tgt = _fwd_target(j)
            labels[j] = 1 if (np.isfinite(tgt) and tgt > 0) else 0

        model = None
        last_train_at = -(10**9)
        prob_by_index: Dict[int, float] = {}
        points: List[Dict[str, Any]] = []
        n_correct = 0
        actual_up_count = 0
        pred_up_count = 0
        pred_up_hit = 0

        first_eval = min_train  # 至少积累 min_train 个样本后才开始

        for i in range(first_eval, n):
            # 只在「实际结果已揭晓」的交易日打分
            if i + horizon >= n:
                break

            di = date_strs[i]
            if start_date and di < start_date:
                continue
            if end_date and di > end_date:
                break  # 日期升序，越界即可停

            # 训练集上界：标签必须在 i 时点前已揭晓（close[j+h] <= i）
            train_upper = i - horizon
            if train_upper < min_train - 1:
                continue

            if global_model is not None:
                model = global_model  # 固定用激活的全局模型，不重训
            elif model is None or (i - last_train_at) >= retrain_every:
                X_tr = X_all[: train_upper + 1]
                y_tr = labels[: train_upper + 1]
                # train_ratio=1.0：用全部可用历史训练（防未来函数已在切片层保证）
                model, _ = train_model(X_tr, y_tr, train_ratio=1.0, algorithm=algorithm)
                last_train_at = i

            up_prob = float(model.predict_proba(X_all[i])[0])
            prob_by_index[i] = up_prob
            direction = "up" if up_prob >= threshold else "down"

            # relative 模式：actual_ret 为超额收益(个股−大盘)，direction="up"=预测跑赢
            actual_ret = _fwd_target(i)
            if not np.isfinite(actual_ret):
                prob_by_index.pop(i, None)
                continue
            actual_up = actual_ret > 0
            correct = (direction == "up") == bool(actual_up)

            if correct:
                n_correct += 1
            if actual_up:
                actual_up_count += 1
            if direction == "up":
                pred_up_count += 1
                if actual_up:
                    pred_up_hit += 1

            points.append(
                {
                    "date": di,
                    "up_probability": round(up_prob, 4),
                    "direction": direction,
                    "actual_return_pct": round(actual_ret * 100, 2),
                    "correct": bool(correct),
                }
            )

        n_pred = len(points)
        if n_pred == 0:
            raise PredictionError(
                "选定区间内没有可评估的样本；请放宽日期区间或减小回溯/步进参数"
            )

        accuracy = n_correct / n_pred
        baseline = max(actual_up_count, n_pred - actual_up_count) / n_pred
        up_precision = (pred_up_hit / pred_up_count) if pred_up_count else None

        equity = self._build_equity(
            close=close,
            date_strs=date_strs,
            prob_by_index=prob_by_index,
            horizon=horizon,
            threshold=threshold,
            allow_short=allow_short,
            start_date=start_date,
            end_date=end_date,
            first_eval=first_eval,
            n=n,
            fwd_target=_fwd_target,
            label_mode=label_mode,
        )

        return {
            "stock_code": symbol,
            "stock_name": stock_name,
            "horizon_days": horizon,
            "lookback_days": lookback_days,
            "retrain_every": retrain_every,
            "threshold": round(threshold, 4),
            "allow_short": allow_short,
            "model_mode": "global" if global_model is not None else "per_stock",
            "label_mode": label_mode,
            "start_date": points[0]["date"],
            "end_date": points[-1]["date"],
            "n_predictions": n_pred,
            "correct": n_correct,
            "accuracy": round(accuracy, 4),
            "baseline_accuracy": round(baseline, 4),
            "up_precision": round(up_precision, 4) if up_precision is not None else None,
            "pred_up_count": pred_up_count,
            "actual_up_ratio": round(actual_up_count / n_pred, 4),
            **equity,
            "points": points,
            "disclaimer": (
                "回测基于历史数据滚动步进检验，过往表现不代表未来收益，不构成任何投资建议。"
                + (
                    "【注意】global 模式复用已训练好的全局模型逐日打分，该模型训练时"
                    "已包含此区间数据，结果偏乐观（属样本内一致性检验）；无偏样本外"
                    "估计请用 per_stock 单票滚动重训模式。"
                    if global_model is not None else ""
                )
            ),
        }

    def _build_equity(
        self,
        *,
        close: np.ndarray,
        date_strs: List[str],
        prob_by_index: Dict[int, float],
        horizon: int,
        threshold: float,
        allow_short: bool,
        start_date: Optional[str],
        end_date: Optional[str],
        first_eval: int,
        n: int,
        fwd_target=None,
        label_mode: str = "absolute",
    ) -> Dict[str, Any]:
        """按非重叠(每 horizon 天一笔)交易构造资金曲线，避免持仓重叠导致收益虚高。

        - absolute：每笔收益=个股收益；benchmark=买入持有。
        - relative：每笔收益=超额收益(个股−大盘，等价 long 个股/short 指数的市场中性)；
          benchmark=始终持有该组合(always 跑赢下注)，用于衡量“择时超额”是否胜过“一直持有超额”。
        """
        eq_strat = 1.0
        eq_bench = 1.0
        curve: List[Dict[str, Any]] = []
        trades = 0
        wins = 0
        peak = 1.0
        max_dd = 0.0

        idx = first_eval
        # 找到第一个落在区间内的评估点
        while idx + horizon < n:
            di = date_strs[idx]
            if start_date and di < start_date:
                idx += 1
                continue
            if end_date and di > end_date:
                break
            prob = prob_by_index.get(idx)
            if prob is None:
                idx += 1
                continue

            ret = fwd_target(idx) if fwd_target is not None else (
                close[idx + horizon] / close[idx] - 1.0
            )
            if not np.isfinite(ret):
                idx += 1
                continue
            if prob >= threshold:
                signal = 1
            elif allow_short:
                signal = -1
            else:
                signal = 0

            if signal != 0:
                trades += 1
                if signal * ret > 0:
                    wins += 1
            eq_strat *= 1.0 + signal * ret
            eq_bench *= 1.0 + ret

            peak = max(peak, eq_strat)
            if peak > 0:
                max_dd = max(max_dd, (peak - eq_strat) / peak)

            curve.append(
                {
                    "date": date_strs[idx + horizon],
                    "strategy": round(eq_strat, 4),
                    "benchmark": round(eq_bench, 4),
                }
            )
            idx += horizon

        return {
            "n_trades": trades,
            "win_rate": round(wins / trades, 4) if trades else None,
            "strategy_return_pct": round((eq_strat - 1.0) * 100, 2),
            "benchmark_return_pct": round((eq_bench - 1.0) * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "equity_curve": curve,
        }
