# -*- coding: utf-8 -*-
"""百度股市通 K 线抓取与解析（vapi/v1/getquotation 接口）。

该接口是百度股市通网页前端真正在用的 K 线端点，相比旧的 ``selfselect/getstockquotation``：

- 默认返回**正价不复权(bfq)**，不会出现旧接口 qfq 导致的历史负价（茅台 2015 负价问题消除）；
- 自带 换手率/涨跌幅/昨收/MA5·10·20（价+量）等字段；
- 额外返回 ``reportData``（财报披露日）。

鉴权：该端点**强制要求 ``acs-token`` 请求头**（cookie 不需要）。token 由百度前端风控 SDK
（``window.paris_2108`` → ``getAcsInstance().getSign()``）本地生成，**服务端严格校验签名**，
纯本地伪造必然 403（已实测）。

token 获取有两种方式（按优先级）：

1. **自动获取（推荐）**：注入 ``BaiduTokenProvider``，由 Playwright 无头加载个股页、驱动页面
   SDK 拿到真 token。token 按 TTL 缓存（默认 10 分钟），过期或遇到 403 自动刷新，浏览器实例常驻
   复用——回填全程只需启动一次浏览器。依赖：``pip install playwright && python -m playwright install chromium``。
2. **手动注入（兜底）**：设置环境变量 ``BAIDU_ACS_TOKEN``，或从浏览器个股 K 线页 Network →
   ``getquotation`` 请求头复制 token 后通过构造参数 ``acs_token=`` 传入。

``BaiduBackfillService`` 已默认接入方式 1，开箱即用、无需手动粘贴 token。

marketData 字段顺序与百度 getquotation keys 完全一致（见 _BAIDU_FIELD_INDEX），
落库列（date/open/high/low/close/volume/amount/range/ratio/turnoverratio/preClose/
ma5avgprice/ma5volume/ma10avgprice/ma10volume/ma20avgprice/ma20volume/timestamp/time）
与百度 keys 保持一致。
"""

from __future__ import annotations

import calendar
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError
from .baidu_token_provider import BaiduTokenProvider

logger = logging.getLogger(__name__)

_BAIDU_KLINE_ENDPOINT = "https://finance.pae.baidu.com/vapi/v1/getquotation"
_BAIDU_HTTP_TIMEOUT = 30

# 旧接口 ktype("1") → vapi ktype("day"/"week"/"month")
_KTYPE_MAP = {"1": "day", "day": "day", "week": "week", "month": "month", "101": "day"}

# vapi 必需的请求头（缺 acs-token 直接 403）
_ACCEPT = "application/vnd.finance-web.v1+json"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def _to_unix_ts(value: Optional[str]) -> Optional[str]:
    """百度接口的 end_time/start_time 需要 Unix 时间戳（秒）。

    - 已是纯数字（时间戳）则原样返回；
    - 否则按 ``YYYY-MM-DD`` 解析为 UTC 时间戳（用 ``calendar.timegm`` 避免受本地时区影响）。
    """
    if not value:
        return None
    v = str(value).strip()
    if v.lstrip("-").isdigit():
        return v
    try:
        dt = datetime.strptime(v[:10], "%Y-%m-%d")
        return str(calendar.timegm(dt.timetuple()))
    except (ValueError, TypeError):
        return v


# 百度 getquotation 响应的原始 keys（字段顺序，索引从 0 开始，来自接口 keys 元数据）：
# ['timestamp','time','open','close','volume','high','low','amount',
#  'range','ratio','turnoverratio','preClose',
#  'ma5avgprice','ma5volume','ma10avgprice','ma10volume','ma20avgprice','ma20volume']
# 注意：百度把 position 8 标为 range，但其真实含义是「涨跌额(change)」而非振幅；
#       此处按需求“字段与百度一致”，沿用百度原始命名，不做语义纠正。
#       旧代码把 ma10/ma20 指到 13/14，实际 13=ma5volume、14=ma10avgprice、16=ma20avgprice，
#       已在此按 keys 顺序修正，并补全 ma5/10/20 的成交量 MA。
_BAIDU_FIELD_INDEX = {
    "timestamp": 0,
    "time": 1,
    "open": 2,
    "close": 3,
    "volume": 4,
    "high": 5,
    "low": 6,
    "amount": 7,
    "range": 8,          # 百度 keys 标 range（实为涨跌额 change）
    "ratio": 9,          # 涨跌幅（%）
    "turnoverratio": 10, # 换手率（%）
    "preClose": 11,      # 昨收
    "ma5avgprice": 12,
    "ma5volume": 13,
    "ma10avgprice": 14,
    "ma10volume": 15,
    "ma20avgprice": 16,
    "ma20volume": 17,
}

# 字符串型字段（不参与数值化）
_BAIDU_STR_FIELDS = {"time"}

# 百度全字段列表（落库结构化字段，顺序与 keys 一致）
_BAIDU_STRUCTURED_FIELDS = list(_BAIDU_FIELD_INDEX.keys())

_STANDARD_KEEP = [
    "date", "open", "high", "low", "close", "volume", "amount", "ratio",
]


def parse_baidu_response(payload: Optional[Dict[str, Any]], *, ktype: str = "1") -> pd.DataFrame:
    """把百度 getquotation 响应解析为结构化 DataFrame。

    Returns:
        含列的 DataFrame：date, ktype, 与百度 keys 完全一致的结构化字段
        (timestamp/time/open/close/volume/high/low/amount/range/ratio/turnoverratio/
        preClose/ma5avgprice/ma5volume/ma10avgprice/ma10volume/ma20avgprice/ma20volume)。
        时间字段 time 只保留日期部分（YYYY-MM-DD）。无数据返回空 DataFrame（列为上述全集）。
    """
    columns = ["date", "ktype"] + _BAIDU_STRUCTURED_FIELDS
    if not payload:
        return pd.DataFrame(columns=columns)

    result = payload.get("Result") or {}
    md = result.get("newMarketData") or {}
    market_data = md.get("marketData") or ""
    raw_rows = [r for r in market_data.split(";") if r]
    if not raw_rows:
        return pd.DataFrame(columns=columns)

    records: list[Dict[str, Any]] = []
    for r in raw_rows:
        fields = r.split(",")
        if len(fields) < 11:
            continue
        date_str = fields[1][:10] if len(fields) > 1 else ""
        if not date_str:
            continue
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        rec: Dict[str, Any] = {"date": date_str, "ktype": ktype}
        for name, idx in _BAIDU_FIELD_INDEX.items():
            if idx < len(fields):
                if name in _BAIDU_STR_FIELDS:
                    value = fields[idx]
                    # 时间字段按需求只保留日期部分（YYYY-MM-DD）
                    rec[name] = value[:10] if name == "time" and len(value) >= 10 else value
                else:
                    rec[name] = _safe_float(fields[idx])
            else:
                rec[name] = None
        records.append(rec)

    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records)


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).lstrip("+"))
    except (TypeError, ValueError):
        return None


def _filter_by_range(
    df: pd.DataFrame,
    start_time: str,
    end_time: Optional[str] = None,
) -> pd.DataFrame:
    """vapi 默认返回整段历史，按 [start_time, end_time] 在本地裁剪。

    即使接口忽略 start/end 区间参数（实测未确认），本地过滤也能保证只落库目标窗口，
    既兼容「只要历史某段」也兼容「全量回填到当前」。
    """
    if df is None or df.empty:
        return df
    mask = True
    if start_time:
        mask = mask & (df["date"] >= str(start_time)[:10])
    if end_time:
        mask = mask & (df["date"] <= str(end_time)[:10])
    return df.loc[mask].reset_index(drop=True)


class BaiduFetcher(BaseFetcher):
    """百度股市通日线 K 线抓取（vapi，正价 + 换手率/振幅/MA 全字段）。

    与 TencentFetcher 类似，单表 HTTP 直连、整段一次取；区别是百度自带换手率
    与 MA，无需再走 quote --date 补换手率，且默认正价不复权。
    """

    name = "BaiduFetcher"
    priority = 1
    allow_empty_daily_data = True

    def __init__(
        self,
        acs_token: Optional[str] = None,
        *,
        market_type: str = "ab",
        name: str = "",
        token_provider: Optional[BaiduTokenProvider] = None,
    ):
        # token 优先级：构造参数 > 环境变量 BAIDU_ACS_TOKEN > token_provider 自动获取
        self._acs_token = acs_token or os.getenv("BAIDU_ACS_TOKEN")
        self._token_provider = token_provider
        self._market_type = market_type
        self._name = name

    def _build_params(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
    ) -> Dict[str, str]:
        # 旧 ktype("1") 映射为 vapi 的 "day"
        api_ktype = _KTYPE_MAP.get(ktype, ktype)
        params = {
            "srcid": "5353",
            "pointType": "string",
            "group": "quotation_kline_ab",
            "query": code,
            "code": code,
            "market_type": self._market_type,
            "newFormat": "1",
            "is_kc": "0",
            "ktype": api_ktype,
            "finClientType": "pc",
            # 关键：不带 all=1 时接口只返回最近 2001 行（约 2018 年起），
            # 无法回溯更早历史；带 all=1 返回全量（茅台可回 2001 上市）。
            # start/end 仍发送以便服务端裁剪，本地再 _filter_by_range 兜底。
            "all": "1",
            "chartType": "kline",
            "stock_type": self._market_type,
            "financeType": "stock",
        }
        # name 为展示字段，百度按 code 查询；未提供则不发送，避免空值干扰
        if self._name:
            params["name"] = self._name
        start_ts = _to_unix_ts(start_time)
        if start_ts:
            params["start_time"] = start_ts
        end_ts = _to_unix_ts(end_time) if end_time else None
        if end_ts:
            params["end_time"] = end_ts
        return params

    def _build_headers(self) -> Dict[str, str]:
        token = self._resolve_token()
        return {
            "accept": _ACCEPT,
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "origin": "https://finance.baidu.com",
            "referer": "https://finance.baidu.com/",
            "user-agent": _USER_AGENT,
            "acs-token": token,
        }

    def _resolve_token(self) -> str:
        """按优先级解析 acs-token：构造参数/环境变量 > token_provider 自动获取。"""
        if self._acs_token:
            return self._acs_token
        if self._token_provider is not None:
            return self._token_provider.get_token()
        raise DataFetchError(
            "BaiduFetcher 缺少 acs-token：请设置环境变量 BAIDU_ACS_TOKEN，或注入 BaiduTokenProvider 自动获取（需 playwright + chromium）。"
        )

    def fetch_kline_df(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
    ) -> pd.DataFrame:
        """请求百度 vapi K 线并解析为结构化 DataFrame。"""
        params = self._build_params(code, start_time, end_time, ktype)
        try:
            resp = requests.get(
                _BAIDU_KLINE_ENDPOINT,
                params=params,
                headers=self._build_headers(),
                timeout=_BAIDU_HTTP_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("百度 vapi 请求异常 %s [%s~%s]: %s", code, start_time, end_time, exc)
            raise DataFetchError(f"BaiduFetcher 请求失败: {exc}") from exc

        if resp.status_code == 403:
            # token 可能过期：若由 provider 托管，强制刷新并重试一次
            if self._token_provider is not None and not self._acs_token:
                logger.warning("百度 vapi 403，强制刷新 acs-token 后重试 %s", code)
                try:
                    new_token = self._token_provider.get_token(force=True)
                except DataFetchError as exc:
                    raise DataFetchError(
                        f"BaiduFetcher 403 且刷新 token 失败：{exc}"
                    ) from exc
                try:
                    resp = requests.get(
                        _BAIDU_KLINE_ENDPOINT,
                        params=params,
                        headers={**self._build_headers(), "acs-token": new_token},
                        timeout=_BAIDU_HTTP_TIMEOUT,
                    )
                except Exception as exc:
                    raise DataFetchError(f"BaiduFetcher 重试请求失败: {exc}") from exc
            if resp.status_code == 403:
                raise DataFetchError(
                    "BaiduFetcher 收到 403：acs-token 缺失或已过期，请刷新 BAIDU_ACS_TOKEN 后重试。"
                )
        if resp.status_code != 200:
            raise DataFetchError(f"BaiduFetcher HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
        except Exception as exc:
            raise DataFetchError(f"BaiduFetcher 响应非 JSON: {exc}") from exc

        if payload.get("ResultCode") not in (0, None):
            raise DataFetchError(
                f"BaiduFetcher ResultCode={payload.get('ResultCode')}: {payload.get('Result')}"
            )

        df = parse_baidu_response(payload, ktype=ktype)
        return _filter_by_range(df, start_time, end_time)

    def fetch_report_dates(self, code: str, ktype: str = "1") -> list[str]:
        """返回该票的财报披露日列表（vapi 独有 ``reportData``，如 ['2024-04-03', ...]）。

        用于「财报事件」特征；与日 K 落库解耦，不改动 stock_daily_baidu 表结构。
        """
        params = self._build_params(code, "2010-01-01", None, ktype)
        try:
            resp = requests.get(
                _BAIDU_KLINE_ENDPOINT,
                params=params,
                headers=self._build_headers(),
                timeout=_BAIDU_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("百度 reportData 获取失败 %s: %s", code, exc)
            return []
        report = (payload.get("Result") or {}).get("reportData") or {}
        return sorted(report.keys())

    # ── BaseFetcher 抽象方法实现（便于注册进 DataFetcherManager） ──
    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return self.fetch_kline_df(stock_code, start_date, end_date, ktype="1")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount", "ratio"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        keep = [c for c in _STANDARD_KEEP if c in normalized.columns]
        return normalized[keep]
