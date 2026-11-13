<#
Deploy the public NextNode API to Google Cloud Run (scale to zero, one instance at most).

    .\deploy\cloudrun\deploy.ps1 -Project <gcp-project-id> -SetSecrets   # first time: asks for the 4 secrets
    .\deploy\cloudrun\deploy.ps1 -Project <gcp-project-id>               # later: rebuild and redeploy

What it creates (see deploy/cloudrun/README.md for costs):
  - APIs: Cloud Run, Cloud Build, Artifact Registry, Secret Manager
  - a service account "nextnode-api" that can read only its 4 secrets
  - secrets neo4j-uri, neo4j-username, neo4j-password, gemini-api-key (values typed by you, never echoed)
  - the Cloud Run service "nextnode-api": PUBLIC_MODE=1, min 0 / max 1 instance, 512 MiB, 120 s timeout
Nothing here reads .env: you paste each secret value when asked.
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Region = "us-east1",
    [string]$Service = "nextnode-api",
    [string]$Origins = "https://gargi-09.github.io",
    [int]$GeminiRpd = 200,
    [switch]$SetSecrets
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Sa = "$Service@$Project.iam.gserviceaccount.com"
$Secrets = [ordered]@{ "NEO4J_URI" = "neo4j-uri"; "NEO4J_USERNAME" = "neo4j-username";
                       "NEO4J_PASSWORD" = "neo4j-password"; "GEMINI_API_KEY" = "gemini-api-key" }

function Run([string]$what, [scriptblock]$cmd) {
    Write-Host "`n>> $what" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "failed: $what" }
}

Write-Host "Project: $Project   Region: $Region   Service: $Service"
Write-Host "This enables APIs, builds an image with Cloud Build and deploys to Cloud Run."
Write-Host "Costs: free tiers cover a low-traffic demo; set a budget alert first (README)."
if ((Read-Host "Type yes to continue") -ne "yes") { Write-Host "Stopped."; exit 1 }

Run "Select the project" { gcloud config set project $Project --quiet }
Run "Enable the APIs" {
    gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
}

$saExists = gcloud iam service-accounts list --filter="email:$Sa" --format="value(email)"
if (-not $saExists) {
    Run "Create the service account" { gcloud iam service-accounts create $Service --display-name "NextNode public API" }
}

if ($SetSecrets) {
    foreach ($env in $Secrets.Keys) {
        $name = $Secrets[$env]
        $secure = Read-Host "Paste $env (input hidden)" -AsSecureString
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        $exists = gcloud secrets list --filter="name~/$name$" --format="value(name)"
        if (-not $exists) {
            Run "Create secret $name" { gcloud secrets create $name --replication-policy=automatic }
        }
        Run "Store a new version of $name" { $plain | gcloud secrets versions add $name --data-file=- }
        Remove-Variable plain
        Run "Let $Service read $name" {
            gcloud secrets add-iam-policy-binding $name --member="serviceAccount:$Sa" `
                --role=roles/secretmanager.secretAccessor --quiet | Out-Null
        }
    }
}

$secretArgs = ($Secrets.Keys | ForEach-Object { "$_=$($Secrets[$_]):latest" }) -join ","
Push-Location $Root
try {
    Run "Build and deploy $Service" {
        gcloud run deploy $Service --source . --region $Region --service-account $Sa `
            --allow-unauthenticated --min-instances 0 --max-instances 1 --concurrency 20 `
            --cpu 1 --memory 512Mi --timeout 120 `
            --set-env-vars "PUBLIC_MODE=1,PUBLIC_ORIGINS=$Origins,GEMINI_RPD=$GeminiRpd" `
            --set-secrets $secretArgs --quiet
    }
} finally { Pop-Location }

$url = gcloud run services describe $Service --region $Region --format="value(status.url)"
Write-Host "`nDeployed: $url" -ForegroundColor Green
Write-Host "Check:    curl $url/health   and   curl $url/health/graph"
