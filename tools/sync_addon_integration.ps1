param(
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $ScriptDir
$SrcIntegration = Join-Path $RepoRoot "custom_components\can_gateway_v3"
$DestIntegration = Join-Path $RepoRoot "can_gateway\integration\can_gateway_v3"

if (-not (Test-Path -LiteralPath $SrcIntegration)) {
    throw "Brak integracji: $SrcIntegration"
}

New-Item -ItemType Directory -Force -Path $DestIntegration | Out-Null
robocopy $SrcIntegration $DestIntegration /MIR /XD __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed: $LASTEXITCODE"
}

if (-not $Quiet) {
    Write-Host "Zsynchronizowano integracje -> $DestIntegration" -ForegroundColor Green
}

exit 0
