# -*- coding: utf-8 -*-
"""
===================================
选股推荐服务（横截面强弱榜）
===================================

把「经 walk-forward / CPCV 验证、扣成本能跑赢基准」的横截面排序能力，做成
**主动推荐**：系统扫描全市场给每只票打强弱分，用户打开即看榜单，无需输入。

分层与性能取舍：
    - compute_snapshot()  重活：扫全市场逐票打分 → 落库(stock_rank_snapshot)。
      给全市场每只票构造特征+推理很重，故由后台任务/定时每日算一次。
    - get_recommendations() 轻活：读快照 + 按行业过滤 + 组内重排 + 等权建议权重，
      毫秒级返回。「全市场打分只算一次，行业榜靠过滤派生」是核心设计。

用途拆分（与 prediction_service 一致）：
    - 单票 /predict：绝对涨跌方向（能力有限，≈52%）
    - 选股推荐：横截面「谁比谁强」+ 建议权重（稳定 alpha，主动推给用户）

⚠️ 仅供技术研究，不构成投资建议。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.repositories.rank_snapshot_repo import RankSnapshotRepository
from src.services.prediction_service import (
    DEFAULT_RANK_MODEL,
    PredictionError,
    load_market_df,
    load_ranking_model,
    score_codes,
)

logger = logging.getLogger(__name__)


class StockRankingError(Exception):
    """选股推荐可预期的业务错误。"""


class StockRankingService:
    """横截面强弱榜：全市场打分预计算 + 行业/全市场推荐查询。"""

    def __init__(self, db_manager=None):
        self.repo = RankSnapshotRepository(db_manager)

    # ───────────────────────── 重活：预计算落库 ─────────────────────────
    def compute_snapshot(
        self,
        *,
        model_name: str = DEFAULT_RANK_MODEL,
        lookback_days: int = 250,
        universe: Optional[List[str]] = None,
        limit: Optional[int] = None,
        exclude_st: bool = True,
    ) -> Dict[str, Any]:
        """扫描全市场（或给定票池）逐票打强弱分，附行业与名称，落库为当日快照。

        纯本地缓存打分（refresh=False），避免上千次联网。行业归属取自
        stock_industry 最新快照；名称取自 stocks.index.json（均免联网）。
        默认剔除 ST/退市风险股（与回测口径一致，避免把风险股推给用户）。

        Returns: 概览 {as_of_date, scored, written, industries, model_*}
        """
        model, record = load_ranking_model(model_name)

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

        # 附行业 + 名称（免联网）；打分日取多数票的最新交易日
        for it in scored:
            up = it["code"].strip().upper()
            it["industry"] = ind_map.get(up)
            it["stock_name"] = name_map.get(up)
        as_of = self._dominant_as_of(scored)

        written = self.repo.save_snapshot(
            scored, as_of_date=as_of,
            model_name=str(record.get("name") or model_name),
            model_version=record.get("version"),
        )
        industries = len({it["industry"] for it in scored if it.get("industry")})
        logger.info("[rank] 全市场打分完成：打分 %d 只，落库 %d 条，as_of=%s", len(scored), written, as_of)
        return {
            "as_of_date": as_of.isoformat(),
            "scored": len(scored),
            "written": written,
            "industries": industries,
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
        "backtest": "长周期回测(2020–2026,扣成本)：年化≈19.4%、夏普0.80、回撤-16.6%（等权基准夏普0.64/回撤-29.1%）",
    }
    DEFAULT_INDUSTRY_CAP = 3  # 全市场推荐时每行业最多几只（分散、抗扎堆）

    def get_recommendations(
        self,
        *,
        industry: Optional[str] = None,
        top_n: int = 20,
        industry_cap: Optional[int] = DEFAULT_INDUSTRY_CAP,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """读取当日快照，按行业(可选)出强弱榜；给出全局分位与等权建议权重。

        - industry=None：全市场强弱榜，默认按 industry_cap 做行业分散（每行业≤N 只），
          避免清单被单一板块霸榜（长周期回测显示分散+等权+双周调仓风险调整后最优）。
        - industry=X：仅该行业内排名（行业内自然无需再设上限）。
        分位(rank_pct)按所选范围全体计算；建议权重在返回清单内等权归一(∑=1)。
        """
        top_n = int(max(1, min(top_n, 200)))
        rows = self.repo.get_ranking(industry=industry, as_of_date=as_of_date)
        if not rows:
            raise StockRankingError(
                "暂无强弱榜数据；请先运行 python rank_snapshot.py 生成当日快照"
                + ("，或所选行业无数据" if industry else "")
            )

        # 所选范围全体的强弱分位（在裁剪/分散之前，保证名次口径稳定）
        s = np.array([r["strength_score"] for r in rows], dtype=float)
        pct = pd.Series(s).rank(pct=True, method="average").to_numpy()
        for i, r in enumerate(rows):
            r["rank_pct"] = round(float(pct[i]), 4)

        # 行业分散上限：仅全市场推荐生效（行业查询本身已聚焦单行业）
        cap = industry_cap if (industry is None and industry_cap and industry_cap > 0) else None
        if cap:
            capped, cnt = [], {}
            for r in rows:  # rows 已按强弱降序
                ind = r.get("industry")
                if ind and cnt.get(ind, 0) >= cap:
                    continue
                if ind:
                    cnt[ind] = cnt.get(ind, 0) + 1
                capped.append(r)
            selectable = capped
        else:
            selectable = rows

        picks = selectable[:top_n]
        for i, r in enumerate(picks):  # 展示名次按最终清单重排 1..N
            r["rank"] = i + 1
        # 组合建议权重：等权（∑=1）。长周期回测显示等权稳定优于概率加权，
        # 故线上口径与回测最优（双周·等权·缓冲·行业≤3）保持一致。
        ew = 1.0 / len(picks) if picks else 0.0
        for r in picks:
            r["suggested_weight"] = round(ew, 4)

        as_of = rows[0].get("as_of_date")
        return {
            "scope": industry or "全市场",
            "industry": industry,
            "as_of_date": as_of if isinstance(as_of, str) else (self.repo.latest_snapshot_date().isoformat() if self.repo.latest_snapshot_date() else None),
            "universe_size": len(rows),
            "count": len(picks),
            "industry_cap": cap,
            "strategy": {**self.STRATEGY_HINT, "industry_cap": cap},
            "items": picks,
            "disclaimer": "强弱分为横截面相对排序(非绝对涨跌概率)，仅供技术研究，不构成投资建议。",
        }

    def list_industries(self, as_of_date: Optional[date] = None) -> Dict[str, Any]:
        """当日快照的可选行业清单（含各行业股票数），供前端下拉。"""
        items = self.repo.list_industries(as_of_date=as_of_date)
        latest = self.repo.latest_snapshot_date()
        return {
            "as_of_date": latest.isoformat() if latest else None,
            "count": len(items),
            "industries": items,
        }

    def summary(self) -> Dict[str, Any]:
        return self.repo.summary()

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
