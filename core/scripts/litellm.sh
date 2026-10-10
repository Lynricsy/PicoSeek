#!/usr/bin/env bash
# 由 ./PicoSeek start 调用。LiteLLM 代理，4000 端口（Codex / ask.py 连这里）。
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="${PICOSEEK_ENV:-$(cd "$ROOT/.." && pwd)/.venv}"
[ -x "$ENV/bin/litellm" ] || { echo "找不到 litellm: $ENV/bin/litellm（pip install 'litellm[proxy]'，或设 PICOSEEK_ENV）"; exit 1; }
export PATH="$ENV/bin:$PATH"
mkdir -p "$ROOT/logs"
cd "$ROOT"
echo "Starting LiteLLM proxy -> http://127.0.0.1:4000  (log: $ROOT/logs/litellm.log)"
exec "$ENV/bin/litellm" --config "$ROOT/litellm_config.yaml" --port 4000 --host 127.0.0.1 2>&1 | tee -a "$ROOT/logs/litellm.log"
