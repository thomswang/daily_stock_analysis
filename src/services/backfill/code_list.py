# -*- coding: utf-8 -*-
"""A 股代码清单加载。"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

INDEX_CANDIDATES = [
    os.path.join("data", "cache", "stocks.index.json"),
    os.path.join("static", "stocks.index.json"),
    os.path.join("apps", "dsa-web", "public", "stocks.index.json"),
]


class BackfillError(Exception):
    """回填流程可预期的业务错误。"""


class CodeListLoader:
    """从 stocks.index.json 读取 A 股代码。"""

    @staticmethod
    def resolve_index_path(index_path: Optional[str] = None) -> Optional[str]:
        if index_path and os.path.exists(index_path):
            return index_path
        for cand in INDEX_CANDIDATES:
            if os.path.exists(cand):
                return cand
        return None

    @classmethod
    def load_all_cn_codes(cls, index_path: Optional[str] = None) -> List[str]:
        path = cls.resolve_index_path(index_path)
        if not path:
            raise BackfillError(
                "未找到 stocks.index.json（尝试位置："
                + " / ".join(INDEX_CANDIDATES)
                + "）；请用 --codes-file 指定，或先生成索引。"
            )
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)

        codes: List[str] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 8:
                continue
            ts_code = row[0]
            country = row[6] if len(row) > 6 else None
            sec_type = row[7] if len(row) > 7 else None
            listed = row[8] if len(row) > 8 else True
            if country == "CN" and sec_type == "stock" and listed and ts_code:
                codes.append(str(ts_code).strip().upper())

        seen = set()
        uniq = [c for c in codes if not (c in seen or seen.add(c))]
        logger.info("从 %s 载入 A 股代码 %d 只", path, len(uniq))
        return uniq

    @classmethod
    def load_cn_name_map(cls, index_path: Optional[str] = None) -> Dict[str, str]:
        path = cls.resolve_index_path(index_path)
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 stocks.index.json 名称映射失败：%s", exc)
            return {}

        name_map: Dict[str, str] = {}
        for row in rows:
            if not isinstance(row, list) or len(row) < 8:
                continue
            ts_code, name = row[0], row[2] if len(row) > 2 else None
            country = row[6] if len(row) > 6 else None
            sec_type = row[7] if len(row) > 7 else None
            if country == "CN" and sec_type == "stock" and ts_code and name:
                name_map[str(ts_code).strip().upper()] = str(name).strip()
        return name_map
