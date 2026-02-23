#!/bin/bash
# pdf2skill 前后端服务管理脚本
# 用法: ./dev.sh start | stop | restart

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$DIR/.pids"
mkdir -p "$PID_DIR"

BACKEND_PID="$PID_DIR/backend.pid"
FRONTEND_PID="$PID_DIR/frontend.pid"
BACKEND_LOG="$PID_DIR/backend.log"
FRONTEND_LOG="$PID_DIR/frontend.log"

start_backend() {
  if [ -f "$BACKEND_PID" ] && kill -0 "$(cat "$BACKEND_PID")" 2>/dev/null; then
    echo "⚠️  后端已在运行 (PID $(cat "$BACKEND_PID"))"
    return
  fi
  echo "🚀 启动后端 (uvicorn)..."
  cd "$DIR"
  PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"
  nohup "$PYTHON" -m uvicorn src.web_ui:app --host 0.0.0.0 --port 8000 --reload \
    > "$BACKEND_LOG" 2>&1 &
  echo $! > "$BACKEND_PID"
  echo "   PID: $(cat "$BACKEND_PID") | 日志: $BACKEND_LOG"
}

start_frontend() {
  if [ -f "$FRONTEND_PID" ] && kill -0 "$(cat "$FRONTEND_PID")" 2>/dev/null; then
    echo "⚠️  前端已在运行 (PID $(cat "$FRONTEND_PID"))"
    return
  fi
  echo "🚀 启动前端 (vite dev)..."
  cd "$DIR/frontend"
  nohup npm run dev -- --host > "$FRONTEND_LOG" 2>&1 &
  echo $! > "$FRONTEND_PID"
  echo "   PID: $(cat "$FRONTEND_PID") | 日志: $FRONTEND_LOG"
}

stop_service() {
  local name=$1 pidfile=$2
  if [ -f "$pidfile" ]; then
    local pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      echo "🛑 停止${name} (PID $pid)..."
      kill "$pid" 2>/dev/null
      # 等待进程退出
      for i in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      # 强制杀
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
  else
    echo "   ${name}未在运行"
  fi
}

do_start() {
  start_backend
  start_frontend
  echo ""
  echo "✅ 服务已启动"
  echo "   后端: http://localhost:8000"
  echo "   前端: http://localhost:4000"
}

do_stop() {
  stop_service "后端" "$BACKEND_PID"
  stop_service "前端" "$FRONTEND_PID"
  echo "✅ 服务已停止"
}

do_restart() {
  echo "♻️  重启服务..."
  do_stop
  sleep 1
  do_start
}

do_status() {
  echo "── 服务状态 ──"
  if [ -f "$BACKEND_PID" ] && kill -0 "$(cat "$BACKEND_PID")" 2>/dev/null; then
    echo "  后端: ✅ 运行中 (PID $(cat "$BACKEND_PID"))"
  else
    echo "  后端: ❌ 未运行"
  fi
  if [ -f "$FRONTEND_PID" ] && kill -0 "$(cat "$FRONTEND_PID")" 2>/dev/null; then
    echo "  前端: ✅ 运行中 (PID $(cat "$FRONTEND_PID"))"
  else
    echo "  前端: ❌ 未运行"
  fi
}

case "${1:-}" in
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_restart ;;
  status)  do_status ;;
  *)
    echo "用法: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
