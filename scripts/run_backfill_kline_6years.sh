#!/usr/bin/env bash
# 一键打开 6 个终端/标签页，并行 kline 回填 2021–2026
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WESTOCK_DATA_DIR="${WESTOCK_DATA_DIR:-e:/analysis/westock-data}"
SLEEP="${SLEEP:-0.05}"
RETRY="${RETRY:-2}"
ADJ="${ADJ:-qfq}"
END_2026="${END_2026:-2026-07-03}"

YEARS=(
  "2021|2021-01-01|2021-12-31"
  "2022|2022-01-01|2022-12-31"
  "2023|2023-01-01|2023-12-31"
  "2024|2024-01-01|2024-12-31"
  "2025|2025-01-01|2025-12-31"
  "2026|2026-01-01|${END_2026}"
)

launch() {
  local year=$1 start=$2 end=$3
  local inner
  inner=$(cat <<EOF
cd '${ROOT}' || exit 1
export WESTOCK_DATA_DIR='${WESTOCK_DATA_DIR}'
export WESTOCK_KLINE_SLEEP='${SLEEP}'
python backfill_kline.py --all --mode range --start ${start} --end ${end} --progress data/kline_progress_${year}.json --sleep ${SLEEP} --retry ${RETRY} --adj ${ADJ}
echo
echo '[kline ${year}] 结束，exit='\$?
read -p '按 Enter 关闭...'
EOF
)

  if command -v wt.exe >/dev/null 2>&1; then
    wt -w 0 new-tab --title "kline ${year}" bash -lc "${inner}"
  elif [[ -n "${MSYSTEM:-}" ]] || [[ "$(uname -s 2>/dev/null)" == MINGW* ]]; then
    start "kline ${year}" bash -lc "${inner}"
  else
    echo "非 Windows 环境，后台启动 kline ${year}..."
    bash -lc "${inner}" &
  fi
}

echo "项目目录: ${ROOT}"
echo "WESTOCK_DATA_DIR=${WESTOCK_DATA_DIR}  sleep=${SLEEP}  retry=${RETRY}  adj=${ADJ}"
echo "启动 6 个 kline 回填进程..."

for entry in "${YEARS[@]}"; do
  IFS='|' read -r year start end <<< "$entry"
  launch "$year" "$start" "$end"
  sleep 0.3
done

echo "已发出 6 个窗口/标签页（kline 2021–2026）"
