# -*- coding: utf-8
"""
训练用日线加载层：在 kline（前复权 OHLCV）与 quote（不复权截面）之间显式分流。

环境变量 TRAIN_BAR_SOURCE：
  - kline（默认）：stock_daily_kline，纯技术因子
  - quote：stock_daily_quote，含估值/真实价 OHLC
  - auto：优先 kline，无数据时回退 quote
  - merged：kline OHLCV + quote 估值列 LEFT JOIN（需两表均有数据）
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
    KLINE = "kline"
    QUOTE = "quote"
    AUTO = "auto"
    MERGED = "merged"


def resolve_train_bar_source(raw: Optional[str] = None) -> TrainBarSource:
    val = (raw or os.getenv("TRAIN_BAR_SOURCE", "kline")).strip().lower()
    try:
        return TrainBarSource(val)
    except ValueError:
        logger.warning("未知 TRAIN_BAR_SOURCE=%s，回退 kline", val)
        return TrainBarSource.KLINE


def load_training_bars_bulk(
    codes: List[str],
    start_date: date,
    end_date: date,
    *,
    source: Optional[TrainBarSource] = None,
    batch_size: int = DEFAULT_TRAIN_BULK_BATCH,
    adj_type: str = "qfq",
) -> Dict[str, pd.DataFrame]:
    """批量加载训练用日线（统一输出 close + turnover_rate 等列）。"""
    repo = StockRepository()
    src = source or resolve_train_bar_source()

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

    if src == TrainBarSource.AUTO:
        out: Dict[str, pd.DataFrame] = dict(kline_frames)
        for code in codes:
            key = (code or "").strip().upper()
            if key and key not in out and key in quote_frames:
                out[key] = quote_frames[key]
        return out

    # MERGED: kline OHLCV + quote fundamentals
    return _merge_kline_quote(kline_frames, quote_frames, codes)


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
