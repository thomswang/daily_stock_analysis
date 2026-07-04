# -*- coding: utf-8 -*-
"""
行情数据验证测试服务（独立启动，不依赖主 WebUI）。

用法：
  cd daily_stock_analysis
  WESTOCK_DATA_DIR=../westock-data python test/market_data/server.py

浏览器打开：http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# 项目根目录
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import setup_env

setup_env()

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("market_data_test")
_HERE = Path(__file__).resolve().parent

app = FastAPI(title="Market Data Verify", docs_url="/docs")


@app.exception_handler(Exception)
async def _unhandled_exception(_request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常也返回 JSON，避免前端解析纯文本 Internal Server Error。"""
    logger.exception("verify 服务未捕获异常")
    return JSONResponse(
        {"success": False, "error": str(exc), "errors": [str(exc)]},
        status_code=500,
    )


def _latest_weekday(d: Optional[date] = None) -> date:
    d = d or date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _json_safe(value: Any) -> Any:
    """将 NaN/Inf 转为 None，便于 JSON 序列化。"""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        try:
            return _json_safe(value.item())
        except Exception:  # noqa: BLE001
            pass
    return value


def _row_to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return _json_safe(obj)
    out: Dict[str, Any] = {}
    for col in getattr(obj, "__table__", {}).columns:
        val = getattr(obj, col.name, None)
        if isinstance(val, (date, datetime)):
            out[col.name] = val.isoformat()
        else:
            out[col.name] = val
    return _json_safe(out)


def _parse_raw_json(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_HERE / "index.html")


@app.get("/api/verify")
def verify(
    code: str = Query("600519", description="股票代码，如 600519"),
    quote_date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD，默认最近工作日"),
    save: bool = Query(False, description="是否拉取后写入 SQLite"),
) -> JSONResponse:
    """拉取 Tencent K 线 + westock quote，并对比 DB 已存数据。"""
    t0 = time.time()
    code = (code or "").strip().upper()
    if not code:
        return JSONResponse({"success": False, "error": "代码不能为空"}, status_code=400)

    try:
        d = date.fromisoformat(quote_date[:10]) if quote_date else _latest_weekday()
    except ValueError:
        return JSONResponse({"success": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400)

    from data_provider.westock_client import fetch_quote_snapshot, parse_quote_snapshot, to_westock_symbol
    from src.ingest import DailyIngestService
    from src.ingest.tencent_kline import TencentKlineIngestor
    from src.repositories.stock_repo import StockRepository

    repo = StockRepository()
    ingest = DailyIngestService(repo)
    kline_ingestor = TencentKlineIngestor()
    symbol = to_westock_symbol(code)
    errors: list[str] = []

    # ── 实时拉取 K 线（预览不写库；save 时入库）──
    kline_live: Dict[str, Any] = {}
    try:
        fetched = kline_ingestor.fetch(code, start=d, end=d)
        df = fetched.df
        if df is None or df.empty:
            kline_live["error"] = "K 线返回空"
        else:
            row = df.iloc[-1].to_dict()
            for k, v in list(row.items()):
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()[:10] if k == "date" else str(v)
            kline_live["source"] = fetched.source
            kline_live["fields"] = _json_safe({
                "date": d.isoformat(),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "last": row.get("last", row.get("close")),
                "volume": row.get("volume"),
                "amount": row.get("amount"),
                "exchange": row.get("exchange"),
            })
        if save:
            added = repo.save_dataframe(df, code, data_source=fetched.source)
            kline_live["rows_saved"] = added
    except Exception as exc:  # noqa: BLE001
        errors.append(f"K线: {exc}")
        kline_live["error"] = str(exc)

    # ── 实时拉取 quote ──
    quote_live: Dict[str, Any] = {"westock_symbol": symbol}
    try:
        if not symbol:
            raise ValueError("非 A 股 6 位代码，无法转 westock symbol")
        raw = fetch_quote_snapshot(code, d.isoformat())
        if not raw:
            quote_live["error"] = "quote 返回空"
        else:
            parsed = parse_quote_snapshot(raw, quote_date=d.isoformat())
            quote_live["fields"] = _json_safe({k: v for k, v in parsed.items() if k != "raw_json"})
            quote_live["raw_json"] = _json_safe(raw)
            quote_live["raw_key_count"] = len(raw.keys())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"quote: {exc}")
        quote_live["error"] = str(exc)

    saved = False
    if save:
        try:
            q = ingest.ingest_quote(code, start=d, end=d)
            quote_live["quote_rows_saved"] = q.quote_added
            saved = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"入库 quote: {exc}")

    # ── DB 快照 ──
    kline_db: Dict[str, Any] = {}
    quote_db: Dict[str, Any] = {}
    try:
        rows = repo.get_range(code, d, d)
        if rows:
            kline_db = _row_to_dict(rows[0])
        qrows = repo.get_quote_range(code, d, d)
        if qrows:
            quote_db = _row_to_dict(qrows[0])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"读库: {exc}")

    elapsed_ms = int((time.time() - t0) * 1000)
    return JSONResponse(_json_safe({
        "success": len(errors) == 0,
        "code": code,
        "date": d.isoformat(),
        "westock_symbol": symbol,
        "saved": saved,
        "elapsed_ms": elapsed_ms,
        "errors": errors,
        "kline_live": kline_live,
        "quote_live": quote_live,
        "kline_db": kline_db,
        "quote_db": quote_db,
    }))


app.mount("/static", StaticFiles(directory=str(_HERE)), name="static")


def main() -> None:
    import uvicorn

    host = os.getenv("MARKET_TEST_HOST", "127.0.0.1")
    port = int(os.getenv("MARKET_TEST_PORT", "8765"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(f"\n  行情验证页: http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
