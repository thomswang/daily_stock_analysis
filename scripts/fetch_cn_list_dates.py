#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量拉取 A 股上市日（westock profile listedDate）并写入 data/cache/cn_list_dates.json。

支持断点续传；支持多进程分片（--shard）与合并（--merge）。

用法：
  set WESTOCK_DATA_DIR=e:/analysis/westock-data

  # 三进程并行（各开一终端）：
  python scripts/fetch_cn_list_dates.py --shard 0/3
  python scripts/fetch_cn_list_dates.py --shard 1/3
  python scripts/fetch_cn_list_dates.py --shard 2/3

  # 全部完成后合并：
  python scripts/fetch_cn_list_dates.py --merge

  # 单进程：
  python scripts/fetch_cn_list_dates.py
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402

setup_env()

from data_provider.westock_client import WestockCliError, fetch_profile_listed_dates  # noqa: E402
from data_provider.base import normalize_stock_code  # noqa: E402
from src.services.cn_list_date_store import (  # noqa: E402
    CnListDateStore,
    DEFAULT_LIST_DATES_PATH,
    default_shard_path,
    merge_shard_files,
)
from src.services.backfill import CodeListLoader

logger = logging.getLogger("fetch_cn_list_dates")


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _parse_shard(text: str) -> Tuple[int, int]:
    """'0/3' -> (0, 3)"""
    parts = text.strip().split("/")
    if len(parts) != 2:
        raise ValueError(f"分片格式应为 INDEX/TOTAL，例如 0/3，收到: {text}")
    idx, total = int(parts[0]), int(parts[1])
    if total < 1 or idx < 0 or idx >= total:
        raise ValueError(f"无效分片 {text}（INDEX 须满足 0 <= INDEX < TOTAL）")
    return idx, total


def shard_codes(codes: List[str], shard_index: int, shard_total: int) -> List[str]:
    """按序号取模分片，保证多进程互不重叠。"""
    return [c for i, c in enumerate(codes) if i % shard_total == shard_index]


def run_fetch(
    *,
    index_path: str | None,
    output_path: str,
    batch_size: int,
    sleep: float,
    limit: int | None,
    force: bool,
    shard_index: Optional[int] = None,
    shard_total: Optional[int] = None,
) -> dict:
    codes = [
        normalize_stock_code(c)
        for c in CodeListLoader.load_all_cn_codes(index_path=index_path)
    ]
    if limit is not None:
        codes = codes[:limit]

    if shard_index is not None and shard_total is not None:
        codes = shard_codes(codes, shard_index, shard_total)
        logger.info("分片 %d/%d：本进程负责 %d 只", shard_index, shard_total, len(codes))

    store = CnListDateStore(output_path)
    if force:
        pending = codes
    else:
        pending = [c for c in codes if not store.has_record(c)]

    total = len(codes)
    todo = len(pending)
    logger.info("待拉上市日 %d / %d 只（batch=%d）→ %s", todo, total, batch_size, output_path)

    ok = 0
    miss = 0
    failed_batches = 0

    for i, batch in enumerate(_chunks(pending, batch_size), 1):
        try:
            rows = fetch_profile_listed_dates(batch)
        except WestockCliError as exc:
            failed_batches += 1
            logger.warning("批次 %d 失败 (%s): %s", i, batch[:3], exc)
            if sleep > 0:
                time.sleep(sleep)
            continue

        # 本批 westock 未返回的代码也写入空记录，避免反复请求
        for code in batch:
            if code not in rows:
                rows[code] = {"list_date": None, "name": None}

        batch_ok = store.upsert_many(rows)
        for code in batch:
            if not rows.get(code, {}).get("list_date"):
                miss += 1
        ok += batch_ok
        store.set_meta(
            source="westock profile listedDate",
            shard=f"{shard_index}/{shard_total}" if shard_index is not None else None,
            shard_total=total,
            fetched=len(store.data["codes"]),
            last_batch=i,
        )
        store.save()

        if i % 10 == 0 or i * batch_size >= todo:
            with_date = sum(
                1 for rec in store.data["codes"].values()
                if isinstance(rec, dict) and rec.get("list_date")
            )
            logger.info(
                "[%d/%d 批] 本批有日期 %d，本分片累计 %d（缺 %d）",
                i, (todo + batch_size - 1) // batch_size, batch_ok, with_date, miss,
            )
        if sleep > 0:
            time.sleep(sleep)

    with_date = sum(
        1 for rec in store.data["codes"].values()
        if isinstance(rec, dict) and rec.get("list_date")
    )
    store.set_meta(
        source="westock profile listedDate",
        shard=f"{shard_index}/{shard_total}" if shard_index is not None else None,
        shard_total=total,
        fetched=len(store.data["codes"]),
        with_list_date=with_date,
        missing=miss,
        failed_batches=failed_batches,
    )
    store.save()

    return {
        "total": total,
        "pending": todo,
        "written": ok,
        "with_list_date": with_date,
        "missing": miss,
        "failed_batches": failed_batches,
        "path": output_path,
    }


def run_merge(*, output_path: str, shard_glob: str, delete_shards: bool) -> dict:
    paths = sorted(glob.glob(shard_glob))
    if not paths:
        raise SystemExit(f"未找到分片文件：{shard_glob}")
    logger.info("合并 %d 个分片 → %s", len(paths), output_path)
    return merge_shard_files(output_path, paths, delete_shards=delete_shards)


def main() -> int:
    parser = argparse.ArgumentParser(description="批量拉取 A 股上市日（westock profile）")
    parser.add_argument("--index-path", default=None, help="stocks.index.json 路径")
    parser.add_argument("--output", default=None, help="输出 JSON 路径")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("WESTOCK_PROFILE_BATCH", "20")))
    parser.add_argument("--sleep", type=float, default=float(os.getenv("WESTOCK_PROFILE_SLEEP", "0.3")))
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 只（试跑）")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存，全量重拉")
    parser.add_argument(
        "--shard", type=str, default=None, metavar="INDEX/TOTAL",
        help="多进程分片，如 0/3、1/3、2/3（各进程写独立文件）",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="合并 data/cache/cn_list_dates.shard*of*.json → cn_list_dates.json",
    )
    parser.add_argument(
        "--shard-glob", default=os.path.join("data", "cache", "cn_list_dates.shard*of*.json"),
        help="--merge 时匹配的分片 glob",
    )
    parser.add_argument("--delete-shards", action="store_true", help="合并后删除分片文件")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.merge:
        stats = run_merge(
            output_path=args.output or DEFAULT_LIST_DATES_PATH,
            shard_glob=args.shard_glob,
            delete_shards=args.delete_shards,
        )
        print("\n===== 分片合并完成 =====")
        for k, v in stats.items():
            print(f"{k}: {v}")
        print("========================\n")
        return 0

    shard_index: Optional[int] = None
    shard_total: Optional[int] = None
    if args.shard:
        shard_index, shard_total = _parse_shard(args.shard)

    output_path = args.output
    if not output_path:
        if shard_index is not None:
            output_path = default_shard_path(shard_index, shard_total)
        else:
            output_path = DEFAULT_LIST_DATES_PATH

    stats = run_fetch(
        index_path=args.index_path,
        output_path=output_path,
        batch_size=max(1, args.batch_size),
        sleep=max(0.0, args.sleep),
        limit=args.limit,
        force=args.force,
        shard_index=shard_index,
        shard_total=shard_total,
    )
    print("\n===== 上市日拉取完成 =====")
    for k, v in stats.items():
        print(f"{k}: {v}")
    print("==========================\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
