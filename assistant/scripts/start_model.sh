#!/usr/bin/env bash
# Start the local model server (llama.cpp) on 127.0.0.1:8080.
# One conversation slot, small context and 3 threads: the container has a 2-core
# CPU quota and a 3 GB memory cap, so bigger settings only add latency.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${OPERATOR_MODEL_FILE:-.models/qwen2.5-1.5b-instruct-q4_k_m.gguf}"
SERVER="${OPERATOR_LLAMA_SERVER:-.tools/llama-b12418/llama-server}"
PORT="${OPERATOR_MODEL_PORT:-8080}"
THREADS="${OPERATOR_MODEL_THREADS:-3}"

if [ ! -f "$MODEL" ]; then
  echo "model not found: $MODEL" >&2
  echo "download it first, see README / memory/handoff.md" >&2
  exit 1
fi
if [ ! -x "$SERVER" ]; then
  echo "llama-server not found: $SERVER" >&2
  exit 1
fi

exec "$SERVER" \
  -m "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  -c 4096 -t "$THREADS" --jinja -np 1 \
  -ctk q8_0 -ctv q8_0
