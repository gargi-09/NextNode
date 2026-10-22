# Private tunnel from this machine to the vLLM server on the GPU cluster (Windows PowerShell 5.1+).
#
#   .\scripts\vllm_tunnel.ps1 -Deploy        # first time: apply manifests, create the API-key Secret, then tunnel
#   .\scripts\vllm_tunnel.ps1                # later: just open the tunnel (Ctrl+C closes it)
#   .\scripts\vllm_tunnel.ps1 -LocalPort 8001  # if the NextNode API already uses 8000
#   .\scripts\vllm_tunnel.ps1 -Deploy -Local # rehearsal on Docker Desktop Kubernetes: CPU image, tiny model
#
# Exact commands this script runs (namespace nextnode-inference):
#   kubectl apply -k deploy/k8s/vllm            (-Local: deploy/k8s/vllm-local)
#   kubectl -n nextnode-inference rollout status deployment/vllm --timeout=30m
#   kubectl -n nextnode-inference port-forward --address 127.0.0.1 svc/vllm 8000:8000
# --address 127.0.0.1 keeps the tunnel on loopback: nothing else on your network can reach it.
#
# The API key lives only in the cluster Secret `vllm-auth`. Copy it into Omnigent yourself
# (`omnigent setup`, gateway `vllm-gpu`, base URL http://localhost:<LocalPort>/v1):
#   kubectl -n nextnode-inference get secret vllm-auth -o jsonpath="{.data.api-key}"   (base64; decode it)
param(
    [switch]$Deploy,
    [switch]$Local,
    [int]$LocalPort = 8000,
    [string]$Namespace = "nextnode-inference",
    [string]$Context
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
# PowerShell 5.1 turns native stderr into terminating errors under "Stop": relax it for probes.
function Quiet([scriptblock]$cmd) { $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $cmd 2>$null } finally { $ErrorActionPreference = $old } }

if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) { Fail "kubectl is not on PATH." }
$kc = @(); if ($Context) { $kc = @("--context", $Context) }
$current = if ($Context) { $Context } else { Quiet { kubectl config current-context } }
if (-not $current) { Fail "No kubectl context. Create the cluster first: .\scripts\aws_gpu.ps1 up" }
Write-Host "Cluster context: $current"
# -Local only on a laptop cluster, and the GPU manifests never there (the pod would stay Pending).
$isLocal = $current -match '^(docker-desktop|kind-.+|minikube|rancher-desktop|orbstack)$'
if ($Deploy -and $Local -and -not $isLocal) { Fail "-Local is for a laptop cluster, but the context is '$current'. Switch: kubectl config use-context docker-desktop" }
if ($Deploy -and -not $Local -and $isLocal) { Fail "'$current' has no GPU. Rehearse with -Deploy -Local, or switch to the cloud cluster's context." }
$manifests = if ($Local) { "deploy\k8s\vllm-local" } else { "deploy\k8s\vllm" }

if ($Deploy) {
    kubectl @kc apply -k (Join-Path $Root $manifests)
    if ($LASTEXITCODE -ne 0) { Fail "kubectl apply failed." }

    Quiet { kubectl @kc -n $Namespace get secret vllm-auth } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # Random key, piped to kubectl on stdin: never on the command line, on disk or printed.
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $key = [Convert]::ToBase64String($bytes)
        $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($key))
        $manifest = '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"vllm-auth","namespace":"' + $Namespace +
                    '"},"type":"Opaque","data":{"api-key":"' + $b64 + '"}}'
        $manifest | kubectl @kc apply -f - | Out-Null
        Remove-Variable key, b64, manifest
        if ($LASTEXITCODE -ne 0) { Fail "Could not create the vllm-auth Secret." }
        Write-Host "Created Secret vllm-auth. Add its key to Omnigent (see the header of this script)."
        kubectl @kc -n $Namespace rollout restart deployment/vllm | Out-Null   # pod was waiting for the Secret
    }

    if ($Local) { Write-Host "Waiting for vLLM (first start pulls a multi-GB CPU image plus a 1.5 GB model) ..." }
    else { Write-Host "Waiting for vLLM to load the model (first start downloads ~8 GB) ..." }
    kubectl @kc -n $Namespace rollout status deployment/vllm --timeout=30m
    if ($LASTEXITCODE -ne 0) { Fail "vLLM is not ready. Check: kubectl -n $Namespace logs deploy/vllm" }
}

if (Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue) {
    Fail "Port $LocalPort is in use (the NextNode API defaults to 8000). Use -LocalPort 8001 and point the vllm-gpu gateway at it."
}

Write-Host "Opening tunnel 127.0.0.1:$LocalPort -> svc/vllm:8000 ..."
$pf = Start-Process -FilePath "kubectl" -PassThru -NoNewWindow `
    -ArgumentList ($kc + @("-n", $Namespace, "port-forward", "--address", "127.0.0.1", "svc/vllm", "${LocalPort}:8000"))
try {
    $ready = $false
    foreach ($i in 1..30) {
        Start-Sleep -Milliseconds 500
        if ($pf.HasExited) { break }
        try { Invoke-WebRequest "http://127.0.0.1:$LocalPort/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready = $true; break } catch {}
    }
    if (-not $ready) { Fail "Tunnel did not come up. Is the pod ready? kubectl -n $Namespace get pods" }
    Write-Host "vLLM reachable at http://localhost:$LocalPort/v1 (model qwen3-4b). Ctrl+C closes the tunnel." -ForegroundColor Green
    Write-Host "Next, in another terminal: venv\Scripts\python scripts\vllm_smoke.py --port $LocalPort --key-from-cluster"
    Wait-Process -Id $pf.Id
} finally {
    if (-not $pf.HasExited) { Stop-Process -Id $pf.Id -Force }
}
