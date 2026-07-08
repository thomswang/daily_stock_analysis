#!/usr/bin/env bash
# =====================================================================
# 走势预测/分析系统 · 服务统一控制脚本
# ---------------------------------------------------------------------
# 用子命令统一管理本地 FastAPI 服务，底层复用 restart_server.sh。
#
# 用法：
#   ./server.sh start            # 启动（等同重启：先停旧再起新，含前端打包）
#   ./server.sh restart          # 重启（打包前端 + 重新拉起后端）
#   ./server.sh serve            # 只启动后端服务（跳过前端打包，每次都是重启）
#   ./server.sh stop             # 关闭服务
#   ./server.sh status           # 查看运行状态（只读）
#   ./server.sh logs             # 实时跟踪日志（Ctrl+C 退出）
#   ./server.sh restart --no-build   # 透传参数：跳过前端打包
#   ./server.sh restart --port 8000  # 透传参数：指定端口
#
# 说明：start/restart/serve/stop 的所有可选参数（--port/--host/--no-build/
#       --reinstall/--foreground 等）均会原样透传给 restart_server.sh。
#       serve 等同于 restart --no-build：先停旧服务再拉起新的，但不打包前端。
#
# 兼容 Windows(Git Bash) 与 Linux/macOS。
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RESTART_SH="$SCRIPT_DIR/restart_server.sh"
LOG_FILE="$SCRIPT_DIR/logs/server.local.log"

usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
}

cmd="${1:-}"
[[ $# -gt 0 ]] && shift || true

case "$cmd" in
  start|restart)
    exec bash "$RESTART_SH" "$@"
    ;;
  serve)
    # 只启动后端服务，跳过前端打包（每次都是重启：先停旧再起新）
    exec bash "$RESTART_SH" --no-build "$@"
    ;;
  stop)
    exec bash "$RESTART_SH" --stop "$@"
    ;;
  status)
    exec bash "$RESTART_SH" --status "$@"
    ;;
  logs)
    if [[ ! -f "$LOG_FILE" ]]; then
      echo "日志文件不存在：$LOG_FILE（服务可能尚未启动过）" >&2
      exit 1
    fi
    exec tail -n 200 -f "$LOG_FILE"
    ;;
  -h|--help|help|"")
    usage
    exit 0
    ;;
  *)
    echo "未知命令: $cmd" >&2
    echo "可用命令: start | serve | stop | restart | status | logs | help" >&2
    exit 2
    ;;
esac
