#!/usr/bin/env bash
# 2021–2026：3 进程 × 2 年（共 6 年，比 6×1 年少开窗口）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
END_2026="${END_2026:-2026-07-03}"

export SLICES="$(cat <<EOF
2021_2022|2021-01-01|2022-12-31
2023_2024|2023-01-01|2024-12-31
2025_2026|2025-01-01|${END_2026}
EOF
)"

exec bash "${SCRIPT_DIR}/run_backfill_kline_parallel.sh"
