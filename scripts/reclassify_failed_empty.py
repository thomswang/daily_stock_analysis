# -*- coding: utf-8 -*-
"""用 baostock 作权威源，把回填台账里“确无数据”的 failed 复核改标为 empty。

背景：当 eastmoney 等源被封/熔断时，次新股（请求区间尚未上市）会因错误信息里
混入 CircuitOpen/连接错误而被误判为 failed（而非 empty），导致反复重试。
本脚本用 baostock（A 股全历史，可回溯 2006）逐一核对：
  - baostock 在该区间返回 0 行 → 确无数据 → 标 empty（终态，不再重试）
  - baostock 有数据 → 说明是真可补的股票 → 保留 failed 并列出，交由正常回填补齐

用法：
  python scripts/reclassify_failed_empty.py data/backfill_prev6y_2018-07-01_2020-06-30.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime


def _bscode(code: str) -> str:
    c = code.split(".")[0]
    if not c:
        return ""
    if c[0] == "6":
        return "sh." + c
    if c[0] in ("0", "3"):
        return "sz." + c
    if c[0] in ("8", "4"):
        return "bj." + c  # 北交所（baostock 可能不支持）
    return "sz." + c


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/reclassify_failed_empty.py <台账json路径>")
        return 2
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("meta", {})
    start = meta.get("start_date")
    end = meta.get("end_date")
    codes = data.get("codes", {})
    failed = [c for c, v in codes.items() if v.get("status") == "failed"]
    print(f"台账: {path}")
    print(f"区间: {start} ~ {end}   待复核 failed: {len(failed)}")
    if not failed:
        print("没有 failed，无需处理。")
        return 0

    import baostock as bs

    bs.login()
    marked_empty = 0
    has_data = []
    unsupported = []
    errors = 0
    try:
        for i, code in enumerate(failed, 1):
            bc = _bscode(code)
            if bc.startswith("bj."):
                unsupported.append(code)
                continue
            try:
                rs = bs.query_history_k_data_plus(
                    bc, "date,close", start_date=start, end_date=end,
                    frequency="d", adjustflag="2",
                )
                n = 0
                while rs.error_code == "0" and rs.next():
                    n += 1
                    if n >= 1:
                        break  # 只需判断有无
            except Exception:  # noqa: BLE001
                errors += 1
                continue

            if n > 0:
                has_data.append(code)
            else:
                rec = codes[code]
                rec["status"] = "empty"
                rec["error"] = "baostock 确认区间无数据（未上市/无记录）"
                rec["updated_at"] = datetime.now().isoformat(timespec="seconds")
                marked_empty += 1

            if i % 100 == 0:
                print(f"  进度 {i}/{len(failed)}  已标empty={marked_empty} 有数据={len(has_data)}")
    finally:
        bs.logout()

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print("\n===== 复核完成 =====")
    print(f"标为 empty（确无数据）: {marked_empty}")
    print(f"仍有数据可补（保留 failed）: {len(has_data)}")
    print(f"北交所暂不支持复核（保留 failed）: {len(unsupported)}")
    print(f"复核出错跳过: {errors}")
    if has_data:
        print("有数据可补的代码：")
        print(",".join(has_data))
    if unsupported:
        print("北交所代码：")
        print(",".join(unsupported))
    return 0


if __name__ == "__main__":
    sys.exit(main())
