<#
.SYNOPSIS  Removes the Windows services, firewall rule, backup task and IIS site. Keeps the database, data and backups
           unless -RemoveDatabase is given (and confirmed by typing the database name).
#>
[CmdletBinding()]
param([switch]$RemoveDatabase)
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Admin
$Root = $script:KzRoot
$envv = Read-EnvFile
$nssm = Find-Exe 'nssm.exe' @("$Root\tools\nssm\nssm.exe")
foreach ($s in 'KZ-Gateway', 'KZ-API') {
    if (Get-Service $s -ErrorAction SilentlyContinue) {
        Stop-Service $s -ErrorAction SilentlyContinue
        if ($nssm) { & $nssm remove $s confirm | Out-Null } else { & sc.exe delete $s | Out-Null }
        Write-Ok "service $s removed"
    }
}
Get-NetFirewallRule -DisplayName 'Kiswa-Zaibus Web' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Unregister-ScheduledTask -TaskName 'Kiswa-Zaibus nightly backup' -Confirm:$false -ErrorAction SilentlyContinue
if (Get-Module -ListAvailable WebAdministration) {
    Import-Module WebAdministration
    if (Get-Website -Name 'KiswaZaibus' -ErrorAction SilentlyContinue) { Remove-Website -Name 'KiswaZaibus'; Write-Ok 'IIS site removed' }
}
Write-Ok 'firewall rule and backup task removed'
if ($RemoveDatabase) {
    $db = $envv['DB_NAME']
    $typed = Read-Host "This permanently deletes database '$db'. Type its name to confirm"
    if ($typed -eq $db) {
        & (Join-Path $PSScriptRoot 'backup.ps1') -KeepDays 3650
        $rootPw = (Get-Content (Join-Path $Root 'secrets\mysql-root.txt') -Raw).Trim()
        $opt = New-MySqlOptionFile 'root' $rootPw ([int]$envv['DB_PORT'])
        try { Invoke-MySql $envv['MYSQL_BIN'] $opt "DROP DATABASE IF EXISTS ``$db``; DROP USER IF EXISTS '$($envv['DB_USER'])'@'localhost', '$($envv['DB_USER'])'@'127.0.0.1';" | Out-Null }
        finally { Remove-Item $opt -Force }
        Write-Ok "database $db removed (a final backup was kept in $($envv['BACKUP_DIR']))"
    } else { Write-Host 'Database kept.' }
} else {
    Write-Host "Database '$($envv['DB_NAME'])', data, logs and backups were kept." -ForegroundColor Gray
}
