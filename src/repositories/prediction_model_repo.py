# -*- coding: utf-8 -*-
"""
===================================
走势预测模型数据访问层
===================================

职责：
1. 持久化已训练的预测模型（参数 + 版本 + 指标）
2. 提供"当前激活模型"查询，供预测服务加载推理
3. 支持列出历史版本、切换激活版本（回滚）

写法对齐 backtest_repo：通过 DatabaseManager.get_session() 操作 ORM。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, select, update

from src.storage import DatabaseManager, PredictionModel

logger = logging.getLogger(__name__)


class PredictionModelRepository:
    """prediction_models 表的数据访问层。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_model(
        self,
        *,
        name: str,
        version: str,
        algorithm: str,
        params: Dict[str, Any],
        feature_names: List[str],
        trained_symbols: List[str],
        train_start_date: Optional[date],
        train_end_date: Optional[date],
        horizon_days: int,
        metrics: Dict[str, Any],
        set_active: bool = True,
        notes: Optional[str] = None,
    ) -> int:
        """插入一条模型记录，返回新记录 id。

        set_active=True 时，会先把同名模型的其它版本取消激活，再激活本条，
        保证任意时刻同名模型只有一个激活版本。
        """
        with self.db.get_session() as session:
            if set_active:
                session.execute(
                    update(PredictionModel)
                    .where(PredictionModel.name == name)
                    .values(is_active=False)
                )

            record = PredictionModel(
                name=name,
                version=version,
                algorithm=algorithm,
                trained_symbols_json=json.dumps(trained_symbols, ensure_ascii=False),
                symbol_count=len(trained_symbols),
                train_start_date=train_start_date,
                train_end_date=train_end_date,
                horizon_days=horizon_days,
                feature_names_json=json.dumps(feature_names, ensure_ascii=False),
                params_json=json.dumps(params, ensure_ascii=False),
                train_samples=int(metrics.get("train_samples", 0) or 0),
                valid_samples=int(metrics.get("valid_samples", 0) or 0),
                train_accuracy=metrics.get("train_accuracy"),
                valid_accuracy=metrics.get("valid_accuracy"),
                baseline_accuracy=metrics.get("baseline_accuracy"),
                metrics_json=json.dumps(metrics, ensure_ascii=False),
                is_active=set_active,
                notes=notes,
                created_at=datetime.now(),
            )
            session.add(record)
            session.flush()
            new_id = int(record.id)
            session.commit()
            return new_id

    def get_active(self, name: str = "trend_lr") -> Optional[Dict[str, Any]]:
        """返回指定名称的激活模型（转 dict），无则 None。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(PredictionModel)
                .where(
                    and_(
                        PredictionModel.name == name,
                        PredictionModel.is_active.is_(True),
                    )
                )
                .order_by(desc(PredictionModel.created_at))
                .limit(1)
            ).scalar_one_or_none()
            return self._to_dict(row) if row else None

    def get_by_id(self, model_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.get(PredictionModel, model_id)
            return self._to_dict(row) if row else None

    def list_models(self, *, name: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """按时间倒序列出模型版本。"""
        with self.db.get_session() as session:
            stmt = select(PredictionModel)
            if name:
                stmt = stmt.where(PredictionModel.name == name)
            stmt = stmt.order_by(desc(PredictionModel.created_at)).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [self._to_dict(r) for r in rows]

    def set_active(self, model_id: int) -> bool:
        """把指定版本设为激活（同名其它版本取消激活）。用于回滚。"""
        with self.db.get_session() as session:
            row = session.get(PredictionModel, model_id)
            if row is None:
                return False
            session.execute(
                update(PredictionModel)
                .where(PredictionModel.name == row.name)
                .values(is_active=False)
            )
            row.is_active = True
            session.commit()
            return True

    def delete_except(self, keep_id: int) -> int:
        """删除除 keep_id 以外的全部模型记录，并将 keep_id 设为激活。返回删除条数。"""
        from sqlalchemy import delete as _sa_delete

        with self.db.get_session() as session:
            keep = session.get(PredictionModel, keep_id)
            if keep is None:
                raise SystemExit(f"未找到模型 id={keep_id}，无法执行删除保留")
            deleted = session.execute(
                _sa_delete(PredictionModel).where(PredictionModel.id != keep_id)
            ).rowcount
            keep.is_active = True  # 保证保留模型处于激活态
            session.commit()
            return int(deleted)

    @staticmethod
    def _to_dict(row: PredictionModel) -> Dict[str, Any]:
        def _load(raw: Optional[str], default: Any) -> Any:
            if not raw:
                return default
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return default

        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "algorithm": row.algorithm,
            "trained_symbols": _load(row.trained_symbols_json, []),
            "symbol_count": row.symbol_count,
            "train_start_date": row.train_start_date.isoformat() if row.train_start_date else None,
            "train_end_date": row.train_end_date.isoformat() if row.train_end_date else None,
            "horizon_days": row.horizon_days,
            "feature_names": _load(row.feature_names_json, []),
            "params": _load(row.params_json, {}),
            "train_samples": row.train_samples,
            "valid_samples": row.valid_samples,
            "train_accuracy": row.train_accuracy,
            "valid_accuracy": row.valid_accuracy,
            "baseline_accuracy": row.baseline_accuracy,
            "metrics": _load(row.metrics_json, {}),
            "is_active": bool(row.is_active),
            "notes": row.notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
