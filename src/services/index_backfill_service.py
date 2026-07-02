# -*- coding: utf-8 -*-
"""
=========================================
大盘指数日线回填服务（Index Backfill）
=========================================

目标：把 A 股主要宽基指数的历史日线灌入本地 stock_daily 缓存，作为预测建模的
「大盘环境 / 相对强弱」特征来源（个股短期涨跌很大程度由大盘 β 驱动）。

取数策略（无需 TUSHARE_TOKEN）：
    akshare 新浪指数日线 ak.stock_zh_index_daily(symbol="sh000300")
    → 返回 date/open/high/low/close/volume 全历史
    → 以 canonical 代码（如 000300.SH）幂等 upsert 进 stock_daily（复用同一张表）

指数与个股同表存储：code 唯一区分（如 000300.SH），互不冲突；建模时按已知
指数代码显式取用。

⚠️ 数据仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# 默认回填的宽基指数：canonical 代码 -> (中文名, 新浪 symbol)
# 覆盖大盘(上证/沪深300)、中盘(中证500)、小盘(中证1000)、成长(创业板/深成)。
DEFAULT_INDEXES: Dict[str, Tuple[str, str]] = {
    "000001.SH": ("上证指数", "sh000001"),
    "000300.SH": ("沪深300", "sh000300"),
    "000905.SH": ("中证500", "sh000905"),
    "000852.SH": ("中证1000", "sh000852"),
    "399001.SZ": ("深证成指", "sz399001"),
    "399006.SZ": ("创业板指", "sz399006"),
}


class IndexBackfillError(Exception):
    """指数回填流程可预期的业务错误（数据源不可用等）。"""


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class IndexBackfillService:
    """基于 akshare 的宽基指数日线回填。"""

    def __init__(self, repo=None):
        self._repo = repo  # 延迟初始化，避免导入期触发 DB

    @property
    def repo(self):
        if self._repo is None:
            from src.repositories.stock_repo import StockRepository

            self._repo = StockRepository()
        return self._repo

    def _fetch_one(self, sina_symbol: str, *, retry: int = 1):
        """拉取单个指数全历史日线，返回 DataFrame(date/open/high/low/close/volume)。"""
        import akshare as ak

        last_err: Optional[str] = None
        for attempt in range(retry + 1):
            try:
                df = ak.stock_zh_index_daily(symbol=sina_symbol)
                if df is not None and not df.empty:
                    return df, None
                last_err = "返回空数据"
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retry:
                time.sleep(0.8 * (attempt + 1))
        return None, last_err

    def run(
        self,
        codes: Optional[List[str]] = None,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sleep: float = 0.5,
        retry: int = 1,
    ) -> Dict[str, Any]:
        """回填指定指数（默认全部宽基指数）的日线到 stock_daily。

        Args:
            codes: canonical 指数代码列表（如 ["000300.SH"]），None=全部默认指数
            start_date/end_date: 只保留该区间的行（默认全历史）
            sleep: 每个指数请求后的限流秒数
            retry: 单个指数失败重试次数
        """
        try:
            import akshare  # noqa: F401
        except ImportError as exc:  # noqa: BLE001
            raise IndexBackfillError("未安装 akshare，无法获取指数日线（pip install akshare）") from exc

        import pandas as pd

        targets: List[str]
        if codes:
            targets = [c.strip().upper() for c in codes if c.strip()]
        else:
            targets = list(DEFAULT_INDEXES.keys())

        start_d = _parse_date(start_date)
        end_d = _parse_date(end_date)

        stats = {"total": len(targets), "fetched": 0, "empty": 0, "failed": 0, "rows_added": 0}
        logger.info("开始回填指数日线：%d 个，区间 %s ~ %s",
                    len(targets), start_date or "全史", end_date or "今天")

        for i, code in enumerate(targets, 1):
            meta = DEFAULT_INDEXES.get(code)
            if not meta:
                logger.warning("[%d/%d] %s 未在已知指数映射中，跳过（可扩展 DEFAULT_INDEXES）",
                               i, len(targets), code)
                stats["failed"] += 1
                continue
            name, sina_symbol = meta

            df, err = self._fetch_one(sina_symbol, retry=retry)
            if sleep > 0:
                time.sleep(sleep)

            if err is not None or df is None:
                logger.warning("[%d/%d] %s(%s) 拉取失败：%s", i, len(targets), code, name, err)
                stats["failed"] += 1
                continue

            df = df.copy()
            # 统一列名（新浪指数日线列：date/open/high/low/close/volume）
            df.columns = [str(c).strip().lower() for c in df.columns]
            if "date" not in df.columns:
                logger.warning("[%d/%d] %s(%s) 返回缺少 date 列，跳过", i, len(targets), code, name)
                stats["failed"] += 1
                continue

            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
            df = df.dropna(subset=["date"])
            if start_d is not None:
                df = df[df["date"] >= start_d]
            if end_d is not None:
                df = df[df["date"] <= end_d]

            if df.empty:
                logger.info("[%d/%d] %s(%s) 区间内无数据", i, len(targets), code, name)
                stats["empty"] += 1
                continue

            added = self.repo.save_dataframe(df, code, data_source="akshare_index")
            stats["fetched"] += 1
            stats["rows_added"] += int(added)
            logger.info("[%d/%d] %s(%s) → 保存 %d 行（新增 %d），区间 %s ~ %s",
                        i, len(targets), code, name, len(df), int(added),
                        df["date"].min(), df["date"].max())

        logger.info("指数回填结束：成功 %d / 空 %d / 失败 %d，新增行 %d",
                    stats["fetched"], stats["empty"], stats["failed"], stats["rows_added"])
        return stats
