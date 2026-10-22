# Create, pause, resume and delete the EKS GPU cluster for vLLM (Windows PowerShell 5.1+).
#
#   .\scripts\aws_gpu.ps1 status   what exists and what it costs right now
#   .\scripts\aws_gpu.ps1 up       create the cluster (first time, 15-20 min) or resume the GPU node
#   .\scripts\aws_gpu.ps1 pause    GPU node to 0: stops the ~$0.80/h GPU, the ~$0.10/h EKS fee continues
#   .\scripts\aws_gpu.ps1 down     delete the model cache volume and the whole cluster: $0/h
#
# After `up`, deploy vLLM and open the tunnel:  .\scripts\vllm_tunnel.ps1 -Deploy
# Uses your AWS CLI credentials (profile from AWS_PROFILE, e.g. `setx AWS_PROFILE nextnode`).
# Never reads or writes .env; the vLLM API key lives only in the cluster.
param(
    [Parameter(Position = 0)][ValidateSet("status", "up", "pause", "down")][string]$Action = "status",
    [switch]$Force,
    [string]$Region = "us-east-1",
    [string]$Cluster = "nextnode",
    [string]$NodeGroup = "gpu"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Root "deploy\aws\eksctl-cluster.yaml"
$Namespace = "nextnode-inference"
$GpuQuotaCode = "L-DB2E81BA"            # Running On-Demand G and VT instances (vCPUs)
$Rates = @{ gpu = 0.80; eks = 0.10 }    # approximate on-demand USD/hour in us-east-1

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host $msg -ForegroundColor Cyan }
# PowerShell 5.1 turns native stderr into terminating errors under "Stop": relax it for probes.
function Quiet([scriptblock]$cmd) { $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $cmd 2>$null } finally { $ErrorActionPreference = $old } }
function Check($what) { if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)." } }

foreach ($tool in "aws", "eksctl", "kubectl") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Fail "$tool is not on PATH." }
}
$who = Quiet { aws sts get-caller-identity --query Arn --output text }
if (-not $who) { Fail "AWS credentials not working. Run: aws sts get-caller-identity --profile nextnode" }
Write-Host "AWS identity: $who  (profile: $(if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { 'default' }))"

function Cluster-Exists { $s = Quiet { aws eks describe-cluster --name $Cluster --region $Region --query cluster.status --output text }; return [bool]$s }
function Node-Count {
    $n = Quiet { aws eks describe-nodegroup --cluster-name $Cluster --nodegroup-name $NodeGroup --region $Region --query nodegroup.scalingConfig.desiredSize --output text }
    if ($n) { [int]$n } else { 0 }
}
function Use-Cluster { Quiet { aws eks update-kubeconfig --name $Cluster --region $Region } | Out-Null }

function Wait-GpuNode {
    Info "Waiting for the GPU node to join and expose its GPU (a few minutes) ..."
    foreach ($i in 1..90) {
        $gpus = Quiet { kubectl get nodes -o "jsonpath={.items[*].status.allocatable.nvidia\.com/gpu}" }
        if ("$gpus".Trim() -match "^[1-9]") { Info "GPU node ready."; return }
        Start-Sleep -Seconds 10
    }
    Fail "No schedulable GPU after 15 min. Check: kubectl get nodes; kubectl -n kube-system get pods"
}

switch ($Action) {
    "status" {
        if (-not (Cluster-Exists)) { Write-Host "Cluster '$Cluster': not created.  Cost now: `$0/h"; break }
        Use-Cluster
        $nodes = Node-Count
        $cost = $Rates.eks + $nodes * $Rates.gpu
        Write-Host "Cluster '$Cluster': exists. GPU nodes: $nodes.  Cost now: ~`$$('{0:N2}' -f $cost)/h (~`$$('{0:N0}' -f ($cost * 24))/day)"
        Quiet { kubectl get nodes -o wide }
        Quiet { kubectl -n $Namespace get pods,pvc }
        if ($nodes -eq 0) { Write-Host "Paused. Run 'down' if you won't use it for a while (the EKS fee is ~`$2.40/day)." }
    }

    "up" {
        $quota = Quiet { aws service-quotas get-service-quota --service-code ec2 --quota-code $GpuQuotaCode --region $Region --query Quota.Value --output text }
        if (-not $quota -or [double]$quota -lt 4) { Fail "GPU quota in $Region is '$quota' vCPUs; g6.xlarge needs 4." }

        if (-not (Cluster-Exists)) {
            Info "Creating EKS cluster '$Cluster' with one g6.xlarge (15-20 min). Billing starts now: ~`$$($Rates.eks + $Rates.gpu)/h."
            eksctl create cluster -f $Config; Check "eksctl create cluster"
        } else {
            Info "Cluster exists; scaling GPU node group to 1 ..."
            eksctl scale nodegroup --cluster $Cluster --name $NodeGroup --nodes 1 --nodes-min 0 --nodes-max 1 --region $Region
            Check "eksctl scale nodegroup"
            Use-Cluster
        }
        kubectl apply -f (Join-Path $Root "deploy\aws\storageclass-gp3.yaml"); Check "StorageClass"
        Wait-GpuNode
        if (Quiet { kubectl -n $Namespace get deployment vllm -o name }) {
            kubectl -n $Namespace scale deployment/vllm --replicas=1 | Out-Null
            Info "vLLM resuming. Open the tunnel:  .\scripts\vllm_tunnel.ps1"
        } else {
            Info "Next (first time): .\scripts\vllm_tunnel.ps1 -Deploy"
        }
        Write-Host "When you're done: .\scripts\aws_gpu.ps1 pause  (or down)" -ForegroundColor Yellow
    }

    "pause" {
        if (-not (Cluster-Exists)) { Write-Host "No cluster: nothing is running."; break }
        Use-Cluster
        Quiet { kubectl -n $Namespace scale deployment/vllm --replicas=0 } | Out-Null
        eksctl scale nodegroup --cluster $Cluster --name $NodeGroup --nodes 0 --nodes-min 0 --nodes-max 1 --region $Region
        Check "eksctl scale nodegroup"
        Info "GPU node stopping. Still billed: EKS ~`$0.10/h + the 30 GiB model-cache volume (~`$0.08/day)."
        Write-Host "Resume with 'up'; delete everything with 'down'."
    }

    "down" {
        if (-not (Cluster-Exists)) { Write-Host "No cluster: nothing to delete."; break }
        if (-not $Force) {
            $answer = Read-Host "Delete cluster '$Cluster', its model cache and the vLLM API key? Type 'delete' to confirm"
            if ($answer -ne "delete") { Write-Host "Cancelled."; exit 0 }
        }
        Use-Cluster
        # Delete the namespace first so the PVC's EBS volume is deleted with it (reclaimPolicy Delete).
        Quiet { kubectl delete namespace $Namespace --wait=true --timeout=5m } | Out-Null
        foreach ($i in 1..30) {
            if (-not (Quiet { kubectl get pv -o name })) { break }
            Start-Sleep -Seconds 5
        }
        Info "Deleting cluster '$Cluster' (about 10 min) ..."
        eksctl delete cluster -f $Config --disable-nodegroup-eviction --wait; Check "eksctl delete cluster"

        $left = Quiet { aws ec2 describe-volumes --region $Region --filters "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$Namespace" --query "Volumes[].VolumeId" --output text }
        if ("$left".Trim()) { Write-Host "WARNING: leftover EBS volumes still billing: $left (delete them in the EC2 console)." -ForegroundColor Yellow }
        else { Info "Everything deleted. Cost now: `$0/h. (A new 'up' creates a new vLLM API key.)" }
    }
}
