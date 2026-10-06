param(
    [string]$Input = "C:\LocalBackup\Dell\Projects\WikipediaPolcyChangeResearch\wiki-ai-collateral-auditor\wiki-ai-collateral-auditor\data\processed\cohort_manifest.parquet",
    [string]$Output,
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

if (-not (Test-Path $Input)) {
    throw "Input file not found: '$Input'"
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    $inputFile = Get-Item $Input
    $Output = Join-Path $inputFile.DirectoryName ($inputFile.BaseName + ".csv")
}

Push-Location $repoRoot
try {
    & $PythonExe (Join-Path $scriptDir "parquet_to_csv.py") $Input $Output
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
