# -*- coding: utf-8 -*-
"""
=========================================
个股行业归属快照服务（Industry Snapshot）
=========================================

目标：把「个股 -> 所属行业」按 as_of_date 定期快照存入 stock_industry 表，
逐步积累 point-in-time 归属历史，供预测建模按日期对齐使用（避免未来函数）。

取数策略（无需 TUSHARE_TOKEN）：
    akshare 东财行业板块列表(stock_board_industry_name_em)
      → 逐个板块拉成分股(stock_board_industry_cons_em)
      → 反查出「个股 -> 行业」映射（约 90 个板块，一票只属一个东财一级行业）

相比逐只 get_belong_board（5000+ 次请求），按板块反查只需约 90 次，成本低得多。

代码口径：成分股返回的是 6 位纯代码，这里统一转成与 stock_daily 一致的
canonical 带后缀格式（如 600519 -> 600519.SH）后落库。

⚠️ 数据仅供技术研究，不构成任何投资建议。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class IndustrySnapshotError(Exception):
    """行业快照流程可预期的业务错误（数据源不可用等）。"""


def _canonical_bs(code: str) -> Optional[str]:
    """baostock 代码 'sh.600000'/'sz.000001' -> 与 stock_daily 对齐的 '600000.SH'。"""
    raw = (code or "").strip().lower()
    if "." not in raw:
        return None
    ex, num = raw.split(".", 1)
    num = num.strip()
    ex_map = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    suffix = ex_map.get(ex)
    if not suffix or not num.isdigit():
        return None
    return f"{num}.{suffix}"


def _split_industry(text: str) -> Tuple[Optional[str], str]:
    """证监会行业串 'J66货币金融服务' -> (industry_code='J66', name='货币金融服务')。

    无法识别前缀码时返回 (None, 原串)。
    """
    s = (text or "").strip()
    m = re.match(r"^([A-Z]\d*)\s*(.*)$", s)
    if m and m.group(2):
        return m.group(1), m.group(2).strip()
    return None, s


def _canonical(code: str) -> Optional[str]:
    """akshare 6 位纯代码 -> 带交易所后缀（与 stock_daily.code 对齐，如 600519->600519.SH）。

    stock_daily 里 A 股以 "XXXXXX.SH/.SZ/.BJ" 存储（回填喂的是带后缀 ts_code），
    而 akshare 行业成分股给的是 6 位纯代码，这里补上交易所后缀以便两表 join。
    失败返回 None。
    """
    try:
        from src.services.stock_code_utils import _infer_cn_exchange, normalize_code

        base = normalize_code((code or "").strip())
        if not base:
            return None
        if base.isdigit() and len(base) == 6:
            ex = _infer_cn_exchange(base)
            return f"{base}.{ex}" if ex else base
        return base
    except Exception:  # noqa: BLE001 - 单只转换失败不应中断整体
        return None


def _pick(row: Dict[str, Any], *names: str) -> Any:
    """从一行（dict）里按候选列名取第一个非空值。"""
    for n in names:
        if n in row and row[n] is not None and str(row[n]).strip() != "":
            return row[n]
    return None


class IndustrySnapshotService:
    """基于 akshare 东财行业板块，构建并存档个股行业归属快照。"""

    def __init__(self, repo=None):
        self._repo = repo  # 延迟初始化，避免导入期触发 DB

    @property
    def repo(self):
        if self._repo is None:
            from src.repositories.stock_industry_repo import StockIndustryRepository

            self._repo = StockIndustryRepository()
        return self._repo

    def build_mapping_baostock(self) -> Dict[str, Dict[str, Any]]:
        """用 baostock query_stock_industry 构建「canonical code -> {industry, industry_code}」。

        baostock 用证监会行业分类（门类如 J66 货币金融服务），免费稳定、一次请求拉全市场，
        比东财逐板块请求可靠得多，作为首选源。行业为空的票跳过。
        """
        try:
            import baostock as bs
        except ImportError as exc:  # noqa: BLE001
            raise IndustrySnapshotError("未安装 baostock（pip install baostock）") from exc

        lg = bs.login()
        if getattr(lg, "error_code", "1") != "0":
            raise IndustrySnapshotError(f"baostock 登录失败：{getattr(lg, 'error_msg', '未知')}")
        try:
            rs = bs.query_stock_industry()
            if getattr(rs, "error_code", "1") != "0":
                raise IndustrySnapshotError(f"baostock 行业查询失败：{getattr(rs, 'error_msg', '未知')}")
            mapping: Dict[str, Dict[str, Any]] = {}
            # 字段顺序: updateDate, code, code_name, industry, industryClassification
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                if len(row) < 4:
                    continue
                canon = _canonical_bs(row[1])
                industry = (row[3] or "").strip()
                if not canon or not industry:
                    continue
                ind_code, ind_name = _split_industry(industry)
                if canon not in mapping:
                    mapping[canon] = {"industry": ind_name or industry, "industry_code": ind_code}
        finally:
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass

        if not mapping:
            raise IndustrySnapshotError("baostock 未返回任何有效行业归属")
        logger.info("baostock 行业映射构建完成：%d 只股票", len(mapping))
        return mapping

    def build_mapping(self, *, sleep: float = 0.3, limit_boards: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        """拉取全部行业板块成分股，构建「canonical code -> {industry, industry_code}」。"""
        try:
            import akshare as ak
        except ImportError as exc:  # noqa: BLE001
            raise IndustrySnapshotError("未安装 akshare，无法获取行业板块数据（pip install akshare）") from exc

        try:
            boards = ak.stock_board_industry_name_em()
        except Exception as exc:  # noqa: BLE001
            raise IndustrySnapshotError(f"获取行业板块列表失败：{exc}") from exc

        if boards is None or boards.empty:
            raise IndustrySnapshotError("行业板块列表为空，数据源可能不可用")

        board_rows = boards.to_dict(orient="records")
        if limit_boards is not None:
            board_rows = board_rows[:limit_boards]

        mapping: Dict[str, Dict[str, Any]] = {}
        total_boards = len(board_rows)
        logger.info("开始构建行业快照：共 %d 个行业板块", total_boards)

        for i, brow in enumerate(board_rows, 1):
            board_name = _pick(brow, "板块名称", "name", "行业名称")
            board_code = _pick(brow, "板块代码", "code")
            if not board_name:
                continue
            board_name = str(board_name).strip()
            board_code = str(board_code).strip() if board_code else None

            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
            except Exception as exc:  # noqa: BLE001 - 单板块失败不中断整体
                logger.warning("[%d/%d] 行业「%s」成分股获取失败，跳过：%s",
                               i, total_boards, board_name, exc)
                continue

            if cons is None or cons.empty:
                logger.warning("[%d/%d] 行业「%s」成分股为空，跳过", i, total_boards, board_name)
                continue

            added = 0
            for crow in cons.to_dict(orient="records"):
                raw_code = _pick(crow, "代码", "股票代码", "code")
                if not raw_code:
                    continue
                canon = _canonical(str(raw_code))
                if not canon:
                    continue
                # 一票只保留首个命中的行业（东财一级行业互斥）
                if canon not in mapping:
                    mapping[canon] = {"industry": board_name, "industry_code": board_code}
                    added += 1

            logger.info("[%d/%d] 行业「%s」→ 新增 %d 只（累计 %d）",
                        i, total_boards, board_name, added, len(mapping))
            if sleep > 0:
                time.sleep(sleep)

        if not mapping:
            raise IndustrySnapshotError("未构建出任何行业归属，数据源可能全部失败")
        return mapping

    def run(
        self,
        *,
        as_of: Optional[date] = None,
        source: str = "auto",
        sleep: float = 0.3,
        limit_boards: Optional[int] = None,
    ) -> Dict[str, Any]:
        """构建并落库一次行业快照，返回摘要。

        source:
        - "auto"（默认）：先试 baostock（稳定、一次拉全市场），失败再退回东财板块。
        - "baostock" / "akshare_em"：只用指定源。
        """
        as_of = as_of or date.today()

        order = []
        if source in ("auto", "baostock"):
            order.append("baostock")
        if source in ("auto", "akshare_em"):
            order.append("akshare_em")
        if not order:
            raise IndustrySnapshotError(f"未知行业数据源：{source}")

        mapping: Optional[Dict[str, Dict[str, Any]]] = None
        used_source: Optional[str] = None
        errors = []
        for src in order:
            try:
                if src == "baostock":
                    mapping = self.build_mapping_baostock()
                else:
                    mapping = self.build_mapping(sleep=sleep, limit_boards=limit_boards)
                used_source = src
                break
            except Exception as exc:  # noqa: BLE001 - 尝试下一个源
                errors.append(f"{src}: {exc}")
                logger.warning("行业源 %s 不可用，尝试下一个：%s", src, exc)

        if not mapping or used_source is None:
            raise IndustrySnapshotError("所有行业数据源均失败：" + " | ".join(errors))

        written = self.repo.save_snapshot(mapping, as_of_date=as_of, source=used_source)
        industries = len({v["industry"] for v in mapping.values()})
        summary = {
            "as_of_date": as_of.isoformat(),
            "codes": len(mapping),
            "industries": industries,
            "written": written,
            "source": used_source,
        }
        logger.info("行业快照完成：%s 覆盖 %d 只 / %d 个行业，写入 %d 条",
                    summary["as_of_date"], summary["codes"], summary["industries"], written)
        return summary
