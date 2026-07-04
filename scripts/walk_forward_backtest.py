# -*- coding: utf-8 -*-
"""
================================================================
横截面策略 · 滚动资金曲线回测 (Walk-Forward Backtest)
================================================================

模拟真实上线流程，得到一条"可相信"的样本外资金曲线：

    每月末 → 用"截止当月、且已 purge/embargo"的历史重训横截面 LightGBM
    → 下个月每 H 日按预测概率分五组建仓（多头前20% / 多空 前20%-后20%）
    → 持有 H 日的真实收益串成资金曲线

防未来函数（de Prado《Advances in Financial ML》Ch.7 口径）：
    - 标签=未来 H 日收益，训练只用 date < (调仓日 − H) 的样本（purge 重叠标签）
    - 额外 embargo 缓冲，杜绝序列相关泄露
    - 调仓间隔 = H，收益窗口不重叠

专业评估指标（不以分类准确率为核心）：
    Rank IC / ICIR、分五组收益、多空&多头资金曲线、年化收益、
    最大回撤、夏普、换手率、交易成本后收益。

用法：
    python scripts/walk_forward_backtest.py [--stocks 1000] [--start 2024-01-01]
        [--end 2026-07-01] [--train-days 756] [--horizon 5] [--cost-bps 10]

⚠️ 研究用途，不构成投资建议。
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from src.services.history_backfill_service import HistoryBackfillService
from src.services.model_training_service import MIN_NAMES_PER_DAY
from src.services.prediction_service import (
    FEATURE_ORDER,
    _load_cached_df,
    build_features,
    load_market_df,
    make_forward_return,
    train_model,
)

TRADING_DAYS_YEAR = 252.0


def log(*a):
    print(*a, flush=True)


# ────────────────────────────── 样本池 ──────────────────────────────
def collect_pool(n_stocks: int, horizon: int, seed: int = 7, lookback: int = 1600):
    """构造全市场抽样的横截面样本池：X(特征) / fwd(未来H日收益) / date / code。"""
    codes = HistoryBackfillService().load_all_cn_codes()
    rng = np.random.default_rng(seed)
    if n_stocks and n_stocks > 0:
        codes = list(rng.permutation(codes))[:n_stocks]  # 抽样
    else:
        codes = list(codes)  # n_stocks<=0 → 全市场
    mkt = load_market_df()
    Xs, fwd, dts, cds, cls = [], [], [], [], []
    ok = 0
    for i, code in enumerate(codes):
        try:
            df = _load_cached_df(code, lookback)  # 纯读本地缓存，绝不联网
        except Exception:
            continue
        if df is None or df.empty:
            continue
        f = build_features(df, market_df=mkt)
        if len(f) < 150:
            continue
        fr = make_forward_return(f["close"], horizon=horizon)
        u = f.iloc[:-horizon]
        v = fr.iloc[:-horizon].to_numpy()
        m = ~np.isnan(v)
        if not m.any():
            continue
        Xs.append(u[FEATURE_ORDER].to_numpy(dtype=float)[m])
        fwd.append(v[m])
        dts.append(pd.to_datetime(u["date"]).to_numpy()[m])
        cds.append(np.array([code] * int(m.sum())))
        cls.append(u["close"].to_numpy(dtype=float)[m])
        ok += 1
        if (i + 1) % 150 == 0:
            log(f"  载入 {i+1}/{len(codes)}  有效票 {ok}")
    X = np.vstack(Xs)
    pool = pd.DataFrame({
        "date": np.concatenate(dts),
        "code": np.concatenate(cds),
        "fwd": np.concatenate(fwd),
        "close": np.concatenate(cls),
    })
    pool["row"] = np.arange(len(pool))
    log(f"样本池: {len(pool):,} 条, {ok} 只票, {pool['date'].min().date()} → {pool['date'].max().date()}")
    return X, pool


def xsec_label(fwd: np.ndarray, dts: np.ndarray):
    """同日全市场 top50% 记 1（票数不足的日剔除）。返回 (y, keep_mask)。"""
    fr = pd.DataFrame({"d": dts, "fwd": fwd})
    g = fr.groupby("d")["fwd"]
    pct = g.rank(pct=True, method="average").to_numpy()
    cnt = g.transform("count").to_numpy()
    return (pct > 0.5).astype(float), cnt >= MIN_NAMES_PER_DAY


# ────────────────────────────── 组合权重 ──────────────────────────────
def w_topq(codes, prob, q, longshort):
    """死板分桶：等权买前 q 分位（多空则再等权卖后 q 分位）。返回 {code: weight}。"""
    n = len(prob)
    k = max(int(n * q), 1)
    order = np.argsort(prob)
    w = {c: 1.0 / k for c in codes[order[-k:]]}
    if longshort:
        for c in codes[order[:k]]:
            w[c] = w.get(c, 0.0) - 1.0 / k
    return w


def w_rank(codes, prob, longshort):
    """概率(排名)加权：权重∝去均值后的分位，强的多、弱的空，敞口更分散、换手更低。"""
    r = (pd.Series(prob).rank().to_numpy() - 0.5) / len(prob)  # (0,1)
    s = r - r.mean()
    if longshort:
        denom = np.abs(s).sum() or 1.0
        w = s / denom                       # 多空毛敞口=1
    else:
        pos = np.clip(s, 0.0, None)
        w = pos / (pos.sum() or 1.0)        # 纯多头，权重和=1
    return dict(zip(codes, w))


def _turnover(w_new: dict, w_old: dict) -> float:
    """两期权重变化的绝对值之和（=需要交易的总权重，含买卖两腿）。"""
    keys = set(w_new) | set(w_old)
    return float(sum(abs(w_new.get(k, 0.0) - w_old.get(k, 0.0)) for k in keys))


def _port_ret(w: dict, ret_map: dict) -> float:
    """组合真实持有期收益：Σ 权重×个股持有期收益（无价的票按0处理=平仓）。"""
    return float(sum(wt * ret_map.get(c, 0.0) for c, wt in w.items()))


# ────────────────────────────── 回测主流程 ──────────────────────────────
STRATS = [
    ("top_long", "多头·前20%等权 ", lambda cs, p, q: w_topq(cs, p, q, False)),
    ("top_ls", "多空·前后20%等权", lambda cs, p, q: w_topq(cs, p, q, True)),
    ("rank_long", "多头·概率加权  ", lambda cs, p, q: w_rank(cs, p, False)),
    ("rank_ls", "多空·概率加权  ", lambda cs, p, q: w_rank(cs, p, True)),
]


def run(args):
    X, pool = collect_pool(args.stocks, args.horizon, lookback=args.lookback)
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    H, REBAL = args.horizon, args.rebal
    emb = pd.Timedelta(days=int(H * 1.6))

    # 每只票的 (日期→收盘价) 查表，用于算真实持有期收益
    close_lut = {c: g.set_index("date")["close"] for c, g in pool[["code", "date", "close"]].groupby("code")}

    all_days = np.sort(pool["date"].unique())
    test_days = all_days[(all_days >= np.datetime64(start)) & (all_days < np.datetime64(end))]
    rebal_days = list(test_days[::REBAL])
    log(f"回测区间 {start.date()}~{end.date()}：{len(test_days)} 交易日 → {len(rebal_days)} 次调仓"
        f"（每 {REBAL} 交易日调仓，训练标签前瞻 {H} 日）\n")

    recs = []
    prev_w = {k: {} for k, _, _ in STRATS}
    model, cur_key = None, None

    for idx, d in enumerate(rebal_days):
        d_ts = pd.Timestamp(d)
        rm = max(1, int(args.retrain_months))
        mkey = (d_ts.year * 12 + (d_ts.month - 1)) // rm  # 每 rm 个月重训一次
        if mkey != cur_key:
            tr_hi = d_ts - emb
            tr_lo = d_ts - pd.Timedelta(days=int(args.train_days * 1.5))
            sub = pool[(pool["date"] >= tr_lo) & (pool["date"] < tr_hi)]
            if len(sub) >= 5000:
                y, keep = xsec_label(sub["fwd"].to_numpy(), sub["date"].to_numpy())
                rows = sub["row"].to_numpy()[keep]
                model, _ = train_model(
                    X[rows], y[keep], embargo=H, dates=sub["date"].to_numpy()[keep],
                    algorithm="lightgbm", train_ratio=0.85,
                )
                cur_key = mkey
                log(f"  [{d_ts.date()}] 重训：样本 {len(rows):,}")
        if model is None:
            continue

        day = pool[pool["date"] == d]
        if len(day) < 30:
            continue
        codes = day["code"].to_numpy()
        prob = model.predict_proba(X[day["row"].to_numpy()])

        # 真实持有期收益：从本次调仓日收盘 → 下次调仓日收盘（无下一日则用样本内最后价）
        nxt = rebal_days[idx + 1] if idx + 1 < len(rebal_days) else None
        ret_map = {}
        for c, c0 in zip(codes, day["close"].to_numpy()):
            s = close_lut.get(c)
            if s is None or nxt is None:
                continue
            fut = s[s.index >= nxt]
            if len(fut) and c0 > 0:
                ret_map[c] = float(fut.iloc[0]) / float(c0) - 1.0
        if not ret_map:
            continue
        mkt_ret = float(np.mean(list(ret_map.values())))

        rec = {"date": d_ts, "mkt": mkt_ret, "n": len(codes)}
        rec["ic"] = np.corrcoef(pd.Series(prob).rank(),
                                pd.Series([ret_map.get(c, np.nan) for c in codes]).rank())[0, 1]
        for key, _lbl, fw in STRATS:
            w = fw(codes, prob, args.quantile)
            gross = _port_ret(w, ret_map)
            turn = _turnover(w, prev_w[key])
            net = gross - turn * (args.cost_bps / 10000.0)
            rec[f"{key}_g"], rec[f"{key}_n"], rec[f"{key}_t"] = gross, net, turn
            prev_w[key] = w
        recs.append(rec)

    if not recs:
        log("无有效调仓，退出。")
        return
    report(pd.DataFrame(recs), args)


# ────────────────────────────── 指标与出图 ──────────────────────────────
def _ann(mean_per, std_per, per_year):
    if std_per == 0 or np.isnan(std_per):
        return float("nan")
    return mean_per / std_per * np.sqrt(per_year)


def _mdd(equity):
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1.0).min())


def report(r: pd.DataFrame, args):
    per_year = TRADING_DAYS_YEAR / args.rebal
    ic = r["ic"].to_numpy()
    icir = _ann(np.nanmean(ic), np.nanstd(ic), per_year)

    def stat(name, per_rets):
        eq = np.cumprod(1 + per_rets)
        yrs = len(per_rets) / per_year
        cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 and eq[-1] > 0 else float("nan")
        return name, eq, eq[-1] - 1, cagr, _ann(np.mean(per_rets), np.std(per_rets), per_year), _mdd(eq), float((per_rets > 0).mean())

    eq_mkt = np.cumprod(1 + r["mkt"].to_numpy())
    rows = [("市场·等权基准 ", eq_mkt, eq_mkt[-1] - 1,
             eq_mkt[-1] ** (per_year / len(r)) - 1,
             _ann(r["mkt"].mean(), r["mkt"].std(), per_year), _mdd(eq_mkt),
             float((r["mkt"] > 0).mean()))]
    for key, lbl, _ in STRATS:
        rows.append(stat(lbl, r[f"{key}_n"].to_numpy()))

    log("\n================ 滚动样本外回测（真实持有期收益·扣成本）================")
    log(f"调仓 {len(r)} 次 | 每 {args.rebal} 日调仓 | 抽样 {args.stocks} 只 | 成本 {args.cost_bps}bp/边")
    log(f"Rank IC 均值 {np.nanmean(ic):+.4f} | ICIR(年化) {icir:+.2f} | IC>0 占比 {np.nanmean(ic > 0) * 100:.0f}%")
    log(f"\n{'组合':<16}{'累计':>10}{'年化':>9}{'夏普':>8}{'最大回撤':>11}{'胜率':>7}{'换手/调仓':>11}")
    log("-" * 74)
    turn_map = {key: r[f"{key}_t"].mean() for key, _, _ in STRATS}
    for i, (name, eq, cum, cagr, sh, mdd, win) in enumerate(rows):
        tv = "" if i == 0 else f"{turn_map[STRATS[i-1][0]] * 100:>9.0f}%"
        log(f"{name:<16}{cum*100:>9.2f}%{cagr*100:>8.2f}%{sh:>8.2f}{mdd*100:>10.2f}%{win*100:>6.0f}%{tv:>11}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        dates = r["date"].to_numpy()
        fig, ax = plt.subplots(2, 1, figsize=(11, 8), height_ratios=[3, 1])
        ax[0].plot(dates, eq_mkt, label="Market (eq-weight)", lw=1.5, ls="--", color="gray")
        palette = {"top_long": "tab:blue", "top_ls": "tab:orange",
                   "rank_long": "tab:green", "rank_ls": "tab:red"}
        names_en = {"top_long": "Long top20% (net)", "top_ls": "LS top20% (net)",
                    "rank_long": "Long prob-wt (net)", "rank_ls": "LS prob-wt (net)"}
        for key, _lbl, _ in STRATS:
            ax[0].plot(dates, np.cumprod(1 + r[f"{key}_n"].to_numpy()),
                       label=names_en[key], lw=2, color=palette[key])
        ax[0].axhline(1.0, color="k", lw=0.6)
        ax[0].set_title(f"Walk-Forward Equity (net)  |  {args.start}~{args.end}  |  rebal={args.rebal}d, cost={args.cost_bps}bp")
        ax[0].legend(ncol=2); ax[0].grid(alpha=0.3); ax[0].set_ylabel("Equity (x)")
        ax[1].plot(dates, np.nancumsum(ic), color="tab:green", lw=1.5)
        ax[1].set_title("Cumulative Rank IC"); ax[1].grid(alpha=0.3)
        fig.autofmt_xdate(); fig.tight_layout()
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", f"walk_forward_r{args.rebal}.png")
        fig.savefig(out, dpi=110)
        log(f"\n资金曲线图已保存: {out}")
    except Exception as exc:  # noqa: BLE001
        log(f"绘图失败（忽略）: {exc}")


def parse_args():
    p = argparse.ArgumentParser(description="横截面策略滚动资金曲线回测")
    p.add_argument("--stocks", type=int, default=1000, help="抽样股票数（默认1000；<=0 全市场）")
    p.add_argument("--start", type=str, default="2024-01-01", help="回测开始日")
    p.add_argument("--end", type=str, default="2026-07-01", help="回测结束日")
    p.add_argument("--train-days", type=int, default=756, help="滚动训练窗口(交易日≈日历天*1.5)")
    p.add_argument("--horizon", type=int, default=5, help="训练标签前瞻天数")
    p.add_argument("--lookback", type=int, default=1600,
                   help="每票回溯自然日(默认1600≈到2019；跑2018需≥2600)")
    p.add_argument("--retrain-months", type=int, default=1,
                   help="每几个月重训一次(默认1=月度；长周期建议3=季度)")
    p.add_argument("--rebal", type=int, default=10, help="调仓间隔(交易日，默认10，降换手)")
    p.add_argument("--quantile", type=float, default=0.2, help="分桶分位（默认前后20%）")
    p.add_argument("--cost-bps", type=float, default=10.0, help="单边交易成本(基点，默认10bp=0.1%)")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
