# -*- coding: utf-8 -*-
"""百度股市通 acs-token 自动获取（基于 Playwright 驱动页面内置 Paris/ACS SDK）。

背景
----
百度 vapi 系列接口（``vapi/v1/getquotation`` 等）强制要求 ``acs-token`` 请求头，
该 token 由百度前端风控 SDK（``window.paris_2108`` → ``getAcsInstance().getSign()``）
本地生成。**服务端严格校验签名**，纯本地伪造必然 403（已实测）。

因此采用「驱动页面 SDK 拿真 token」的方案：
- 用 Playwright 无头加载个股页，页面会自动加载 ParisFactory 并初始化 ``paris_2108``；
- 在页面内调用 ``getAcsInstance().getSign()`` 即可拿到服务端认可的 token；
- token 按 TTL 缓存，过期或 403 时自动刷新；浏览器实例常驻复用，刷新仅重算签名不重载页。

依赖：``pip install playwright`` 且 ``python -m playwright install chromium``。
若环境无法启动浏览器，则回退到 ``BAIDU_ACS_TOKEN`` 环境变量（需手动粘贴）。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .base import DataFetchError

logger = logging.getLogger(__name__)

# 个股页（加载后会注入 window.paris_2108）
_DEFAULT_PAGE = "https://finance.baidu.com/stock/ab-600519"
# token 默认有效期按保守的 10 分钟缓存；实际观察可更长，但刷新成本低，宁早勿晚
_DEFAULT_TTL = 600

# 在页面上下文内拿 token：getAcsInstance 回 (err, instance)，instance.getSign 回 (err, token)
_JS_GET_TOKEN = """
() => new Promise((resolve) => {
  const p = window.paris_2108;
  if (!p) return resolve({err: 'no paris_2108'});
  p.getAcsInstance((e, inst) => {
    if (e || !inst) return resolve({err: 'getAcsInstance fail: ' + (e && e.code)});
    inst.getSign((err, token) => {
      if (err) return resolve({err: 'getSign fail: ' + (err && err.code)});
      resolve({token: token});
    });
  });
})
"""


class BaiduTokenProvider:
    """按需获取并缓存百度 acs-token（Playwright 驱动页面 SDK）。

    典型用法::

        provider = BaiduTokenProvider()          # 懒加载浏览器
        token = provider.get_token()             # 命中缓存或自动刷新
        # 进程退出前：provider.close()
    """

    def __init__(
        self,
        page_url: str = _DEFAULT_PAGE,
        ttl_seconds: int = _DEFAULT_TTL,
        headless: bool = True,
        fallback_env_token: Optional[str] = None,
    ):
        self._page_url = page_url
        self._ttl = ttl_seconds
        self._headless = headless
        self._fallback = fallback_env_token
        self._token: Optional[str] = None
        self._token_time: float = 0.0
        self._pw = None
        self._browser = None
        self._page = None

    # ── 公共接口 ──
    def get_token(self, force: bool = False) -> str:
        """返回可用的 acs-token；必要时自动刷新。force=True 强制刷新。"""
        now = time.time()
        if not force and self._token and (now - self._token_time) < self._ttl:
            return self._token
        return self._refresh()

    @property
    def has_cached(self) -> bool:
        return bool(self._token)

    def close(self) -> None:
        """关闭浏览器与 Playwright 实例，释放资源。"""
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._page = None
        self._pw = None

    # ── 内部实现 ──
    def _ensure_browser(self) -> None:
        """懒启动浏览器并加载页面；失败抛 DataFetchError（提示环境/手动 token）。"""
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise DataFetchError(
                "BaiduTokenProvider 需要 playwright：pip install playwright && "
                "python -m playwright install chromium。或设置 BAIDU_ACS_TOKEN 手动注入。"
            ) from exc

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self._headless)
            self._page = self._browser.new_page()
            self._page.goto(self._page_url, wait_until="domcontentloaded", timeout=60000)
            self._page.wait_for_function("() => !!window.paris_2108", timeout=60000)
            # 给 SDK 一点初始化时间（acs 模块异步加载）
            self._page.wait_for_timeout(4000)
        except Exception as exc:
            self.close()
            raise DataFetchError(
                f"BaiduTokenProvider 启动浏览器失败：{exc}。可改用 BAIDU_ACS_TOKEN 手动注入。"
            ) from exc

    def _refresh(self) -> str:
        # 优先尝试页面 SDK；若浏览器不可用但有手动 token 则回退
        try:
            self._ensure_browser()
        except DataFetchError:
            if self._fallback:
                logger.warning("浏览器不可用，回退使用 BAIDU_ACS_TOKEN 手动 token。")
                self._token = self._fallback
                self._token_time = time.time()
                return self._token
            raise

        # 页面可能长时间 idle 后 SDK 失效：evaluate 失败则重载一次页面再试
        for attempt in (1, 2):
            try:
                res = self._page.evaluate(_JS_GET_TOKEN)
            except Exception as exc:
                if attempt == 1:
                    logger.warning("getSign 执行异常，重载页面后重试：%s", exc)
                    self._reload_page()
                    continue
                raise DataFetchError(f"BaiduTokenProvider 调用 getSign 失败：{exc}") from exc

            token = res.get("token") if isinstance(res, dict) else None
            # 百度 getSign 成功回调即为权威 token；其格式为 ``<ts>_<ts>_<base64>``
            # （如 1783425605921_...），并不以 P1_ 开头——此前误加前缀校验导致
            # 把真 token 当成「非 token」反复重载页面而失败。只要返回非空字符串即可。
            if isinstance(token, str) and token.strip():
                self._token = token
                self._token_time = time.time()
                return token

            # 拿到的是错误对象
            if attempt == 1:
                logger.warning("getSign 返回非 token（%s），重建页面后重试。", res)
                self._reload_page()
                continue
            raise DataFetchError(f"BaiduTokenProvider 获取 token 失败：{res}")

        raise DataFetchError("BaiduTokenProvider 无法获取 token（未知原因）。")

    def _reload_page(self) -> None:
        """重建页面上下文以强制 Paris SDK 重新注入 ``window.paris_2108``。

        仅 ``page.goto`` 同 URL 时 SDK 未必重新初始化，故直接关闭旧页面、
        开新页面并重载；等待上限放宽到 60s，降低网络抖动导致的超时失败。
        """
        try:
            try:
                if self._page is not None:
                    self._page.close()
            except Exception:  # noqa: BLE001
                pass
            self._page = self._browser.new_page()
            self._page.goto(self._page_url, wait_until="domcontentloaded", timeout=60000)
            self._page.wait_for_function("() => !!window.paris_2108", timeout=60000)
            # 给 SDK 异步加载 acs 模块留出时间
            self._page.wait_for_timeout(4000)
        except Exception as exc:  # noqa: BLE001
            logger.warning("重建页面失败：%s", exc)
