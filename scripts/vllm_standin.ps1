# Local stand-in for the vLLM tunnel (Windows PowerShell 5.1+): the same model, Qwen3-4B-Instruct-2507,
# on Ollama, listening on 127.0.0.1:8000 under the name the agents ask for (qwen3-4b). The `vllm-gpu`
# gateway, scripts/omnigent_up.ps1 and scripts/vllm_smoke.py then work unchanged, on a laptop, for $0.
# Use it for the first real agent run before paying for a cloud GPU (docs/TDD-phase4-vllm.md section 5).
#
#   .\scripts\vllm_standin.ps1              # keep this terminal open; Ctrl+C stops the stand-in
#   .\scripts\vllm_standin.ps1 -Port 8001   # if the NextNode API already uses 8000
#
# Exact commands (the normal Ollama app must be running on localhost:11434):
#   ollama pull qwen3:4b-instruct-2507-q4_K_M
#   ollama cp qwen3:4b-instruct-2507-q4_K_M qwen3-4b
#   ollama pull llama3.1:8b                                  (the Reviewer, served by the normal Ollama)
#   OLLAMA_HOST=127.0.0.1:8000 OLLAMA_CONTEXT_LENGTH=16384 ollama serve
# Differences from vLLM: 4-bit weights, no API key (Ollama ignores it), no /health route.
param(
    [int]$Port = 8000,
    [string]$Model = "qwen3:4b-instruct-2507-q4_K_M",
    [string]$ServedName = "qwen3-4b",
    [string]$ReviewerModel = "llama3.1:8b",
    [int]$ContextLength = 16384        # same as vLLM's --max-model-len; Ollama's default is too small for the agents
)

$ErrorActionPreference = "Stop"
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Fail "ollama is not on PATH. Install it from https://ollama.com/download" }
try { $tags = (Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3).models.name }
catch { Fail "Ollama is not running on localhost:11434. Start the Ollama app first." }

foreach ($m in @($Model, $ReviewerModel)) {
    if ($tags -notcontains $m) { ollama pull $m; if ($LASTEXITCODE -ne 0) { Fail "ollama pull $m failed." } }
}
if ($tags -notcontains "${ServedName}:latest") {
    ollama cp $Model $ServedName
    if ($LASTEXITCODE -ne 0) { Fail "ollama cp failed." }
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Fail "Port $Port is in use (the NextNode API or the vLLM tunnel?). Use -Port 8001 and point the vllm-gpu gateway at it."
}

# A second Ollama server, loopback only. Env vars are set for the child process and restored after.
$saved = @{ OLLAMA_HOST = $env:OLLAMA_HOST; OLLAMA_CONTEXT_LENGTH = $env:OLLAMA_CONTEXT_LENGTH }
$env:OLLAMA_HOST = "127.0.0.1:$Port"
$env:OLLAMA_CONTEXT_LENGTH = "$ContextLength"
try { $srv = Start-Process -FilePath "ollama" -ArgumentList "serve" -PassThru -NoNewWindow }
finally { $env:OLLAMA_HOST = $saved.OLLAMA_HOST; $env:OLLAMA_CONTEXT_LENGTH = $saved.OLLAMA_CONTEXT_LENGTH }

try {
    $ready = $false
    foreach ($i in 1..30) {
        Start-Sleep -Milliseconds 500
        if ($srv.HasExited) { break }
        try { Invoke-WebRequest "http://127.0.0.1:$Port/v1/models" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready = $true; break } catch {}
    }
    if (-not $ready) { Fail "The stand-in did not start on 127.0.0.1:$Port." }
    Write-Host "Stand-in for vLLM at http://localhost:$Port/v1 (model $ServedName = $Model). Ctrl+C stops it." -ForegroundColor Green
    Write-Host "Next, in another terminal: venv\Scripts\python scripts\vllm_smoke.py --port $Port --mcp"
    Wait-Process -Id $srv.Id
} finally {
    if (-not $srv.HasExited) { Stop-Process -Id $srv.Id -Force }
}
