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

from src.storage import DatabaseManager, StockDaily, StockDailyKline, StockDailyQuote

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
        """quote 单表按 date 读取（westock 全字段）。"""
        df = self.load_merged_df(code, start_date, end_date)
        if df.empty:
            return []
        return df.to_dict(orient="records")

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

    def get_coverage(self, code: str) -> Dict[str, Any]:
        """查询 stock_daily_quote 已存最早/最晚日期与条数（单表权威源）。"""
        return self.get_quote_coverage(code)

    def get_quote_coverage(self, code: str) -> Dict[str, Any]:
        """查询 stock_daily_quote 已存最早/最晚日期与条数。"""
        try:
            with self.db.get_session() as session:
                first, last, cnt = session.execute(
                    select(
                        func.min(StockDailyQuote.date),
                        func.max(StockDailyQuote.date),
                        func.count(),
                    ).where(StockDailyQuote.code == code)
                ).one()
            return {"first": first, "last": last, "rows": int(cnt or 0)}
        except Exception as e:
            logger.error(f"查询 {code} quote 覆盖失败: {e}")
            return {"first": None, "last": None, "rows": 0}

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

        batch_size = max(1, int(batch_size))
        result: Dict[str, pd.DataFrame] = {}
        try:
            with self.db.get_session() as session:
                for i in range(0, len(norm_codes), batch_size):
                    batch = norm_codes[i : i + batch_size]
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
                        result[str(code).upper()] = g
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
        return bulk.get((code or "").strip().upper(), pd.DataFrame())

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
