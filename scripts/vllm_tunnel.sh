#!/usr/bin/env sh
# Private tunnel from this machine to the vLLM server on the GPU cluster (macOS / Linux).
#
#   scripts/vllm_tunnel.sh deploy     # first time: apply manifests, create the API-key Secret, then tunnel
#   scripts/vllm_tunnel.sh            # later: just open the tunnel (Ctrl+C closes it)
#   LOCAL_PORT=8001 scripts/vllm_tunnel.sh   # if the NextNode API already uses 8000
#
# Exact commands (namespace nextnode-inference):
#   kubectl apply -k deploy/k8s/vllm
#   kubectl -n nextnode-inference rollout status deployment/vllm --timeout=20m
#   kubectl -n nextnode-inference port-forward --address 127.0.0.1 svc/vllm 8000:8000
#
# Copy the API key into Omnigent yourself (`omnigent setup`, gateway `vllm-gpu`,
# base URL http://localhost:<LOCAL_PORT>/v1):
#   kubectl -n nextnode-inference get secret vllm-auth -o jsonpath='{.data.api-key}' | base64 -d
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
NS="${NAMESPACE:-nextnode-inference}"
LOCAL_PORT="${LOCAL_PORT:-8000}"
fail() { echo "ERROR: $1" >&2; exit 1; }

command -v kubectl >/dev/null 2>&1 || fail "kubectl is not on PATH"
CTX=$(kubectl config current-context 2>/dev/null) || fail "no kubectl context; get cluster credentials first"
echo "Cluster context: $CTX"

if [ "${1:-}" = "deploy" ]; then
  kubectl apply -k "$ROOT/deploy/k8s/vllm"
  if ! kubectl -n "$NS" get secret vllm-auth >/dev/null 2>&1; then
    # Random key piped on stdin: never on the command line, on disk or printed.
    KEY_B64=$(head -c 32 /dev/urandom | base64 | tr -d '\n' | base64 | tr -d '\n')
    printf '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"vllm-auth","namespace":"%s"},"type":"Opaque","data":{"api-key":"%s"}}' \
      "$NS" "$KEY_B64" | kubectl apply -f - >/dev/null
    unset KEY_B64
    echo "Created Secret vllm-auth. Add its key to Omnigent (see the header of this script)."
    kubectl -n "$NS" rollout restart deployment/vllm >/dev/null
  fi
  echo "Waiting for vLLM to load the model (first start downloads ~8 GB) ..."
  kubectl -n "$NS" rollout status deployment/vllm --timeout=20m || fail "vLLM not ready: kubectl -n $NS logs deploy/vllm"
fi

if curl -s -o /dev/null "http://127.0.0.1:$LOCAL_PORT/" 2>/dev/null; then
  fail "port $LOCAL_PORT is in use (the NextNode API defaults to 8000); rerun with LOCAL_PORT=8001"
fi

echo "Opening tunnel 127.0.0.1:$LOCAL_PORT -> svc/vllm:8000 ..."
kubectl -n "$NS" port-forward --address 127.0.0.1 svc/vllm "$LOCAL_PORT:8000" &
PF=$!
trap 'kill $PF 2>/dev/null' EXIT INT TERM
i=0
until curl -sf "http://127.0.0.1:$LOCAL_PORT/health" >/dev/null; do
  i=$((i + 1)); [ "$i" -gt 30 ] && fail "tunnel did not come up; check: kubectl -n $NS get pods"
  sleep 0.5
done
echo "vLLM reachable at http://localhost:$LOCAL_PORT/v1 (model qwen3-4b). Ctrl+C closes the tunnel."
echo "Next, in another terminal: VLLM_PORT=$LOCAL_PORT scripts/omnigent_up.sh"
wait "$PF"
