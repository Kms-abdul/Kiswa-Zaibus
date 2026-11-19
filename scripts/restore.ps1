<#
.SYNOPSIS  Restores a backup made by backup.ps1. REPLACES the current database. Stops the services while it runs.
.EXAMPLE   .\scripts\restore.ps1 -BackupFile D:\kz\data\backups\kz-backup-20261025-013000.zip
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$BackupFile)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Admin
$envv = Read-EnvFile
$db = $envv['DB_NAME']
if (-not (Test-Path $BackupFile)) { throw "Backup not found: $BackupFile" }
Write-Host "This replaces ALL data in database '$db' with the backup $BackupFile." -ForegroundColor Yellow
Write-Host 'A safety backup of the current data is taken first.' -ForegroundColor Yellow
$typed = Read-Host "Type the database name ($db) to continue"
if ($typed -ne $db) { Write-Host 'Cancelled.'; return }

$rootFile = Join-Path $script:KzRoot 'secrets\mysql-root.txt'
if (Test-Path $rootFile) { $rootPw = (Get-Content $rootFile -Raw).Trim() }
else { $s = Read-Host 'MySQL root password' -AsSecureString; $rootPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)) }

$work = Join-Path $env:TEMP ('kz-restore-' + (Get-Date -Format 'yyyyMMddHHmmss'))
Expand-Archive $BackupFile -DestinationPath $work
$sql = Get-ChildItem $work -Filter '*.sql' | Select-Object -First 1
if (-not $sql) { throw 'The backup contains no .sql file.' }
$man = Get-Content (Join-Path $work 'MANIFEST.txt') -ErrorAction SilentlyContinue | Where-Object { $_ -like 'sql_sha256=*' }
if ($man -and ($man -replace 'sql_sha256=', '') -ne (Get-FileHash $sql.FullName -Algorithm SHA256).Hash) { throw 'Checksum mismatch: the backup file is damaged.' }

Write-Step 'Safety backup of the current data'
& (Join-Path $PSScriptRoot 'backup.ps1') -KeepDays 3650
Write-Step 'Stopping services'
Stop-Service KZ-Gateway, KZ-API -ErrorAction SilentlyContinue
$opt = New-MySqlOptionFile 'root' $rootPw ([int]$envv['DB_PORT'])
try {
    Write-Step 'Restoring'
    Invoke-MySql $envv['MYSQL_BIN'] $opt "DROP DATABASE IF EXISTS ``$db``; CREATE DATABASE ``$db`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" | Out-Null
    $path = $sql.FullName -replace '\\', '/'
    Invoke-MySql $envv['MYSQL_BIN'] $opt "source $path" $db | Out-Null
    if (Test-Path (Join-Path $work 'evidence')) { Copy-Item (Join-Path $work 'evidence\*') $envv['EVIDENCE_DIR'] -Recurse -Force }
    Write-Ok 'database restored'
}
finally {
    Remove-Item $opt -Force -ErrorAction SilentlyContinue
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    Start-Service KZ-API, KZ-Gateway -ErrorAction SilentlyContinue
}
Write-Ok 'services started'
