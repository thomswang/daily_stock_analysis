# -*- coding: utf-8 -*-
"""回填进度台账（原子 JSON 落盘）。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict


class ProgressLedger:
    """记录每只股票的回填状态，支持随时中断重跑。"""

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {"meta": {}, "codes": {}}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                self.data.setdefault("meta", {})
                self.data.setdefault("codes", {})
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("进度台账读取失败，将重建：%s", exc)
                self.data = {"meta": {}, "codes": {}}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)

        # Windows 下 os.replace 偶发 PermissionError（IDE/杀软占用目标文件），
        # 重试 3 次，每次间隔递增。
        last_err = None
        for attempt in range(3):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError as exc:
                last_err = exc
                import time
                time.sleep(0.1 * (attempt + 1))
        # 重试失败：退回直接写入（非原子，但保证数据不丢）
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            raise last_err

    def get(self, code: str) -> Dict[str, Any]:
        return self.data["codes"].get(code, {})

    def update(self, code: str, **fields: Any) -> None:
        rec = self.data["codes"].get(code, {})
        rec.update(fields)
        rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.data["codes"][code] = rec

    def set_meta(self, **fields: Any) -> None:
        self.data["meta"].update(fields)
        self.data["meta"]["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for rec in self.data["codes"].values():
            st = rec.get("status", "unknown")
            counts[st] = counts.get(st, 0) + 1
        return counts
