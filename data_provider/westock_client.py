# -*- coding: utf-8 -*-
"""WeStock Data CLI 客户端（调用 westock-data/scripts/index.js）。

与 westock-data/test 对齐的两套接口：

| 命令           | 底层           | 一次返回 | 时间维度           | 用途           |
|----------------|----------------|----------|--------------------|----------------|
| kline          | K 线历史接口   | 多天     | 时间序列           | OHLCV → stock_daily |
| quote --date   | 行情快照接口   | 单天     | 截面（按天循环查） | 换手率等 → stock_daily_quote |

核心矛盾：kline 高效但仅 8 列；quote --date 字段全（含逐日 turnover_rate /
float_shares）但需按交易日逐天请求。test/index.html 的 runDailyK() 即后者逻辑。
"""

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
    candidates = [
        here.parents[2].parent / "westock-data" / "scripts" / "index.js",
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


def fetch_quote_snapshots_range(
    stock_code: str,
    *,
    start_date: str,
    end_date: str,
    batch_size: int = _DEFAULT_QUOTE_BATCH,
    sleep_between_batches: float = 0.0,
    timeout: float = _DEFAULT_TIMEOUT,
) -> List[Tuple[str, Dict[str, Any]]]:
    """按交易日循环 quote --date（并发分批，对齐 test/server.js + index.html runDailyK）。"""
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
    """将 quote --date JSON 映射为 stock_daily_quote 列。"""
    def _f(key: str) -> Optional[float]:
        return _to_float(raw.get(key))

    float_shares = _f("float_shares")
    if float_shares is not None and float_shares <= 0:
        float_shares = None
    total_shares = _f("total_shares")
    if total_shares is not None and total_shares <= 0:
        total_shares = None

    return {
        "turnover_rate": _f("turnover_rate"),
        "float_shares": float_shares,
        "total_shares": total_shares,
        "volume_ratio": _f("volume_ratio"),
        "range_pct": _f("range_pct"),
        "change_amount": _f("change"),
        "change_percent": _f("change_percent"),
        "pe_ratio": _f("pe_ratio"),
        "pb_ratio": _f("pb_ratio"),
        "total_market_cap": _f("total_market_cap"),
        "circulating_market_cap": _f("circulating_market_cap"),
        "prev_close": _f("prev_close"),
        "inner_volume": _f("inner_volume"),
        "outer_volume": _f("outer_volume"),
    }


def _parse_iso_date(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
