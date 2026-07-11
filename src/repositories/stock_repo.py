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
from sqlalchemy import and_, bindparam, desc, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from src.storage import DatabaseManager, StockDailyQuote, StockDailyOhlcv

logger = logging.getLogger(__name__)


# 训练批量预读：每批 IN 子句包含的股票数（SQLite 绑定参数上限 ~999）
DEFAULT_TRAIN_BULK_BATCH = int(os.getenv("TRAIN_BULK_BATCH", "500"))

# 全量历史起点：lookback_days<=0 时从此日期加载该票全部本地历史（不指定回溯=全量）
FULL_HISTORY_START = date(2000, 1, 1)


# 与 prediction_service._load_cached_df 一致：多取日历日以保证 rolling 后样本够
def compute_training_date_range(
    lookback_days: Optional[int],
    *,
    end_date: Optional[date] = None,
) -> Tuple[date, date]:
    """推算训练/预测取数的 [start, end] 日期窗口。

    - end：截止时间，end_date 指定则用之（留出近期做样本外），否则到今天（最新）。
    - start：lookback_days>0 时按回溯天数从 end 倒推；lookback_days<=0/None 表示
      「全量历史」，start 取 FULL_HISTORY_START，加载该票全部本地历史。
    """
    end_d = end_date or date.today()
    if lookback_days is None or lookback_days <= 0:
        return FULL_HISTORY_START, end_d
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
    
    封装 stock_daily_ohlcv / stock_daily_quote 表的数据库操作
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据访问层
        
        Args:
            db_manager: 数据库管理器（可选，默认使用单例）
        """
        self.db = db_manager or DatabaseManager.get_instance()
    
    def get_latest(self, code: str, days: int = 2) -> List[StockDailyOhlcv]:
        """
        获取最近 N 天的数据
        
        Args:
            code: 股票代码
            days: 获取天数
            
        Returns:
            StockDailyOhlcv 对象列表（按日期降序）
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
    ) -> List[StockDailyOhlcv]:
        """
        获取指定日期范围的数据
        
        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            StockDailyOhlcv 对象列表
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

    def get_start_daily(self, *, code: str, analysis_date: date) -> Optional[StockDailyOhlcv]:
        """返回 analysis_date 当日（优先）或最近的前一交易日的日线（stock_daily_ohlcv）。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDailyOhlcv)
                .where(and_(
                    StockDailyOhlcv.code == code,
                    StockDailyOhlcv.ktype == "1",
                    StockDailyOhlcv.adj_type == "qfq",
                    StockDailyOhlcv.date <= analysis_date,
                ))
                .order_by(desc(StockDailyOhlcv.date), desc(StockDailyOhlcv.id))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_daily_on_date(self, *, code: str, target_date: date) -> Optional[StockDailyOhlcv]:
        """返回精确 target_date 当日的日线（不做交易日回退），读 stock_daily_ohlcv。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDailyOhlcv)
                .where(and_(
                    StockDailyOhlcv.code == code,
                    StockDailyOhlcv.ktype == "1",
                    StockDailyOhlcv.adj_type == "qfq",
                    StockDailyOhlcv.date == target_date,
                ))
                .order_by(desc(StockDailyOhlcv.id))
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

    def get_coverage(self, code: str) -> Dict[str, Any]:
        """查询某股票在 stock_daily_ohlcv（日线/前复权）里已存的最早/最晚日期与条数。

        供历史回填的断点续传判定使用（DB 为数据真相源）。按 date 去重计数
        （多源同一交易日只算一天）。

        Returns:
            {"first": date|None, "last": date|None, "rows": int}
        """
        try:
            with self.db.get_session() as session:
                first, last, cnt = session.execute(
                    select(
                        func.min(StockDailyOhlcv.date),
                        func.max(StockDailyOhlcv.date),
                        func.count(func.distinct(StockDailyOhlcv.date)),
                    ).where(and_(
                        StockDailyOhlcv.code == code,
                        StockDailyOhlcv.ktype == "1",
                        StockDailyOhlcv.adj_type == "qfq",
                    ))
                ).one()
            return {"first": first, "last": last, "rows": int(cnt or 0)}
        except Exception as e:
            logger.error(f"查询 {code} 数据覆盖范围失败: {e}")
            return {"first": None, "last": None, "rows": 0}

    def get_forward_bars(self, *, code: str, analysis_date: date, eval_window_days: int) -> List[StockDailyOhlcv]:
        """返回 analysis_date 之后、最多 eval_window_days 个交易日的前向日线（stock_daily_ohlcv）。

        stock_daily_ohlcv 同一 (code,date) 可能有多源行，先按 date 去重（保留 id 最大者），
        再取前 eval_window_days 个交易日，避免重复交易日污染前向窗口计数。
        """
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDailyOhlcv)
                .where(and_(
                    StockDailyOhlcv.code == code,
                    StockDailyOhlcv.ktype == "1",
                    StockDailyOhlcv.adj_type == "qfq",
                    StockDailyOhlcv.date > analysis_date,
                ))
                .order_by(StockDailyOhlcv.date, desc(StockDailyOhlcv.id))
            ).scalars().all()
        # 去重（每交易日保留 id 最大者），按 date 升序取前 N 天
        best: Dict[date, StockDailyOhlcv] = {}
        for row in rows:
            cur = best.get(row.date)
            if cur is None or (row.id or 0) > (cur.id or 0):
                best[row.date] = row
        ordered = [best[d] for d in sorted(best.keys())]
        return ordered[: int(eval_window_days)]

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

        # stock_daily_ohlcv.code 存的是「裸码」(如 000001)，而票池/全市场清单常传
        # 带交易所后缀的全码(如 000001.SZ)。为两种形态都能命中，无论传入形态如何，
        # 都同时按「原样 + 裸码 + 裸码补 .SH/.SZ/.BJ 后缀」查询。
        _SUFFIXES = (".SH", ".SZ", ".BJ")
        query_codes: List[str] = []
        _seen: set = set()
        for nc in norm_codes:
            bare = nc.split(".")[0]
            cands = [nc, bare, *(bare + s for s in _SUFFIXES)]
            for c in cands:
                if c not in _seen:
                    _seen.add(c)
                    query_codes.append(c)

        batch_size = max(1, int(batch_size))
        result: Dict[str, pd.DataFrame] = {}
        try:
            with self.db.get_session() as session:
                # 预编译两条等价 SQL：带 INDEXED BY（快路径）/ 不带（兜底路径）。
                # 后者仅在前者的复合索引被迁移/重建误删时触发，避免硬绑定导致
                # "no such index" 让所有取数功能整体崩溃（仅降级变慢，功能不挂）。
                sql_indexed = text(
                    """
                    SELECT code, date, open, high, low, close, volume, amount,
                           turnoverratio
                    FROM stock_daily_ohlcv INDEXED BY ix_ohlcv_code_date_adj
                    WHERE code IN :codes
                      AND ktype = :ktype
                      AND adj_type = :adj_type
                      AND date >= :start
                      AND date <= :end
                    ORDER BY code, date
                    """
                ).bindparams(bindparam("codes", expanding=True))
                sql_plain = text(
                    """
                    SELECT code, date, open, high, low, close, volume, amount,
                           turnoverratio
                    FROM stock_daily_ohlcv
                    WHERE code IN :codes
                      AND ktype = :ktype
                      AND adj_type = :adj_type
                      AND date >= :start
                      AND date <= :end
                    ORDER BY code, date
                    """
                ).bindparams(bindparam("codes", expanding=True))
                for i in range(0, len(query_codes), batch_size):
                    batch = query_codes[i : i + batch_size]
                    # 强制走 (code,date,adj_type) 复合索引：stock_daily_ohlcv 有 870 万+ 行，
                    # SQLite 优化器（无统计信息）会误选单列 adj_type 索引，退化为对全部
                    # qfq 行做全表扫描，导致训练/预测取数卡死数分钟。用 INDEXED BY 显式指定
                    # 后，同样查询从数十秒降到 0.03s。
                    # 注：SQLAlchemy 的 with_hint 对 SQLite 不可靠（生成的 [INDEXED BY] 在
                    # 运行时被忽略），故这里用 text() 直出带 INDEXED BY 的 SQL。
                    params = {
                        "codes": tuple(batch),
                        "ktype": ktype,
                        "adj_type": adj_type,
                        "start": start_date,
                        "end": end_date,
                    }
                    try:
                        rows = session.execute(sql_indexed, params).mappings().all()
                    except SQLAlchemyError as exc:
                        logger.warning(
                            "复合索引 ix_ohlcv_code_date_adj 不可用(%s)，回退到无 hint 查询",
                            exc,
                        )
                        rows = session.execute(sql_plain, params).mappings().all()
                    if not rows:
                        continue
                    records: List[Dict[str, Any]] = [
                        {
                            "code": r["code"],
                            # text() 不走 ORM 类型转换，date 为原始字符串，这里转回 date 对齐
                            "date": pd.to_datetime(r["date"]).date(),
                            "open": r["open"],
                            "high": r["high"],
                            "low": r["low"],
                            "close": r["close"],
                            "volume": r["volume"],
                            "amount": r["amount"],
                            # 换手率：ohlcv 列名 turnoverratio → 统一契约 turnover_rate
                            "turnover_rate": r["turnoverratio"],
                        }
                        for r in rows
                    ]
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
