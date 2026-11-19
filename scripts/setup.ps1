<#
.SYNOPSIS
    One-command installer for Kiswa-Zaibus Operations on Windows Server.

.DESCRIPTION
    Installs (or reuses) MySQL 8, Python 3.12, Node.js 20 LTS and NSSM; creates the database and its
    application account; loads the schema and the go-live master data; builds the API, gateway and web app;
    registers the KZ-API and KZ-Gateway Windows services; opens the firewall; schedules the nightly backup;
    and checks that everything answers. Safe to run again: completed steps are detected and skipped,
    existing secrets and data are kept.

.EXAMPLE
    # Typical first install (downloads what is missing, installs MySQL as a service, HTTP on port 8080 for LAN testing)
    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

.EXAMPLE
    # Production: IIS in front with your certificate, public name ops.kiswazaibus.in
    .\scripts\setup.ps1 -ReverseProxy IIS -Domain ops.kiswazaibus.in -TlsPfxPath C:\certs\ops.pfx

.EXAMPLE
    # Existing MySQL server already installed, no internet on the server (installers copied to D:\kz-offline)
    .\scripts\setup.ps1 -UseExistingMySql -OfflineDir D:\kz-offline
#>
[CmdletBinding()]
param(
    [string]$DbName = 'kiswa_zaibus',
    [string]$DbUser = 'kz_app',
    [int]$DbPort = 3306,
    [switch]$UseExistingMySql,                 # MySQL 8 already installed; you will be asked for its root password
    [string]$MySqlServiceName = 'MySQL80',
    [string]$MySqlBufferPool = '1G',
    [int]$ApiPort = 5000,
    [int]$WebPort = 8080,
    [ValidateSet('None', 'IIS')][string]$ReverseProxy = 'None',
    [string]$Domain = '',
    [string]$TlsPfxPath = '',
    [switch]$DemoStudents,                     # load the 35 cached demo student records
    [switch]$TestMode,                         # shows sign-in codes on screen (OTP_DEMO); never use in production
    [string]$OfflineDir = '',                  # folder with pre-downloaded installers (same file names as the URLs)
    [switch]$SkipServices,
    [switch]$SkipBackupTask
)

. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Admin
$Root = $script:KzRoot
$Logs = Join-Path $Root 'logs'; $Data = Join-Path $Root 'data'; $Secrets = Join-Path $Root 'secrets'; $Tools = Join-Path $Root 'tools'
New-Item -ItemType Directory -Force -Path $Logs, $Data, (Join-Path $Data 'evidence'), (Join-Path $Data 'backups'), $Secrets, $Tools | Out-Null
Protect-File $Secrets
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Transcript -Path (Join-Path $Logs "setup-$stamp.log") | Out-Null

$V = @{ Python = '3.12.7'; Node = '20.18.1'; MySql = '8.0.40'; Nssm = '2.24' }
$Url = @{
    Python = "https://www.python.org/ftp/python/$($V.Python)/python-$($V.Python)-amd64.exe"
    Node   = "https://nodejs.org/dist/v$($V.Node)/node-v$($V.Node)-x64.msi"
    MySql  = "https://cdn.mysql.com/archives/mysql-8.0/mysql-$($V.MySql)-winx64.zip"
    Nssm   = "https://nssm.cc/release/nssm-$($V.Nssm).zip"
    VcRedist = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
    UrlRewrite = 'https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi'
    Arr = 'https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/requestRouter_amd64.msi'
}
$Dl = Join-Path $env:TEMP 'kz-setup'; New-Item -ItemType Directory -Force -Path $Dl | Out-Null
$haveWinget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
$rootOpt = $null

function Install-Package([string]$name, [string]$wingetId, [scriptblock]$direct) {
    if ($haveWinget -and -not $OfflineDir) {
        Write-Note "winget install $wingetId"
        & winget.exe install --id $wingetId -e --silent --accept-package-agreements --accept-source-agreements --scope machine | Out-Host
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warn2 "winget could not install $name (exit $LASTEXITCODE); using the direct installer"
    }
    & $direct
}

try {
    Write-Host "Kiswa-Zaibus Operations - setup ($stamp)" -ForegroundColor White
    Write-Host "Install folder: $Root"
    $os = (Get-CimInstance Win32_OperatingSystem)
    Write-Note "$($os.Caption) build $($os.BuildNumber)"
    if ([int]$os.BuildNumber -lt 17763) { Write-Warn2 'Windows Server 2019 or later is recommended.' }

    # ------------------------------------------------------------------ 1. prerequisites
    Write-Step 'Checking prerequisites'
    $vc = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
    if (-not $vc -or -not $vc.Installed) {
        $f = Get-Download $Url.VcRedist (Join-Path $Dl 'vc_redist.x64.exe') $OfflineDir
        Start-Process $f -ArgumentList '/install', '/quiet', '/norestart' -Wait
        Write-Ok 'Visual C++ runtime installed'
    } else { Write-Ok 'Visual C++ runtime present' }

    $py = Find-Exe 'py.exe' @()
    $python = $null
    if ($py) { $python = Invoke-Probe $py @('-3.12', '-c', 'import sys;print(sys.executable)') }
    if (-not $python) { $python = Find-Exe 'python.exe' @("$env:ProgramFiles\Python312\python.exe") }
    if ($python) { $pv = (& $python -c 'import sys;print("%d.%d" % sys.version_info[:2])') } else { $pv = '' }
    if (-not $python -or [version]$pv -lt [version]'3.10') {
        Install-Package 'Python' 'Python.Python.3.12' {
            $f = Get-Download $Url.Python (Join-Path $Dl "python-$($V.Python)-amd64.exe") $OfflineDir
            Start-Process $f -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0', 'Include_launcher=1' -Wait
        }
        $python = Find-Exe 'python.exe' @("$env:ProgramFiles\Python312\python.exe")
        if (-not $python) { throw 'Python 3.12 was not found after installation.' }
    }
    Write-Ok "Python: $python"

    $node = Find-Exe 'node.exe' @("$env:ProgramFiles\nodejs\node.exe")
    if ($node) { $nv = (& $node --version).TrimStart('v') } else { $nv = '0' }
    if (-not $node -or [version]$nv -lt [version]'20.0') {
        Install-Package 'Node.js' 'OpenJS.NodeJS.LTS' {
            $f = Get-Download $Url.Node (Join-Path $Dl "node-v$($V.Node)-x64.msi") $OfflineDir
            Invoke-Native 'msiexec.exe' @('/i', $f, '/qn', '/norestart') 'Node.js install'
        }
        $node = Find-Exe 'node.exe' @("$env:ProgramFiles\nodejs\node.exe")
        if (-not $node) { throw 'Node.js was not found after installation.' }
    }
    $npm = Join-Path (Split-Path $node -Parent) 'npm.cmd'
    Write-Ok "Node.js: $node ($(& $node --version))"

    $nssm = Find-Exe 'nssm.exe' @("$Tools\nssm\nssm.exe")
    if (-not $nssm) {
        $f = Get-Download $Url.Nssm (Join-Path $Dl "nssm-$($V.Nssm).zip") $OfflineDir
        Expand-Archive -Path $f -DestinationPath $Dl -Force
        New-Item -ItemType Directory -Force -Path "$Tools\nssm" | Out-Null
        Copy-Item (Join-Path $Dl "nssm-$($V.Nssm)\win64\nssm.exe") "$Tools\nssm\nssm.exe" -Force
        $nssm = "$Tools\nssm\nssm.exe"
    }
    Write-Ok "NSSM: $nssm"

    # ------------------------------------------------------------------ 2. MySQL
    Write-Step 'MySQL'
    $rootFile = Join-Path $Secrets 'mysql-root.txt'
    $svc = Get-Service -Name $MySqlServiceName -ErrorAction SilentlyContinue
    $mysqlBin = Find-MySqlBin
    if ($svc -or $UseExistingMySql) {
        if (-not $mysqlBin) { throw "MySQL service found but mysql.exe was not. Add MySQL's bin folder to PATH and run again." }
        if (Test-Path $rootFile) { $rootPw = (Get-Content -LiteralPath $rootFile -Raw).Trim() }
        else {
            $sec = Read-Host "MySQL root password for the existing server" -AsSecureString
            $rootPw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        }
        if ($svc -and $svc.Status -ne 'Running') { Start-Service $MySqlServiceName }
        Write-Ok "Using existing MySQL ($mysqlBin)"
    } else {
        $mysqlHome = Join-Path $Root 'mysql'; $mysqlData = Join-Path $Data 'mysql'
        if (-not (Test-Path "$mysqlHome\bin\mysqld.exe")) {
            $f = Get-Download $Url.MySql (Join-Path $Dl "mysql-$($V.MySql)-winx64.zip") $OfflineDir
            Write-Note 'extracting MySQL (about 250 MB)'
            Expand-Archive -Path $f -DestinationPath $Dl -Force
            Move-Item (Join-Path $Dl "mysql-$($V.MySql)-winx64") $mysqlHome
        }
        $ini = Join-Path $mysqlHome 'my.ini'
        $fw = { param($p) $p -replace '\\', '/' }
        @(
            '[mysqld]', "basedir=$(& $fw $mysqlHome)", "datadir=$(& $fw $mysqlData)", "port=$DbPort", 'bind-address=127.0.0.1',
            'character-set-server=utf8mb4', 'collation-server=utf8mb4_unicode_ci', "default-time-zone='+05:30'",
            "innodb_buffer_pool_size=$MySqlBufferPool", 'max_connections=200', 'binlog_expire_logs_seconds=604800', 'log_bin_trust_function_creators=1',
            "log-error=$(& $fw (Join-Path $Logs 'mysql-error.log'))", '', '[client]', 'default-character-set=utf8mb4', "port=$DbPort"
        ) | Set-Content -LiteralPath $ini -Encoding ASCII
        if (-not (Test-Path (Join-Path $mysqlData 'mysql'))) {
            Write-Note 'initialising the data directory'
            Invoke-Native "$mysqlHome\bin\mysqld.exe" @("--defaults-file=$ini", '--initialize-insecure', '--console') 'MySQL initialise'
        }
        Invoke-Native "$mysqlHome\bin\mysqld.exe" @('--install', $MySqlServiceName, "--defaults-file=$ini") 'MySQL service install'
        Start-Service $MySqlServiceName
        Start-Sleep -Seconds 5
        $mysqlBin = "$mysqlHome\bin"
        $rootPw = New-Secret -Password
        $tmp = New-MySqlOptionFile 'root' '' $DbPort
        try { Invoke-MySql $mysqlBin $tmp "ALTER USER 'root'@'localhost' IDENTIFIED BY '$rootPw';" | Out-Null } finally { Remove-Item $tmp -Force }
        Write-Ok "MySQL $($V.MySql) installed as service $MySqlServiceName (localhost only)"
    }
    Set-Content -LiteralPath $rootFile -Value $rootPw -Encoding ASCII
    Protect-File $rootFile
    $rootOpt = New-MySqlOptionFile 'root' $rootPw $DbPort

    # ------------------------------------------------------------------ 3. configuration (.env)
    Write-Step 'Configuration'
    $envv = Read-EnvFile
    function SetDefault($k, $v) { if (-not $envv.Contains($k) -or -not $envv[$k]) { $envv[$k] = $v } }
    $https = [bool]$TlsPfxPath
    SetDefault 'DB_HOST' '127.0.0.1'; SetDefault 'DB_PORT' "$DbPort"; SetDefault 'DB_NAME' $DbName; SetDefault 'DB_USER' $DbUser
    SetDefault 'DB_PASSWORD' (New-Secret -Password); SetDefault 'SECRET_KEY' (New-Secret 32); SetDefault 'INTERNAL_KEY' (New-Secret 32)
    SetDefault 'API_HOST' '127.0.0.1'; SetDefault 'API_PORT' "$ApiPort"
    $envv['GATEWAY_HOST'] = $(if ($ReverseProxy -eq 'IIS') { '127.0.0.1' } else { '0.0.0.0' })
    $envv['GATEWAY_PORT'] = "$WebPort"
    $envv['PUBLIC_HTTPS'] = $(if ($https) { '1' } else { '0' })
    $envv['COOKIE_SECURE'] = $(if ($https) { '1' } else { '0' })
    if ($https -and $ReverseProxy -eq 'None') { $envv['TLS_PFX_PATH'] = $TlsPfxPath }
    $envv['OTP_DEMO'] = $(if ($TestMode) { '1' } else { '0' })
    SetDefault 'DATA_DIR' $Data; SetDefault 'EVIDENCE_DIR' (Join-Path $Data 'evidence'); SetDefault 'LOG_DIR' $Logs
    $envv['MYSQL_BIN'] = $mysqlBin; SetDefault 'BACKUP_DIR' (Join-Path $Data 'backups'); SetDefault 'BACKUP_KEEP_DAYS' '30'
    foreach ($k in 'SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM', 'SMS_API_URL', 'SMS_API_KEY', 'SMS_SENDER_ID',
                    'WHATSAPP_API_URL', 'WHATSAPP_API_TOKEN', 'RAZORPAY_WEBHOOK_SECRET_KG', 'RAZORPAY_WEBHOOK_SECRET_ZE') { SetDefault $k '' }
    SetDefault 'SMTP_PORT' '587'
    Write-EnvFile $envv
    Protect-File $script:EnvFile @('NT AUTHORITY\LocalService')
    Write-Ok "$script:EnvFile written (secrets kept on re-run; readable by Administrators and the service account only)"

    # ------------------------------------------------------------------ 4. database and account
    Write-Step 'Database'
    $appPw = $envv['DB_PASSWORD']
    $sql = @"
CREATE DATABASE IF NOT EXISTS ``$DbName`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DbUser'@'localhost' IDENTIFIED BY '$appPw';
CREATE USER IF NOT EXISTS '$DbUser'@'127.0.0.1' IDENTIFIED BY '$appPw';
ALTER USER '$DbUser'@'localhost' IDENTIFIED BY '$appPw';
ALTER USER '$DbUser'@'127.0.0.1' IDENTIFIED BY '$appPw';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE ON ``$DbName``.* TO '$DbUser'@'localhost', '$DbUser'@'127.0.0.1';
GRANT ALL ON ``${DbName}_test``.* TO '$DbUser'@'localhost', '$DbUser'@'127.0.0.1';
FLUSH PRIVILEGES;
"@
    Invoke-MySql $mysqlBin $rootOpt $sql | Out-Null
    Write-Ok "database $DbName and account $DbUser (data rights only; schema changes run as root)"

    # ------------------------------------------------------------------ 5. Python API
    Write-Step 'API (Python)'
    $venv = Join-Path $Root 'backend\.venv'
    if (-not (Test-Path "$venv\Scripts\python.exe")) { Invoke-Native $python @('-m', 'venv', $venv) 'virtualenv' }
    $vpy = "$venv\Scripts\python.exe"
    Invoke-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '--upgrade', 'pip') 'pip upgrade'
    Invoke-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', (Join-Path $Root 'backend\requirements.txt')) 'pip install'
    Write-Ok 'packages installed'

    $env:DB_USER = 'root'; $env:DB_PASSWORD = $rootPw          # schema objects (triggers) are created by root
    Invoke-Native $vpy @((Join-Path $Root 'database\apply_schema.py')) 'schema'
    Remove-Item Env:\DB_USER, Env:\DB_PASSWORD
    $seedArgs = @((Join-Path $Root 'database\seed.py'))
    if ($DemoStudents) { $seedArgs += '--demo-students' }
    $env:SEED_TEMP_PASSWORD = 'Kiswa@2026'
    Invoke-Native $vpy $seedArgs 'seed'
    Remove-Item Env:\SEED_TEMP_PASSWORD

    # ------------------------------------------------------------------ 6. gateway and web app
    Write-Step 'Gateway and web app (Node.js)'
    $env:Path = (Split-Path $node -Parent) + ';' + $env:Path
    foreach ($d in 'gateway', 'frontend') {
        Push-Location (Join-Path $Root $d)
        try {
            Invoke-Native $npm @('ci', '--no-audit', '--no-fund', '--loglevel=error') "npm ci ($d)"
            Invoke-Native $npm @('run', 'build') "build ($d)"
        } finally { Pop-Location }
        Write-Ok "$d built"
    }

    # ------------------------------------------------------------------ 7. folder permissions for the service account
    Write-Step 'Permissions'
    foreach ($p in $Logs, $Data) { & icacls.exe $p /grant 'NT AUTHORITY\LocalService:(OI)(CI)M' | Out-Null }
    foreach ($p in 'backend', 'gateway', 'frontend', 'database') { & icacls.exe (Join-Path $Root $p) /grant 'NT AUTHORITY\LocalService:(OI)(CI)RX' | Out-Null }
    if ($https -and $ReverseProxy -eq 'None') { & icacls.exe $TlsPfxPath /grant 'NT AUTHORITY\LocalService:(R)' | Out-Null }
    Write-Ok 'services run as LocalService: read the app, write only logs and data'

    # ------------------------------------------------------------------ 8. Windows services
    if (-not $SkipServices) {
        Write-Step 'Windows services'
        $mysqlDep = $(if (Get-Service $MySqlServiceName -ErrorAction SilentlyContinue) { $MySqlServiceName } else { '' })
        $defs = @(
            @{ Name = 'KZ-API'; Exe = $vpy; Args = 'run.py'; Dir = (Join-Path $Root 'backend'); Dep = $mysqlDep; Desc = 'Kiswa-Zaibus API (Flask on waitress, localhost only)' },
            @{ Name = 'KZ-Gateway'; Exe = $node; Args = 'dist\server.js'; Dir = (Join-Path $Root 'gateway'); Dep = 'KZ-API'; Desc = 'Kiswa-Zaibus web gateway (web app, API proxy, Razorpay webhooks, alerts, scheduler)' }
        )
        foreach ($s in $defs) {
            if (Get-Service $s.Name -ErrorAction SilentlyContinue) {
                & $nssm stop $s.Name | Out-Null
                & $nssm remove $s.Name confirm | Out-Null
            }
            Invoke-Native $nssm @('install', $s.Name, $s.Exe, $s.Args) "service $($s.Name)"
            & $nssm set $s.Name AppDirectory $s.Dir | Out-Null
            & $nssm set $s.Name Description $s.Desc | Out-Null
            & $nssm set $s.Name Start SERVICE_AUTO_START | Out-Null
            & $nssm set $s.Name ObjectName 'NT AUTHORITY\LocalService' | Out-Null
            & $nssm set $s.Name AppStdout (Join-Path $Logs "$($s.Name)-service.log") | Out-Null
            & $nssm set $s.Name AppStderr (Join-Path $Logs "$($s.Name)-service.log") | Out-Null
            & $nssm set $s.Name AppRotateFiles 1 | Out-Null
            & $nssm set $s.Name AppRotateOnline 1 | Out-Null
            & $nssm set $s.Name AppRotateBytes 10485760 | Out-Null
            & $nssm set $s.Name AppExit Default Restart | Out-Null
            & $nssm set $s.Name AppRestartDelay 5000 | Out-Null
            if ($s.Dep) { & $nssm set $s.Name DependOnService $s.Dep | Out-Null }
            Start-Service $s.Name
            Write-Ok "$($s.Name) running"
        }
    }

    # ------------------------------------------------------------------ 9. IIS reverse proxy (optional)
    if ($ReverseProxy -eq 'IIS') {
        Write-Step 'IIS reverse proxy'
        Install-WindowsFeature -Name Web-Server, Web-WebSockets, Web-Mgmt-Console | Out-Null
        if (-not (Test-Path "$env:windir\system32\inetsrv\rewrite.dll")) {
            $f = Get-Download $Url.UrlRewrite (Join-Path $Dl 'rewrite_amd64_en-US.msi') $OfflineDir
            Invoke-Native 'msiexec.exe' @('/i', $f, '/qn', '/norestart') 'IIS URL Rewrite'
        }
        if (-not (Test-Path "$env:ProgramFiles\IIS\Application Request Routing")) {
            $f = Get-Download $Url.Arr (Join-Path $Dl 'requestRouter_amd64.msi') $OfflineDir
            Invoke-Native 'msiexec.exe' @('/i', $f, '/qn', '/norestart') 'IIS Application Request Routing'
        }
        $appcmd = "$env:windir\system32\inetsrv\appcmd.exe"
        & $appcmd set config -section:system.webServer/proxy /enabled:"True" /preserveHostHeader:"True" /reverseRewriteHostInResponseHeaders:"False" /commit:apphost | Out-Null
        & $appcmd set config -section:system.webServer/rewrite/allowedServerVariables /+"[name='HTTP_X_FORWARDED_PROTO']" /commit:apphost 2>$null | Out-Null
        $site = Join-Path $Root 'iis'; New-Item -ItemType Directory -Force -Path $site | Out-Null
        Copy-Item (Join-Path $Root 'docs\iis-web.config') (Join-Path $site 'web.config') -Force
        $wc = (Get-Content (Join-Path $site 'web.config') -Raw) -replace '127.0.0.1:8080', "127.0.0.1:$WebPort"
        if ($https) { $wc = $wc -replace 'name="https-redirect" enabled="false"', 'name="https-redirect" enabled="true"' }
        [IO.File]::WriteAllText((Join-Path $site 'web.config'), $wc, (New-Object Text.UTF8Encoding($false)))
        Import-Module WebAdministration
        if (Get-Website -Name 'KiswaZaibus' -ErrorAction SilentlyContinue) { Remove-Website -Name 'KiswaZaibus' }
        New-Website -Name 'KiswaZaibus' -PhysicalPath $site -Port 80 -HostHeader $Domain -Force | Out-Null
        if ($https) {
            $pfxPw = Read-Host 'Password for the certificate file' -AsSecureString
            $cert = Import-PfxCertificate -FilePath $TlsPfxPath -CertStoreLocation Cert:\LocalMachine\My -Password $pfxPw
            New-WebBinding -Name 'KiswaZaibus' -Protocol https -Port 443 -HostHeader $Domain -SslFlags 1
            (Get-WebBinding -Name 'KiswaZaibus' -Protocol https).AddSslCertificate($cert.Thumbprint, 'My')
        }
        Write-Ok "IIS site KiswaZaibus -> http://127.0.0.1:$WebPort"
    }

    # ------------------------------------------------------------------ 10. firewall
    Write-Step 'Firewall'
    $ports = $(if ($ReverseProxy -eq 'IIS') { @(80, 443) } else { @($WebPort) })
    Get-NetFirewallRule -DisplayName 'Kiswa-Zaibus Web' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName 'Kiswa-Zaibus Web' -Direction Inbound -Protocol TCP -LocalPort $ports -Action Allow -Profile Domain, Private, Public | Out-Null
    Write-Ok "inbound TCP $($ports -join ', ') allowed (MySQL and the API stay on localhost)"

    # ------------------------------------------------------------------ 11. nightly backup
    if (-not $SkipBackupTask) {
        Write-Step 'Nightly backup'
        $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'backup.ps1')`""
        $trg = New-ScheduledTaskTrigger -Daily -At 1:30am
        $pr = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName 'Kiswa-Zaibus nightly backup' -Action $act -Trigger $trg -Principal $pr -Force | Out-Null
        Write-Ok "daily at 01:30 to $($envv['BACKUP_DIR']) (kept $($envv['BACKUP_KEEP_DAYS']) days)"
    }

    # ------------------------------------------------------------------ 12. health check
    Write-Step 'Health check'
    $ok = $false
    for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
        try { $h = Invoke-RestMethod "http://127.0.0.1:$WebPort/healthz" -TimeoutSec 5; $ok = ($h.api.status -eq 'ok') } catch { Start-Sleep -Seconds 3 }
    }
    if ($ok) { Write-Ok "gateway and API answer; schema $($h.api.schema)" } else { Write-Warn2 "no healthy answer yet - see $Logs" }

    $scheme = $(if ($https) { 'https' } else { 'http' })
    $hostName = $(if ($Domain) { $Domain } else { $env:COMPUTERNAME })
    $portPart = $(if ($ReverseProxy -eq 'IIS' -or ($https -and $WebPort -eq 443)) { '' } else { ":$WebPort" })
    Write-Host ''
    Write-Host '------------------------------------------------------------------' -ForegroundColor White
    Write-Host " Kiswa-Zaibus is installed:  ${scheme}://$hostName$portPart" -ForegroundColor Green
    Write-Host ' First sign-in: IT Super Admin  KZ-IT-001  temporary password Kiswa@2026'
    Write-Host '                Director        KZ-HO-001  (same temporary password; each user must change it)'
    Write-Host " Configuration: $script:EnvFile"
    Write-Host "   - fill SMTP_* and SMS_API_* so sign-in codes and alerts are delivered"
    Write-Host "   - fill RAZORPAY_WEBHOOK_SECRET_KG / _ZE, then restart: Restart-Service KZ-API, KZ-Gateway"
    if (-not $https) { Write-Host ' Running over plain HTTP: use on the office network only until a certificate is added.' -ForegroundColor Yellow }
    if ($TestMode) { Write-Host ' TEST MODE: sign-in codes are shown on screen. Re-run without -TestMode before go-live.' -ForegroundColor Yellow }
    Write-Host " Setup log: $(Join-Path $Logs "setup-$stamp.log")"
    Write-Host '------------------------------------------------------------------' -ForegroundColor White
}
finally {
    if ($rootOpt -and (Test-Path $rootOpt)) { Remove-Item $rootOpt -Force }
    Stop-Transcript | Out-Null
}
