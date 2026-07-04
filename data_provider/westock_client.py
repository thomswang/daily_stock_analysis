# -*- coding: utf-8 -*-
"""WeStock Data CLI 客户端（调用 westock-data/scripts/index.js）。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import normalize_stock_code, is_bse_code
from data_provider.westock_fields import (
    DEFAULT_KLINE_ADJ,
    WESTOCK_A_SHARE_QUOTE_FLOAT_FIELDS,
    WESTOCK_A_SHARE_QUOTE_TEXT_FIELDS,
    WESTOCK_KLINE_PERSIST_FIELDS,
    WESTOCK_QUOTE_PERSIST_FIELDS,
    WESTOCK_QUOTE_TEXT_FIELDS,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 45.0
_DEFAULT_QUOTE_BATCH = 3


class WestockCliError(Exception):
    """westock-data CLI 调用失败。"""


def resolve_westock_index_js() -> Optional[str]:
    """解析 westock-data/scripts/index.js 路径。"""
    env_dir = (os.getenv("WESTOCK_DATA_DIR") or "").strip()
    if env_dir:
        candidate = Path(env_dir) / "scripts" / "index.js"
        if candidate.is_file():
            return str(candidate)

    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    candidates = [
        repo_root / ".claude" / "skills" / "westock-data" / "scripts" / "index.js",
        repo_root.parent / "westock-data" / "scripts" / "index.js",
        here.parents[1] / "westock-data" / "scripts" / "index.js",
        Path.cwd().parent / "westock-data" / "scripts" / "index.js",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def to_westock_symbol(stock_code: str) -> Optional[str]:
    """600519 / 600519.SH -> sh600519；北交所 -> bj*。"""
    code = normalize_stock_code(stock_code)
    if not code.isdigit() or len(code) != 6:
        return None
    if is_bse_code(code):
        return f"bj{code}"
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    return f"sz{code}"


def run_westock_raw(args: List[str], *, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    """执行 ``node index.js <args> --raw`` 并解析 JSON。"""
    index_js = resolve_westock_index_js()
    if not index_js:
        raise WestockCliError(
            "未找到 westock-data/scripts/index.js；请设置环境变量 WESTOCK_DATA_DIR"
        )

    cmd = ["node", index_js, *args, "--raw"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(Path(index_js).parents[1]),
            env={**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise WestockCliError(f"westock 超时 ({timeout}s): {' '.join(args)}") from exc
    except OSError as exc:
        raise WestockCliError(f"westock 进程启动失败: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise WestockCliError(f"westock 退出码 {proc.returncode}: {err[:500]}")

    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WestockCliError(f"westock JSON 解析失败: {raw[:200]}") from exc


def enum_weekday_dates(start_date: str, end_date: str) -> List[str]:
    """枚举区间内的工作日（与 test/index.html enumDates 一致，跳过周六日）。"""
    start = _parse_iso_date(start_date)
    end = _parse_iso_date(end_date)
    if start is None or end is None or start > end:
        return []

    dates: List[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return dates


def fetch_quote_snapshot(stock_code: str, quote_date: str) -> Optional[Dict[str, Any]]:
    """单日 quote --date 截面快照（westock-data/test 逐日循环用的接口）。"""
    symbol = to_westock_symbol(stock_code)
    if not symbol:
        return None
    payload = run_westock_raw(["quote", symbol, "--date", quote_date[:10]])
    if isinstance(payload, list) and payload:
        row = payload[0]
        return row if isinstance(row, dict) else None
    if isinstance(payload, dict):
        return payload
    return None


def fetch_profile_listed_dates(
    stock_codes: List[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Dict[str, Dict[str, Optional[str]]]:
    """批量 profile → {plain_code: {list_date, name}}（westock listedDate）。"""
    symbols: List[str] = []
    code_by_symbol: Dict[str, str] = {}
    for raw in stock_codes:
        plain = normalize_stock_code(raw)
        sym = to_westock_symbol(plain)
        if not sym:
            continue
        symbols.append(sym)
        code_by_symbol[sym] = plain

    if not symbols:
        return {}

    payload = run_westock_raw(["profile", ",".join(symbols)], timeout=timeout)
    rows: List[Any]
    if isinstance(payload, dict):
        rows = payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    out: Dict[str, Dict[str, Optional[str]]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "").strip().lower()
        plain = code_by_symbol.get(sym) or normalize_stock_code(sym)
        if not plain:
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        listed = None
        name = None
        if isinstance(data, dict):
            listed_raw = data.get("listedDate") or data.get("list_date")
            if listed_raw:
                parsed = _parse_iso_date(str(listed_raw)[:10])
                listed = parsed.isoformat() if parsed else str(listed_raw)[:10]
            name_val = data.get("name")
            name = str(name_val).strip() if name_val else None
        out[plain] = {"list_date": listed, "name": name}
    return out


def fetch_quote_snapshots_range(
    stock_code: str,
    *,
    start_date: str,
    end_date: str,
    batch_size: int = _DEFAULT_QUOTE_BATCH,
    sleep_between_batches: float = 0.0,
    timeout: float = _DEFAULT_TIMEOUT,
) -> List[Tuple[str, Dict[str, Any]]]:
    """按交易日循环 quote --date（并发分批，对齐 test/index.html runDailyK）。"""
    dates = enum_weekday_dates(start_date, end_date)
    if not dates:
        return []

    results: List[Tuple[str, Dict[str, Any]]] = []
    batch_size = max(1, int(batch_size))

    def _one(d: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        try:
            row = fetch_quote_snapshot(stock_code, d)
            return d, row
        except WestockCliError as exc:
            logger.debug("quote %s %s 失败: %s", stock_code, d, exc)
            return d, None

    for i in range(0, len(dates), batch_size):
        batch = dates[i : i + batch_size]
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            futures = [pool.submit(_one, d) for d in batch]
            for fut in as_completed(futures):
                d, row = fut.result()
                if row:
                    results.append((d, row))
        if sleep_between_batches > 0 and i + batch_size < len(dates):
            time.sleep(sleep_between_batches)

    results.sort(key=lambda x: x[0])
    return results


def parse_quote_to_record(raw: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """兼容旧调用：返回 parse_quote_snapshot 的数值子集。"""
    snap = parse_quote_snapshot(raw)
    return {k: snap.get(k) for k in WESTOCK_A_SHARE_QUOTE_FLOAT_FIELDS if k in snap}


def fetch_kline_range(
    stock_code: str,
    *,
    start_date: str,
    end_date: str,
    adj: str = DEFAULT_KLINE_ADJ,
    timeout: float = _DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """一次 westock kline 拉取区间日 K（默认前复权 qfq）。"""
    symbol = to_westock_symbol(stock_code)
    if not symbol:
        return []

    args = [
        "kline",
        symbol,
        "--period",
        "day",
        "--start",
        start_date[:10],
        "--end",
        end_date[:10],
        "--fq",
        adj,
    ]
    payload = run_westock_raw(args, timeout=timeout)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        if payload.get("success") is False:
            err = payload.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise WestockCliError(msg or "kline 查询失败")
        return [payload]
    return []


def parse_kline_row(
    raw: Dict[str, Any],
    *,
    adj: str = DEFAULT_KLINE_ADJ,
) -> Dict[str, Any]:
    """westock kline JSON → stock_daily_kline 行（last→close, exchange→turnover_rate）。"""
    record: Dict[str, Any] = {"adj_type": adj}
    for key in WESTOCK_KLINE_PERSIST_FIELDS:
        if key == "close":
            val = _to_float(raw.get("last") if raw.get("last") is not None else raw.get("close"))
        elif key == "turnover_rate":
            val = _to_float(raw.get("exchange") if raw.get("exchange") is not None else raw.get("turnover_rate"))
        else:
            val = _to_float(raw.get(key))
        record[key] = val

    d_str = raw.get("date")
    if d_str:
        parsed = _parse_iso_date(str(d_str)[:10])
        if parsed:
            record["date"] = parsed
    return record


def parse_quote_snapshot(
    raw: Dict[str, Any],
    *,
    quote_date: Optional[str] = None,
) -> Dict[str, Any]:
    """将 quote --date JSON 映射为 stock_daily_quote 行（键名与 index.html FIELD_DICT 一致）。"""
    record: Dict[str, Any] = {}

    for key in WESTOCK_A_SHARE_QUOTE_FLOAT_FIELDS:
        val = _to_float(raw.get(key))
        if key in ("float_shares", "total_shares") and val is not None and val <= 0:
            val = None
        record[key] = val

    for key in WESTOCK_A_SHARE_QUOTE_TEXT_FIELDS:
        val = raw.get(key)
        record[key] = str(val).strip() if val is not None and str(val).strip() else None

    d_str = quote_date or raw.get("date") or raw.get("time")
    if d_str:
        parsed = _parse_iso_date(str(d_str)[:10])
        if parsed:
            record["date"] = parsed

    return record


def _parse_iso_date(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() in ("", "--", "undefined", "null"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
