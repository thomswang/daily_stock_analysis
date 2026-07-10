#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build Stock Autocomplete Index from EastMoney (free, no token/points required).

This is an alternative data source to Tushare for refreshing
``apps/dsa-web/public/stocks.index.json``. EastMoney's public ``clist`` API
requires no token and no points, and returns the full A-share list
(Shanghai / Shenzhen / Beijing).

Design goal: the output JSON layout is *byte-for-byte structurally identical*
to the asset produced by ``generate_index_from_csv.py`` (compressed array
form), so the Web autocomplete and backend parser keep working unchanged.

To guarantee this, we reuse the core builders of ``generate_index_from_csv.py``
(``build_stock_index`` / ``compress_index`` / pinyin / alias / name-strip
logic) and only swap the data source.

Usage:
    python scripts/build_stock_index_from_eastmoney.py            # verify only
    python scripts/build_stock_index_from_eastmoney.py --apply    # write index
    python scripts/build_stock_index_from_eastmoney.py --limit 50 # quick smoke test

Note:
    EastMoney's push2 endpoint may be reset (RST) by some corporate/ISP
    networks. If every host fails, switch network (or use a proxy/VPN) and
    retry; or fall back to the official Tushare source.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

# Reuse the official index builders so the output layout stays identical.
sys.path.insert(0, str(Path(__file__).parent))
import generate_index_from_csv as gen  # noqa: E402

try:
    import requests  # noqa: E402
    from urllib3.util.retry import Retry  # noqa: E402
except ImportError:
    print("[Error] requests/urllib3 not available; install with: pip install requests")
    sys.exit(1)


# Candidate hosts. Different networks resolve/allow different ones.
# `push2delay` is EastMoney's delayed-quote node and is often NOT blocked by
# corporate/ISP networks that reset the `push2`/`push2his` nodes, so we try it
# first. The response shape (f12/f13/f14) is identical to the live node.
#
# ⚠️ Important side-effect: on the delayed/backup nodes, EastMoney's *dedicated*
# Beijing Stock Exchange board `m:0+t:90` is NOT served (returns total=0), even
# though the same board may work on the live `push2` node. That is exactly why
# we do NOT use `m:0+t:90` for BSE — instead we pull the NEEQ board `m:0+t:81`
# (which IS served) and filter out pure-BSE codes. See EM_FS_BSE below.
EM_HOSTS = [
    "https://push2delay.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://push2his.eastmoney.com",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# Board filters covering Shanghai / Shenzhen A-shares (excluding BSE, which is
# handled separately via EM_FS_BSE because EastMoney's live `m:0+t:90` board is
# not served by the delayed/backup hosts — see notes in fetch_bse_name_map).
#   m:1+t:2  -> SH main board
#   m:1+t:23 -> SH STAR (科创板)
#   m:0+t:6  -> SZ main board
#   m:0+t:80 -> SZ ChiNext (创业板)
EM_FS_BOARDS = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80"

# 北交所数据源：东方财富的新三板板块 m:0+t:81（含北交所 + 老三板 + 新三板
# 基础/创新层，共约 6800 条）。
#
# 为什么不用北交所独占板块 m:0+t:90？
#   因为当前可用的延时节点（push2delay）对 m:0+t:90 直接返回 total=0（该板块
#   不在延时节点下发），而对 m:0+t:81 正常下发。所以只能从「新三板大池子」里
#   把北交所捞出来。
#
# 为什么能干净捞出纯北交所？
#   该板块无法用任何字段（f13 恒为 0）区分北交所 vs 新三板/老三板，因此北交所
#   代码清单以现有官方索引的 `.BJ` 条目为种子（Tushare 的 BJ=纯北交所，无杂质），
#   再从此处按代码匹配刷新名称，保证只含纯北交所。
EM_FS_BSE = "m:0+t:81"


def build_session():
    """Session with retry/backoff so transient RSTs can self-heal."""
    retry = Retry(
        connect=3,
        read=2,
        redirect=0,
        status=0,
        other=0,
        backoff_factor=0.5,
        raise_on_status=False,
        allowed_methods=frozenset(["GET"]),
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def code_to_tscode(code: str):
    """Map a bare EastMoney code to a Tushare-style ts_code.

    We decide the exchange suffix by code prefix (more reliable than f13):
      - 60xxxx / 688xxx / 689xxx / 900xxx -> .SH
      - 000xxx / 001xxx / 002xxx / 003xxx / 300xxx / 301xxx / 200xxx -> .SZ
      - 8xxxxx / 4xxxxx / 92xxxx -> .BJ (Beijing)
    """
    c = code.strip()
    if not c:
        return None
    if c[0] == "6" or c.startswith(("688", "689", "900")):
        return f"{c}.SH"
    if c[0] in ("8", "4") or c.startswith("92"):
        return f"{c}.BJ"
    return f"{c}.SZ"


def fetch_em_page(session, host: str, pn: int, pz: int, fs: str = None):
    """Fetch one page of the EastMoney clist from a given host.

    Returns (total, rows); raises on persistent network/HTTP failure.
    A single transient RST is retried with backoff before giving up, because
    EastMoney may reset connections under bursty requests.

    Args:
        fs: board filter string (defaults to EM_FS_BOARDS).
    """
    # 同一个东方财富 clist 接口，A 股/北交所/主板全部复用；唯一的差别就是
    # `fs`（板块筛选）参数：主板/创业板用 EM_FS_BOARDS，北交所用 EM_FS_BSE。
    # 所以拉不到北交所绝不是接口坏了，而是 `fs` 选错了板块（见 EM_FS_BSE 说明）。
    url = f"{host}/api/qt/clist/get"
    params = {
        "pn": pn,
        "pz": pz,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": fs or EM_FS_BOARDS,
        "fields": "f12,f13,f14",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    last_err = None
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            payload = resp.json() or {}
            data = payload.get("data") or {}
            return data.get("total", 0), data.get("diff") or []
        except Exception as e:  # RST / timeout / HTTP error
            last_err = e
            wait = 2 + attempt * 2
            print(f"      [重试] 第{pn}页连接被重置，{wait}s 后重试 ({attempt + 1}/3)")
            time.sleep(wait)
    raise last_err


def pick_host(session):
    """Return the first reachable EastMoney host, or None."""
    for host in EM_HOSTS:
        try:
            _, rows = fetch_em_page(session, host, 1, 10)
            if rows is not None:
                return host
        except Exception:
            continue
    return None


def fetch_all_a_shares(session, host: str):
    """Fetch the full A-share list from EastMoney via the given host, paged.

    Returns (total_reported, stocks) where stocks is a list of unified dicts:
        {"ts_code", "symbol", "name", "market", "aliases"}
    """
    stocks = []
    seen = set()
    pn = 1
    pz = 1000
    total = None

    while True:
        page_total, rows = fetch_em_page(session, host, pn, pz)
        if total is None:
            total = page_total
        if not rows:
            break

        for r in rows:
            code = (r.get("f12") or "").strip()
            name = (r.get("f14") or "").strip()
            ts_code = code_to_tscode(code)
            if not ts_code or not name:
                continue
            if ts_code in seen:
                continue
            seen.add(ts_code)

            market = gen.determine_market(ts_code)
            # Strip short-status XD/XR/DR prefixes via the same rule as the
            # Tushare path, so the stored name matches the official index.
            name = gen.normalize_stock_name_for_index(name, market)
            if not name:
                continue

            stocks.append({
                "ts_code": ts_code,
                "symbol": code,
                "name": name,
                "market": market,
                "aliases": [],
            })

        if len(stocks) >= total:
            break
        pn += 1
        time.sleep(random.uniform(0.5, 1.2))

    return total, stocks


def fetch_em_name_map(session, host: str, fs: str, pz: int = 1000):
    """Fetch a {code: name} map for an EastMoney board (all pages).

    Used for the Beijing Stock Exchange: EastMoney serves BSE together with the
    whole NEEQ (新三板) board ``m:0+t:81``, and that board cannot be filtered by
    a single field (f13 is always 0). So we pull the full name map and later
    match it against the `.BJ` code seed from the existing official index.
    """
    name_map = {}
    pn = 1
    while True:
        page_total, rows = fetch_em_page(session, host, pn, pz, fs=fs)
        if not rows:
            break
        for r in rows:
            code = (r.get("f12") or "").strip()
            name = (r.get("f14") or "").strip()
            if code:
                name_map[code] = name
        if pn * pz >= page_total:
            break
        pn += 1
        time.sleep(random.uniform(0.3, 0.8))
    return name_map


def collect_bse_seed_codes(official_path: Path):
    """Collect `.BJ` (ts_code, official_name) pairs from the existing official
    index as the BSE seed.

    The official index (built from Tushare exchange='BJ') contains only BSE
    stocks (no NEEQ/老三板), so it is a clean seed. Returns a list of
    ``(ts_code, name)`` tuples. The official name is kept as a fallback for
    seeds no longer returned by EastMoney (e.g. delisted BSE stocks).
    """
    if not official_path.exists():
        return []
    try:
        old = json.loads(official_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    # row = [canonicalCode, displayCode, nameZh, ...]
    return [(row[0], row[2]) for row in old if row[0].endswith(".BJ")]


def write_index(output_path: Path, compressed):
    """Write the compressed index in the same layout as generate_index_from_csv."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("[\n")
        for i, item in enumerate(compressed):
            json.dump(item, f, ensure_ascii=False, separators=(",", ":"))
            if i < len(compressed) - 1:
                f.write(",\n")
            else:
                f.write("\n")
        f.write("]\n")


def compare_with_official(compressed):
    """Print a diff summary against the existing official index file."""
    official = (
        Path(__file__).parent.parent
        / "apps"
        / "dsa-web"
        / "public"
        / "stocks.index.json"
    )
    if not official.exists():
        print("      未找到正式文件，跳过对比。")
        return

    old = json.loads(official.read_text(encoding="utf-8"))
    old_codes = {row[0] for row in old}
    new_codes = {row[0] for row in compressed}

    print(f"      正式文件条数：{len(old)}；新生成条数：{len(compressed)}")
    print(f"      新增代码数（新有旧无）：{len(new_codes - old_codes)}")
    print(f"      缺失代码数（旧有新无）：{len(old_codes - new_codes)}")

    st_cnt = sum(1 for r in compressed if r[2].startswith(("*ST", "ST")))
    xd_cnt = sum(1 for r in compressed if r[2].startswith(("XD", "XR", "DR")))
    print(f"      *ST/ST 风险前缀残留数：{st_cnt}（保留为正常）")
    print(f"      XD/XR/DR 短期前缀残留数：{xd_cnt}（应为 0）")

    added = sorted(new_codes - old_codes)[:10]
    if added:
        print(f"      新增样例：{added}")


def main():
    parser = argparse.ArgumentParser(
        description="从东方财富免费接口构建股票自动补全索引（与 Tushare 产物结构一致）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入正式 public/stocks.index.json 并同步 static/（默认仅验证）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅取前 N 只用于快速冒烟测试（默认 0 = 全量）",
    )
    parser.add_argument(
        "--output",
        dest="output",
        default=None,
        help="指定验证产物文件名/路径（默认: public/stocks.index.eastmoney.verify.json）",
    )
    args = parser.parse_args()

    if not gen.require_pypinyin():
        return 1

    print("=" * 60)
    print("股票索引生成工具（东方财富源，无 token）")
    print("=" * 60)

    print("\n[1/5] 选择可达的东方财富 host...")
    session = build_session()
    host = pick_host(session)
    if not host:
        print("\n[!] 所有东方财富 host 均不可达（连接被远端重置）。")
        print("    可能当前网络环境屏蔽了 EastMoney push2 API。建议：")
        print("      1) 更换网络 / 开启代理或 VPN 后重试；")
        print("      2) 或改用官方 Tushare 源（需 TUSHARE_TOKEN 且积分>=2000）。")
        print("    诊断参考：百度/GitHub 可正常访问，唯独 EastMoney 被 RST。")
        return 2

    print(f"      使用 host: {host}")

    print("\n[2/5] 拉取东方财富 A 股列表...")
    try:
        total, stocks = fetch_all_a_shares(session, host)
    except Exception as e:
        print(f"\n[!] 拉取中断：{type(e).__name__}。可能触发了东方财富限流，请稍后重试或换网络。")
        return 2
    if not stocks:
        print("\n[!] 未拉取到任何股票数据，退出。")
        return 2
    print(f"      接口报告 total={total}，本地解析 {len(stocks)} 只")

    # 已收录代码的去重集合（含 A 股），北交所合并时复用
    seen = {s["ts_code"] for s in stocks}

    # [2.5/5] 北交所：以官方 .BJ 为种子，名字从东方财富新三板板块刷新
    # 不能直接用 m:0+t:90（北交所独占板块在延时节点返回 0），故改用 m:0+t:81
    # 新三板板块拉名称池，再按 .BJ 种子代码匹配，过滤出纯北交所。
    official = (
        Path(__file__).parent.parent / "apps" / "dsa-web" / "public" / "stocks.index.json"
    )
    bse_seed = collect_bse_seed_codes(official)
    if bse_seed:
        print("\n[2.5/5] 拉取北交所名称（东方财富新三板板块，按种子代码匹配）...")
        bse_map = fetch_em_name_map(session, host, EM_FS_BSE)
        print(f"      新三板/北交所名称池：{len(bse_map)} 条；北交所种子：{len(bse_seed)} 只")
        bse_added = 0
        bse_fallback = 0
        for ts_code, official_name in bse_seed:
            symbol = ts_code.split(".")[0]
            # 优先用东方财富实时名称，匹配不到则保留官方名称（如已退市）
            raw_name = bse_map.get(symbol) or official_name
            if not raw_name:
                continue
            market = gen.determine_market(ts_code)
            name = gen.normalize_stock_name_for_index(raw_name, market)
            if not name:
                continue
            if ts_code in seen:
                continue
            seen.add(ts_code)
            stocks.append({
                "ts_code": ts_code,
                "symbol": symbol,
                "name": name,
                "market": market,
                "aliases": [],
            })
            bse_added += 1
            if symbol not in bse_map:
                bse_fallback += 1
        print(f"      成功加入北交所 {bse_added} 只（其中 {bse_fallback} 只使用官方名称兜底）")
    else:
        print("\n[!] 未找到官方 .BJ 种子（stocks.index.json 缺失或为空），跳过北交所。")
        print("    如需北交所，请保留现有官方索引文件作为代码种子。")

    if args.limit:
        stocks = stocks[: args.limit]
        print(f"      --limit 生效，仅用前 {len(stocks)} 只验证")

    print("\n[3/5] 生成索引（拼音/别名/去前缀复用 generate_index_from_csv）...")
    index = gen.build_stock_index(stocks)

    print("\n[4/5] 压缩索引...")
    compressed = gen.compress_index(index)

    public_dir = (
        Path(__file__).parent.parent / "apps" / "dsa-web" / "public"
    )
    if args.output:
        verify_path = Path(args.output)
        if not verify_path.is_absolute():
            verify_path = public_dir / verify_path
    else:
        verify_path = public_dir / "stocks.index.eastmoney.verify.json"
    print(f"\n[5/5] 写出验证文件：{verify_path}")
    write_index(verify_path, compressed)
    compare_with_official(compressed)

    print("\n验证完成。")
    if not args.apply:
        print("      未加 --apply，未写入正式文件。确认无误后加 --apply 写入。")
        return 0

    official = public_dir / "stocks.index.json"
    print(f"\n[apply] 写入正式索引：{official}")
    write_index(official, compressed)

    static_path = (
        Path(__file__).parent.parent / "apps" / "dsa-web" / "static" / "stocks.index.json"
    )
    if static_path.parent.exists():
        write_index(static_path, compressed)
        print(f"       同步 static：{static_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
