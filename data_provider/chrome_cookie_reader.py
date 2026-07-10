# -*- coding: utf-8 -*-
"""【已废弃 / 不再使用】从本机已登录的 Chrome 提取 baidu 会话 cookie。

废弃原因（实测验证）
--------------------
百度 ``getquotation`` 的 403 与「登录 cookie」无关：匿名访问 finance.baidu.com 时
浏览器会自动拿到 ``BAIDUID`` / ``ab_sr`` / ``ppfuid`` 等匿名 cookie，与是否登录无关；
而百度的真正风控点是：① token 必须由「有头真实 Chrome 会话」签出；② 必须用
``page.request.get`` 原始请求（页面内 fetch 会被 302 到 HTML 拦截页）。因此
``BaiduBrowserFetcher`` 已改为「有头真实 Chrome + page.request.get」，不再读取/注入
本机 cookie。本模块保留仅供审计，请勿在新代码中引用。


背景
----
百度 K 线接口（``finance.pae.baidu.com/vapi/v1/getquotation``）对**匿名请求返回 403**，
必须携带已登录账号的会话 cookie（``ppfuid`` / ``ZFY`` / ``ab_sr`` / ``H_WISE_SIDS`` /
``H_PS_PSSID`` 等，用户浏览器 DevTools 的 curl 里可见）。Playwright 自带 Chromium 是
全新匿名上下文，缺这些 cookie → 403。

而直接拉起真实 Chrome profile（``launch_persistent_context``）在本机会**无限卡死**
（profile 被占用时新进程把请求交接给已有 Chrome 后退出）。因此这里只「读」真实
Chrome 的 cookie 库（DPAPI 解密），注入到自带 Chromium，既拿到登录态又避免卡死。

前提：运行前**关闭 Chrome**（否则 Cookies 库被锁，读不到最新值）。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_KEY_PREFIX = b"DPAPI"


def _dpapi_unprotect(data: bytes) -> bytes:
    """用 Windows DPAPI 解密（优先 pywin32，回退 ctypes）。"""
    try:
        import win32crypt  # type: ignore

        import win32cryptcon  # type: ignore

        blob = win32crypt.CryptUnprotectData(
            data, None, None, None, win32cryptcon.CRYPTPROTECT_UI_FORBIDDEN
        )
        return blob[1]
    except Exception:
        pass
    # ctypes 回退
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    p_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    p_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(p_in), None, None, None, None, 0, ctypes.byref(p_out)
    ):
        raise RuntimeError("CryptUnprotectData 失败")
    buf = ctypes.string_at(p_out.pbData, p_out.cbData)
    ctypes.windll.kernel32.LocalFree(p_out.pbData)
    return buf


def _get_aes_key(local_state_path: str) -> bytes:
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)
    raw = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    if raw.startswith(_KEY_PREFIX):
        raw = raw[len(_KEY_PREFIX):]
    return _dpapi_unprotect(raw)


def _decrypt_value(encrypted: bytes, aes_key: bytes) -> str:
    if encrypted[:3] in (b"v10", b"v11"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = encrypted[3:15]
        ciphertext = encrypted[15:]
        return AESGCM(aes_key).decrypt(nonce, ciphertext, None).decode("utf-8", "replace")
    # 旧版直接 DPAPI 加密
    return _dpapi_unprotect(encrypted).decode("utf-8", "replace")


def _read_profile_cookies(profile_dir: str, aes_key: bytes) -> List[Dict[str, object]]:
    candidates = [
        os.path.join(profile_dir, "Network", "Cookies"),
        os.path.join(profile_dir, "Cookies"),
    ]
    db_path = next((p for p in candidates if os.path.exists(p)), None)
    if db_path is None:
        return []

    tmp = None
    try:
        # 复制避免 Chrome 占用导致 SQLite 锁
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(tmp)
        rows = conn.execute(
            "SELECT host_key, name, value, encrypted_value, path, expires_utc, "
            "is_secure, is_httponly, samesite "
            "FROM cookies WHERE host_key LIKE '%baidu.com'"
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 Chrome cookie 库失败 %s: %s", db_path, exc)
        return []
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)

    out: List[Dict[str, object]] = []
    for host_key, name, value, enc, path, _exp, secure, httponly, _same in rows:
        try:
            v = value if value else _decrypt_value(bytes(enc), aes_key)
        except Exception:  # noqa: BLE001
            continue
        if not v:
            continue
        out.append(
            {
                "name": name,
                "value": v,
                "domain": host_key,
                "path": path or "/",
                "secure": bool(secure),
                "httpOnly": bool(httponly),
            }
        )
    return out


def read_baidu_cookies(user_data_dir: str) -> List[Dict[str, object]]:
    """从 Chrome ``User Data`` 目录读取所有 profile 的 baidu cookie。

    返回可直接传给 ``browser_context.add_cookies`` 的字典列表；
    任何步骤失败都返回空列表（调用方退化为匿名请求）。
    """
    local_state = os.path.join(user_data_dir, "Local State")
    if not os.path.exists(local_state):
        logger.warning("Chrome Local State 不存在：%s", local_state)
        return []

    try:
        aes_key = _get_aes_key(local_state)
    except Exception as exc:  # noqa: BLE001
        logger.warning("解密 Chrome cookie 密钥失败：%s", exc)
        return []

    merged: List[Dict[str, object]] = []
    seen = set()
    # 扫描常见 profile 目录
    sub_dirs = ["Default", "Profile 1", "Profile 2", "Profile 3", "Guest Profile"]
    # 也包含 User Data 下任意 "Profile N"
    if os.path.isdir(user_data_dir):
        for entry in os.listdir(user_data_dir):
            full = os.path.join(user_data_dir, entry)
            if os.path.isdir(full) and (entry.startswith("Profile") or entry == "Default" or entry == "Guest Profile"):
                if entry not in sub_dirs:
                    sub_dirs.append(entry)

    for sub in sub_dirs:
        profile_dir = os.path.join(user_data_dir, sub)
        if not os.path.isdir(profile_dir):
            continue
        for c in _read_profile_cookies(profile_dir, aes_key):
            key = (c["domain"], c["name"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)

    if merged:
        logger.info("已从 Chrome 提取 %d 个 baidu cookie（含登录态）", len(merged))
    else:
        logger.warning("未从 Chrome 提取到任何 baidu cookie（请确认已登录百度且已关闭 Chrome）")
    return merged
