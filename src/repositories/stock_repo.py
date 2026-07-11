# -*- coding: utf-8 -*-
"""
===================================
股票数据访问层
===================================

职责：
1. 封装股票数据的数据库操作
2. 提供日线数据查询接口
"""

import logging
import os
from datetime import date, timedelta
from typing import Optional, List, Dict, Any, Iterable, Tuple

import pandas as pd
from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, StockDaily, StockDailyKline, StockDailyQuote, StockDailyOhlcv

logger = logging.getLogger(__name__)


# 训练批量预读：每批 IN 子句包含的股票数（SQLite 绑定参数上限 ~999）
DEFAULT_TRAIN_BULK_BATCH = int(os.getenv("TRAIN_BULK_BATCH", "500"))

# 与 prediction_service._load_cached_df 一致：多取日历日以保证 rolling 后样本够
def compute_training_date_range(
    lookback_days: int,
    *,
    end_date: Optional[date] = None,
) -> Tuple[date, date]:
    end_d = end_date or date.today()
    start_d = end_d - timedelta(days=int((lookback_days + 90) * 1.6) + 30)
    return start_d, end_d


def _normalize_codes(codes: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in codes:
        code = (raw or "").strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _legacy_turnover_fallback() -> bool:
    """是否允许从 stock_daily 的旧 turnover_rate / float_shares / volume_ratio 列兜底读取。

    默认 False——stock_daily_quote 是唯一权威源。仅在需要兼容旧库时打开：
        export DSA_LEGACY_TURNOVER_FALLBACK=1
    """
    return os.getenv("DSA_LEGACY_TURNOVER_FALLBACK", "").lower() in ("1", "true", "yes")


class StockRepository:
    """
    股票数据访问层
    
    封装 StockDaily 表的数据库操作
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据访问层
        
        Args:
            db_manager: 数据库管理器（可选，默认使用单例）
        """
        self.db = db_manager or DatabaseManager.get_instance()
    
    def get_latest(self, code: str, days: int = 2) -> List[StockDaily]:
        """
        获取最近 N 天的数据
        
        Args:
            code: 股票代码
            days: 获取天数
            
        Returns:
            StockDaily 对象列表（按日期降序）
        """
        try:
            return self.db.get_latest_data(code, days)
        except Exception as e:
            logger.error(f"获取最新数据失败: {e}")
            return []
    
    def get_range(
        self,
        code: str,
        start_date: date,
        end_date: date
    ) -> List[StockDaily]:
        """
        获取指定日期范围的数据
        
        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            StockDaily 对象列表
        """
        try:
            return self.db.get_data_range(code, start_date, end_date)
        except Exception as e:
            logger.error(f"获取日期范围数据失败: {e}")
            return []
    
    def save_dataframe(
        self,
        df: pd.DataFrame,
        code: str,
        data_source: str = "Unknown"
    ) -> int:
        """
        保存 DataFrame 到数据库
        
        Args:
            df: 包含日线数据的 DataFrame
            code: 股票代码
            data_source: 数据来源
            
        Returns:
            保存的记录数
        """
        try:
            return self.db.save_daily_data(df, code, data_source)
        except Exception as e:
            logger.error(f"保存日线数据失败: {e}")
            return 0
    
    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查是否有指定日期的数据
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            是否存在数据
        """
        try:
            return self.db.has_today_data(code, target_date)
        except Exception as e:
            logger.error(f"检查数据存在失败: {e}")
            return False
    
    def get_analysis_context(
        self, 
        code: str, 
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取分析上下文
        
        Args:
            code: 股票代码
            target_date: 目标日期
            
        Returns:
            分析上下文字典
        """
        try:
            return self.db.get_analysis_context(code, target_date)
        except Exception as e:
            logger.error(f"获取分析上下文失败: {e}")
            return None

    def get_start_daily(self, *, code: str, analysis_date: date) -> Optional[StockDaily]:
        """Return StockDaily for analysis_date (preferred) or nearest previous date."""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date <= analysis_date))
                .order_by(desc(StockDaily.date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_daily_on_date(self, *, code: str, target_date: date) -> Optional[StockDaily]:
        """Return StockDaily for the exact target_date without trading-day fallback."""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date == target_date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_quote_range(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> List[StockDailyQuote]:
        """获取 stock_daily_quote 指定日期范围的截面数据。"""
        try:
            return self.db.get_daily_quote_range(code, start_date, end_date)
        except Exception as e:
            logger.error(f"获取 quote 截面失败: {e}")
            return []

    def get_range_merged(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> List[Dict[str, Any]]:
        """K 线 + quote 截面 join（按 date 对齐，供训练/特征工程）。

        权威源：换手率 / 流通股本 / 量比 来自 stock_daily_quote；
        stock_daily 的同名列已 deprecated（详见 storage.py::StockDaily）。
        如需兼容旧库，设置 ``DSA_LEGACY_TURNOVER_FALLBACK=1`` 打开兜底。
        """
        daily_rows = self.get_range(code, start_date, end_date)
        quote_rows = self.get_quote_range(code, start_date, end_date)
        quote_by_date = {r.date: r for r in quote_rows}
        allow_legacy = _legacy_turnover_fallback()
        merged: List[Dict[str, Any]] = []
        for row in daily_rows:
            q = quote_by_date.get(row.date)
            if q is not None:
                turnover_rate = q.turnover_rate
                float_shares = q.float_shares
                volume_ratio = q.volume_ratio
            elif allow_legacy:
                turnover_rate = getattr(row, "turnover_rate", None)
                float_shares = getattr(row, "float_shares", None)
                volume_ratio = row.volume_ratio
            else:
                turnover_rate = None
                float_shares = None
                volume_ratio = None
            merged.append({
                "date": row.date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "amount": row.amount,
                "pct_chg": row.pct_chg,
                "turnover_rate": turnover_rate,
                "float_shares": float_shares,
                "volume_ratio": volume_ratio,
            })
        return merged

    def get_coverage(self, code: str) -> Dict[str, Any]:
        """查询某股票在 stock_daily 里已存的最早/最晚日期与条数。

        供历史回填的断点续传判定使用（DB 为数据真相源）。

        Returns:
            {"first": date|None, "last": date|None, "rows": int}
        """
        try:
            with self.db.get_session() as session:
                first, last, cnt = session.execute(
                    select(
                        func.min(StockDaily.date),
                        func.max(StockDaily.date),
                        func.count(),
                    ).where(StockDaily.code == code)
                ).one()
            return {"first": first, "last": last, "rows": int(cnt or 0)}
        except Exception as e:
            logger.error(f"查询 {code} 数据覆盖范围失败: {e}")
            return {"first": None, "last": None, "rows": 0}

    def get_forward_bars(self, *, code: str, analysis_date: date, eval_window_days: int) -> List[StockDaily]:
        """Return forward daily bars after analysis_date, up to eval_window_days."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date > analysis_date))
                .order_by(StockDaily.date)
                .limit(eval_window_days)
            ).scalars().all()
            return list(rows)

    def load_merged_bulk(
        self,
        codes: List[str],
        start_date: date,
        end_date: date,
        *,
        batch_size: int = DEFAULT_TRAIN_BULK_BATCH,
    ) -> Dict[str, pd.DataFrame]:
        """批量读 stock_daily_quote（单表；训练预加载暂用，后续可改专用 API）。

        OHLC 来自 quote；close = coalesce(last, price)。
        """
        norm_codes = _normalize_codes(codes)
        if not norm_codes:
            return {}

        batch_size = max(1, int(batch_size))
        result: Dict[str, pd.DataFrame] = {}
        try:
            with self.db.get_session() as session:
                conn = session.connection()
                for i in range(0, len(norm_codes), batch_size):
                    batch = norm_codes[i : i + batch_size]
                    stmt = (
                        select(StockDailyQuote)
                        .where(
                            StockDailyQuote.code.in_(batch),
                            StockDailyQuote.date >= start_date,
                            StockDailyQuote.date <= end_date,
                        )
                        .order_by(StockDailyQuote.code, StockDailyQuote.date)
                    )
                    rows = session.execute(stmt).scalars().all()
                    if not rows:
                        continue
                    records: List[Dict[str, Any]] = []
                    for row in rows:
                        rec: Dict[str, Any] = {
                            "code": row.code,
                            "date": row.date,
                            "open": row.open,
                            "high": row.high,
                            "low": row.low,
                            "volume": row.volume,
                            "amount": row.amount,
                            "turnover_rate": getattr(row, "turnover_rate", None),
                            "float_shares": getattr(row, "float_shares", None),
                            "volume_ratio": getattr(row, "volume_ratio", None),
                            "change": getattr(row, "change", None),
                            "change_percent": getattr(row, "change_percent", None),
                        }
                        last = getattr(row, "price", None)
                        rec["last"] = last
                        rec["close"] = last
                        records.append(rec)
                    chunk = pd.DataFrame(records)
                    chunk["code"] = chunk["code"].astype(str).str.upper()
                    for code, group in chunk.groupby("code", sort=False):
                        g = group.drop(columns=["code"]).sort_values("date").reset_index(drop=True)
                        result[str(code).upper()] = g
        except Exception as exc:
            logger.error("批量读 quote 失败: %s", exc)
            raise
        return result

    def load_merged_df(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """单票读 stock_daily_quote（训练暂用，后续可改）。"""
        bulk = self.load_merged_bulk(
            [code], start_date, end_date, batch_size=1,
        )
        return bulk.get((code or "").strip().upper(), pd.DataFrame())

    def get_kline_coverage(
        self,
        code: str,
        *,
        adj_type: str = "qfq",
    ) -> Dict[str, Any]:
        """查询 stock_daily_kline 已存最早/最晚日期与条数。"""
        try:
            with self.db.get_session() as session:
                first, last, cnt = session.execute(
                    select(
                        func.min(StockDailyKline.date),
                        func.max(StockDailyKline.date),
                        func.count(),
                    ).where(
                        and_(
                            StockDailyKline.code == code,
                            StockDailyKline.adj_type == adj_type,
                        )
                    )
                ).one()
            return {"first": first, "last": last, "rows": int(cnt or 0)}
        except Exception as e:
            logger.error("查询 %s kline 覆盖失败: %s", code, e)
            return {"first": None, "last": None, "rows": 0}

    def get_ohlcv_coverage(
        self,
        code: str,
        *,
        ktype: str = "1",
        adj_type: str = "qfq",
        data_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """查询 stock_daily_ohlcv 已存最早/最晚日期与条数（源无关）。"""
        try:
            with self.db.get_session() as session:
                stmt = select(
                    func.min(StockDailyOhlcv.date),
                    func.max(StockDailyOhlcv.date),
                    func.count(),
                ).where(
                    and_(
                        StockDailyOhlcv.code == code,
                        StockDailyOhlcv.ktype == ktype,
                        StockDailyOhlcv.adj_type == adj_type,
                    )
                )
                if data_source:
                    stmt = stmt.where(StockDailyOhlcv.data_source == data_source)
                first, last, cnt = session.execute(stmt).one()
            return {"first": first, "last": last, "rows": int(cnt or 0)}
        except Exception as e:
            logger.error("查询 %s ohlcv 覆盖失败: %s", code, e)
            return {"first": None, "last": None, "rows": 0}

    def load_kline_bulk(
        self,
        codes: List[str],
        start_date: date,
        end_date: date,
        *,
        batch_size: int = DEFAULT_TRAIN_BULK_BATCH,
        adj_type: str = "qfq",
    ) -> Dict[str, pd.DataFrame]:
        """批量读 stock_daily_kline（前复权 OHLCV，供技术因子训练）。

        注意：amount/turnover_rate 列恒为 NULL（fqkline 不含），读取后为 NaN。
        build_features 对 turnover 缺失填 0（可选特征），不影响训练。
        """
        norm_codes = _normalize_codes(codes)
        if not norm_codes:
            return {}

        # 库里 stock_daily_kline.code 存的是带交易所后缀格式(如 600519.SH)，
        # 而训练/预测传入的 code 多为裸代码(如 600519)。为兼容两种格式，
        # 查询时同时枚举裸码与其 .SH/.SZ/.BJ 后缀变体。
        _SUFFIXES = (".SH", ".SZ", ".BJ")
        query_codes: List[str] = []
        _seen: set = set()
        for nc in norm_codes:
            cands = [nc] if "." in nc else [nc, *(nc + s for s in _SUFFIXES)]
            for c in cands:
                if c not in _seen:
                    _seen.add(c)
                    query_codes.append(c)

        batch_size = max(1, int(batch_size))
        result: Dict[str, pd.DataFrame] = {}
        try:
            with self.db.get_session() as session:
                for i in range(0, len(query_codes), batch_size):
                    batch = query_codes[i : i + batch_size]
                    stmt = (
                        select(StockDailyKline)
                        .where(
                            StockDailyKline.code.in_(batch),
                            StockDailyKline.adj_type == adj_type,
                            StockDailyKline.date >= start_date,
                            StockDailyKline.date <= end_date,
                        )
                        .order_by(StockDailyKline.code, StockDailyKline.date)
                    )
                    rows = session.execute(stmt).scalars().all()
                    if not rows:
                        continue
                    records: List[Dict[str, Any]] = []
                    for row in rows:
                        records.append({
                            "code": row.code,
                            "date": row.date,
                            "open": row.open,
                            "high": row.high,
                            "low": row.low,
                            "close": row.close,
                            "volume": row.volume,
                            "amount": row.amount,
                            "turnover_rate": row.turnover_rate,
                        })
                    chunk = pd.DataFrame(records)
                    chunk["code"] = chunk["code"].astype(str).str.upper()
                    for code, group in chunk.groupby("code", sort=False):
                        g = group.drop(columns=["code"]).sort_values("date").reset_index(drop=True)
                        # 以裸代码为 key，兼容上层 df_cache[裸code] 命中
                        bare = str(code).split(".")[0].upper()
                        result[bare] = g
        except Exception as exc:
            logger.error("批量读 kline 失败: %s", exc)
            raise
        return result

    def load_kline_df(
        self,
        code: str,
        start_date: date,
        end_date: date,
        *,
        adj_type: str = "qfq",
    ) -> pd.DataFrame:
        bulk = self.load_kline_bulk(
            [code], start_date, end_date, batch_size=1, adj_type=adj_type,
        )
        key = (code or "").strip().upper()
        if key in bulk:
            return bulk[key]
        # load_kline_bulk 以裸码(000001)为 key，票池常传带后缀全码(000001.SZ)，
        # 全码未命中时回退按裸码查，避免误判「本地无数据」而回退联网。
        return bulk.get(key.split(".")[0], pd.DataFrame())

    # 输出列契约：与训练/预测特征层（build_features）对齐。turnoverratio 在此
    # 重命名为 turnover_rate，使换手率特征（turnover_norm/turnover_rel）激活。
    _OHLCV_OUT_COLS = [
        "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate",
    ]

    def load_ohlcv_bulk(
        self,
        codes: List[str],
        start_date: date,
        end_date: date,
        *,
        batch_size: int = DEFAULT_TRAIN_BULK_BATCH,
        ktype: str = "1",
        adj_type: str = "qfq",
    ) -> Dict[str, pd.DataFrame]:
        """批量读 stock_daily_ohlcv（源无关通用层，供训练/预测/打分统一取数）。

        相比 stock_daily_kline，本表单表自带 turnoverratio（换手率）/amount（成交额），
        故加载时把 turnoverratio 重命名为 turnover_rate，激活换手率类特征。

        约定（与 load_kline_bulk 对齐，保证 df_cache 命中）：
          - 裸码/带后缀(.SH/.SZ/.BJ)兼容查询，结果以「裸码 upper」为 key；
          - 默认过滤 ktype='1'（日线）、adj_type='qfq'（前复权）；
          - 唯一键含 data_source，同 (code,date) 可能多行，按 date 去重(keep=last)，
            避免重复交易日污染 rolling 特征。
        """
        norm_codes = _normalize_codes(codes)
        if not norm_codes:
            return {}

        _SUFFIXES = (".SH", ".SZ", ".BJ")
        query_codes: List[str] = []
        _seen: set = set()
        for nc in norm_codes:
            cands = [nc] if "." in nc else [nc, *(nc + s for s in _SUFFIXES)]
            for c in cands:
                if c not in _seen:
                    _seen.add(c)
                    query_codes.append(c)

        batch_size = max(1, int(batch_size))
        result: Dict[str, pd.DataFrame] = {}
        try:
            with self.db.get_session() as session:
                for i in range(0, len(query_codes), batch_size):
                    batch = query_codes[i : i + batch_size]
                    stmt = (
                        select(StockDailyOhlcv)
                        .where(
                            StockDailyOhlcv.code.in_(batch),
                            StockDailyOhlcv.ktype == ktype,
                            StockDailyOhlcv.adj_type == adj_type,
                            StockDailyOhlcv.date >= start_date,
                            StockDailyOhlcv.date <= end_date,
                        )
                        .order_by(StockDailyOhlcv.code, StockDailyOhlcv.date)
                    )
                    rows = session.execute(stmt).scalars().all()
                    if not rows:
                        continue
                    records: List[Dict[str, Any]] = []
                    for row in rows:
                        records.append({
                            "code": row.code,
                            "date": row.date,
                            "open": row.open,
                            "high": row.high,
                            "low": row.low,
                            "close": row.close,
                            "volume": row.volume,
                            "amount": row.amount,
                            # 换手率：ohlcv 列名 turnoverratio → 统一契约 turnover_rate
                            "turnover_rate": row.turnoverratio,
                        })
                    chunk = pd.DataFrame(records)
                    chunk["code"] = chunk["code"].astype(str).str.upper()
                    for code, group in chunk.groupby("code", sort=False):
                        g = (
                            group.drop(columns=["code"])
                            .sort_values("date")
                            # 跨 data_source 同一交易日去重，保留最后一条
                            .drop_duplicates(subset=["date"], keep="last")
                            .reset_index(drop=True)
                        )
                        # 以裸代码为 key，兼容上层 df_cache[裸code] 命中
                        bare = str(code).split(".")[0].upper()
                        result[bare] = g
        except Exception as exc:
            logger.error("批量读 ohlcv 失败: %s", exc)
            raise
        return result

    def load_ohlcv_df(
        self,
        code: str,
        start_date: date,
        end_date: date,
        *,
        ktype: str = "1",
        adj_type: str = "qfq",
    ) -> pd.DataFrame:
        """单票读 stock_daily_ohlcv（内部走 load_ohlcv_bulk 复用同一取数与去重逻辑）。"""
        bulk = self.load_ohlcv_bulk(
            [code], start_date, end_date, batch_size=1,
            ktype=ktype, adj_type=adj_type,
        )
        key = (code or "").strip().upper()
        if key in bulk:
            return bulk[key]
        return bulk.get(key.split(".")[0], pd.DataFrame())
