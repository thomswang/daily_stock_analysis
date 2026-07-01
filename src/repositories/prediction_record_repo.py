# -*- coding: utf-8 -*-
"""
===================================
走势预测记录数据访问层
===================================

职责：
1. 持久化每次预测结果
2. 分页/过滤查询历史预测（供前端「历史预测」列表）
3. 取出到期待评估记录、回填真实结果
4. 聚合准确率统计（供「准确率」看板）
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, PredictionRecord

logger = logging.getLogger(__name__)


class PredictionRecordRepository:
    """prediction_records 表的数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save(self, data: Dict[str, Any]) -> int:
        """插入一条预测记录，返回新记录 id。"""
        with self.db.get_session() as session:
            record = PredictionRecord(
                code=data["code"],
                stock_name=data.get("stock_name"),
                as_of_date=data["as_of_date"],
                horizon_days=int(data.get("horizon_days", 5)),
                direction=data["direction"],
                up_probability=data.get("up_probability"),
                confidence=data.get("confidence"),
                expected_return_pct=data.get("expected_return_pct"),
                last_close=data.get("last_close"),
                model_source=data.get("model_source"),
                model_name=data.get("model_name"),
                model_version=data.get("model_version"),
                eval_status="pending",
                created_at=datetime.now(),
            )
            session.add(record)
            session.flush()
            new_id = int(record.id)
            session.commit()
            return new_id

    def list_records(
        self,
        *,
        code: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """分页查询历史预测，返回 (记录列表, 总数)。"""
        with self.db.get_session() as session:
            conditions = []
            if code:
                conditions.append(PredictionRecord.code == code.strip().upper())
            if status:
                conditions.append(PredictionRecord.eval_status == status)
            where = and_(*conditions) if conditions else None

            count_stmt = select(func.count()).select_from(PredictionRecord)
            if where is not None:
                count_stmt = count_stmt.where(where)
            total = int(session.execute(count_stmt).scalar_one())

            stmt = select(PredictionRecord)
            if where is not None:
                stmt = stmt.where(where)
            stmt = stmt.order_by(desc(PredictionRecord.created_at)).limit(limit).offset(offset)
            rows = session.execute(stmt).scalars().all()
            return [self._to_dict(r) for r in rows], total

    def get_pending_for_eval(self, *, limit: int = 500) -> List[Dict[str, Any]]:
        """取出待评估（pending）的记录。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(PredictionRecord)
                .where(PredictionRecord.eval_status == "pending")
                .order_by(PredictionRecord.created_at)
                .limit(limit)
            ).scalars().all()
            return [self._to_dict(r) for r in rows]

    def update_eval(
        self,
        record_id: int,
        *,
        eval_status: str,
        actual_close: Optional[float] = None,
        actual_return_pct: Optional[float] = None,
        actual_direction: Optional[str] = None,
        is_correct: Optional[bool] = None,
    ) -> bool:
        """回填单条记录的评估结果。"""
        with self.db.get_session() as session:
            row = session.get(PredictionRecord, record_id)
            if row is None:
                return False
            row.eval_status = eval_status
            if actual_close is not None:
                row.actual_close = actual_close
            if actual_return_pct is not None:
                row.actual_return_pct = actual_return_pct
            if actual_direction is not None:
                row.actual_direction = actual_direction
            if is_correct is not None:
                row.is_correct = is_correct
            if eval_status == "evaluated":
                row.evaluated_at = datetime.now()
            session.commit()
            return True

    def accuracy_stats(self, *, code: Optional[str] = None) -> Dict[str, Any]:
        """聚合准确率统计。"""
        with self.db.get_session() as session:
            base_conditions = []
            if code:
                base_conditions.append(PredictionRecord.code == code.strip().upper())

            def _count(*extra) -> int:
                stmt = select(func.count()).select_from(PredictionRecord)
                conds = base_conditions + list(extra)
                if conds:
                    stmt = stmt.where(and_(*conds))
                return int(session.execute(stmt).scalar_one())

            total = _count()
            pending = _count(PredictionRecord.eval_status == "pending")
            evaluated = _count(PredictionRecord.eval_status == "evaluated")
            correct = _count(
                PredictionRecord.eval_status == "evaluated",
                PredictionRecord.is_correct.is_(True),
            )

            # 平均期望收益 / 实际收益（仅已评估）
            avg_stmt = select(
                func.avg(PredictionRecord.expected_return_pct),
                func.avg(PredictionRecord.actual_return_pct),
            ).where(PredictionRecord.eval_status == "evaluated")
            if base_conditions:
                avg_stmt = avg_stmt.where(and_(*base_conditions))
            avg_expected, avg_actual = session.execute(avg_stmt).one()

            accuracy = (correct / evaluated) if evaluated else None
            return {
                "total": total,
                "pending": pending,
                "evaluated": evaluated,
                "correct": correct,
                "accuracy": accuracy,
                "avg_expected_return_pct": float(avg_expected) if avg_expected is not None else None,
                "avg_actual_return_pct": float(avg_actual) if avg_actual is not None else None,
            }

    @staticmethod
    def _to_dict(row: PredictionRecord) -> Dict[str, Any]:
        return {
            "id": row.id,
            "code": row.code,
            "stock_name": row.stock_name,
            "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
            "horizon_days": row.horizon_days,
            "direction": row.direction,
            "up_probability": row.up_probability,
            "confidence": row.confidence,
            "expected_return_pct": row.expected_return_pct,
            "last_close": row.last_close,
            "model_source": row.model_source,
            "model_name": row.model_name,
            "model_version": row.model_version,
            "eval_status": row.eval_status,
            "actual_close": row.actual_close,
            "actual_return_pct": row.actual_return_pct,
            "actual_direction": row.actual_direction,
            "is_correct": row.is_correct,
            "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
