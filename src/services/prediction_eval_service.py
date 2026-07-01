# -*- coding: utf-8 -*-
"""
===================================
走势预测评估（回填）服务
===================================

职责：把「待评估」的历史预测，在其预测窗口到期后，用真实行情回填打分。

流程：
    取 pending 记录 → 按股票分组（可选联网刷新缓存以补齐前向 K 线）
    → 用 stock_daily 的前向 K 线取 as_of + horizon 交易日的收盘
    → 计算实际收益/方向 → 与预测方向比对 → 回填 is_correct

准确率口径：预测方向(direction) vs 实际区间(as_of → as_of+horizon 交易日)收益方向。

⚠️ 本服务只负责"记账/打分"，不修改模型；让模型变准需另行"用真实样本再训练"。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PredictionEvalService:
    """回填历史预测的真实结果并打分。"""

    def __init__(self, db_manager=None):
        from src.repositories.prediction_record_repo import PredictionRecordRepository
        from src.repositories.stock_repo import StockRepository

        self.repo = PredictionRecordRepository(db_manager)
        self.stock_repo = StockRepository(db_manager)

    def evaluate_pending(self, *, refresh: bool = True, limit: int = 500) -> Dict[str, Any]:
        """评估所有待评估记录。

        Args:
            refresh: 评估前是否按股票联网刷新一次缓存（补齐前向 K 线）
            limit: 单次最多处理的记录数

        Returns:
            统计：processed / evaluated / insufficient / errors
        """
        pending = self.repo.get_pending_for_eval(limit=limit)
        stats = {"processed": 0, "evaluated": 0, "insufficient": 0, "errors": 0}
        if not pending:
            return stats

        # 按股票分组，避免重复刷新
        by_code: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for rec in pending:
            by_code[rec["code"]].append(rec)

        for code, records in by_code.items():
            if refresh:
                self._refresh_code(code)
            for rec in records:
                stats["processed"] += 1
                try:
                    outcome = self._evaluate_one(rec)
                    if outcome is None:
                        stats["insufficient"] += 1
                    else:
                        self.repo.update_eval(rec["id"], **outcome)
                        stats["evaluated"] += 1
                except Exception as exc:  # noqa: BLE001 - 单条失败不中断整体
                    logger.warning("评估预测记录 %s 失败: %s", rec.get("id"), exc)
                    stats["errors"] += 1

        logger.info(
            "[predict-eval] 完成：processed=%d evaluated=%d insufficient=%d errors=%d",
            stats["processed"], stats["evaluated"], stats["insufficient"], stats["errors"],
        )
        return stats

    def _refresh_code(self, code: str) -> None:
        """尽力联网刷新某股票的缓存，补齐用于评估的前向 K 线。"""
        try:
            from src.services.prediction_service import _load_daily_df

            _load_daily_df(code, 120, use_cache=True, refresh=True, resolve_name=False)
        except Exception as exc:  # noqa: BLE001 - 刷新失败仍可用已有缓存评估
            logger.debug("评估前刷新 %s 缓存失败（忽略）: %s", code, exc)

    def _evaluate_one(self, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """评估单条记录；数据不足返回 None（保持 pending）。"""
        last_close = rec.get("last_close")
        if not last_close:
            return None
        try:
            as_of = date.fromisoformat(str(rec["as_of_date"])[:10])
        except (ValueError, TypeError):
            return None

        horizon = int(rec.get("horizon_days") or 5)
        bars = self.stock_repo.get_forward_bars(
            code=rec["code"], analysis_date=as_of, eval_window_days=horizon
        )
        if not bars or len(bars) < horizon:
            return None  # 窗口未到期或数据不足，下次再评

        target_bar = bars[horizon - 1]
        actual_close = float(target_bar.close)
        actual_return_pct = round((actual_close / float(last_close) - 1.0) * 100, 4)
        actual_direction = "up" if actual_return_pct > 0 else "down"
        is_correct = (rec["direction"] == actual_direction)

        return {
            "eval_status": "evaluated",
            "actual_close": round(actual_close, 4),
            "actual_return_pct": actual_return_pct,
            "actual_direction": actual_direction,
            "is_correct": is_correct,
        }
