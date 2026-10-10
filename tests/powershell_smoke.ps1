# Run on Windows with both Windows PowerShell 5.1 and PowerShell 7.
$ErrorActionPreference = "Stop"
$pfRepo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $pfRepo
if (Test-Path .\.env) { throw "Smoke test requires a clean checkout without a real .env" }
$pfProject = "projects\PowerShell paper with spaces"
& .\.venv\Scripts\python.exe -m paperforge init $pfProject --topic "PowerShell credential loading test"
if ($LASTEXITCODE -ne 0) { throw "Project initialization failed" }
$pfShared = Join-Path $pfRepo ".env"
$pfPrivate = Join-Path $pfRepo "$pfProject\.env"
$pfBom = New-Object System.Text.UTF8Encoding($true)
# Synthetic values only; never use live keys in this test.
[System.IO.File]::WriteAllText($pfShared, (@("PF_PS_TEST_KEY=shared", "PF_PS_SHARED_ONLY=preserved") -join [Environment]::NewLine), $pfBom)
[System.IO.File]::WriteAllText($pfPrivate, "PF_PS_TEST_KEY=project", $pfBom)
Remove-Item Env:PF_PS_TEST_KEY -ErrorAction SilentlyContinue
Remove-Item Env:PF_PS_SHARED_ONLY -ErrorAction SilentlyContinue
try {
    . .\scripts\Enter-PaperForge.ps1 -Project $pfProject
    if ($env:PF_PS_TEST_KEY -ne "project") { throw "Project dotenv precedence failed" }
    if ($env:PF_PS_SHARED_ONLY -ne "preserved") { throw "Shared dotenv fallback failed" }
    if (-not $env:VIRTUAL_ENV) { throw "Virtual environment activation failed" }
    $env:PF_PS_TEST_KEY = "manual"
    . .\scripts\Enter-PaperForge.ps1 -Project $pfProject
    if ($env:PF_PS_TEST_KEY -ne "manual") { throw "Process precedence failed" }
    [System.IO.File]::WriteAllText($pfPrivate, "PF_PS_TEST_KEY=rotated", $pfBom)
    . .\scripts\Enter-PaperForge.ps1 -Project $pfProject -ReloadEnv
    if ($env:PF_PS_TEST_KEY -ne "rotated") { throw "Explicit refresh failed" }
    paperforge doctor $pfProject
    if ($LASTEXITCODE -ne 0) { throw "Doctor failed" }
    Remove-Item Env:PF_PS_TEST_KEY -ErrorAction SilentlyContinue
    $env:PF_PS_PROJECT = $pfProject
    & .\.venv\Scripts\python.exe -c 'import os; from pathlib import Path; from paperforge.environment import load_environment; load_environment(Path(os.environ.get(''PF_PS_PROJECT''))); assert os.environ.get(''PF_PS_TEST_KEY'')==''rotated'''
    if ($LASTEXITCODE -ne 0) { throw "Direct Python dotenv loading failed" }
}
finally {
    Remove-Item -LiteralPath $pfShared -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $pfPrivate -ErrorAction SilentlyContinue
    Remove-Item Env:PF_PS_TEST_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:PF_PS_SHARED_ONLY -ErrorAction SilentlyContinue
    Remove-Item Env:PF_PS_PROJECT -ErrorAction SilentlyContinue
}
