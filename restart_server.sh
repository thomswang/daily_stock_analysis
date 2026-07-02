#!/usr/bin/env bash
# =====================================================================
# 走势预测/分析系统 · 本地服务重启脚本
# ---------------------------------------------------------------------
# 功能：
#   1. 打包前端（apps/dsa-web -> 项目根 static/），可用 --no-build 跳过
#   2. 若服务已在运行（占用端口）则先优雅关闭
#   3. 后台重新拉起 FastAPI 服务（python main.py --serve-only）
#
# 用法：
#   ./restart_server.sh                 # 默认端口 8020，打包前端后重启
#   ./restart_server.sh --port 8000     # 指定端口
#   ./restart_server.sh --no-build      # 跳过前端打包，仅重启后端
#   ./restart_server.sh --reinstall     # 强制重装前端依赖后再打包
#   ./restart_server.sh --stop          # 只停止，不启动
#   ./restart_server.sh --status        # 只查看运行状态，不做任何改动
#   ./restart_server.sh --foreground    # 前台运行（Ctrl+C 退出）
#
# 依赖安装策略：默认按 package-lock.json 内容哈希判断，仅在依赖缺失或清单
# 变化时自动重装（npm ci）；未变化则跳过，保证重启快速。
#
# 兼容 Windows(Git Bash) 与 Linux/macOS。
# =====================================================================

set -euo pipefail

# ── 解析参数 ─────────────────────────────────────────────
PORT="${API_PORT:-8020}"
HOST="0.0.0.0"
DO_BUILD=1
STOP_ONLY=0
STATUS_ONLY=0
FOREGROUND=0
FORCE_REINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --no-build) DO_BUILD=0; shift ;;
    --reinstall) FORCE_REINSTALL=1; shift ;;
    --stop) STOP_ONLY=1; shift ;;
    --status) STATUS_ONLY=1; shift ;;
    --foreground|--fg) FOREGROUND=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# ── 定位项目根目录（脚本所在目录）─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE="$SCRIPT_DIR/.server.pid"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/server.local.log"
mkdir -p "$LOG_DIR"

IS_WINDOWS=0
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
esac

PYTHON_BIN="${PYTHON_BIN:-python}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── 找到监听指定端口的进程 PID ────────────────────────────
find_pid_by_port() {
  if [[ "$IS_WINDOWS" -eq 1 ]]; then
    # Windows: netstat -ano，最后一列是 PID
    netstat -ano 2>/dev/null \
      | grep -iE "LISTENING" \
      | grep -E "[:.]$PORT[[:space:]]" \
      | awk '{print $NF}' | sort -u | head -1
  else
    # Linux/macOS
    if command -v lsof >/dev/null 2>&1; then
      lsof -ti tcp:"$PORT" -s tcp:LISTEN 2>/dev/null | head -1
    else
      # 退回 ss/netstat
      { ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null; } \
        | grep -E "[:.]$PORT[[:space:]]" \
        | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
    fi
  fi
}

kill_pid() {
  local pid="$1"
  [[ -z "$pid" ]] && return 0
  log "关闭进程 PID=$pid ..."
  if [[ "$IS_WINDOWS" -eq 1 ]]; then
    MSYS_NO_PATHCONV=1 taskkill /F /T /PID "$pid" >/dev/null 2>&1 || true
  else
    kill "$pid" >/dev/null 2>&1 || true
    # 等待优雅退出，超时强杀
    for _ in 1 2 3 4 5; do
      kill -0 "$pid" >/dev/null 2>&1 || return 0
      sleep 0.5
    done
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
}

# ── 停止现有服务 ──────────────────────────────────────────
stop_service() {
  local stopped=0
  # 1) 通过 PID 文件
  if [[ -f "$PID_FILE" ]]; then
    local old_pid; old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]]; then
      kill_pid "$old_pid"; stopped=1
    fi
    rm -f "$PID_FILE"
  fi
  # 2) 通过端口兜底（防止 PID 文件丢失）
  local port_pid; port_pid="$(find_pid_by_port || true)"
  if [[ -n "$port_pid" ]]; then
    kill_pid "$port_pid"; stopped=1
  fi
  if [[ "$stopped" -eq 1 ]]; then
    log "已停止占用端口 $PORT 的旧服务。"
    sleep 1
  else
    log "未发现正在运行的服务（端口 $PORT 空闲）。"
  fi
}

# ── 查看服务状态（只读，不做任何改动）──────────────────────
status_service() {
  local pid_file_pid="" port_pid
  [[ -f "$PID_FILE" ]] && pid_file_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  port_pid="$(find_pid_by_port || true)"

  if [[ -n "$port_pid" ]]; then
    log "✅ 运行中：端口 $PORT 由 PID=$port_pid 监听（http://localhost:$PORT）"
    if [[ -n "$pid_file_pid" && "$pid_file_pid" != "$port_pid" ]]; then
      log "   注意：PID 文件记录=$pid_file_pid，与端口占用进程不一致"
    fi
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS "http://127.0.0.1:$PORT/api/v1/health" >/dev/null 2>&1 \
         || curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
        log "   健康检查：通过"
      else
        log "   健康检查：端口在监听但接口未响应（可能仍在启动）"
      fi
    fi
    return 0
  fi

  if [[ -n "$pid_file_pid" ]] && kill -0 "$pid_file_pid" >/dev/null 2>&1; then
    log "⚠️ 进程存活(PID=$pid_file_pid)，但未监听端口 $PORT。日志：$LOG_FILE"
    return 0
  fi

  log "⛔ 未运行（端口 $PORT 空闲）。"
  return 1
}

# ── 确保前端依赖已安装（依赖清单变化时自动重装）──────────────
_file_hash() {
  # 跨平台取文件内容哈希：优先 sha1sum，退回 git hash-object / cksum
  if command -v sha1sum >/dev/null 2>&1; then
    sha1sum "$1" 2>/dev/null | awk '{print $1}'
  elif command -v git >/dev/null 2>&1; then
    git hash-object "$1" 2>/dev/null
  else
    cksum "$1" 2>/dev/null | awk '{print $1}'
  fi
}

ensure_frontend_deps() {
  local web_dir="$SCRIPT_DIR/apps/dsa-web"
  local lock="$web_dir/package-lock.json"
  [[ -f "$lock" ]] || lock="$web_dir/package.json"
  local stamp="$web_dir/node_modules/.dsa-deps-hash"

  local want have reason
  want="$(_file_hash "$lock")"
  have="$(cat "$stamp" 2>/dev/null || true)"

  if [[ ! -d "$web_dir/node_modules" \
        || ! -e "$web_dir/node_modules/.bin/tsc" \
        || ! -e "$web_dir/node_modules/.bin/vite" ]]; then
    reason="依赖缺失"
  elif [[ "$FORCE_REINSTALL" -eq 1 ]]; then
    reason="--reinstall 强制重装"
  elif [[ -n "$want" && "$want" != "$have" ]]; then
    reason="依赖清单($(basename "$lock"))已变化"
  else
    return 0  # 依赖齐全且未变化 → 跳过，秒过
  fi

  log "前端依赖需要安装（$reason），开始安装（首次/更新较慢）..."
  if [[ -f "$web_dir/package-lock.json" ]]; then
    ( cd "$web_dir" && npm ci )
  else
    ( cd "$web_dir" && npm install )
  fi
  echo "$want" > "$stamp"   # 记录本次安装对应的依赖清单哈希
  log "前端依赖安装完成。"
}

# ── 打包前端 ──────────────────────────────────────────────
build_frontend() {
  ensure_frontend_deps
  log "开始打包前端（apps/dsa-web -> static/）..."
  ( cd "$SCRIPT_DIR/apps/dsa-web" && npm run build )
  log "前端打包完成。"
}

# ── 启动服务 ──────────────────────────────────────────────
start_service() {
  log "启动服务：$PYTHON_BIN main.py --serve-only --host $HOST --port $PORT"
  if [[ "$FOREGROUND" -eq 1 ]]; then
    exec "$PYTHON_BIN" main.py --serve-only --host "$HOST" --port "$PORT"
  fi
  nohup "$PYTHON_BIN" main.py --serve-only --host "$HOST" --port "$PORT" \
    >"$LOG_FILE" 2>&1 &
  local new_pid=$!
  echo "$new_pid" > "$PID_FILE"
  log "服务已在后台启动，PID=$new_pid，日志：$LOG_FILE"

  # 健康探测
  log "等待服务就绪 ..."
  for i in $(seq 1 20); do
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS "http://127.0.0.1:$PORT/api/v1/health" >/dev/null 2>&1 \
         || curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
        log "✅ 服务已就绪：http://localhost:$PORT"
        return 0
      fi
    fi
    # 进程若已退出则立刻报错
    if ! kill -0 "$new_pid" >/dev/null 2>&1; then
      log "❌ 服务进程已退出，请查看日志：$LOG_FILE"
      tail -n 30 "$LOG_FILE" || true
      return 1
    fi
    sleep 1
  done
  log "⚠️ 未在 20s 内探测到健康接口，但进程仍在运行。请查看日志：$LOG_FILE"
}

# ── 主流程 ────────────────────────────────────────────────
main() {
  if [[ "$STATUS_ONLY" -eq 1 ]]; then
    status_service || true
    exit 0
  fi
  stop_service
  if [[ "$STOP_ONLY" -eq 1 ]]; then
    log "已按 --stop 停止服务，退出。"
    exit 0
  fi
  if [[ "$DO_BUILD" -eq 1 ]]; then
    build_frontend
  else
    log "跳过前端打包（--no-build）。"
  fi
  start_service
}

main
