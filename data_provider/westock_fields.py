# -*- coding: utf-8 -*-
"""
westock-data/test/index.html FIELD_DICT 对齐的字段清单。

权威存储：stock_daily_quote（quote --date 逐日循环，一天 40+ 字段）。
"""

from __future__ import annotations

from typing import Tuple

# westock kline 8 字段（index.html FIELD_DICT K线段）
WESTOCK_KLINE_FLOAT_FIELDS: Tuple[str, ...] = (
    "open",
    "high",
    "low",
    "last",
    "volume",
    "amount",
    "exchange",
)

# quote --date 数值字段（与 index.html FIELD_DICT 键名一致）
WESTOCK_QUOTE_FLOAT_FIELDS: Tuple[str, ...] = (
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
    "last",
    "exchange",
    "lot",
    "adr_conversion_price",
    "relative_hk_stock_price",
    "relative_hk_stock_chg_pct",
    "dividend_ttm",
    "eps_ttm",
    "pre_market_price",
    "pre_market_price_chg",
    "pre_market_price_chg_pct",
    "post_market_price",
    "post_market_price_chg",
    "post_market_price_chg_pct",
)

WESTOCK_QUOTE_TEXT_FIELDS: Tuple[str, ...] = (
    "market_type",
    "market_name",
    "name",
    "symbol",
    "time",
)

WESTOCK_QUOTE_PERSIST_FIELDS: Tuple[str, ...] = (
    *WESTOCK_QUOTE_FLOAT_FIELDS,
    *WESTOCK_QUOTE_TEXT_FIELDS,
)
