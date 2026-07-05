# -*- coding: utf-8 -*-
"""重置回填进度台账里被「kline 返回空 / quote 返回空」误标 empty 的记录。

背景：之前 KlineBackfillService._ingest_with_retry 把「rows_saved == 0」
统一置为 "kline 返回空" 并命中 _NO_DATA_MARKERS，导致凌晨接口批量风控时
大量票被永久标为 empty（终态不再重试）。修复代码后，用本脚本清掉
台账里那些错误的 empty 记录，下次 backfill 就会重新拉这些票。

用法：
    # dry-run 看会清多少
    python scripts/reset_false_empty.py --list

    # 真正清理默认所有 data/*progress*.json
    python scripts/reset_false_empty.py

    # 只清理某个台账
    python scripts/reset_false_empty.py --files data/kline_progress_2023_2024.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

# 错误信息含以下任一子串、且 status=empty 的记录被视为「被误伤」，会被清除
FALSE_EMPTY_MARKERS = (
    "返回空",           # 包含 "kline 返回空" / "quote 返回空"
    "疑似瞬时问题",
)


def _find_default_progress_files() -> List[str]:
    root = Path(__file__).resolve().parents[1] / "data"
    if not root.is_dir():
        return []
    hits = sorted({
        *glob.glob(str(root / "*progress*.json")),
        *glob.glob(str(root / "kline_progress_*.json")),
        *glob.glob(str(root / "quote_progress_*.json")),
        *glob.glob(str(root / "backfill_progress*.json")),
    })
    # 排除 .tmp 副本
    return [p for p in hits if not p.endswith(".tmp")]


def _is_false_empty(rec: dict) -> bool:
    if not isinstance(rec, dict):
        return False
    if rec.get("status") != "empty":
        return False
    err = str(rec.get("error") or "")
    return any(m in err for m in FALSE_EMPTY_MARKERS)


def process_file(path: str, *, dry_run: bool) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    codes = data.get("codes") or {}
    false_empty_codes = [c for c, rec in codes.items() if _is_false_empty(rec)]

    stats = {
        "file": path,
        "total_codes": len(codes),
        "false_empty": len(false_empty_codes),
        "sample": false_empty_codes[:5],
    }

    if dry_run or not false_empty_codes:
        return stats

    # 备份原文件
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak_{ts}"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # 删除被误伤的记录（让下次跑当作新票处理）
    for c in false_empty_codes:
        del codes[c]
    data["codes"] = codes
    meta = data.setdefault("meta", {})
    meta["reset_false_empty_at"] = datetime.now().isoformat(timespec="seconds")
    meta["reset_false_empty_count"] = (
        int(meta.get("reset_false_empty_count") or 0) + len(false_empty_codes)
    )

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

    stats["backup"] = backup
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="重置被误标 empty 的回填台账")
    parser.add_argument("--files", nargs="*", help="指定台账 JSON 文件（默认自动发现）")
    parser.add_argument("--list", action="store_true", help="仅统计不修改")
    args = parser.parse_args()

    files = args.files or _find_default_progress_files()
    if not files:
        print("未找到任何回填台账文件（data/*progress*.json）")
        return 1

    total_reset = 0
    for path in files:
        try:
            stats = process_file(path, dry_run=args.list)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {path}: {exc}")
            continue
        head = "[DRY]" if args.list else "[OK ]"
        print(
            f"{head} {stats['file']}\n"
            f"       codes={stats['total_codes']}, false_empty={stats['false_empty']}"
            + (f", sample={stats['sample']}" if stats["sample"] else "")
            + (f", backup={stats.get('backup')}" if stats.get("backup") else "")
        )
        total_reset += stats["false_empty"]

    print(f"\n总计{'待' if args.list else '已'}重置 {total_reset} 条 empty 记录")
    if args.list:
        print("如确认，去掉 --list 参数再跑一次即可真正重置（会自动备份原文件）")
    else:
        print("下次 `python backfill.py kline ...` 会重新拉这些票")
    return 0


if __name__ == "__main__":
    sys.exit(main())
