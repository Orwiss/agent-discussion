param(
    [string]$ProjectId = "mas-503315",
    [string]$Region = "asia-northeast3",
    [string]$ServiceName = "mas-ideation",
    [string]$SupabaseUrl = $env:SUPABASE_URL,
    [string]$AdminSharedPassword = $env:ADMIN_SHARED_PASSWORD,
    [string]$GcloudPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $GcloudPath) {
    $gcloudCommand = Get-Command gcloud -ErrorAction SilentlyContinue
    if ($gcloudCommand) {
        $GcloudPath = $gcloudCommand.Source
    }
    else {
        $localGcloud = Join-Path $env:LOCALAPPDATA "GoogleCloudCLI\google-cloud-sdk\bin\gcloud.cmd"
        if (Test-Path -LiteralPath $localGcloud) {
            $GcloudPath = $localGcloud
        }
    }
}
if (-not $GcloudPath -or -not (Test-Path -LiteralPath $GcloudPath)) {
    throw "gcloud CLI가 설치되어 있지 않습니다."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$surveyRoot = (Resolve-Path (Join-Path $repoRoot "..\agent-web-survey")).Path
$surveyDist = Join-Path $surveyRoot "dist"

Push-Location $surveyRoot
try {
    npm run build
}
finally {
    Pop-Location
}

$stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mas-cloudrun-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stageRoot | Out-Null

$excluded = @(
    ".git", ".env", ".claude", ".omc", ".omx", "__pycache__",
    "autogen_logs", "data", "literature", "logs", "outputs",
    "secrets", "survey-dist"
)

Get-ChildItem -LiteralPath $repoRoot -Force |
    Where-Object { $_.Name -notin $excluded } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $stageRoot -Recurse -Force
    }

$stagedSurvey = Join-Path $stageRoot "survey-dist"
Copy-Item -LiteralPath $surveyDist -Destination $stagedSurvey -Recurse -Force

# MAX_ACTIVE_SESSIONS/EXPERIMENT_MODEL은 비밀값이 아니라 항상 지정. SUPABASE_URL/
# ADMIN_SHARED_PASSWORD는 인수로 안 넘기면 생략 — --update-env-vars는 지정한 키만
# 갱신하고 나머지는 그대로 두므로, 생략 시 지금 라이브 리비전에 이미 설정된 값이
# 그대로 유지된다(따로 값을 안 갖고 있어도 매번 재배포 가능).
$envVarParts = @(
    "MAX_ACTIVE_SESSIONS=3",
    "EXPERIMENT_MODEL=google/gemini-3.1-flash-lite"
)
if ($SupabaseUrl) { $envVarParts += "SUPABASE_URL=$SupabaseUrl" }
if ($AdminSharedPassword) { $envVarParts += "ADMIN_SHARED_PASSWORD=$AdminSharedPassword" }
$envVarsString = $envVarParts -join ","

try {
    & $GcloudPath config set project $ProjectId
    & $GcloudPath run deploy $ServiceName `
        --source $stageRoot `
        --project $ProjectId `
        --region $Region `
        --allow-unauthenticated `
        --min 0 `
        --max 1 `
        --concurrency 20 `
        --cpu 1 `
        --memory 2Gi `
        --timeout 60 `
        --quiet `
        --update-env-vars $envVarsString `
        --set-secrets "OPENROUTER_API_KEY=openrouter-api-key:latest,SUPABASE_SERVICE_ROLE_KEY=supabase-service-role-key:latest,AUTH_COOKIE_SECRET=auth-cookie-secret:latest"
}
finally {
    $resolvedStage = (Resolve-Path -LiteralPath $stageRoot).Path
    $resolvedTemp = (Resolve-Path ([System.IO.Path]::GetTempPath())).Path
    if ($resolvedStage.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
