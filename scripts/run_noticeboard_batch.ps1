
param(
    [switch]$DryRun = $false,
    [int]$Limit,
    [int]$MaxRecords,
    [string]$Output,
    [string]$Config = "config/settings.yaml",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
}

if (-not (Test-Path $PythonExe)) {
    throw "Python interpreter not found at '$PythonExe'. Create a venv and install requirements first."
}

$args = @("-m", "src.ingestion.noticeboard_scraper")
if ($DryRun) { $args += "--dry-run" }
if ($PSBoundParameters.ContainsKey("Limit")) { $args += @("--limit", $Limit.ToString()) }
if ($PSBoundParameters.ContainsKey("MaxRecords")) { $args += @("--max-records", $MaxRecords.ToString()) }
if (-not [string]::IsNullOrWhiteSpace($Output)) { $args += @("--output", $Output) }
if (-not [string]::IsNullOrWhiteSpace($Config)) { $args += @("--config", $Config) }

Push-Location $repoRoot
try {
    & $PythonExe @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
