# Starts the local NextNode + Omnigent stack (Windows PowerShell 5.1+).
#
#   .\scripts\omnigent_up.ps1                      # start MCP server + Omnigent server
#   .\scripts\omnigent_up.ps1 -Run -UserId u1 -JobId j1 -ResumeFile cv.txt -JobFile jd.txt
#   ... -KeyFromCluster                            # against the real vLLM tunnel (AWS)
#
# One-time setup first (see omnigent/README.md): install Omnigent and Ollama, pull the
# Reviewer's model, add the two providers to ~/.omnigent/config.yaml, and open the model
# server in another terminal: .\scripts\vllm_tunnel.ps1 (or .\scripts\vllm_standin.ps1)
# This script never reads or writes .env; NextNode loads its own settings itself.
param(
    [switch]$Run,
    [switch]$KeyFromCluster,      # real vLLM: read its API key from the vllm-auth Secret
    [string]$UserId,
    [string]$JobId,
    [string]$ResumeFile,
    [string]$JobFile,
    [int]$McpPort = 8765,
    [int]$VllmPort = 8000,
    [int]$OmnigentPort = 6767,
    [string]$LocalModel = "llama3.1:8b"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"
$Logs = Join-Path $Root ".omnigent-logs"
New-Item -ItemType Directory -Force $Logs | Out-Null

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Preconditions ──
if (-not (Get-Command omnigent -ErrorAction SilentlyContinue)) {
    Fail "omnigent is not on PATH. Install it: uv tool install omnigent  (needs Python 3.12+)"
}
if (-not (Test-Path $Python)) { Fail "NextNode venv not found at $Python" }
if (-not (Test-Path (Join-Path $Root "app\mcp_server.py"))) {
    Fail "app/mcp_server.py does not exist yet. Build the NextNode MCP server (TDD-phase3 section 3) first."
}
# Any HTTP answer counts: vLLM returns 401 here without its key, the Ollama stand-in returns 200.
$vllmUp = try { Invoke-WebRequest "http://127.0.0.1:$VllmPort/v1/models" -UseBasicParsing -TimeoutSec 3 | Out-Null; $true }
          catch { $null -ne $_.Exception.Response }
if (-not $vllmUp) { Fail "No model server on 127.0.0.1:$VllmPort. Open the tunnel (.\scripts\vllm_tunnel.ps1) or the local stand-in (.\scripts\vllm_standin.ps1)." }

# Key for the `vllm-gpu` provider (~/.omnigent/config.yaml: api_key_ref keychain:vllm-gpu).
# Omnigent's runners get a filtered environment, so the key goes into Omnigent's secret store
# (Windows Credential Manager), piped on stdin: never in argv, a file or the console.
# Real vLLM: the current key from the cluster Secret. Ollama stand-in: a placeholder it ignores.
$isVllm = try { (Invoke-WebRequest "http://127.0.0.1:$VllmPort/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false }
if ($isVllm -and -not $KeyFromCluster) { Fail "That is the real vLLM: rerun with -KeyFromCluster so it gets its API key." }
$OmniPython = Join-Path (uv tool dir) "omnigent\Scripts\python.exe"
if (-not (Test-Path $OmniPython)) { Fail "Omnigent's Python not found at $OmniPython (uv tool install omnigent==0.19.1)" }
if ($KeyFromCluster) {
    $b64 = kubectl -n nextnode-inference get secret vllm-auth -o "jsonpath={.data.api-key}"
    if ($LASTEXITCODE -ne 0 -or -not $b64) { Fail "Could not read Secret vllm-auth (wrong kubectl context, or not deployed)." }
    $key = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
} else { $key = "local-standin" }
$key | & $OmniPython -c "import sys; from omnigent.onboarding import secrets; secrets.store_secret('vllm-gpu', sys.stdin.read().strip())"
$stored = $LASTEXITCODE
Remove-Variable key; if ($b64) { Remove-Variable b64 }
if ($stored -ne 0) { Fail "Could not store the vllm-gpu key in Omnigent's secret store." }
try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null }
catch { Fail "Ollama is not running on localhost:11434. Start it, then: ollama pull $LocalModel" }
$tags = (Invoke-RestMethod "http://localhost:11434/api/tags").models.name
if ($tags -notcontains $LocalModel) { Fail "Local model $LocalModel missing. Run: ollama pull $LocalModel" }

# Shared secret between Omnigent and the MCP server; session-only, never written to disk.
if (-not $env:NEXTNODE_MCP_TOKEN) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $env:NEXTNODE_MCP_TOKEN = [Convert]::ToBase64String($bytes)
}

# ── 2. NextNode MCP server (stateless, loopback only) ──
Write-Host "Starting NextNode MCP server on 127.0.0.1:$McpPort ..."
$mcp = Start-Process -FilePath $Python -WorkingDirectory $Root -PassThru -NoNewWindow `
    -ArgumentList "-m", "app.mcp_server", "--transport", "http", "--host", "127.0.0.1", "--port", "$McpPort" `
    -RedirectStandardOutput (Join-Path $Logs "mcp.out.log") -RedirectStandardError (Join-Path $Logs "mcp.err.log")

$ready = $false
foreach ($i in 1..20) {
    Start-Sleep -Milliseconds 500
    try { Invoke-WebRequest "http://127.0.0.1:$McpPort/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready = $true; break } catch {}
}
if (-not $ready) { Stop-Process -Id $mcp.Id -Force; Fail "MCP server did not become healthy; see $Logs\mcp.err.log" }

# ── 3. Omnigent server with the NextNode policy module on PYTHONPATH ──
$env:PYTHONPATH = (Join-Path $Root "omnigent\policies") + [IO.Path]::PathSeparator + $env:PYTHONPATH
$OmniUrl = "http://127.0.0.1:$OmnigentPort"
try { Invoke-WebRequest "$OmniUrl/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
      Stop-Process -Id $mcp.Id -Force
      Fail "An Omnigent server is already running on port $OmnigentPort (without NextNode's guardrails, maybe). Stop it: omnigent stop" } catch {}
# Loopback bind = single-user mode, no login. --no-open: no browser tab.
Write-Host "Starting Omnigent server with omnigent/server_config.yaml on $OmniUrl ..."
$omni = Start-Process -FilePath "omnigent" -WorkingDirectory $Root -PassThru -NoNewWindow `
    -ArgumentList "server", "--config", (Join-Path $Root "omnigent\server_config.yaml"), "--host", "127.0.0.1", "--port", "$OmnigentPort", "--no-open" `
    -RedirectStandardOutput (Join-Path $Logs "omnigent.out.log") -RedirectStandardError (Join-Path $Logs "omnigent.err.log")
$ready = $false
foreach ($i in 1..60) {
    Start-Sleep -Milliseconds 500
    if ($omni.HasExited) { break }
    try { Invoke-WebRequest "$OmniUrl/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready = $true; break } catch {}
}
if (-not $ready) { Stop-Process -Id $mcp.Id -Force; Fail "Omnigent server did not become healthy; see $Logs\omnigent.err.log" }

Write-Host "MCP server PID $($mcp.Id), Omnigent server PID $($omni.Id). Logs: $Logs"
Write-Host "Stop both with: Stop-Process -Id $($mcp.Id),$($omni.Id)"

# ── 4. Optional: run the curriculum workflow once ──
if ($Run) {
    if (-not $UserId -or -not $JobId) { Fail "-Run needs -UserId and -JobId" }
    $task = "user_id: $UserId`njob_id: $JobId`n"
    if ($ResumeFile) { $task += "`nRESUME TEXT:`n" + (Get-Content -Raw $ResumeFile) }
    if ($JobFile)    { $task += "`nJOB DESCRIPTION TEXT:`n" + (Get-Content -Raw $JobFile) }
    # --server: use OUR server (with server_config.yaml's guardrails). Without it, Omnigent 0.19+
    # spawns its own background server that knows nothing about NextNode's policies.
    omnigent run (Join-Path $Root "omnigent\nextnode_curriculum") --server $OmniUrl -p $task
}
