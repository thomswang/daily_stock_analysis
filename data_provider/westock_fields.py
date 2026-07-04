# -*- coding: utf-8 -*-
"""
A 股 quote --date 持久化字段（对齐 westock test/index.html，去掉港股/美股/kline 冗余列）。

权威存储：stock_daily_quote（code+date 单表；仅结构化列，不存 raw_json）。
"""

from __future__ import annotations

from typing import Tuple

# ── 已废弃：全市场字段清单（解析 raw_json 时仍可含这些键，但不落列）──
WESTOCK_KLINE_FLOAT_FIELDS: Tuple[str, ...] = (
    "open", "high", "low", "last", "volume", "amount", "exchange",
)

WESTOCK_QUOTE_FLOAT_FIELDS: Tuple[str, ...] = (
    "price", "prev_close", "open", "high", "low", "change", "change_percent",
    "volume", "amount", "turnover_rate", "volume_ratio", "range_pct", "avg_price",
    "wb_ratio", "pe_ratio", "pe_fwd", "pe_lyr", "pb_ratio", "dividend_ratio_ttm",
    "total_market_cap", "circulating_market_cap", "total_shares", "float_shares",
    "high_52week", "low_52week", "chg_5d", "chg_10d", "chg_20d", "chg_60d", "chg_ytd",
    "price_ceiling", "price_floor", "inner_volume", "outer_volume",
    "last", "exchange", "lot", "adr_conversion_price",
    "relative_hk_stock_price", "relative_hk_stock_chg_pct", "dividend_ttm", "eps_ttm",
    "pre_market_price", "pre_market_price_chg", "pre_market_price_chg_pct",
    "post_market_price", "post_market_price_chg", "post_market_price_chg_pct",
)

# ── A 股落库字段（沪/深/北；不含港股 ADR、美股盘前盘后、kline 冗余 last/exchange）──
WESTOCK_A_SHARE_QUOTE_FLOAT_FIELDS: Tuple[str, ...] = (
    "price",
    "prev_close",
    "open",
    "high",
    "low",
    "change",
    "change_percent",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "range_pct",
    "avg_price",
    "wb_ratio",
    "pe_ratio",
    "pe_fwd",
    "pe_lyr",
    "pb_ratio",
    "dividend_ratio_ttm",
    "total_market_cap",
    "circulating_market_cap",
    "total_shares",
    "float_shares",
    "high_52week",
    "low_52week",
    "chg_5d",
    "chg_10d",
    "chg_20d",
    "chg_60d",
    "chg_ytd",
    "price_ceiling",
    "price_floor",
    "inner_volume",
    "outer_volume",
)

WESTOCK_A_SHARE_QUOTE_TEXT_FIELDS: Tuple[str, ...] = (
    "market_type",
    "market_name",
    "name",
    "symbol",
    "time",
)

WESTOCK_A_SHARE_QUOTE_PERSIST_FIELDS: Tuple[str, ...] = (
    *WESTOCK_A_SHARE_QUOTE_FLOAT_FIELDS,
    *WESTOCK_A_SHARE_QUOTE_TEXT_FIELDS,
)

# 入库默认使用 A 股精简列
WESTOCK_QUOTE_TEXT_FIELDS = WESTOCK_A_SHARE_QUOTE_TEXT_FIELDS
WESTOCK_QUOTE_PERSIST_FIELDS = WESTOCK_A_SHARE_QUOTE_PERSIST_FIELDS
