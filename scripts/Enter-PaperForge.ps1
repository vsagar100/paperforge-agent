# Dot-source this script: . .\scripts\Enter-PaperForge.ps1 -Project projects/thermal
# Keys stay in local .env files; this script never prints their values.
[CmdletBinding()]
param(
    [string]$Project,
    [switch]$ReloadEnv
)

$pfRoot = Split-Path -Parent $PSScriptRoot
$pfProjectPath = ""
if ($Project) {
    $pfCandidate = if ([System.IO.Path]::IsPathRooted($Project)) { $Project } else { Join-Path $pfRoot $Project }
    if (-not (Test-Path -LiteralPath $pfCandidate -PathType Container)) {
        throw "Project directory does not exist. Initialize it first, or omit -Project."
    }
    $pfProjectPath = (Resolve-Path -LiteralPath $pfCandidate).Path
}
$pfActivation = Join-Path $pfRoot ".venv\Scripts\Activate.ps1"
$pfPython = Join-Path $pfRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pfActivation) -or -not (Test-Path -LiteralPath $pfPython)) {
    throw "Create .venv with python -m venv .venv and install PaperForge before using this helper."
}
. $pfActivation
$pfRaw = & $pfPython -c 'import json,sys; from pathlib import Path; from paperforge.environment import file_values; print(json.dumps(file_values(Path(sys.argv[2]) if len(sys.argv)>2 and sys.argv[2] else None, Path(sys.argv[1]))))' $pfRoot $pfProjectPath
if ($LASTEXITCODE -ne 0) {
    throw "Environment loading failed. Install the current package with python -m pip install -e ."
}
$pfValues = $pfRaw | ConvertFrom-Json
$pfLoadedCount = 0
foreach ($pfEntry in $pfValues.PSObject.Properties) {
    $pfCurrent = [Environment]::GetEnvironmentVariable($pfEntry.Name, "Process")
    if ($ReloadEnv -or [string]::IsNullOrEmpty($pfCurrent)) {
        [Environment]::SetEnvironmentVariable($pfEntry.Name, [string]$pfEntry.Value, "Process")
        $pfLoadedCount++
    }
}
$pfRaw = $null
$pfValues = $null
Write-Host "PaperForge activated; $pfLoadedCount saved environment values loaded. Values are not displayed."
Write-Host "Keys: process environment > project .env > repository .env. Use -ReloadEnv after editing saved keys."
