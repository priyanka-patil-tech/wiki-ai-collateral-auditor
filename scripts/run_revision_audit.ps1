param(
    [switch]$All,
    [switch]$Poc,
    [string]$User = "Re000searchist",
    [int]$Limit = 20,
    [int]$BatchSize = 50,
    [int]$Concurrency = 5,
    [int]$TreatedLimit = 100,
    [int]$ControlLimit = 100,
    [string]$Manifest = "data/processed/cohort_manifest.parquet",
    [string]$DbPath = "data/processed/pr_auditor.duckdb",
    [string]$Checkpoint = "data/processed/poc_100_treated_100_control_checkpoint.json",
    [switch]$DebugLogs
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter not found at '$pythonExe'."
}

$manifestPath = if ([System.IO.Path]::IsPathRooted($Manifest)) { $Manifest } else { Join-Path $repoRoot $Manifest }
$dbPath = if ([System.IO.Path]::IsPathRooted($DbPath)) { $DbPath } else { Join-Path $repoRoot $DbPath }
$checkpointPath = if ([System.IO.Path]::IsPathRooted($Checkpoint)) { $Checkpoint } else { Join-Path $repoRoot $Checkpoint }

$arguments = @("-m", "src.extraction.minimal_pipeline")
if ($All) {
    $arguments += "--all"
}
elseif ($Poc) {
    $arguments += "--poc"
}
else {
    $arguments += $User
}

$arguments += @("--limit", $Limit)
$arguments += @("--batch-size", $BatchSize)
$arguments += @("--concurrency", $Concurrency)
$arguments += @("--manifest", $manifestPath)
$arguments += @("--db-path", $dbPath)
$arguments += @("--checkpoint", $checkpointPath)

if ($Poc) {
    $arguments += @("--treated-limit", $TreatedLimit)
    $arguments += @("--control-limit", $ControlLimit)
}

if ($DebugLogs) {
    $arguments += "--debug-logs"
}

Push-Location $repoRoot
try {
    & $pythonExe $arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
