#!/usr/bin/env bash
# 启动 PowerContext Server（Basic Memory 模式，不需要模型 API）。
# 用法：./scripts/serve_powercontext.sh
# 可用 MMCTX_PY 指定解释器，PC_PORT 指定端口，POWERCONTEXT_HOME 指定数据目录。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${MMCTX_PY:-}" ]; then
  PY="$MMCTX_PY"
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

# 默认放在用户数据目录（Linux / Docker 通用）；WSL 下建议显式
# export POWERCONTEXT_HOME=/mnt/wsl/pc-home，避免把 sqlite 写进 /mnt/c。
export POWERCONTEXT_HOME="${POWERCONTEXT_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/powercontext}"
mkdir -p "$POWERCONTEXT_HOME"
export POWERCONTEXT_SERVER_HTTP_HOST="${POWERCONTEXT_SERVER_HTTP_HOST:-127.0.0.1}"
export POWERCONTEXT_SERVER_HTTP_PORT="${PC_PORT:-8000}"
export POWERCONTEXT_SERVER_DATABASE_KIND="${POWERCONTEXT_SERVER_DATABASE_KIND:-sqlite}"
export POWERCONTEXT_SERVER_MCP_ENABLED=false
export POWERCONTEXT_SERVER_ACCESS_MODE=disabled

echo "[serve] PY=$PY HOME=$POWERCONTEXT_HOME PORT=$POWERCONTEXT_SERVER_HTTP_PORT"
exec "$PY" -u -c "import sys; from powercontext.server.cli import app; sys.argv=['powercontext-server','run']; app()"
