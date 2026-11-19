<#
.SYNOPSIS  Consistent backup of the database and evidence files. Run nightly by the scheduled task; safe to run any time.
.EXAMPLE   .\scripts\backup.ps1                 # to BACKUP_DIR from backend\.env
.EXAMPLE   .\scripts\backup.ps1 -To E:\kz-backups
#>
[CmdletBinding()]
param([string]$To = '', [int]$KeepDays = 0)
. (Join-Path $PSScriptRoot 'common.ps1')
$envv = Read-EnvFile
if (-not $envv.Contains('DB_NAME')) { throw "No configuration found at $script:EnvFile. Run setup.ps1 first." }
$dest = $(if ($To) { $To } else { $envv['BACKUP_DIR'] })
$keep = $(if ($KeepDays) { $KeepDays } else { [int]$envv['BACKUP_KEEP_DAYS'] })
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$work = Join-Path $env:TEMP "kz-backup-$stamp"; New-Item -ItemType Directory -Force -Path $work | Out-Null
# Root is used so triggers (the append-only audit guard) are included; the app account cannot read trigger definitions.
$rootFile = Join-Path $script:KzRoot 'secrets\mysql-root.txt'
if (-not (Test-Path $rootFile)) { throw "Missing $rootFile (written by setup.ps1)." }
$opt = New-MySqlOptionFile 'root' ((Get-Content $rootFile -Raw).Trim()) ([int]$envv['DB_PORT'])
$logFile = Join-Path $envv['LOG_DIR'] 'backup.log'
try {
    $sqlFile = Join-Path $work "$($envv['DB_NAME'])-$stamp.sql"
    $dump = Join-Path $envv['MYSQL_BIN'] 'mysqldump.exe'
    # --single-transaction: a consistent snapshot without locking the shops out
    Invoke-Native $dump @("--defaults-extra-file=$opt", '--single-transaction', '--quick', '--triggers', '--routines', '--no-tablespaces',
                          '--hex-blob', '--default-character-set=utf8mb4', "--result-file=$sqlFile", $envv['DB_NAME']) 'mysqldump'
    $ev = $envv['EVIDENCE_DIR']
    if ($ev -and (Test-Path $ev) -and (Get-ChildItem $ev -Recurse -File | Select-Object -First 1)) { Copy-Item $ev (Join-Path $work 'evidence') -Recurse }
    $hash = (Get-FileHash $sqlFile -Algorithm SHA256).Hash
    Set-Content (Join-Path $work 'MANIFEST.txt') -Encoding ASCII -Value @("database=$($envv['DB_NAME'])", "created=$stamp", "host=$env:COMPUTERNAME", "sql_sha256=$hash")
    $zip = Join-Path $dest "kz-backup-$stamp.zip"
    Compress-Archive -Path (Join-Path $work '*') -DestinationPath $zip -CompressionLevel Optimal
    $old = Get-ChildItem $dest -Filter 'kz-backup-*.zip' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$keep) }
    $old | Remove-Item -Force
    $msg = "$(Get-Date -Format s) OK $zip ($([Math]::Round((Get-Item $zip).Length / 1MB, 1)) MB); removed $(@($old).Count) older than $keep days"
    Add-Content $logFile $msg; Write-Host $msg -ForegroundColor Green
}
catch {
    Add-Content $logFile "$(Get-Date -Format s) FAILED $($_.Exception.Message)"
    throw
}
finally {
    Remove-Item $opt -Force -ErrorAction SilentlyContinue
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
