#!/usr/bin/env sh
# Local stand-in for the vLLM tunnel (macOS / Linux): the same model, Qwen3-4B-Instruct-2507, on Ollama,
# listening on 127.0.0.1:8000 under the name the agents ask for (qwen3-4b). The `vllm-gpu` gateway,
# scripts/omnigent_up.sh and scripts/vllm_smoke.py then work unchanged, on a laptop, for $0.
#
#   scripts/vllm_standin.sh              # keep this terminal open; Ctrl+C stops the stand-in
#   PORT=8001 scripts/vllm_standin.sh    # if the NextNode API already uses 8000
#
# Exact commands (the normal Ollama must be running on localhost:11434):
#   ollama pull qwen3:4b-instruct-2507-q4_K_M && ollama cp qwen3:4b-instruct-2507-q4_K_M qwen3-4b
#   ollama pull llama3.1:8b                                  (the Reviewer, served by the normal Ollama)
#   OLLAMA_HOST=127.0.0.1:8000 OLLAMA_CONTEXT_LENGTH=16384 ollama serve
# Differences from vLLM: 4-bit weights, no API key (Ollama ignores it), no /health route.
set -eu

PORT="${PORT:-8000}"
MODEL="${MODEL:-qwen3:4b-instruct-2507-q4_K_M}"
SERVED_NAME="${SERVED_NAME:-qwen3-4b}"
REVIEWER_MODEL="${REVIEWER_MODEL:-llama3.1:8b}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-16384}"   # same as vLLM's --max-model-len
fail() { echo "ERROR: $1" >&2; exit 1; }

command -v ollama >/dev/null 2>&1 || fail "ollama is not on PATH; install it from https://ollama.com/download"
TAGS=$(curl -sf http://localhost:11434/api/tags) || fail "Ollama is not running on localhost:11434; start it first"
for m in "$MODEL" "$REVIEWER_MODEL"; do
  echo "$TAGS" | grep -q "\"$m\"" || ollama pull "$m"
done
echo "$TAGS" | grep -q "\"$SERVED_NAME:latest\"" || ollama cp "$MODEL" "$SERVED_NAME"

if curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
  fail "port $PORT is in use (the NextNode API or the vLLM tunnel?); rerun with PORT=8001"
fi

# A second Ollama server, loopback only.
OLLAMA_HOST="127.0.0.1:$PORT" OLLAMA_CONTEXT_LENGTH="$CONTEXT_LENGTH" ollama serve &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT INT TERM
i=0
until curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; do
  i=$((i + 1)); [ "$i" -gt 30 ] && fail "the stand-in did not start on 127.0.0.1:$PORT"
  sleep 0.5
done
echo "Stand-in for vLLM at http://localhost:$PORT/v1 (model $SERVED_NAME = $MODEL). Ctrl+C stops it."
echo "Next, in another terminal: venv/bin/python scripts/vllm_smoke.py --port $PORT --mcp"
wait "$SRV"
