param(
    [string]$ParquetDir = "data/processed",
    [string]$DbPath = "data/processed/pr_auditor.duckdb",
    [int]$Limit = 500,
    [int]$BatchSize = 50,
    [int]$Concurrency = 5,
    [switch]$DebugLogs
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python interpreter not found at '$pythonExe'."
}

$parquetPath = if ([System.IO.Path]::IsPathRooted($ParquetDir)) { $ParquetDir } else { Join-Path $repoRoot $ParquetDir }
$dbPath = if ([System.IO.Path]::IsPathRooted($DbPath)) { $DbPath } else { Join-Path $repoRoot $DbPath }

if (-not (Test-Path $parquetPath -PathType Container)) {
    throw "Parquet directory not found: '$parquetPath'"
}

$arguments = @(
    "-m", "src.extraction.minimal_pipeline",
    "--all-parquet",
    "--parquet-dir", $parquetPath,
    "--db-path", $dbPath,
    "--limit", $Limit,
    "--batch-size", $BatchSize,
    "--concurrency", $Concurrency
)

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
