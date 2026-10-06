param(
    [int]$IntervalSeconds = 300,
    [switch]$DryRun,
    [int]$Limit,
    [string]$Output,
    [string]$Config = "config/settings.yaml"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
Push-Location $repoRoot

try {
    while ($true) {
        $args = @("-File", (Join-Path $scriptDir "run_noticeboard_batch.ps1"))
        if ($DryRun) { $args += "-DryRun" }
        if ($PSBoundParameters.ContainsKey("Limit")) { $args += @("-Limit", $Limit) }
        if (-not [string]::IsNullOrWhiteSpace($Output)) { $args += @("-Output", $Output) }
        if (-not [string]::IsNullOrWhiteSpace($Config)) { $args += @("-Config", $Config) }

        & powershell @args
        Start-Sleep -Seconds $IntervalSeconds
    }
}
finally {
    Pop-Location
}
