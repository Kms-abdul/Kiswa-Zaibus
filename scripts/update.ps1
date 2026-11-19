<#
.SYNOPSIS  Applies a new release: copy the new package over the install folder (keep backend\.env, data, logs, secrets), then run this.
           Takes a backup, stops the services, installs packages, applies schema changes, rebuilds, and starts again.
#>
[CmdletBinding()]
param([switch]$SkipBackup)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Admin
$Root = $script:KzRoot
$envv = Read-EnvFile
if (-not $SkipBackup) { Write-Step 'Backup before update'; & (Join-Path $PSScriptRoot 'backup.ps1') -KeepDays 3650 }
Write-Step 'Stopping services'
Stop-Service KZ-Gateway, KZ-API -ErrorAction SilentlyContinue
try {
    $vpy = Join-Path $Root 'backend\.venv\Scripts\python.exe'
    Invoke-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', (Join-Path $Root 'backend\requirements.txt')) 'pip install'
    $rootFile = Join-Path $Root 'secrets\mysql-root.txt'
    if (Test-Path $rootFile) { $rootPw = (Get-Content $rootFile -Raw).Trim() }
    else { $s = Read-Host 'MySQL root password' -AsSecureString; $rootPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)) }
    $env:DB_USER = 'root'; $env:DB_PASSWORD = $rootPw
    try { Invoke-Native $vpy @((Join-Path $Root 'database\apply_schema.py')) 'schema' } finally { Remove-Item Env:\DB_USER, Env:\DB_PASSWORD }
    $node = Find-Exe 'node.exe' @("$env:ProgramFiles\nodejs\node.exe"); $npm = Join-Path (Split-Path $node -Parent) 'npm.cmd'
    $env:Path = (Split-Path $node -Parent) + ';' + $env:Path
    foreach ($d in 'gateway', 'frontend') {
        Push-Location (Join-Path $Root $d)
        try { Invoke-Native $npm @('ci', '--no-audit', '--no-fund', '--loglevel=error') "npm ci ($d)"; Invoke-Native $npm @('run', 'build') "build ($d)" } finally { Pop-Location }
    }
}
finally {
    Write-Step 'Starting services'
    Start-Service KZ-API, KZ-Gateway
}
$port = $(if ($envv['GATEWAY_PORT']) { $envv['GATEWAY_PORT'] } else { '8080' })
Start-Sleep -Seconds 4
try { $h = Invoke-RestMethod "http://127.0.0.1:$port/healthz" -TimeoutSec 10; Write-Ok "healthy, schema $($h.api.schema)" } catch { Write-Warn2 'not answering yet; check the logs folder' }
