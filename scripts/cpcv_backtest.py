# -*- coding: utf-8 -*-
"""
================================================================
组合 Purged 交叉验证 (CPCV) —— 策略稳健性背书
================================================================

回答一个问题：walk-forward 那条"跑赢市场"的曲线，是稳定能力还是过拟合运气？

做法（de Prado《Advances in Financial ML》Ch.12）：
    1. 把时间轴切成 N 个连续块；
    2. 穷举「取 K 块做测试、其余训练」的所有 C(N,K) 组合；
    3. 每个组合：训练集对测试块做 purge(去重叠标签)+embargo(隔离缓冲)，
       训练横截面 LightGBM，在测试块上跑「概率加权多头/多空」策略；
    4. 汇总得到 OOS 夏普 / IC 的**分布**（几十条路径），
       看是否**一致为正**——一致 → 稳健；忽正忽负 → 过拟合。

输出：各组合 OOS 的 IC、概率加权多头超额夏普、市场中性多空夏普 的分布，
      以及"跑赢基准/正收益"的路径占比。

⚠️ 研究用途，不构成投资建议。
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.services.prediction_service import train_model
# 复用 walk-forward 里的样本池/标签/权重构造，保证口径一致
from scripts.walk_forward_backtest import collect_pool, w_rank, xsec_label  # noqa: E402

TRADING_DAYS_YEAR = 252.0


def log(*a):
    print(*a, flush=True)


def _sharpe(rets, per_year):
    rets = np.asarray(rets, dtype=float)
    rets = rets[~np.isnan(rets)]
    if len(rets) < 3 or rets.std() == 0:
        return float("nan")
    return float(rets.mean() / rets.std() * np.sqrt(per_year))


def run(args):
    X, pool = collect_pool(args.stocks, args.horizon, lookback=args.lookback)
    H = args.horizon
    per_year = TRADING_DAYS_YEAR / H
    emb_days = int(H * 1.6)

    # 只在评估区间内切块（保证每块都有足够历史可训）
    start = pd.Timestamp(args.start)
    days = np.sort(pool["date"].unique())
    days = days[days >= np.datetime64(start)]
    N, K = args.blocks, args.test_blocks
    blocks = np.array_split(days, N)
    block_span = [(pd.Timestamp(b[0]).date(), pd.Timestamp(b[-1]).date()) for b in blocks]
    combos = list(itertools.combinations(range(N), K))
    log(f"CPCV：{N} 块 × 每次测 {K} 块 = {len(combos)} 个组合；"
        f"评估区间 {block_span[0][0]} ~ {block_span[-1][1]}\n")

    date_arr = pool["date"].to_numpy()
    res = []  # 每组合一条：ic / long_excess_sharpe / ls_sharpe / long_ex_mean

    for ci, combo in enumerate(combos):
        test_days = np.concatenate([blocks[i] for i in combo])
        test_set = set(test_days.tolist())
        test_min, test_max = test_days.min(), test_days.max()
        # purge+embargo：训练样本须远离测试块（标签前瞻 + 缓冲）
        buf = np.timedelta64(emb_days, "D")
        in_test = np.isin(date_arr, test_days)
        near_test = (date_arr >= test_min - buf) & (date_arr <= test_max + buf)
        train_mask = ~near_test
        if train_mask.sum() < 20000:
            continue

        sub = pool[train_mask]
        y, keep = xsec_label(sub["fwd"].to_numpy(), sub["date"].to_numpy())
        rows = sub["row"].to_numpy()[keep]
        model, _ = train_model(
            X[rows], y[keep], embargo=H, dates=sub["date"].to_numpy()[keep],
            algorithm="lightgbm", train_ratio=0.85,
        )

        # 测试块上：逐日跑「概率加权多头/多空」，收益用未来 H 日收益(fwd)
        test = pool[in_test]
        ic_list, long_ex_list, ls_list = [], [], []
        for d, g in test.groupby("date"):
            if len(g) < 30:
                continue
            prob = model.predict_proba(X[g["row"].to_numpy()])
            fwd = g["fwd"].to_numpy()
            codes = g["code"].to_numpy()
            wl = w_rank(codes, prob, longshort=False)
            wls = w_rank(codes, prob, longshort=True)
            long_ret = sum(wt * f for wt, f in zip(wl.values(), fwd))
            ls_ret = sum(wt * f for wt, f in zip(wls.values(), fwd))
            long_ex_list.append(long_ret - float(fwd.mean()))
            ls_list.append(ls_ret)
            ic_list.append(np.corrcoef(pd.Series(prob).rank(), pd.Series(fwd).rank())[0, 1])
        if not ic_list:
            continue
        res.append({
            "ic": float(np.nanmean(ic_list)),
            "long_ex_sharpe": _sharpe(long_ex_list, per_year),
            "ls_sharpe": _sharpe(ls_list, per_year),
            "long_ex_mean": float(np.nanmean(long_ex_list)),
        })
        log(f"  组合 {ci+1}/{len(combos)} 测块{combo}: IC {res[-1]['ic']:+.4f} | "
            f"多头超额夏普 {res[-1]['long_ex_sharpe']:+.2f} | 多空夏普 {res[-1]['ls_sharpe']:+.2f}")

    if not res:
        log("无有效组合。")
        return
    report(pd.DataFrame(res), args, per_year)


def _dist(name, x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    return (f"{name:<18} 均值 {x.mean():+.4f}  中位 {np.median(x):+.4f}  "
            f"区间[{x.min():+.3f},{x.max():+.3f}]  >0占比 {(x > 0).mean()*100:.0f}%")


def report(r: pd.DataFrame, args, per_year):
    log("\n================ CPCV 稳健性分布（跨所有组合的样本外）================")
    log(f"有效组合 {len(r)} 个 | 抽样 {args.stocks} 只 | 标签前瞻 {args.horizon} 日")
    log(_dist("Rank IC", r["ic"]))
    log(_dist("多头超额·夏普", r["long_ex_sharpe"]))
    log(_dist("多空(中性)·夏普", r["ls_sharpe"]))
    log(_dist("多头超额·均值/日", r["long_ex_mean"]))

    ic_pos = (r["ic"] > 0).mean()
    lx_pos = (r["long_ex_sharpe"] > 0).mean()
    verdict = ("稳健：多数路径样本外为正，非单一行情侥幸"
               if ic_pos >= 0.8 and lx_pos >= 0.7 else
               "偏弱/存疑：正收益路径占比不足，边际信号，需谨慎")
    log(f"\n结论：IC>0 路径 {ic_pos*100:.0f}%，多头超额夏普>0 路径 {lx_pos*100:.0f}% → {verdict}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(13, 4))
        for a, col, ttl in zip(
            ax, ["ic", "long_ex_sharpe", "ls_sharpe"],
            ["Rank IC", "Long-excess Sharpe", "Long-short Sharpe"],
        ):
            a.hist(r[col].dropna(), bins=12, color="tab:blue", alpha=0.8, edgecolor="k")
            a.axvline(0, color="r", lw=1.2, ls="--")
            a.set_title(f"{ttl}\n(mean {r[col].mean():+.3f}, >0 {(r[col]>0).mean()*100:.0f}%)")
            a.grid(alpha=0.3)
        fig.suptitle(f"CPCV OOS Distribution  |  {args.blocks} blocks, test {args.test_blocks}  |  {len(r)} paths")
        fig.tight_layout()
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "cpcv_distribution.png")
        fig.savefig(out, dpi=110)
        log(f"\n分布图已保存: {out}")
    except Exception as exc:  # noqa: BLE001
        log(f"绘图失败（忽略）: {exc}")


def parse_args():
    p = argparse.ArgumentParser(description="CPCV 策略稳健性背书")
    p.add_argument("--stocks", type=int, default=1000, help="抽样股票数；<=0 全市场")
    p.add_argument("--start", type=str, default="2022-01-01", help="评估区间起点(之前的数据仅供训练)")
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--lookback", type=int, default=1600,
                   help="每票回溯自然日(默认1600≈到2019；跑2018需≥2600)")
    p.add_argument("--blocks", type=int, default=8, help="时间分块数 N")
    p.add_argument("--test-blocks", type=int, default=2, help="每组合测试块数 K")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
