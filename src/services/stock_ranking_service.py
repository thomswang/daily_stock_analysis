# -*- coding: utf-8 -*-
"""
===================================
选股推荐服务（横截面强弱榜，run 维度）
===================================

把「经 walk-forward / CPCV 验证、扣成本能跑赢基准」的横截面排序能力，做成
**主动推荐**：系统扫描全市场给每只票打强弱分，用户打开即看榜单，无需输入。

分层与性能取舍：
    - compute_snapshot()  重活：扫全市场逐票打分 → 登记一个不可变 run →
      按行业截断前 20 落库(stock_rank_snapshot)。
      每次执行 = 一个新 run，永不覆盖历史，便于回溯「哪个模型/哪个时间预测」。
    - get_recommendations() 轻活：读某 run 的快照 + 组内重排 + 等权建议权重，
      毫秒级返回。「全市场打分只算一次（按 run 存好），查询靠过滤派生」是核心设计。

用途拆分（与 prediction_service 一致）：
    - 单票 /predict：绝对涨跌方向（能力有限，≈52%）
    - 选股推荐：横截面「谁比谁强」+ 建议权重（稳定 alpha，主动推给用户）

⚠️ 仅供技术研究，不构成投资建议。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.repositories.rank_snapshot_repo import PER_INDUSTRY_MAX, RankSnapshotRepository
from src.services.prediction_service import (
    DEFAULT_RANK_MODEL,
    PredictionError,
    load_market_df,
    load_ranking_model,
    score_codes,
)

logger = logging.getLogger(__name__)

# 每个行业最多保留的强弱势条数
PER_INDUSTRY_CAP = PER_INDUSTRY_MAX


class StockRankingError(Exception):
    """选股推荐可预期的业务错误。"""


class StockRankingService:
    """横截面强弱榜：全市场打分预计算（run 维度）+ 行业/全市场推荐查询。"""

    def __init__(self, db_manager=None):
        self.repo = RankSnapshotRepository(db_manager)

    # ───────────────────────── 重活：预计算落库（不可变 run） ─────────────────────────
    def compute_snapshot(
        self,
        *,
        model_name: str = DEFAULT_RANK_MODEL,
        model_id: Optional[int] = None,
        lookback_days: int = 250,
        universe: Optional[List[str]] = None,
        limit: Optional[int] = None,
        exclude_st: bool = True,
    ) -> Dict[str, Any]:
        """扫描全市场（或给定票池）逐票打强弱分，按行业截断前 20，登记一个不可变
        run 并落库。每次执行都是独立 run，绝不覆盖历史，便于回溯/对比。

        纯本地缓存打分（refresh=False），避免上千次联网。行业归属取自
        stock_industry 最新快照；名称取自 stocks.index.json（均免联网）。
        默认剔除 ST/退市风险股（与回测口径一致，避免把风险股推给用户）。

        Returns: 概览 {run_id, as_of_date, scored, written, industries, model_*}
        """
        model, record = load_ranking_model(model_name, model_id=model_id)

        codes = universe or self._load_universe()
        if limit:
            codes = codes[:limit]
        if not codes:
            raise StockRankingError("未能载入待打分的股票池（检查 stocks.index.json）")

        ind_map = self._load_industry_map()
        name_map = self._load_name_map()

        if exclude_st:  # 名称含 ST/*ST 的一律不打分（退市风险股不进榜）
            before = len(codes)
            codes = [c for c in codes if "ST" not in (name_map.get(c.strip().upper(), "")).upper()]
            logger.info("[rank] 剔除 ST 股 %d 只，剩余 %d 只", before - len(codes), len(codes))
        logger.info(
            "[rank] 开始全市场打分：%d 只，模型=%s@%s，行业覆盖 %d 只",
            len(codes), record.get("name"), record.get("version"), len(ind_map),
        )

        scored = score_codes(
            codes, model=model, market_df=load_market_df(),
            lookback_days=lookback_days, resolve_name=False, refresh=False,
        )
        if not scored:
            raise StockRankingError("全市场打分结果为空（缓存数据不足？先跑 python backfill.py quote/kline）")

        # 附行业 + 名称（免联网）
        for it in scored:
            up = it["code"].strip().upper()
            it["industry"] = ind_map.get(up)
            it["stock_name"] = name_map.get(up)
        as_of = self._dominant_as_of(scored)

        # 按行业分组、每组按强弱降序、截断前 PER_INDUSTRY_CAP
        buckets: Dict[Optional[str], List[Dict[str, Any]]] = defaultdict(list)
        for it in scored:
            buckets[it.get("industry")].append(it)

        rows: List[Dict[str, Any]] = []
        industries_covered = 0
        for ind, items in buckets.items():
            items.sort(key=lambda x: float(x.get("strength_score") or 0.0), reverse=True)
            # 真实行业才截断前 N；无行业归属的票保留全部（供全市场榜按强弱入选）
            keep = items if not ind else items[:PER_INDUSTRY_CAP]
            if ind:
                industries_covered += 1
            for rank, it in enumerate(keep, start=1):
                lc = it.get("last_close")
                rows.append({
                    "code": it["code"].strip().upper(),
                    "stock_name": it.get("stock_name"),
                    "industry": ind,
                    "strength_score": float(it["strength_score"]),
                    "rank_in_industry": rank,
                    "last_close": (float(lc) if lc is not None else None),
                })

        # 登记不可变 run
        run_id = self.repo.save_run(
            model_id=record.get("id"),
            model_name=str(record.get("name") or model_name),
            model_version=record.get("version"),
            as_of_date=as_of,
            lookback_days=lookback_days,
            universe_size=len(scored),
            industry_count=industries_covered,
        )
        written = self.repo.save_snapshot_rows(run_id, rows)
        logger.info(
            "[rank] 快照完成：run_id=%d，打分 %d 只，落库 %d 条，行业 %d，as_of=%s",
            run_id, len(scored), written, industries_covered, as_of,
        )
        return {
            "run_id": run_id,
            "as_of_date": as_of.isoformat(),
            "scored": len(scored),
            "written": written,
            "industries": industries_covered,
            "model_name": record.get("name"),
            "model_version": record.get("version"),
        }

    # 经长周期 walk-forward 回测选出的最优交易口径（把清单落到实盘怎么用）
    #   双周·等权·缓冲·行业≤3：2020–2026 样本外扣成本 夏普 0.80、回撤 -16.6%，
    #   优于等权基准（夏普 0.64 / 回撤 -29.1%）。长周期下等权稳定优于概率加权
    #   （概率加权的优势仅存在于 2024–2026 短窗，跨多状态后消失，见 docs/backtest-report-2018.md）。
    STRATEGY_HINT = {
        "name": "双周·等权·缓冲·行业≤3",
        "rebalance": "每2周·周一开盘买入/期末周五收盘",
        "weighting": "等权(入选每只票权重相同)",
        # 行业前 20 已在生成时固定，这里不再做额外行业分散上限
        "industry_cap": None,
        "backtest": "长周期回测(2020–2026,扣成本)：年化≈19.4%、夏普0.80、回撤-16.6%（等权基准夏普0.64/回撤-29.1%）",
    }

    def get_recommendations(
        self,
        *,
        industry: Optional[str] = None,
        top_n: int = 20,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """读取某 run（默认最新）的强弱榜，按行业(可选)出榜；给出全局分位与等权建议权重。

        - industry=None：全市场强弱榜（按强弱降序取前 top_n，top_n≤20）。
        - industry=X：仅该行业内排名（行业内已固定前 20，rank_in_industry 即名次）。
        分位(rank_pct)按所选范围全体计算；建议权重在返回清单内等权归一(∑=1)。
        """
        top_n = int(max(1, min(top_n, PER_INDUSTRY_CAP)))
        run = self.repo.get_run(run_id) if run_id else self.repo.latest_run()
        if not run:
            raise StockRankingError("暂无强弱榜快照；请先运行 python rank_snapshot.py 生成快照")

        rows = self.repo.get_ranking(run["run_id"], industry=industry, top_n=top_n)
        if not rows:
            raise StockRankingError(
                "所选范围暂无强弱榜数据；请先运行 python rank_snapshot.py"
                + ("，或所选行业无数据" if industry else "")
            )

        # 所选范围全体的强弱分位（在裁剪之前，保证名次口径稳定）
        s = np.array([r["strength_score"] for r in rows], dtype=float)
        pct = pd.Series(s).rank(pct=True, method="average").to_numpy()
        for i, r in enumerate(rows):
            r["rank_pct"] = round(float(pct[i]), 4)

        picks = rows[:top_n]
        for i, r in enumerate(picks):  # 展示名次按最终清单重排 1..N
            r["rank"] = i + 1
        # 组合建议权重：等权（∑=1）。长周期回测显示等权稳定优于概率加权，
        # 故线上口径与回测最优（双周·等权·缓冲·行业≤3）保持一致。
        ew = 1.0 / len(picks) if picks else 0.0
        for r in picks:
            r["suggested_weight"] = round(ew, 4)

        return {
            "run_id": run["run_id"],
            "model_id": run["model_id"],
            "model_name": run["model_name"],
            "model_version": run["model_version"],
            "generated_at": run["generated_at"],
            "as_of_date": run["as_of_date"],
            "scope": industry or "全市场",
            "industry": industry,
            "universe_size": run["universe_size"] or len(picks),
            "count": len(picks),
            "industry_cap": None,
            "strategy": {**self.STRATEGY_HINT, "industry_cap": None},
            "items": picks,
            "disclaimer": "强弱分为横截面相对排序(非绝对涨跌概率)，仅供技术研究，不构成投资建议。",
        }

    def list_runs(self, limit: int = 50) -> Dict[str, Any]:
        """历史快照执行列表（最新在前），供前端「快照选择」下拉。"""
        runs = self.repo.list_runs(limit=limit)
        return {"count": len(runs), "runs": runs}

    def list_industries(self, run_id: Optional[int] = None) -> Dict[str, Any]:
        """某 run（默认最新）快照的可选行业清单（含各行业股票数），供行业筛选下拉。"""
        run = self.repo.get_run(run_id) if run_id else self.repo.latest_run()
        if run is None:
            return {"run_id": None, "as_of_date": None, "count": 0, "industries": []}
        items = self.repo.list_industries(run["run_id"])
        return {
            "run_id": run["run_id"],
            "as_of_date": run["as_of_date"],
            "count": len(items),
            "industries": items,
        }

    # ───────────────────────── 内部工具 ─────────────────────────
    @staticmethod
    def _load_universe() -> List[str]:
        from src.services.backfill import CodeListLoader

        try:
            return CodeListLoader.load_all_cn_codes()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[rank] 载入全市场代码失败：%s", exc)
            return []

    @staticmethod
    def _load_name_map() -> Dict[str, str]:
        from src.services.backfill import CodeListLoader

        try:
            return CodeListLoader.load_cn_name_map()
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _load_industry_map() -> Dict[str, str]:
        try:
            from src.repositories.stock_industry_repo import StockIndustryRepository

            return StockIndustryRepository().get_map()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[rank] 载入行业映射失败（行业榜将不可用）：%s", exc)
            return {}

    @staticmethod
    def _dominant_as_of(scored: List[Dict[str, Any]]) -> date:
        """取多数票的最新交易日作为快照日（个别停牌票日期偏旧不影响整体）。"""
        dates = [it.get("as_of_date") for it in scored if it.get("as_of_date")]
        if not dates:
            return datetime.now().date()
        top = pd.Series(dates).mode()
        return pd.to_datetime(top.iloc[0]).date()
