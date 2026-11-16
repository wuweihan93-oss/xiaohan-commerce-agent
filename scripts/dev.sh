#!/usr/bin/env bash
# 同时启动前后端，退出终端时清理本脚本启动的进程。
set -Eeuo pipefail
project_dir=$(cd "$(dirname "$0")/.." && pwd)
cd "$project_dir"
for port in 8020 5175; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    printf '端口 %s 已被占用，请先停止对应服务，未修改任何现有项目。\n' "$port" >&2
    exit 1
  fi
done
(cd backend && uv sync --frozen --python 3.12)
python3 scripts/setup_env.py
(cd frontend && npm ci)
(cd backend && exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8020 --no-proxy-headers) &
backend_pid=$!
(cd frontend && exec node node_modules/vite/bin/vite.js --host 127.0.0.1) &
frontend_pid=$!
cleanup() { kill "$backend_pid" "$frontend_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
printf '\n拾光客服将启动于 http://127.0.0.1:5175 ，按 Ctrl+C 停止。\n'
wait "$backend_pid" "$frontend_pid"
