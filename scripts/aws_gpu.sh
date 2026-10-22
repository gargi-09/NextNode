#!/usr/bin/env sh
# Create, pause, resume and delete the EKS GPU cluster for vLLM (macOS / Linux).
#
#   scripts/aws_gpu.sh status   what exists and what it costs right now
#   scripts/aws_gpu.sh up       create the cluster (first time, 15-20 min) or resume the GPU node
#   scripts/aws_gpu.sh pause    GPU node to 0: stops the ~$0.80/h GPU, the ~$0.10/h EKS fee continues
#   scripts/aws_gpu.sh down     delete the model cache volume and the whole cluster: $0/h
#
# After `up`: scripts/vllm_tunnel.sh deploy.  Uses your AWS CLI credentials (AWS_PROFILE).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CONFIG="$ROOT/deploy/aws/eksctl-cluster.yaml"
REGION="${REGION:-us-east-1}"
CLUSTER="${CLUSTER:-nextnode}"
NODEGROUP=gpu
NS=nextnode-inference
GPU_QUOTA_CODE=L-DB2E81BA
fail() { echo "ERROR: $1" >&2; exit 1; }

for t in aws eksctl kubectl; do command -v "$t" >/dev/null 2>&1 || fail "$t is not on PATH"; done
WHO=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null) || fail "AWS credentials not working"
echo "AWS identity: $WHO (profile: ${AWS_PROFILE:-default})"

cluster_exists() { aws eks describe-cluster --name "$CLUSTER" --region "$REGION" >/dev/null 2>&1; }
use_cluster() { aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" >/dev/null; }
node_count() {
  aws eks describe-nodegroup --cluster-name "$CLUSTER" --nodegroup-name "$NODEGROUP" --region "$REGION" \
    --query nodegroup.scalingConfig.desiredSize --output text 2>/dev/null || echo 0
}
scale_nodes() {
  eksctl scale nodegroup --cluster "$CLUSTER" --name "$NODEGROUP" --nodes "$1" --nodes-min 0 --nodes-max 1 --region "$REGION"
}
wait_gpu() {
  echo "Waiting for the GPU node to join and expose its GPU ..."
  i=0
  until kubectl get nodes -o 'jsonpath={.items[*].status.allocatable.nvidia\.com/gpu}' 2>/dev/null | grep -q '^[1-9]'; do
    i=$((i + 1)); [ "$i" -gt 90 ] && fail "no schedulable GPU after 15 min; check kubectl get nodes"
    sleep 10
  done
  echo "GPU node ready."
}

case "${1:-status}" in
  status)
    if ! cluster_exists; then echo "Cluster '$CLUSTER': not created. Cost now: \$0/h"; exit 0; fi
    use_cluster
    n=$(node_count)
    echo "Cluster '$CLUSTER': exists. GPU nodes: $n. Cost now: ~\$$(awk "BEGIN{printf \"%.2f\", 0.10 + $n * 0.80}")/h"
    kubectl get nodes -o wide 2>/dev/null || true
    kubectl -n "$NS" get pods,pvc 2>/dev/null || true
    [ "$n" = "0" ] && echo "Paused. Run 'down' if you won't use it for a while (EKS fee ~\$2.40/day)."
    ;;
  up)
    q=$(aws service-quotas get-service-quota --service-code ec2 --quota-code "$GPU_QUOTA_CODE" \
        --region "$REGION" --query Quota.Value --output text 2>/dev/null || echo 0)
    awk "BEGIN{exit !($q >= 4)}" || fail "GPU quota in $REGION is '$q' vCPUs; g6.xlarge needs 4"
    if ! cluster_exists; then
      echo "Creating EKS cluster '$CLUSTER' with one g6.xlarge (15-20 min). Billing starts now: ~\$0.90/h."
      eksctl create cluster -f "$CONFIG"
    else
      scale_nodes 1; use_cluster
    fi
    kubectl apply -f "$ROOT/deploy/aws/storageclass-gp3.yaml"
    wait_gpu
    if kubectl -n "$NS" get deployment vllm >/dev/null 2>&1; then
      kubectl -n "$NS" scale deployment/vllm --replicas=1 >/dev/null
      echo "vLLM resuming. Open the tunnel: scripts/vllm_tunnel.sh"
    else
      echo "Next (first time): scripts/vllm_tunnel.sh deploy"
    fi
    echo "When you're done: scripts/aws_gpu.sh pause (or down)"
    ;;
  pause)
    cluster_exists || { echo "No cluster: nothing is running."; exit 0; }
    use_cluster
    kubectl -n "$NS" scale deployment/vllm --replicas=0 >/dev/null 2>&1 || true
    scale_nodes 0
    echo "GPU node stopping. Still billed: EKS ~\$0.10/h + the 30 GiB model-cache volume."
    ;;
  down)
    cluster_exists || { echo "No cluster: nothing to delete."; exit 0; }
    if [ "${2:-}" != "--force" ]; then
      printf "Delete cluster '%s', its model cache and the vLLM API key? Type 'delete': " "$CLUSTER"
      read -r answer; [ "$answer" = "delete" ] || { echo "Cancelled."; exit 0; }
    fi
    use_cluster
    # namespace first, so the PVC's EBS volume is deleted with it (reclaimPolicy Delete)
    kubectl delete namespace "$NS" --wait=true --timeout=5m >/dev/null 2>&1 || true
    i=0; while [ -n "$(kubectl get pv -o name 2>/dev/null)" ] && [ "$i" -lt 30 ]; do i=$((i + 1)); sleep 5; done
    eksctl delete cluster -f "$CONFIG" --disable-nodegroup-eviction --wait
    left=$(aws ec2 describe-volumes --region "$REGION" \
      --filters "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$NS" --query "Volumes[].VolumeId" --output text)
    if [ -n "$left" ]; then echo "WARNING: leftover EBS volumes still billing: $left"; else echo "Everything deleted. Cost now: \$0/h."; fi
    ;;
  *) fail "usage: $0 status|up|pause|down [--force]" ;;
esac
