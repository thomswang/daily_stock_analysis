# -*- coding: utf-8 -*-
"""
================================================================
周度 Top-N 选股回测（周一开盘买入 / 周五收盘卖出）
================================================================

策略规则（用户设定）：
    每周选排名靠前的前 N 只 → 周一集合竞价(≈开盘价)买入 → 持有至本周五收盘卖出
    → 下周一换仓重选。等权持有。

严格防未来函数（这是回测不骗自己的关键）：
    - 选股信号只用「周一前最后一个交易日(上周五)」及更早的数据；
    - 横截面模型只用「信号日 − embargo」之前的样本训练(purge)，月度重训；
    - 买入价=周一开盘、卖出价=周五收盘，单周收益=周五收盘/周一开盘−1（真实成交口径）。

其他贴近实盘的处理：
    - 默认剔除 ST/退市风险股(名称含 ST)；
    - 按每周持仓变动扣交易成本(买卖两腿)；
    - 基准=同规则下「等权全市场」周收益；
    - 停牌(零成交)/一字涨跌停无法成交的票自动跳过；
    - 训练标签与回测执行口径完全对齐(周度 open→close 真实交易收益横截面排名)。

用法：
    python scripts/weekly_topn_backtest.py [--stocks 1500] [--top-n 20]
        [--start 2023-01-01] [--end 2026-07-01] [--cost-bps 15] [--keep-st]
        [--by-year] [--top-pct 0.5]

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

from src.services.backfill import CodeListLoader
from src.services.model_training_service import MIN_NAMES_PER_DAY
from src.services.prediction_service import (
    FEATURE_ORDER,
    _load_cached_df,
    build_features,
    load_market_df,
    train_model,
)

WEEKS_YEAR = 52.0


def log(*a):
    print(*a, flush=True)


def collect_pool(n_stocks, horizon, keep_st, seed=7, lookback=3200):
    """构造样本池：X(信号日特征) + fwd(下周开盘到周末收盘收益) + OHLCV + date/code。"""
    codes = CodeListLoader.load_all_cn_codes()
    name_map = CodeListLoader.load_cn_name_map()
    rng = np.random.default_rng(seed)
    if n_stocks and n_stocks > 0:
        codes = list(rng.permutation(codes))[:n_stocks]  # 抽样
    else:
        codes = list(codes)  # n_stocks<=0 → 全市场
    mkt = load_market_df()
    Xs, dts, cds, ops, cls, hgs, lws, vls = [], [], [], [], [], [], [], []
    ok = 0
    for i, code in enumerate(codes):
        if not keep_st and "ST" in (name_map.get(code.upper(), "")).upper():
            continue
        try:
            df = _load_cached_df(code, lookback)  # 纯读本地缓存，绝不联网
        except Exception:
            continue
        if df is None or df.empty or "open" not in df.columns:
            continue
        f = build_features(df, market_df=mkt)
        if len(f) < 150:
            continue
        # 把 OHLCV 对齐到特征行(按 date 合并)
        ohlcv = df[["date", "open", "high", "low", "volume"]].copy()
        ohlcv["date"] = pd.to_datetime(ohlcv["date"])
        fx = f.copy()
        fx["date"] = pd.to_datetime(fx["date"])
        fx = fx.merge(ohlcv, on="date", how="left")
        u = fx
        m = ~u["open"].isna().to_numpy()
        if not m.any():
            continue
        Xs.append(u[FEATURE_ORDER].to_numpy(dtype=float)[m])
        dts.append(u["date"].to_numpy()[m])
        cds.append(np.array([code] * int(m.sum())))
        ops.append(u["open"].to_numpy(dtype=float)[m])
        cls.append(u["close"].to_numpy(dtype=float)[m])
        hgs.append(u["high"].to_numpy(dtype=float)[m])
        lws.append(u["low"].to_numpy(dtype=float)[m])
        vls.append(u["volume"].to_numpy(dtype=float)[m])
        ok += 1
        if (i + 1) % 200 == 0:
            log(f"  载入 {i+1}/{len(codes)}  有效票{ok}")
    if not Xs:
        raise RuntimeError("没有可用样本，请检查本地行情缓存和股票池")
    X = np.vstack(Xs)
    pool = pd.DataFrame({
        "date": np.concatenate(dts), "code": np.concatenate(cds),
        "open": np.concatenate(ops), "close": np.concatenate(cls),
        "high": np.concatenate(hgs), "low": np.concatenate(lws),
        "volume": np.concatenate(vls),
    })
    pool["row"] = np.arange(len(pool))
    pool = attach_weekly_trade_labels(pool)
    pool = pool[~pool["fwd"].isna()].reset_index(drop=True)
    X = X[pool["row"].to_numpy()]
    pool["row"] = np.arange(len(pool))
    log(f"样本池 {len(pool):,} 条, {ok} 只票, {pool['date'].min().date()} → {pool['date'].max().date()}"
        f"（{'含' if keep_st else '剔除'} ST）")
    return X, pool


def attach_weekly_trade_labels(pool):
    """给每个信号日样本贴真实周度交易标签：下一交易日开盘买，入场周最后交易日收盘卖。"""
    pool = pool.copy()
    pool["date"] = pd.to_datetime(pool["date"])
    all_days = np.sort(pool["date"].unique())
    if len(all_days) < 2:
        pool["label_entry"] = pd.NaT
        pool["label_exit"] = pd.NaT
        pool["fwd"] = np.nan
        return pool

    days_df = pd.DataFrame({"day": pd.to_datetime(all_days)})
    iso = days_df["day"].dt.isocalendar()
    days_df["week_key"] = iso["year"].astype(int) * 100 + iso["week"].astype(int)
    week_exit = days_df.groupby("week_key")["day"].max().to_dict()

    entries, exits = [], []
    for i, _day in enumerate(days_df["day"]):
        if i + 1 >= len(days_df):
            entries.append(pd.NaT)
            exits.append(pd.NaT)
            continue
        entry = days_df.loc[i + 1, "day"]
        entry_key = int(days_df.loc[i + 1, "week_key"])
        entries.append(entry)
        exits.append(week_exit.get(entry_key, pd.NaT))

    signal_map = pd.DataFrame({
        "date": days_df["day"],
        "label_entry": entries,
        "label_exit": exits,
    })
    pool = pool.merge(signal_map, on="date", how="left")

    px = pool[["code", "date", "open", "close"]].drop_duplicates(["code", "date"])
    entry_px = px[["code", "date", "open"]].rename(
        columns={"date": "label_entry", "open": "entry_open"}
    )
    exit_px = px[["code", "date", "close"]].rename(
        columns={"date": "label_exit", "close": "exit_close"}
    )
    pool = pool.merge(entry_px, on=["code", "label_entry"], how="left")
    pool = pool.merge(exit_px, on=["code", "label_exit"], how="left")
    valid = pool["entry_open"].astype(float) > 0
    pool["fwd"] = np.where(
        valid,
        pool["exit_close"].astype(float) / pool["entry_open"].astype(float) - 1.0,
        np.nan,
    )
    return pool

def xsec_label(fwd, dts, top_pct=0.5):
    """横截面排名标签：同一信号日内按 fwd 排名，前 top_pct 记为正样本(1)。"""
    fr = pd.DataFrame({"d": dts, "fwd": fwd})
    g = fr.groupby("d")["fwd"]
    pct = g.rank(pct=True, method="average").to_numpy()
    cnt = g.transform("count").to_numpy()
    return (pct > (1.0 - top_pct)).astype(float), cnt >= MIN_NAMES_PER_DAY


# ---- 组合构建：选股（排名缓冲 + 行业分散上限）与收益/换手 ----
def _pick(order_codes, prev_names, top_n, keep_rank, cap, ind_map):
    """按排名选 top_n；keep_rank>0 启用换手缓冲，cap>0 启用行业分散上限。"""
    picks, ind_cnt = [], {}

    def try_add(c):
        if len(picks) >= top_n or c in picks:
            return
        ind = ind_map.get(c.upper()) if ind_map else None
        if cap and ind and ind_cnt.get(ind, 0) >= cap:
            return
        picks.append(c)
        if ind:
            ind_cnt[ind] = ind_cnt.get(ind, 0) + 1

    if keep_rank:  # 先保留仍在 keep_rank 内的老仓（降换手）
        keep_set = set(order_codes[:keep_rank])
        for c in order_codes:
            if c in prev_names and c in keep_set:
                try_add(c)
    for c in order_codes:  # 其余按排名从头部补足
        try_add(c)
    return picks


# ---- 停牌/涨跌停建模 ----
def _can_buy(open_lut, high_lut, vol_lut, close_lut, code, entry):
    """检查 entry 日能否以开盘价买入（停牌/一字涨停无法买入）。"""
    vol_s = vol_lut.get(code)
    if vol_s is None:
        return False
    vol = vol_s[vol_s.index == entry]
    if len(vol) == 0 or float(vol.iloc[0]) <= 0:
        return False  # 停牌（零成交）
    op_s = open_lut.get(code)
    hi_s = high_lut.get(code)
    if op_s is None or hi_s is None:
        return False
    op = op_s[op_s.index == entry]
    hi = hi_s[hi_s.index == entry]
    if len(op) == 0 or len(hi) == 0:
        return False
    op_v, hi_v = float(op.iloc[0]), float(hi.iloc[0])
    if op_v <= 0:
        return False
    # 一字涨停：开盘=最高 且 相对前收涨幅>=9.5% → 无法买入
    if op_v == hi_v:
        cl_s = close_lut.get(code)
        if cl_s is not None:
            prev = cl_s[cl_s.index < entry]
            if len(prev) > 0 and float(prev.iloc[-1]) > 0:
                if op_v / float(prev.iloc[-1]) >= 1.095:
                    return False
    return True


def _can_sell(close_lut, low_lut, vol_lut, code, exit_):
    """检查 exit 日能否以收盘价卖出（停牌/一字跌停无法卖出）。"""
    vol_s = vol_lut.get(code)
    if vol_s is None:
        return False
    vol = vol_s[vol_s.index == exit_]
    if len(vol) == 0 or float(vol.iloc[0]) <= 0:
        return False  # 停牌
    cl_s = close_lut.get(code)
    lo_s = low_lut.get(code)
    if cl_s is None or lo_s is None:
        return False
    cl = cl_s[cl_s.index == exit_]
    lo = lo_s[lo_s.index == exit_]
    if len(cl) == 0 or len(lo) == 0:
        return False
    cl_v, lo_v = float(cl.iloc[0]), float(lo.iloc[0])
    if cl_v <= 0:
        return False
    # 一字跌停：收盘=最低 且 相对前收跌幅>=9.5% → 无法卖出
    if cl_v == lo_v:
        prev = cl_s[cl_s.index < exit_]
        if len(prev) > 0 and float(prev.iloc[-1]) > 0:
            if cl_v / float(prev.iloc[-1]) <= 0.905:
                return False
    return True


def _ret(weights, entry, exit_, use_open, open_lut, close_lut, vol_lut, high_lut, low_lut):
    """给定 {code:w}，入场=开盘/收盘、出场=持有期末收盘，返回组合收益。

    含停牌/涨跌停过滤：无法成交的票跳过，权重按可成交票归一化。
    """
    tot, wsum = 0.0, 0.0
    for c, w in weights.items():
        if not _can_buy(open_lut, high_lut, vol_lut, close_lut, c, entry):
            continue
        if not _can_sell(close_lut, low_lut, vol_lut, c, exit_):
            continue
        cl = close_lut.get(c)
        ep_ser = (open_lut if use_open else close_lut).get(c)
        if cl is None or ep_ser is None:
            continue
        ep = ep_ser[ep_ser.index == entry]
        xp = cl[cl.index == exit_]
        if len(ep) == 0 or len(xp) == 0 or float(ep.iloc[0]) <= 0:
            continue
        tot += w * (float(xp.iloc[0]) / float(ep.iloc[0]) - 1.0)
        wsum += w
    return tot / wsum if wsum > 0 else 0.0


def _turnover(w_new, w_old):
    codes = set(w_new) | set(w_old)
    return sum(abs(w_new.get(c, 0.0) - w_old.get(c, 0.0)) for c in codes)


def _load_ind_map():
    try:
        from src.repositories.stock_industry_repo import StockIndustryRepository
        return StockIndustryRepository().get_map()  # upper code -> industry
    except Exception as exc:  # noqa: BLE001
        log(f"行业映射载入失败（行业分散将不生效）: {exc}")
        return {}


# 待对比口径：name -> 配置。hold=持有周数(1周/2周/4周≈月)，cap=每行业最多几只
def build_configs(args):
    return {
        "原:周调·前20":        dict(hold=1, mode="eq",    buf=False, cap=0),
        "周调·缓冲·行业≤3":     dict(hold=1, mode="eq",    buf=True,  cap=3),
        "双周·缓冲·行业≤3":     dict(hold=2, mode="eq",    buf=True,  cap=3),
        "月度·缓冲·行业≤3":     dict(hold=4, mode="eq",    buf=True,  cap=3),
        "双周·概率加权50·行业≤3": dict(hold=2, mode="probw", buf=True,  cap=3),
    }


ASCII_MAP = {
    "原:周调·前20": "W1 Top20 (orig)", "周调·缓冲·行业≤3": "W1 buf+indcap",
    "双周·缓冲·行业≤3": "W2 buf+indcap", "月度·缓冲·行业≤3": "W4 buf+indcap",
    "双周·概率加权50·行业≤3": "W2 probW+indcap",
}


def run(args):
    X, pool = collect_pool(args.stocks, args.horizon, args.keep_st, lookback=args.lookback)
    ind_map = _load_ind_map()
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    # 训练过滤不再用自然日估算 embargo，而是逐样本检查 label_exit 是否早于信号日。

    open_lut = {c: g.set_index("date")["open"] for c, g in pool[["code", "date", "open"]].groupby("code")}
    close_lut = {c: g.set_index("date")["close"] for c, g in pool[["code", "date", "close"]].groupby("code")}
    high_lut = {c: g.set_index("date")["high"] for c, g in pool[["code", "date", "high"]].groupby("code")}
    low_lut = {c: g.set_index("date")["low"] for c, g in pool[["code", "date", "low"]].groupby("code")}
    vol_lut = {c: g.set_index("date")["volume"] for c, g in pool[["code", "date", "volume"]].groupby("code")}
    luts = (open_lut, close_lut, vol_lut, high_lut, low_lut)

    all_days = np.sort(pool["date"].unique())
    iso = pd.Series(pd.to_datetime(all_days)).dt.isocalendar()
    wk_key = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).to_numpy()

    weeks = []
    for k in np.unique(wk_key):
        wdays = all_days[wk_key == k]
        entry, exit_ = wdays.min(), wdays.max()
        if start <= pd.Timestamp(entry) < end:
            weeks.append((pd.Timestamp(entry), pd.Timestamp(exit_)))
    log(f"回测区间 {start.date()}~{end.date()}：{len(weeks)} 个交易周\n")

    # ── 阶段1（贵）：逐周打分，缓存每周的排名与概率（只算一次）──
    week_recs = []
    model, cur_key = None, None
    for entry, exit_ in weeks:
        prior = all_days[all_days < np.datetime64(entry)]
        if len(prior) == 0:
            continue
        signal_day = prior.max()
        rm = max(1, int(args.retrain_months))
        mkey = (entry.year * 12 + (entry.month - 1)) // rm  # 每 rm 个月重训一次
        if mkey != cur_key:
            sig_ts = pd.Timestamp(signal_day)
            tr_lo = sig_ts - pd.Timedelta(days=int(args.train_days * 1.5))
            sub = pool[(pool["date"] >= tr_lo) & (pool["label_exit"] < sig_ts)]
            if len(sub) >= 5000:
                y, keep = xsec_label(sub["fwd"].to_numpy(), sub["date"].to_numpy(), top_pct=args.top_pct)
                rows = sub["row"].to_numpy()[keep]
                model, _ = train_model(
                    X[rows], y[keep], embargo=args.horizon, dates=sub["date"].to_numpy()[keep],
                    algorithm="lightgbm", train_ratio=0.85,
                )
                cur_key = mkey
                log(f"  [{entry.date()}] 重训：样本 {len(rows):,}")
        if model is None:
            continue
        sig = pool[pool["date"] == signal_day]
        if len(sig) < 50:
            continue
        prob = model.predict_proba(X[sig["row"].to_numpy()])
        sig_codes = sig["code"].to_numpy()
        order = np.argsort(-prob)
        week_recs.append({
            "entry": entry, "exit": exit_,
            "codes": list(sig_codes[order]),
            "prob": dict(zip(sig_codes, prob)),
        })
    log(f"\n已缓存 {len(week_recs)} 周排名，开始模拟各调仓口径...")

    # ── 阶段2（廉价）：从缓存排名模拟多种调仓/分散口径 ──
    cost = args.cost_bps / 10000.0
    configs = build_configs(args)
    results = {}
    for name, cfg in configs.items():
        results[name] = _simulate(week_recs, cfg, luts, ind_map, args, cost)
    report(results, args)


def _simulate(week_recs, cfg, luts, ind_map, args, cost):
    """按持有周数 hold 换仓，返回每期净收益/日期/换手/同期基准。"""
    open_lut, close_lut, vol_lut, high_lut, low_lut = luts
    H = cfg["hold"]
    rets, dates, turns, bench = [], [], [], []
    prev_w = {}
    i, n = 0, len(week_recs)
    while i < n:
        rec = week_recs[i]
        j = min(i + H - 1, n - 1)
        entry, exit_ = rec["entry"], week_recs[j]["exit"]
        codes, prob_map = rec["codes"], rec["prob"]

        if cfg["mode"] == "probw":
            picks = _pick(codes, set(prev_w), args.probw_k,
                          args.keep_rank if cfg["buf"] else 0, cfg["cap"], ind_map)
            raw = {c: max(prob_map[c] - 0.5, 0.0) for c in picks}
            s = sum(raw.values()) or 1.0
            w = {c: v / s for c, v in raw.items() if v > 0}
        else:
            picks = _pick(codes, set(prev_w), args.top_n,
                          args.keep_rank if cfg["buf"] else 0, cfg["cap"], ind_map)
            w = {c: 1.0 / len(picks) for c in picks} if picks else {}
        if not w:
            i = j + 1
            continue
        gross = _ret(w, entry, exit_, True, open_lut, close_lut, vol_lut, high_lut, low_lut)
        turn = _turnover(w, prev_w)
        prev_w = w
        rets.append(gross - turn * cost)
        turns.append(turn)
        dates.append(entry)
        bench.append(_ret({c: 1.0 for c in codes}, entry, exit_, True,
                          open_lut, close_lut, vol_lut, high_lut, low_lut))
        i = j + 1
    return {"ret": np.array(rets), "date": np.array(dates),
            "turn": np.array(turns), "bench": np.array(bench)}


def _mdd(equity):
    peak = np.maximum.accumulate(equity)
    return float((equity / peak - 1.0).min())


def _ann(res, per_year):
    r = res["ret"]
    eq = np.cumprod(1 + r)
    yrs = len(r) / per_year
    cum = eq[-1] - 1
    cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 and eq[-1] > 0 else float("nan")
    shp = float(r.mean() / r.std() * np.sqrt(per_year)) if r.std() > 0 else float("nan")
    return cum, cagr, shp, _mdd(eq), (r > 0).mean(), eq


def _report_by_year(results, args):
    """分年归因：对每个口径按自然年切分，输出各年收益/夏普/胜率/超额。"""
    log("\n============ 分年归因 ============")
    for name, res in results.items():
        hold = build_configs(args)[name]["hold"]
        per_year = WEEKS_YEAR / hold
        dates = pd.to_datetime(res["date"])
        rets = res["ret"]
        bench = res["bench"]
        years = sorted(set(d.year for d in dates))
        log(f"\n  [{name}]")
        log(f"  {'年份':<6}{'期数':>5}{'年收益':>9}{'夏普':>7}{'胜率':>7}{'超额':>9}")
        log("  " + "-" * 42)
        for yr in years:
            mask = np.array([d.year == yr for d in dates])
            r_y = rets[mask]
            b_y = bench[mask]
            if len(r_y) == 0:
                continue
            eq_y = np.cumprod(1 + r_y)
            yr_ret = float(eq_y[-1] - 1)
            yr_shp = float(r_y.mean() / r_y.std() * np.sqrt(per_year)) if r_y.std() > 0 else float("nan")
            yr_wr = float((r_y > 0).mean())
            beq_y = np.cumprod(1 + b_y)
            yr_bench = float(beq_y[-1] - 1)
            log(f"  {yr:<6}{len(r_y):>5}{yr_ret*100:>8.1f}%{yr_shp:>7.2f}{yr_wr*100:>6.0f}%{(yr_ret-yr_bench)*100:>8.1f}%")


def report(results, args):
    log("\n============ 周度选股·调仓/分散口径对比（信号=期初前一交易日收盘，出场=期末收盘）============")
    log(f"抽样 {args.stocks} 只 | 前 {args.top_n}(概率加权前{args.probw_k}) | 成本 {args.cost_bps}bp/边 | "
        f"{'含' if args.keep_st else '剔除'}ST | 缓冲 rank≤{args.keep_rank} | 行业上限=各配置 | top_pct={args.top_pct}")
    log(f"\n{'口径(均已扣成本)':<22}{'期数':>5}{'累计':>9}{'年化':>8}{'夏普':>7}{'回撤':>9}{'期胜率':>7}{'均换手':>8}{'超额年化':>9}")
    log("-" * 92)
    curves = {}
    for name, res in results.items():
        hold = build_configs(args)[name]["hold"]
        per_year = WEEKS_YEAR / hold
        cum, cagr, shp, mdd, wr, eq = _ann(res, per_year)
        # 同期基准年化（用于超额）
        beq = np.cumprod(1 + res["bench"])
        byrs = len(res["bench"]) / per_year
        bcagr = beq[-1] ** (1 / byrs) - 1 if byrs > 0 and beq[-1] > 0 else float("nan")
        curves[name] = (res["date"], eq, beq)
        log(f"{name:<20}{len(res['ret']):>5}{cum*100:>8.1f}%{cagr*100:>7.1f}%{shp:>7.2f}"
            f"{mdd*100:>8.1f}%{wr*100:>6.0f}%{res['turn'].mean()*100:>7.0f}%{(cagr-bcagr)*100:>8.1f}%")
    # 基准（用周调口径的同期基准，年化按周）
    ref = results["原:周调·前20"]
    bcum, bcagr, bshp, bmdd, bwr, beq = _ann({"ret": ref["bench"]}, WEEKS_YEAR)
    log(f"{'等权全市场·基准(周)':<20}{len(ref['bench']):>5}{bcum*100:>8.1f}%{bcagr*100:>7.1f}%{bshp:>7.2f}"
        f"{bmdd*100:>8.1f}%{bwr*100:>6.0f}%{'-':>7}{'-':>8}")

    # ── 分年归因（验证策略在牛/熊/震荡各状态下的稳健性）──
    if args.by_year:
        _report_by_year(results, args)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 7))
        for name, (dts, eq, _b) in curves.items():
            ax.plot(dts, eq, lw=1.6, label=ASCII_MAP.get(name, name))
        rd, _re, rb = curves["原:周调·前20"]
        ax.plot(rd, rb, lw=2, ls="--", color="black", label="Market eq-weight")
        ax.axhline(1.0, color="gray", lw=0.6)
        ax.set_title(f"Weekly selection: rebalance/diversification (net) | {args.start}~{args.end}, cost={args.cost_bps}bp, top{args.top_n}")
        ax.set_ylabel("Equity (x)"); ax.legend(); ax.grid(alpha=0.3)
        fig.autofmt_xdate(); fig.tight_layout()
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "weekly_variants.png")
        fig.savefig(out, dpi=110)
        log(f"\n资金曲线对比图已保存: {out}")
    except Exception as exc:  # noqa: BLE001
        log(f"绘图失败（忽略）: {exc}")


def parse_args():
    p = argparse.ArgumentParser(description="周度 Top-N 选股回测")
    p.add_argument("--stocks", type=int, default=1500, help="抽样股票数；<=0 表示全市场")
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--start", type=str, default="2023-01-01",
                   help="回测起始日(默认2023-01-01，配合2023-2026 walk-forward)")
    p.add_argument("--end", type=str, default="2026-07-01")
    p.add_argument("--train-days", type=int, default=2520,
                   help="训练窗口自然日(默认2520≈7年，覆盖2015-2022初始训练)")
    p.add_argument("--horizon", type=int, default=5, help="训练标签前瞻天数(≈周)")
    p.add_argument("--lookback", type=int, default=3800,
                   help="每票回溯自然日(默认3800≈覆盖2015以来)")
    p.add_argument("--retrain-months", type=int, default=1,
                   help="每几个月重训一次(默认1=月度；长周期建议3=季度以缩短耗时)")
    p.add_argument("--cost-bps", type=float, default=15.0, help="单边成本(基点，默认15=A股含印花税)")
    p.add_argument("--keep-st", action="store_true", help="保留 ST 股(默认剔除)")
    p.add_argument("--keep-rank", type=int, default=40, help="排名缓冲阈值：跌出该名次才换出")
    p.add_argument("--probw-k", type=int, default=50, help="概率加权取前 K 只")
    p.add_argument("--top-pct", type=float, default=0.5,
                   help="横截面正样本阈值(默认0.5=前50%%；0.2=前20%%)")
    p.add_argument("--by-year", action="store_true", help="输出分年归因(验证牛/熊/震荡各状态稳健性)")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
