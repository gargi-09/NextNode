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
LOCAL_MODEL="${LOCAL_MODEL:-llama3.1:8b}"
mkdir -p "$LOGS"

fail() { echo "ERROR: $1" >&2; exit 1; }

# 1. Preconditions
command -v omnigent >/dev/null 2>&1 || fail "omnigent not on PATH: uv tool install omnigent (Python 3.12+)"
[ -x "$PYTHON" ] || fail "NextNode venv not found at $PYTHON"
[ -f "$ROOT/app/mcp_server.py" ] || fail "app/mcp_server.py does not exist yet; build the NextNode MCP server first (TDD-phase3 section 3)"
curl -sf "http://127.0.0.1:$VLLM_PORT/health" >/dev/null || fail "vLLM not reachable on 127.0.0.1:$VLLM_PORT; open the tunnel: scripts/vllm_tunnel.sh"
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
echo "Starting Omnigent server with omnigent/server_config.yaml ..."
(cd "$ROOT" && omnigent server --config "$ROOT/omnigent/server_config.yaml" \
    >"$LOGS/omnigent.out.log" 2>"$LOGS/omnigent.err.log") &
OMNI_PID=$!
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
  omnigent run "$ROOT/omnigent/nextnode_curriculum" -p "$TASK"
fi
