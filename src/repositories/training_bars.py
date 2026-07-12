# -*- coding: utf-8
"""
训练/预测行情数据网关：全项目训练、预测、打分快照统一经此层取数，杜绝散落直连仓储。

数据源统一为 stock_daily_ohlcv（源无关通用层，单表自带换手率/成交额）：
  旧 stock_daily / stock_daily_kline 已下线，取数只走 stock_daily_ohlcv，无数据即
  视为无数据（不回退到其它表），保证训练样本口径统一。

数据质量（训练必读）：
  stock_daily_ohlcv 的 turnoverratio（换手率）在此网关重命名为 turnover_rate，
  激活 build_features 的 turnover_norm/turnover_rel 特征。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from src.repositories.stock_repo import DEFAULT_TRAIN_BULK_BATCH, StockRepository

logger = logging.getLogger(__name__)

TRAIN_OHLCV_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "turnover_rate"]


def load_training_bars_bulk(
    codes: List[str],
    start_date: date,
    end_date: date,
    *,
    batch_size: int = DEFAULT_TRAIN_BULK_BATCH,
    adj_type: str = "qfq",
    ktype: str = "1",
) -> Dict[str, pd.DataFrame]:
    """批量加载训练/预测用日线（统一输出 TRAIN_OHLCV_COLS：含 close + turnover_rate）。

    只读 stock_daily_ohlcv，无数据即视为无数据（不回退），保证训练样本口径统一。
    """
    repo = StockRepository()
    return _normalize_ohlcv_frames(
        repo.load_ohlcv_bulk(
            codes, start_date, end_date,
            batch_size=batch_size, ktype=ktype, adj_type=adj_type,
        )
    )


def load_training_bar_df(
    code: str,
    start_date: date,
    end_date: date,
    *,
    adj_type: str = "qfq",
    ktype: str = "1",
) -> pd.DataFrame:
    """单票训练/预测行情（stock_daily_ohlcv），输出 TRAIN_OHLCV_COLS。

    内部走 load_training_bars_bulk 复用同一取数/归一化逻辑，避免逻辑分叉。
    """
    bulk = load_training_bars_bulk(
        [code], start_date, end_date,
        batch_size=1, adj_type=adj_type, ktype=ktype,
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
