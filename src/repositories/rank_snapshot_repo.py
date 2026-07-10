# -*- coding: utf-8 -*-
"""
===================================
横截面强弱打分快照 数据访问层（run 维度）
===================================

职责：
1. 一次快照执行 = 一个不可变 run（stock_rank_run），永不覆盖历史。
2. 明细（stock_rank_snapshot）按 run_id 关联，每个行业只存前 20（rank_in_industry）。
3. 查询某 run 的强弱榜（支持按行业过滤 + 取前 N，N 上限 20）。
4. 列出历史 runs / 某 run 的行业清单（供前端「快照选择」下拉）。

写法对齐 stock_industry_repo：通过 DatabaseManager.get_session() 操作 ORM。
业务加工（分位、建议权重、名次）放在 Service 层，本层只负责存取。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete as sa_delete, distinct, func, select

from src.storage import DatabaseManager, StockRankRun, StockRankSnapshot

logger = logging.getLogger(__name__)

# 每个行业最多保留的强弱势条数（生成时即截断）
PER_INDUSTRY_MAX = 20


class RankSnapshotRepository:
    """stock_rank_run / stock_rank_snapshot 表的数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ───────────────────────── run 写入 ─────────────────────────
    def save_run(
        self,
        *,
        model_id: Optional[int],
        model_name: str,
        model_version: Optional[str],
        as_of_date: date,
        lookback_days: Optional[int] = None,
        universe_size: Optional[int] = None,
        industry_count: Optional[int] = None,
        note: Optional[str] = None,
    ) -> int:
        """登记一次快照执行，返回 run_id。run 不可变，绝不覆盖历史。

        model_id 硬关联 prediction_models.id，唯一锁定本次打分所用的训练产物。
        """
        with self.db.get_session() as session:
            run = StockRankRun(
                model_id=model_id,
                model_name=model_name,
                model_version=model_version or "unknown",
                as_of_date=as_of_date,
                generated_at=datetime.now(),
                lookback_days=lookback_days,
                universe_size=universe_size,
                industry_count=industry_count,
                note=note,
            )
            session.add(run)
            session.flush()
            run_id = int(run.run_id)
            session.commit()
        logger.info("快照 run 登记完成：run_id=%d，model=%s@%s，as_of=%s", run_id, model_name, model_version, as_of_date)
        return run_id

    # ───────────────────────── 明细写入（已按行业截断前 20） ─────────────────────────
    def save_snapshot_rows(self, run_id: int, rows: List[Dict[str, Any]]) -> int:
        """批量写入某 run 的明细行（调用方需保证已按行业截断到前 20）。"""
        if not rows:
            return 0
        _CHUNK = 200
        written = 0
        with self.db.get_session() as session:
            for i in range(0, len(rows), _CHUNK):
                chunk = [dict(r, run_id=run_id) for r in rows[i:i + _CHUNK]]
                session.bulk_insert_mappings(StockRankSnapshot, chunk)
                written += len(chunk)
            session.commit()
        logger.info("快照明细写入完成：run_id=%d，%d 条", run_id, written)
        return written

    # ───────────────────────── run 查询 ─────────────────────────
    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """历史快照执行列表（最新在前），供前端「快照选择」下拉。"""
        with self.db.get_session() as session:
            rows = (
                session.execute(
                    select(StockRankRun)
                    .order_by(StockRankRun.run_id.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        return [
            {
                "run_id": r.run_id,
                "model_id": r.model_id,
                "model_name": r.model_name,
                "model_version": r.model_version,
                "as_of_date": r.as_of_date.isoformat() if r.as_of_date else None,
                "generated_at": r.generated_at.isoformat(timespec="seconds") if r.generated_at else None,
                "lookback_days": r.lookback_days,
                "universe_size": r.universe_size,
                "industry_count": r.industry_count,
                "note": r.note,
            }
            for r in rows
        ]

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            r = session.get(StockRankRun, run_id)
            if r is None:
                return None
            return {
                "run_id": r.run_id,
                "model_id": r.model_id,
                "model_name": r.model_name,
                "model_version": r.model_version,
                "as_of_date": r.as_of_date.isoformat() if r.as_of_date else None,
                "generated_at": r.generated_at.isoformat(timespec="seconds") if r.generated_at else None,
                "lookback_days": r.lookback_days,
                "universe_size": r.universe_size,
                "industry_count": r.industry_count,
                "note": r.note,
            }

    def latest_run(self) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            r = (
                session.execute(
                    select(StockRankRun).order_by(StockRankRun.run_id.desc()).limit(1)
                )
                .scalars()
                .first()
            )
            if r is None:
                return None
            return self.get_run(r.run_id)

    def summary_for_run(self, run_id: int) -> Dict[str, Any]:
        """概览：某 run 的覆盖股票数、行业数。"""
        with self.db.get_session() as session:
            codes = int(session.execute(
                select(func.count()).where(StockRankSnapshot.run_id == run_id)
            ).scalar_one() or 0)
            industries = int(session.execute(
                select(func.count(distinct(StockRankSnapshot.industry)))
                .where(StockRankSnapshot.run_id == run_id)
            ).scalar_one() or 0)
        return {"run_id": run_id, "codes": codes, "industries": industries}

    # ───────────────────────── 榜单查询 ─────────────────────────
    def get_ranking(
        self,
        run_id: int,
        *,
        industry: Optional[str] = None,
        top_n: int = PER_INDUSTRY_MAX,
    ) -> List[Dict[str, Any]]:
        """读取某 run 的强弱榜（默认按强弱降序）；可按行业过滤、取前 N（N≤20）。

        Returns: [{code, stock_name, industry, strength_score, rank_in_industry, last_close}, ...]
        """
        top_n = max(1, min(int(top_n), PER_INDUSTRY_MAX))
        stmt = (
            select(
                StockRankSnapshot.code,
                StockRankSnapshot.stock_name,
                StockRankSnapshot.industry,
                StockRankSnapshot.strength_score,
                StockRankSnapshot.rank_in_industry,
                StockRankSnapshot.last_close,
            )
            .where(StockRankSnapshot.run_id == run_id)
            .order_by(StockRankSnapshot.strength_score.desc())
        )
        if industry:
            stmt = stmt.where(StockRankSnapshot.industry == industry)
        stmt = stmt.limit(top_n)
        with self.db.get_session() as session:
            rows = session.execute(stmt).all()
        return [
            {
                "code": c, "stock_name": nm, "industry": ind,
                "strength_score": float(sc) if sc is not None else None,
                "rank_in_industry": int(rk) if rk is not None else None,
                "last_close": float(lc) if lc is not None else None,
            }
            for c, nm, ind, sc, rk, lc in rows
        ]

    def list_industries(self, run_id: int) -> List[Dict[str, Any]]:
        """列出某 run 覆盖的行业清单及每个行业的股票数（按数量降序）。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockRankSnapshot.industry, func.count())
                .where(
                    StockRankSnapshot.run_id == run_id,
                    StockRankSnapshot.industry.isnot(None),
                )
                .group_by(StockRankSnapshot.industry)
                .order_by(func.count().desc())
            ).all()
        return [{"industry": ind, "count": int(cnt)} for ind, cnt in rows if ind]

    def delete_run(self, run_id: int) -> int:
        """删除某 run 及其明细（cleanup 用）。返回删除明细行数。"""
        with self.db.get_session() as session:
            res = session.execute(
                sa_delete(StockRankSnapshot).where(StockRankSnapshot.run_id == run_id)
            )
            session.execute(sa_delete(StockRankRun).where(StockRankRun.run_id == run_id))
            session.commit()
            return int(res.rowcount or 0)
