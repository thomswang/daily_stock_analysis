# -*- coding: utf-8
"""
训练/预测行情数据网关：全项目训练、预测、打分快照统一经此层取数，杜绝散落直连仓储。

环境变量 TRAIN_BAR_SOURCE：
  - ohlcv（默认）：stock_daily_ohlcv，源无关通用层，单表自带换手率/成交额，纯此表不回退
  - kline：stock_daily_kline，纯技术因子（amount/turnover_rate 恒 NULL）
  - quote：stock_daily_quote，含估值/真实价 OHLC
  - auto：优先 ohlcv，无数据时回退 kline，再回退 quote（仅显式指定时启用回退）
  - merged：kline OHLCV + quote 估值列 LEFT JOIN（需两表均有数据）

数据质量（训练必读）：
  默认源 stock_daily_ohlcv 的 turnoverratio（换手率）在此网关重命名为 turnover_rate，
  激活 build_features 的 turnover_norm/turnover_rel 特征（切走前 kline 该列恒 NULL 致特征恒 0）。
"""

from __future__ import annotations

import logging
import os
from datetime import date
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd

from src.repositories.stock_repo import DEFAULT_TRAIN_BULK_BATCH, StockRepository

logger = logging.getLogger(__name__)

TRAIN_OHLCV_COLS = ["date", "open", "high", "low", "close", "volume", "turnover_rate"]
QUOTE_FUNDAMENTAL_COLS = [
    "pe_ratio", "pb_ratio", "total_market_cap", "circulating_market_cap",
    "float_shares", "change_percent", "volume_ratio",
]


class TrainBarSource(str, Enum):
    OHLCV = "ohlcv"
    KLINE = "kline"
    QUOTE = "quote"
    AUTO = "auto"
    MERGED = "merged"


def resolve_train_bar_source(raw: Optional[str] = None) -> TrainBarSource:
    val = (raw or os.getenv("TRAIN_BAR_SOURCE", "ohlcv")).strip().lower()
    try:
        return TrainBarSource(val)
    except ValueError:
        logger.warning("未知 TRAIN_BAR_SOURCE=%s，回退 ohlcv", val)
        return TrainBarSource.OHLCV


def load_training_bars_bulk(
    codes: List[str],
    start_date: date,
    end_date: date,
    *,
    source: Optional[TrainBarSource] = None,
    batch_size: int = DEFAULT_TRAIN_BULK_BATCH,
    adj_type: str = "qfq",
    ktype: str = "1",
) -> Dict[str, pd.DataFrame]:
    """批量加载训练/预测用日线（统一输出 TRAIN_OHLCV_COLS：含 close + turnover_rate）。

    默认源 OHLCV：只读 stock_daily_ohlcv，无数据即视为无数据（不回退），保证训练样本口径统一。
    其它源仅在通过 source/环境变量显式指定时生效。
    """
    repo = StockRepository()
    src = source or resolve_train_bar_source()

    # ── 默认：纯 OHLCV，不回退 ──
    if src == TrainBarSource.OHLCV:
        return _normalize_ohlcv_frames(
            repo.load_ohlcv_bulk(
                codes, start_date, end_date,
                batch_size=batch_size, ktype=ktype, adj_type=adj_type,
            )
        )

    if src == TrainBarSource.QUOTE:
        return _normalize_quote_frames(
            repo.load_merged_bulk(codes, start_date, end_date, batch_size=batch_size)
        )

    kline_frames = _normalize_kline_frames(
        repo.load_kline_bulk(
            codes, start_date, end_date, batch_size=batch_size, adj_type=adj_type,
        )
    )

    if src == TrainBarSource.KLINE:
        return kline_frames

    quote_frames = _normalize_quote_frames(
        repo.load_merged_bulk(codes, start_date, end_date, batch_size=batch_size)
    )

    # AUTO：显式指定时才启用回退链 ohlcv → kline → quote
    if src == TrainBarSource.AUTO:
        ohlcv_frames = _normalize_ohlcv_frames(
            repo.load_ohlcv_bulk(
                codes, start_date, end_date,
                batch_size=batch_size, ktype=ktype, adj_type=adj_type,
            )
        )
        out: Dict[str, pd.DataFrame] = dict(ohlcv_frames)
        for code in codes:
            key = (code or "").strip().upper()
            if not key or key in out:
                continue
            if key in kline_frames:
                out[key] = kline_frames[key]
            elif key in quote_frames:
                out[key] = quote_frames[key]
        return out

    # MERGED: kline OHLCV + quote fundamentals
    return _merge_kline_quote(kline_frames, quote_frames, codes)


def load_training_bar_df(
    code: str,
    start_date: date,
    end_date: date,
    *,
    source: Optional[TrainBarSource] = None,
    adj_type: str = "qfq",
    ktype: str = "1",
) -> pd.DataFrame:
    """单票训练/预测行情（默认 OHLCV、不回退），输出 TRAIN_OHLCV_COLS。

    内部走 load_training_bars_bulk 复用同一取数/归一化逻辑，避免逻辑分叉。
    """
    bulk = load_training_bars_bulk(
        [code], start_date, end_date,
        source=source, batch_size=1, adj_type=adj_type, ktype=ktype,
    )
    key = (code or "").strip().upper()
    if key in bulk:
        return bulk[key]
    # bulk 以裸码为 key，带后缀全码未命中时回退按裸码取。
    # 例：大盘常量 "000300.SH" → 回退裸码 "000300" 命中落库数据
    # （沪深300 的落库 code 恒为裸码 000300，见 baidu_fetcher.INDEX_BAIDU_MAP）。
    return bulk.get(key.split(".")[0], pd.DataFrame())


def _normalize_ohlcv_frames(frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """stock_daily_ohlcv 已在仓储层把 turnoverratio→turnover_rate，这里仅裁剪到统一契约列。"""
    out: Dict[str, pd.DataFrame] = {}
    for code, df in frames.items():
        if df is None or df.empty:
            continue
        out[code] = df[[c for c in TRAIN_OHLCV_COLS if c in df.columns]].copy()
    return out


def _normalize_kline_frames(frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for code, df in frames.items():
        if df is None or df.empty:
            continue
        out[code] = df[[c for c in TRAIN_OHLCV_COLS if c in df.columns]].copy()
    return out


def _normalize_quote_frames(frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for code, df in frames.items():
        if df is None or df.empty:
            continue
        chunk = df.copy()
        if "close" not in chunk.columns and "last" in chunk.columns:
            chunk["close"] = chunk["last"]
        cols = [c for c in TRAIN_OHLCV_COLS if c in chunk.columns]
        out[code] = chunk[cols].copy()
    return out


def _merge_kline_quote(
    kline_frames: Dict[str, pd.DataFrame],
    quote_frames: Dict[str, pd.DataFrame],
    codes: List[str],
) -> Dict[str, pd.DataFrame]:
    repo = StockRepository()
    out: Dict[str, pd.DataFrame] = {}
    for raw in codes:
        code = (raw or "").strip().upper()
        if not code:
            continue
        kdf = kline_frames.get(code)
        if kdf is None or kdf.empty:
            continue
        merged = kdf.copy()
        qrows = repo.get_quote_range(code, merged["date"].min(), merged["date"].max())
        if not qrows:
            out[code] = merged
            continue
        qrecs = []
        for row in qrows:
            qrecs.append({
                "date": row.date,
                **{c: getattr(row, c, None) for c in QUOTE_FUNDAMENTAL_COLS},
            })
        qdf = pd.DataFrame(qrecs)
        merged = merged.merge(qdf, on="date", how="left")
        out[code] = merged
    return out
