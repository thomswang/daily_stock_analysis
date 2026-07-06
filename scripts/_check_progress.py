# -*- coding: utf-8 -*-
"""查看各 progress 文件的所有状态分布 + failed 明细。"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys_path = str(ROOT)
import sys
sys.path.insert(0, sys_path)

for f in sorted(Path(ROOT / "data").glob("kline_progress_*.json")):
    if ".tmp" in f.name or ".bak" in f.name:
        continue
    try:
        d = json.load(open(f, encoding="utf-8"))
        c = d.get("codes", {})
        counter = Counter(r.get("status", "unknown") for r in c.values())

        print(f"\n{'='*70}")
        print(f"{f.name}  (total={len(c)})")
        print(f"{'─'*70}")
        for status, cnt in sorted(counter.items(), key=lambda x: -x[1]):
            print(f"  {status:<12} {cnt}")

        # failed 明细
        failed = {code: r for code, r in c.items() if r.get("status") == "failed"}
        if failed:
            print(f"\n  failed 明细:")
            for code, r in list(failed.items())[:30]:
                err = r.get("error", "")[:50]
                print(f"    {code:<12} {err}")

        # meta 信息
        meta = d.get("meta", {})
        if meta:
            start = meta.get("start_date", "?")
            end = meta.get("end_date", "?")
            adj = meta.get("adj_type", "?")
            print(f"\n  meta: {start} ~ {end}  adj={adj}")
    except Exception as e:
        print(f"\n{f.name}: {e}")
