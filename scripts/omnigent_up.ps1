# Starts the local NextNode + Omnigent stack (Windows PowerShell 5.1+).
#
#   .\scripts\omnigent_up.ps1                      # start MCP server + Omnigent server
#   .\scripts\omnigent_up.ps1 -Run -UserId u1 -JobId j1 -ResumeFile cv.txt -JobFile jd.txt
#
# One-time setup first (see omnigent/README.md): install Omnigent and Ollama, pull the
# Reviewer's model, add the two gateway credentials with `omnigent setup`, and open the
# vLLM tunnel in another terminal: .\scripts\vllm_tunnel.ps1
# This script never reads or writes .env; NextNode loads its own settings itself.
param(
    [switch]$Run,
    [string]$UserId,
    [string]$JobId,
    [string]$ResumeFile,
    [string]$JobFile,
    [int]$McpPort = 8765,
    [int]$VllmPort = 8000,
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
try { Invoke-WebRequest "http://127.0.0.1:$VllmPort/health" -UseBasicParsing -TimeoutSec 3 | Out-Null }
catch { Fail "vLLM is not reachable on 127.0.0.1:$VllmPort. Open the tunnel first: .\scripts\vllm_tunnel.ps1" }
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
Write-Host "Starting Omnigent server with omnigent/server_config.yaml ..."
$omni = Start-Process -FilePath "omnigent" -WorkingDirectory $Root -PassThru -NoNewWindow `
    -ArgumentList "server", "--config", (Join-Path $Root "omnigent\server_config.yaml") `
    -RedirectStandardOutput (Join-Path $Logs "omnigent.out.log") -RedirectStandardError (Join-Path $Logs "omnigent.err.log")

Write-Host "MCP server PID $($mcp.Id), Omnigent server PID $($omni.Id). Logs: $Logs"
Write-Host "Stop both with: Stop-Process -Id $($mcp.Id),$($omni.Id)"

# ── 4. Optional: run the curriculum workflow once ──
if ($Run) {
    if (-not $UserId -or -not $JobId) { Fail "-Run needs -UserId and -JobId" }
    $task = "user_id: $UserId`njob_id: $JobId`n"
    if ($ResumeFile) { $task += "`nRESUME TEXT:`n" + (Get-Content -Raw $ResumeFile) }
    if ($JobFile)    { $task += "`nJOB DESCRIPTION TEXT:`n" + (Get-Content -Raw $JobFile) }
    omnigent run (Join-Path $Root "omnigent\nextnode_curriculum") -p $task
}
