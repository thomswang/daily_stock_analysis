# -*- coding: utf-8 -*-
"""A 股上市日静态缓存（westock profile listedDate）。"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_LIST_DATES_PATH = os.path.join("data", "cache", "cn_list_dates.json")


class CnListDateStore:
    """读写 data/cache/cn_list_dates.json。"""

    def __init__(self, path: str = DEFAULT_LIST_DATES_PATH):
        self.path = path
        self.data: Dict[str, Any] = {"meta": {}, "codes": {}}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            self.data.setdefault("meta", {})
            self.data.setdefault("codes", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("上市日缓存读取失败，将重建：%s", exc)
            self.data = {"meta": {}, "codes": {}}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, code: str) -> Optional[str]:
        plain = _plain_code(code)
        rec = self.data["codes"].get(plain)
        if not rec:
            return None
        if isinstance(rec, str):
            return rec[:10]
        return str(rec.get("list_date") or "")[:10] or None

    def get_date(self, code: str) -> Optional[date]:
        s = self.get(code)
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except (TypeError, ValueError):
            return None

    def upsert(self, code: str, *, list_date: Optional[str], name: Optional[str] = None) -> None:
        plain = _plain_code(code)
        if not plain:
            return
        rec: Dict[str, Any] = self.data["codes"].get(plain, {})
        if list_date:
            rec["list_date"] = list_date[:10]
        if name:
            rec["name"] = name
        rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.data["codes"][plain] = rec

    def upsert_many(self, rows: Dict[str, Dict[str, Optional[str]]]) -> int:
        n = 0
        for code, fields in rows.items():
            self.upsert(
                code,
                list_date=fields.get("list_date"),
                name=fields.get("name"),
            )
            if fields.get("list_date"):
                n += 1
        return n

    def has_record(self, code: str) -> bool:
        return _plain_code(code) in self.data["codes"]

    def merge_from(self, other: "CnListDateStore") -> int:
        """合并另一 store 的 codes，返回新增/覆盖条数。"""
        n = 0
        for code, rec in other.data.get("codes", {}).items():
            plain = _plain_code(code)
            if plain not in self.data["codes"]:
                n += 1
            self.data["codes"][plain] = rec
        return n

    def to_map(self) -> Dict[str, date]:
        out: Dict[str, date] = {}
        for code, rec in self.data.get("codes", {}).items():
            if isinstance(rec, str):
                raw = rec
            else:
                raw = rec.get("list_date")
            if not raw:
                continue
            try:
                out[_plain_code(code)] = date.fromisoformat(str(raw)[:10])
            except (TypeError, ValueError):
                continue
        return out

    def set_meta(self, **fields: Any) -> None:
        self.data["meta"].update(fields)
        self.data["meta"]["updated_at"] = datetime.now().isoformat(timespec="seconds")


def load_list_date_map(path: Optional[str] = None) -> Dict[str, date]:
    """加载全量上市日映射（代码 -> date），文件不存在时返回空 dict。"""
    return CnListDateStore(path or DEFAULT_LIST_DATES_PATH).to_map()


def merge_shard_files(
    output_path: str,
    shard_paths: list[str],
    *,
    delete_shards: bool = False,
) -> Dict[str, Any]:
    """合并多分片上市日 JSON → 单一 cn_list_dates.json。"""
    merged = CnListDateStore(output_path)
    merged_from: list[str] = []
    for path in shard_paths:
        if not os.path.exists(path):
            logger.warning("分片不存在，跳过：%s", path)
            continue
        shard = CnListDateStore(path)
        merged.merge_from(shard)
        merged_from.append(path)

    with_date = sum(
        1 for rec in merged.data["codes"].values()
        if (rec.get("list_date") if isinstance(rec, dict) else rec)
    )
    merged.set_meta(
        source="westock profile listedDate",
        merged_from=merged_from,
        total_codes=len(merged.data["codes"]),
        with_list_date=with_date,
    )
    merged.save()

    if delete_shards:
        for path in merged_from:
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("删除分片失败 %s: %s", path, exc)

    return {
        "output": output_path,
        "shards": len(merged_from),
        "total_codes": len(merged.data["codes"]),
        "with_list_date": with_date,
    }


def default_shard_path(shard_index: int, shard_total: int) -> str:
    base = os.path.join("data", "cache", "cn_list_dates")
    return f"{base}.shard{shard_index}of{shard_total}.json"


def _plain_code(code: str) -> str:
    from data_provider.base import normalize_stock_code

    return normalize_stock_code(code)
