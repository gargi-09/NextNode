#!/usr/bin/env sh
# Starts the local NextNode + Omnigent stack (macOS / Linux).
#
#   scripts/omnigent_up.sh                                   # start both servers
#   scripts/omnigent_up.sh run u1 j1 cv.txt jd.txt           # ...and run the workflow once
#
# One-time setup first (see omnigent/README.md), and open the vLLM tunnel in another
# terminal: scripts/vllm_tunnel.sh. Never reads or writes .env.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/venv/bin/python"
LOGS="$ROOT/.omnigent-logs"
MCP_PORT="${MCP_PORT:-8765}"
VLLM_PORT="${VLLM_PORT:-8000}"
OMNIGENT_PORT="${OMNIGENT_PORT:-6767}"
LOCAL_MODEL="${LOCAL_MODEL:-llama3.1:8b}"
mkdir -p "$LOGS"

fail() { echo "ERROR: $1" >&2; exit 1; }

# 1. Preconditions
command -v omnigent >/dev/null 2>&1 || fail "omnigent not on PATH: uv tool install omnigent (Python 3.12+)"
[ -x "$PYTHON" ] || fail "NextNode venv not found at $PYTHON"
[ -f "$ROOT/app/mcp_server.py" ] || fail "app/mcp_server.py does not exist yet; build the NextNode MCP server first (TDD-phase3 section 3)"
# Any HTTP answer counts: vLLM returns 401 here without its key, the Ollama stand-in returns 200.
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$VLLM_PORT/v1/models")" != "000" ] \
  || fail "no model server on 127.0.0.1:$VLLM_PORT; open the tunnel (scripts/vllm_tunnel.sh) or the stand-in (scripts/vllm_standin.sh)"
# Key for the `vllm-gpu` provider (~/.omnigent/config.yaml: api_key_ref keychain:vllm-gpu).
# Omnigent's runners get a filtered environment, so the key goes into Omnigent's secret store
# (OS keychain), piped on stdin. Real vLLM: KEY_FROM_CLUSTER=1. Stand-in: a placeholder.
if curl -sf "http://127.0.0.1:$VLLM_PORT/health" >/dev/null && [ "${KEY_FROM_CLUSTER:-}" != "1" ]; then
  fail "that is the real vLLM: rerun with KEY_FROM_CLUSTER=1 so it gets its API key"
fi
OMNI_PYTHON="$(uv tool dir)/omnigent/bin/python"
[ -x "$OMNI_PYTHON" ] || fail "Omnigent's Python not found at $OMNI_PYTHON (uv tool install omnigent==0.19.1)"
STORE='import sys; from omnigent.onboarding import secrets; secrets.store_secret("vllm-gpu", sys.stdin.read().strip())'
if [ "${KEY_FROM_CLUSTER:-}" = "1" ]; then
  kubectl -n nextnode-inference get secret vllm-auth -o 'jsonpath={.data.api-key}' | base64 -d | "$OMNI_PYTHON" -c "$STORE" \
    || fail "could not copy the vllm-auth key into Omnigent's secret store"
else
  printf 'local-standin' | "$OMNI_PYTHON" -c "$STORE" || fail "could not store the vllm-gpu placeholder key"
fi
curl -sf http://localhost:11434/api/tags >/dev/null || fail "Ollama is not running; start it, then: ollama pull $LOCAL_MODEL"
curl -sf http://localhost:11434/api/tags | grep -q "\"$LOCAL_MODEL\"" || fail "Run: ollama pull $LOCAL_MODEL"

# Shared secret for the MCP server; lives only in this shell's environment.
: "${NEXTNODE_MCP_TOKEN:=$(head -c 24 /dev/urandom | base64)}"
export NEXTNODE_MCP_TOKEN

# 2. NextNode MCP server (stateless, loopback only)
echo "Starting NextNode MCP server on 127.0.0.1:$MCP_PORT ..."
(cd "$ROOT" && "$PYTHON" -m app.mcp_server --transport http --host 127.0.0.1 --port "$MCP_PORT" \
    >"$LOGS/mcp.out.log" 2>"$LOGS/mcp.err.log") &
MCP_PID=$!
i=0
until curl -sf "http://127.0.0.1:$MCP_PORT/health" >/dev/null; do
  i=$((i + 1)); [ "$i" -gt 20 ] && { kill "$MCP_PID"; fail "MCP server not healthy; see $LOGS/mcp.err.log"; }
  sleep 0.5
done

# 3. Omnigent server with the NextNode policy module importable
export PYTHONPATH="$ROOT/omnigent/policies${PYTHONPATH:+:$PYTHONPATH}"
OMNI_URL="http://127.0.0.1:$OMNIGENT_PORT"
if curl -sf "$OMNI_URL/health" >/dev/null; then
  kill "$MCP_PID"; fail "an Omnigent server already runs on port $OMNIGENT_PORT (maybe without NextNode's guardrails); stop it: omnigent stop"
fi
# Loopback bind = single-user mode, no login. --no-open: no browser tab.
echo "Starting Omnigent server with omnigent/server_config.yaml on $OMNI_URL ..."
(cd "$ROOT" && omnigent server --config "$ROOT/omnigent/server_config.yaml" --host 127.0.0.1 \
    --port "$OMNIGENT_PORT" --no-open >"$LOGS/omnigent.out.log" 2>"$LOGS/omnigent.err.log") &
OMNI_PID=$!
i=0
until curl -sf "$OMNI_URL/health" >/dev/null; do
  i=$((i + 1)); [ "$i" -gt 60 ] && { kill "$MCP_PID" "$OMNI_PID"; fail "Omnigent server not healthy; see $LOGS/omnigent.err.log"; }
  sleep 0.5
done
echo "MCP server PID $MCP_PID, Omnigent server PID $OMNI_PID. Logs: $LOGS"
echo "Stop both with: kill $MCP_PID $OMNI_PID"

# 4. Optional single run
if [ "${1:-}" = "run" ]; then
  [ $# -ge 3 ] || fail "usage: $0 run <user_id> <job_id> [resume.txt] [jd.txt]"
  TASK=$(printf 'user_id: %s\njob_id: %s\n' "$2" "$3")
  [ -n "${4:-}" ] && TASK="$TASK
RESUME TEXT:
$(cat "$4")"
  [ -n "${5:-}" ] && TASK="$TASK
JOB DESCRIPTION TEXT:
$(cat "$5")"
  # --server: use OUR server (server_config.yaml's guardrails); Omnigent 0.19+ otherwise spawns its own.
  omnigent run "$ROOT/omnigent/nextnode_curriculum" --server "$OMNI_URL" -p "$TASK"
fi
