<#
.SYNOPSIS  Runs the automated checks on this server: API tests against a throw-away <DB_NAME>_test database, and the gateway tests.
#>
. (Join-Path $PSScriptRoot 'common.ps1')
$Root = $script:KzRoot
Push-Location (Join-Path $Root 'backend')
try { Invoke-Native (Join-Path $Root 'backend\.venv\Scripts\python.exe') @('-m', 'pytest', '-q') 'API tests' } finally { Pop-Location }
$node = Find-Exe 'node.exe' @("$env:ProgramFiles\nodejs\node.exe"); $npm = Join-Path (Split-Path $node -Parent) 'npm.cmd'
$env:Path = (Split-Path $node -Parent) + ';' + $env:Path
Push-Location (Join-Path $Root 'gateway')
try { Invoke-Native $npm @('test') 'gateway tests' } finally { Pop-Location }
Write-Ok 'all tests passed'
