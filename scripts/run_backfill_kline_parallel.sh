#!/usr/bin/env bash
# 并行 kline 回填：每个进程一个日期区间 + 独立 progress 台账
#
# 用法：
#   # 默认：6 进程 × 2 年（2015–2026，共 12 年）
#   bash scripts/run_backfill_kline_parallel.sh
#
#   # 仅 2021–2026：3 进程 × 2 年
#   SLICES="2021_2022|2021-01-01|2022-12-31
#2023_2024|2023-01-01|2024-12-31
#2025_2026|2025-01-01|2026-07-03" bash scripts/run_backfill_kline_parallel.sh
#
#   # 6 进程 × 1 年（与 run_backfill_kline_6years.sh 等价）
#   bash scripts/run_backfill_kline_6years.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WESTOCK_DATA_DIR="${WESTOCK_DATA_DIR:-e:/analysis/westock-data}"
SLEEP="${SLEEP:-0.05}"
RETRY="${RETRY:-2}"
ADJ="${ADJ:-qfq}"
END_2026="${END_2026:-2026-07-03}"

# 默认：6 个进程，每个 2 年（2015–2026）
DEFAULT_SLICES="$(cat <<EOF
2015_2016|2015-01-01|2016-12-31
2017_2018|2017-01-01|2018-12-31
2019_2020|2019-01-01|2020-12-31
2021_2022|2021-01-01|2022-12-31
2023_2024|2023-01-01|2024-12-31
2025_2026|2025-01-01|${END_2026}
EOF
)"

SLICES="${SLICES:-$DEFAULT_SLICES}"

launch() {
  local tag=$1 start=$2 end=$3
  local progress="data/kline_progress_${tag}.json"
  local inner
  inner=$(cat <<EOF
cd '${ROOT}' || exit 1
export WESTOCK_DATA_DIR='${WESTOCK_DATA_DIR}'
export WESTOCK_KLINE_SLEEP='${SLEEP}'
python backfill_kline.py --all --mode range --start ${start} --end ${end} --progress ${progress} --sleep ${SLEEP} --retry ${RETRY} --adj ${ADJ}
echo
echo '[kline ${tag}] 结束，exit='\$?
read -p '按 Enter 关闭...'
EOF
)

  if command -v wt.exe >/dev/null 2>&1; then
    wt -w 0 new-tab --title "kline ${tag}" bash -lc "${inner}"
  elif [[ -n "${MSYSTEM:-}" ]] || [[ "$(uname -s 2>/dev/null)" == MINGW* ]]; then
    start "kline ${tag}" bash -lc "${inner}"
  else
    echo "非 Windows 环境，后台启动 kline ${tag}..."
    bash -lc "${inner}" &
  fi
}

count=0
echo "项目目录: ${ROOT}"
echo "WESTOCK_DATA_DIR=${WESTOCK_DATA_DIR}  sleep=${SLEEP}  retry=${RETRY}  adj=${ADJ}"
echo "启动 kline 并行回填..."

while IFS= read -r entry; do
  [[ -z "${entry// /}" ]] && continue
  IFS='|' read -r tag start end <<< "$entry"
  launch "$tag" "$start" "$end"
  count=$((count + 1))
  sleep 0.3
done <<< "$SLICES"

echo "已发出 ${count} 个窗口/标签页"
