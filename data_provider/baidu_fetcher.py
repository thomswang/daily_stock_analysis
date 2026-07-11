# -*- coding: utf-8 -*-
"""百度股市通 K 线抓取与解析（vapi/v1/getquotation 接口）。

该接口是百度股市通网页前端真正在用的 K 线端点，相比旧的 ``selfselect/getstockquotation``：

- 默认返回**前复权(qfq)**：以最新价为基准，历史价按累计分红因子下调。
  实测与 westock 不复权(quote)在最近交易日价格完全一致（如 2026-07-07 两源
  close=1188.8、preClose=1206.91、ratio=-1.5% 全部相等），历史日两源相差累计
  分红比例（如 2024-05-20 百度 close=1574.66 vs westock 不复权 1709，差 -7.86%）。
  注意：旧注释称其「不复权(bfq)」是与实测相反的，已纠正。
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

marketData 字段顺序与百度 getquotation keys 完全一致（见 _BAIDU_FIELD_INDEX）。

落库仅保留训练有价值的列（剔除可由 close/volume 推导的 MA、与 time 重复的 timestamp、
以及 =close-preClose 的 range、以及 =前一日 close 的 preClose）：
    date/open/high/low/close/volume/amount/ratio/turnoverratio/time
MA(5/10/20) 价格与成交量由特征工程从 close/volume 滚动重算（analyzer 本就如此），不落库；
preClose(=前一日 close) 同理可在特征工程阶段由 close 偏移得到，不落库。
"""

from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, normalize_stock_code
from .baidu_token_provider import BaiduTokenProvider

logger = logging.getLogger(__name__)

_BAIDU_KLINE_ENDPOINT = "https://finance.pae.baidu.com/vapi/v1/getquotation"
_BAIDU_HTTP_TIMEOUT = 30

# 旧接口 ktype("1") → vapi ktype("day"/"week"/"month")
_KTYPE_MAP = {"1": "day", "day": "day", "week": "week", "month": "month", "101": "day"}

# ⚠️ 指数特殊处理（勿误改落库码！）
# 百度对指数走独立的 ``group=quotation_index_kline``，且沪深300 在百度侧用深证镜像码
# ``399300``（而非裸码 000300）。因此：
#   - 抓取请求码（query/code 参数）= 399300（仅此映射作用于网络请求）；
#   - 落库 code 仍 = 000300（KEY 用裸码，与个股一致），
#     以便训练/预测侧 load_market_df 用 000300.SH 直接命中本地裸码 000300，下游零改动。
# 调用方永远传/存 000300，绝不要传 399300（那只是"对百度发的请求码"）。
INDEX_BAIDU_MAP = {
    "000300": {"group": "quotation_index_kline", "query": "399300", "name": "沪深300"},
}

# vapi 必需的请求头（缺 acs-token 直接 403）
_ACCEPT = "application/vnd.finance-web.v1+json"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def build_baidu_params(
    code: str,
    start_time: str,
    end_time: Optional[str] = None,
    ktype: str = "1",
    full: bool = True,
    *,
    market_type: str = "ab",
    name: str = "",
) -> Dict[str, str]:
    """构造百度 getquotation 查询参数（模块级，供 BaiduFetcher 与浏览器驱动抓取共用）。

    旧 ktype("1") 映射为 vapi 的 "day"。百度 query/code 仅接受纯数字代码
    （市场由 market_type 区分），去后缀避免 .SZ/.SH/.BJ 被拒。
    百度接口实测忽略 start_time/end_time，区间裁剪依赖本地 _filter_by_range。
    """
    api_ktype = _KTYPE_MAP.get(ktype, ktype)
    bare = normalize_stock_code(code)
    idx = INDEX_BAIDU_MAP.get(bare)
    if idx:
        # 指数标的：独立 group=quotation_index_kline + 深证镜像查询码（沪深300：000300→399300），
        # 参数集不含个股专有的 chartType/stock_type/financeType（贴合实测可用的指数请求）。
        query = idx["query"]
        params: Dict[str, str] = {
            "srcid": "5353",
            "pointType": "string",
            "group": idx["group"],
            "query": query,
            "code": query,
            "market_type": market_type,
            "newFormat": "1",
            "name": name or idx.get("name", ""),
            "is_kc": "0",
            "ktype": api_ktype,
            "finClientType": "pc",
        }
    else:
        params = {
            "srcid": "5353",
            "pointType": "string",
            "group": "quotation_kline_ab",
            "query": bare,
            "code": bare,
            "market_type": market_type,
            "newFormat": "1",
            "is_kc": "0",
            "ktype": api_ktype,
            "finClientType": "pc",
            "chartType": "kline",
            "stock_type": market_type,
            "financeType": "stock",
        }
    if full:
        params["all"] = "1"
    # 手动传入 name 时覆盖（含指数分支：用户显式命名优先于内置别名）
    if name:
        params["name"] = name
    start_ts = _to_unix_ts(start_time)
    if start_ts:
        params["start_time"] = start_ts
    end_ts = _to_unix_ts(end_time) if end_time else None
    if end_ts:
        params["end_time"] = end_ts
    return params


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
#
# 落库只保留训练有价值的列，故 _BAIDU_FIELD_INDEX 仅映射这些 key 在其原始行中的下标：
#   time(1)/open(2)/close(3)/volume(4)/high(5)/low(6)/amount(7)/
#   ratio(9)/turnoverratio(10)/preClose(11)
# 删除项（均可由保留列推导，无需落库）：
#   timestamp(0) 与 time 重复且原始 unix 值会引发时间泄漏；
#   range(8) = close - preClose；
#   ma5/10/20 avgprice = close 的滚动均值；ma5/10/20 volume = volume 的滚动均值
#   （特征工程阶段从 close/volume 重算，analyzer 本就如此）。
_BAIDU_FIELD_INDEX = {
    "time": 1,
    "open": 2,
    "close": 3,
    "volume": 4,
    "high": 5,
    "low": 6,
    "amount": 7,
    "ratio": 9,           # 涨跌幅（%）
    "turnoverratio": 10, # 换手率（%）
    "preClose": 11,       # 昨收
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
        含列的 DataFrame：date, ktype, 结构化字段
        (time/open/close/volume/high/low/amount/ratio/turnoverratio/preClose)。
        其中剔除了可由 close/volume 推导的 MA 列、与 time 重复的 timestamp、
        以及 =close-preClose 的 range；时间字段 time 只保留日期部分（YYYY-MM-DD）。
        无数据返回空 DataFrame（列为上述全集）。
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


# 百度 reportData 的文案形如：
#   "财报发布: 2021FY总营收1094.64亿元 归母净利润524.60亿元"
#   "财报发布: 2022Q1总营收331.87亿元 归母净利润172.45亿元"
# 报告期类型：FY（年报）/ Q1/Q2/Q3/Q4（季报）/ H1/H2（半年）/ M3/M6/M9/M12（单季累计）。
def parse_baidu_report_data(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从百度 getquotation 响应体解析 reportData，产出原始财报披露事件。

    每个披露日（reportData 的 key）即一条记录，原样保留该日全部原始
    entries（含 data 文案与 xcxQuery 深链）。结构化指标（营收/净利润等）
    由下游按需从 raw_json 解析，便于以后扩展提取维度。

    Returns:
        列表，单条形如 ``{"report_date": "2021-03-31", "raw": [...]}``。
        无数据或格式非法返回空列表。
    """
    if not payload:
        return []
    report = (payload.get("Result") or {}).get("reportData") or {}
    if not isinstance(report, dict):
        return []

    out: List[Dict[str, Any]] = []
    for date_str, entries in report.items():
        if not isinstance(entries, list) or not entries:
            continue
        report_date = (date_str or "")[:10]
        try:
            datetime.strptime(report_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        out.append({
            "report_date": report_date,
            "raw": entries,
        })
    return out



def _filter_by_range(
    df: pd.DataFrame,
    start_time: str,
    end_time: Optional[str] = None,
) -> pd.DataFrame:
    """百度返回后按 [start_time, end_time] 在本地裁剪落库窗口。

    百度接口实测**忽略** start/end 区间参数（full=True 回传整段历史、
    full=False 只回最近约 2000 行），故区间裁剪完全依赖本地过滤：
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
        full: bool = True,
    ) -> Dict[str, str]:
        return build_baidu_params(
            code, start_time, end_time, ktype, full,
            market_type=self._market_type, name=self._name,
        )

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

    def _request_payload(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
        full: bool = True,
    ) -> Dict[str, Any]:
        """请求百度 vapi K 线，返回解析后的 JSON 响应体（含 marketData 与 reportData）。

        统一处理 403（acs-token 过期则强制刷新重试一次）、HTTP 错误、ResultCode
        错误。K 线 DataFrame 与财报 reportData 均从同一响应体解析，故本方法为二者
        共用入口，避免为财报额外发请求（百度限流敏感）。
        """
        params = self._build_params(code, start_time, end_time, ktype, full=full)
        headers = self._build_headers()
        try:
            resp = requests.get(
                _BAIDU_KLINE_ENDPOINT,
                params=params,
                headers=headers,
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
        return payload

    def fetch_kline_df(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
        full: bool = True,
    ) -> pd.DataFrame:
        """请求百度 vapi K 线并解析为结构化 DataFrame，再按 start/end 本地裁剪。

        full: True 拉全量（all=1，回溯到上市日）；False 仅拉最近约 2000 行尾窗口
        （老票≈2018 起，新股=上市日起）。
        """
        payload = self._request_payload(code, start_time, end_time, ktype=ktype, full=full)
        df = parse_baidu_response(payload, ktype=ktype)
        return _filter_by_range(df, start_time, end_time)

    def fetch_kline_and_reports(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
        full: bool = True,
    ) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
        """单次请求同时返回 (K 线 DataFrame, 财报披露事件列表)。

        百度 vapi 在 K 线响应体内附带 ``reportData``（财报披露事件），尾窗口与全量
        模式均返回且内容一致；故一次请求即可同时拿到日线与财报，无需为财报再发请求
        （百度对单 IP 请求频率敏感，少一次请求少一次限流风险）。
        """
        payload = self._request_payload(code, start_time, end_time, ktype=ktype, full=full)
        df = parse_baidu_response(payload, ktype=ktype)
        df = _filter_by_range(df, start_time, end_time)
        reports = parse_baidu_report_data(payload)
        return df, reports

    def fetch_report_dates(self, code: str, ktype: str = "1") -> list[str]:
        """返回该票的财报披露日列表（vapi 独有 ``reportData``，如 ['2024-04-03', ...]）。

        用于「财报事件」特征；与日 K 落库解耦，不改动 stock_daily_ohlcv 表结构。
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
