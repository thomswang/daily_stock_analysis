# -*- coding: utf-8 -*-
"""
===================================
个股行业归属数据访问层
===================================

职责：
1. 按 as_of_date 快照存档「个股 -> 所属行业」（幂等 upsert）
2. 查询某快照日（或最新）的全市场行业映射，供建模/展示使用
3. 列出已有快照日期

写法对齐 prediction_model_repo：通过 DatabaseManager.get_session() 操作 ORM。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.storage import DatabaseManager, StockIndustry

logger = logging.getLogger(__name__)


class StockIndustryRepository:
    """stock_industry 表的数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_snapshot(
        self,
        mapping: Dict[str, Dict[str, Any]],
        *,
        as_of_date: date,
        source: str = "akshare_em",
    ) -> int:
        """把一批「code -> {industry, industry_code}」按 as_of_date 幂等写入。

        Args:
            mapping: {code: {"industry": str, "industry_code": Optional[str]}}
            as_of_date: 快照日期
            source: 数据来源标识

        Returns:
            实际写入（新增+更新）的记录数
        """
        if not mapping:
            return 0

        now = datetime.now()
        records: List[Dict[str, Any]] = []
        for raw_code, info in mapping.items():
            code = (raw_code or "").strip().upper()
            industry = (info.get("industry") or "").strip()
            if not code or not industry:
                continue
            records.append({
                "code": code,
                "industry": industry,
                "industry_code": (info.get("industry_code") or None),
                "as_of_date": as_of_date,
                "source": source,
                "created_at": now,
            })

        if not records:
            return 0

        written = 0
        _CHUNK = 200  # 每条 6 列，远低于 SQLite 绑定上限
        with self.db.get_session() as session:
            for i in range(0, len(records), _CHUNK):
                chunk = records[i:i + _CHUNK]
                stmt = sqlite_insert(StockIndustry).values(chunk)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["code", "as_of_date"],
                        set_={
                            "industry": excluded.industry,
                            "industry_code": excluded.industry_code,
                            "source": excluded.source,
                        },
                    )
                )
                written += len(chunk)
            session.commit()
        logger.info("行业快照写入完成：%d 条，as_of=%s，source=%s", written, as_of_date, source)
        return written

    def latest_snapshot_date(self) -> Optional[date]:
        """返回最新的快照日期，无数据则 None。"""
        with self.db.get_session() as session:
            return session.execute(
                select(func.max(StockIndustry.as_of_date))
            ).scalar_one_or_none()

    def list_snapshot_dates(self) -> List[date]:
        """返回所有快照日期（升序）。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(distinct(StockIndustry.as_of_date))
                .order_by(StockIndustry.as_of_date)
            ).scalars().all()
            return list(rows)

    def get_map(self, as_of_date: Optional[date] = None) -> Dict[str, str]:
        """返回某快照日（默认最新）的「code -> industry」映射。"""
        target = as_of_date or self.latest_snapshot_date()
        if target is None:
            return {}
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockIndustry.code, StockIndustry.industry)
                .where(StockIndustry.as_of_date == target)
            ).all()
        return {code: industry for code, industry in rows}

    def get_industry(self, code: str, as_of_date: Optional[date] = None) -> Optional[str]:
        """返回单只票在某快照日（默认最新）的行业。"""
        target = as_of_date or self.latest_snapshot_date()
        if target is None:
            return None
        norm = (code or "").strip().upper()
        with self.db.get_session() as session:
            return session.execute(
                select(StockIndustry.industry)
                .where(
                    StockIndustry.code == norm,
                    StockIndustry.as_of_date == target,
                )
                .limit(1)
            ).scalar_one_or_none()

    def summary(self) -> Dict[str, Any]:
        """快照概览：快照日数、最新快照日、最新快照覆盖股票数、行业数。"""
        dates = self.list_snapshot_dates()
        latest = dates[-1] if dates else None
        codes = industries = 0
        if latest is not None:
            with self.db.get_session() as session:
                codes = int(session.execute(
                    select(func.count()).where(StockIndustry.as_of_date == latest)
                ).scalar_one() or 0)
                industries = int(session.execute(
                    select(func.count(distinct(StockIndustry.industry)))
                    .where(StockIndustry.as_of_date == latest)
                ).scalar_one() or 0)
        return {
            "snapshot_count": len(dates),
            "latest": latest.isoformat() if latest else None,
            "latest_codes": codes,
            "latest_industries": industries,
        }
