# -*- coding: utf-8 -*-
"""百度 K 线「浏览器驱动」抓取（规避百度反爬，已实测可行的方案）。

背景与根因（经实测验证）
------------------------
``baidu_fetcher.BaiduFetcher`` 用 ``requests`` 直连 vapi，并注入由浏览器生成的
``acs-token``。但在本机实测中，**任何经 Playwright / CDP 自动化协议驱动的浏览器**
（无论 Playwright 自带 Chromium、还是系统真实 Chrome，也无论有头/无头）请求
``getquotation`` 都会被百度风控拦截：

- 无头（headless）一律 403；
- 有头也多数被识别，返回 200 但内容是 HTML 拦截页（被 302 重定向到股票页）。

关键排查结论：
1. **与「登录 / cookie」无关**——匿名访问 finance.baidu.com 时浏览器会自动拿到
   ``BAIDUID`` / ``ab_sr`` / ``ppfuid`` 等匿名 cookie，与登录态无关；注入登录 cookie
   并不能解除风控。
2. **token 必须由「有头（交互式）真实 Chrome 会话」生成**才被服务端接受。无头或
   自动化会话签出的 token 会被拒（403 或 HTML 拦截页）。
3. **必须用「浏览器网络栈的原始请求（``page.request.get``）」而非页面内 ``fetch``**。
   页面内 ``fetch`` 会被 CORS / 风控识别并 302 到 HTML 拦截页；而 ``page.request.get``
   走浏览器网络栈、自动带匿名 cookie，等价于用户在 DevTools 里「Copy as cURL」的请求，
   能稳定返回 JSON。

最终方案（本模块采用，已端到端验证 200 + JSON）
---------------------------------------------
- 用系统**真实 Chrome** 启动（``channel="chrome"``），**有头模式**（``headless=False``），
  并加 ``--disable-blink-features=AutomationControlled`` + 注入脚本隐藏 ``navigator.webdriver``，
  使会话指纹尽量接近真人；
- 在页面内由 ``window.paris_2108`` SDK 现场签出新鲜 ``acs-token``；
- 用 ``page.request.get(url, headers={...acs-token...})`` 发原始请求，返回 JSON 解析。

整个过程不需要登录，也不需要读取本机 Chrome 的 cookie；浏览器访问时自动携带匿名 cookie。
代价：回填期间会有一个真实 Chrome 窗口常驻（全程复用同一实例，不重复拉起）。

依赖：``pip install playwright`` 且本机已安装 Chrome（``channel="chrome"`` 自动定位）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .base import DataFetchError
from .baidu_fetcher import (
    _BAIDU_KLINE_ENDPOINT,
    _filter_by_range,
    parse_baidu_report_data,
    parse_baidu_response,
    build_baidu_params,
)

logger = logging.getLogger(__name__)

_DEFAULT_PAGE = "https://finance.baidu.com/stock/ab-600519"

# 页面内取新鲜 token：getAcsInstance 回 (err, instance)，instance.getSign 回 (err, token)
_JS_GET_TOKEN = """
() => new Promise((resolve) => {
  const p = window.paris_2108;
  if (!p) return resolve({err: 'no paris_2108'});
  p.getAcsInstance((e, inst) => {
    if (e || !inst) return resolve({err: 'getAcsInstance fail: ' + (e && e.code)});
    inst.getSign((err, token) => resolve(token ? {token: token} : {err: 'getSign fail: ' + (err && err.code)}));
  });
})
"""

# 隐藏自动化特征：让 navigator.webdriver 返回 undefined（真实 Chrome 为 undefined）
_STEALTH_INIT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# 真实 Chrome 指纹更接近真人；有头模式是 token 被接受的前提
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


class BaiduBrowserFetcher:
    """浏览器驱动抓取（有头真实 Chrome + 页面内签 token + page.request.get 原始请求）。

    与 ``BaiduFetcher`` 提供相同的 ``fetch_kline_df`` / ``fetch_kline_and_reports``
    接口，可直接注入 ``BaiduKlineIngestor`` 使用，无需改动落库逻辑。
    """

    name = "BaiduBrowserFetcher"
    priority = 1
    allow_empty_daily_data = True

    def __init__(
        self,
        *,
        market_type: str = "ab",
        name: str = "",
        page_url: str = _DEFAULT_PAGE,
        headless: bool = False,
        executable_path: Optional[str] = None,
        channel: str = "chrome",
    ):
        self._market_type = market_type
        self._name = name
        # 默认有头真实 Chrome（headless=False 是 token 被接受的前提；见模块 docstring）
        self._headless = headless
        # 优先用系统真实 Chrome；可显式指定 executable_path 或切换到其他 channel
        self._executable_path = executable_path
        self._channel = channel if not executable_path else None
        self._page_url = page_url

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ── 公共接口（与 BaiduFetcher 对齐） ──
    def fetch_kline_and_reports(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
        full: bool = True,
    ) -> Tuple[Any, List[Dict[str, Any]]]:
        """页面内签新鲜 token，并用 page.request.get 原始请求拿到 K 线与财报披露事件。"""
        from urllib.parse import urlencode

        self._ensure_browser()
        params = build_baidu_params(
            code, start_time, end_time, ktype, full,
            market_type=self._market_type, name=self._name,
        )
        url = f"{_BAIDU_KLINE_ENDPOINT}?{urlencode(params)}"

        # token 一次性：先签一次；若遇 403 再签一次重试（仍失败则抛出）
        last_err: Optional[str] = None
        for attempt in (1, 2):
            try:
                payload = self._request_json(url)
            except DataFetchError as exc:
                last_err = str(exc)
                logger.warning("BaiduBrowserFetcher 第 %d 次请求失败：%s", attempt, exc)
                continue
            df = parse_baidu_response(payload, ktype=ktype)
            df = _filter_by_range(df, start_time, end_time)
            reports = parse_baidu_report_data(payload)
            return df, reports

        raise DataFetchError(
            f"BaiduBrowserFetcher 请求失败（已重试）：{last_err}。"
            "请确认：① 已安装系统 Chrome 且能被 Playwright 以 channel='chrome' 拉起；"
            "② 未强制 headless（百度要求有头会话签出的 token）；"
            "③ 当前网络/IP 未被百度限流（稍后重试）。"
        )

    def fetch_kline_df(
        self,
        code: str,
        start_time: str,
        end_time: Optional[str] = None,
        ktype: str = "1",
        full: bool = True,
    ) -> Any:
        df, _ = self.fetch_kline_and_reports(code, start_time, end_time, ktype=ktype, full=full)
        return df

    def close(self) -> None:
        """关闭页面 / 上下文 / 浏览器与 Playwright 实例，释放资源。"""
        for attr in ("_page", "_context", "_browser", "_pw"):
            obj = getattr(self, attr, None)
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
            setattr(self, attr, None)

    # ── 内部实现 ──
    def _request_json(self, url: str) -> Dict[str, Any]:
        """页面内签 token + page.request.get 原始请求，返回解析后的 JSON dict。"""
        import json

        token = self._get_token()
        if not token:
            raise DataFetchError("BaiduBrowserFetcher 无法签出 acs-token（paris_2108 未就绪）")
        headers = {
            "accept": "application/vnd.finance-web.v1+json",
            "acs-token": token,
            "origin": "https://finance.baidu.com",
            "referer": "https://finance.baidu.com/",
        }
        try:
            assert self._page is not None
            resp = self._page.request.get(url, headers=headers, timeout=30000)
        except Exception as exc:  # noqa: BLE001
            raise DataFetchError(f"BaiduBrowserFetcher 请求异常: {exc}") from exc

        status = resp.status
        text = resp.text()
        if status == 403:
            raise DataFetchError("BaiduBrowserFetcher 收到 403：token 被拒（可能无头/被限流）")
        if status != 200:
            raise DataFetchError(f"BaiduBrowserFetcher HTTP {status}: {text[:200]}")
        try:
            payload = json.loads(text)
        except Exception as exc:
            raise DataFetchError(f"BaiduBrowserFetcher 响应非 JSON: {exc} | 前100={text[:100]}") from exc
        if payload.get("ResultCode") not in (0, None):
            raise DataFetchError(
                f"BaiduBrowserFetcher ResultCode={payload.get('ResultCode')}: {payload.get('Result')}"
            )
        return payload

    def _get_token(self) -> Optional[str]:
        try:
            assert self._page is not None
            res = self._page.evaluate(_JS_GET_TOKEN)
        except Exception as exc:  # noqa: BLE001
            raise DataFetchError(f"BaiduBrowserFetcher 签 token 异常: {exc}") from exc
        if isinstance(res, dict) and res.get("token"):
            return res["token"]
        raise DataFetchError(f"BaiduBrowserFetcher 签 token 失败: {res}")

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DataFetchError(
                "BaiduBrowserFetcher 需要 playwright：pip install playwright"
            ) from exc

        logger.info(
            "BaiduBrowserFetcher 启动浏览器 channel=%s exe=%s headless=%s（要求有头真实 Chrome）",
            self._channel, self._executable_path, self._headless,
        )
        try:
            self._pw = sync_playwright().start()
            # 优先系统真实 Chrome；失败回退到 Playwright 自带 Chromium（仍保持有头）
            launch_kwargs: Dict[str, Any] = {
                "headless": self._headless,
                "args": _LAUNCH_ARGS,
            }
            if self._executable_path:
                launch_kwargs["executable_path"] = self._executable_path
            elif self._channel:
                launch_kwargs["channel"] = self._channel

            try:
                self._browser = self._pw.chromium.launch(**launch_kwargs)
            except Exception as exc:  # noqa: BLE001
                if self._channel and "channel" in launch_kwargs:
                    logger.warning("以 channel='%s' 启动失败（%s），回退到自带 Chromium", self._channel, exc)
                    launch_kwargs.pop("channel", None)
                    self._browser = self._pw.chromium.launch(**launch_kwargs)
                else:
                    raise

            self._context = self._browser.new_context()
            self._context.add_init_script(_STEALTH_INIT)
            self._page = self._context.new_page()
            logger.info("BaiduBrowserFetcher 打开个股页 %s", self._page_url)
            self._page.goto(self._page_url, wait_until="domcontentloaded", timeout=60000)
            logger.info("BaiduBrowserFetcher 等待 ACS SDK(window.paris_2108) 注入…")
            self._page.wait_for_function("() => !!window.paris_2108", timeout=60000)
            # 给 ACS SDK 异步初始化留出时间
            self._page.wait_for_timeout(4000)
            logger.info("BaiduBrowserFetcher 浏览器就绪，开始抓取")
        except Exception as exc:
            page_info = ""
            try:
                if self._page is not None:
                    page_info = f"（当前页 url={self._page.url} title={self._page.title()!r}）"
            except Exception:  # noqa: BLE001
                pass
            self.close()
            raise DataFetchError(
                f"BaiduBrowserFetcher 启动浏览器失败：{exc}{page_info}。"
                "默认用系统真实 Chrome（channel='chrome'，需本机已装 Chrome）；"
                "若强制 headless 会导致 token 被拒，请保持有头模式。"
            ) from exc
