# Shared helpers for the Kiswa-Zaibus Windows scripts. Windows PowerShell 5.1 compatible. ASCII only.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Invoke-WebRequest is 10x faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$script:KzRoot = Split-Path -Parent $PSScriptRoot
$script:EnvFile = Join-Path $script:KzRoot 'backend\.env'

function Write-Step([string]$msg) { Write-Host ''; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Note([string]$msg) { Write-Host "    ..  $msg" -ForegroundColor Gray }
function Write-Warn2([string]$msg){ Write-Host "    !!  $msg" -ForegroundColor Yellow }

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell (Run as administrator).'
    }
}

function New-Secret([int]$bytes = 32, [switch]$Password) {
    $b = New-Object byte[] $bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($b); $rng.Dispose()
    if ($Password) {
        # URL/shell/ini-safe password with all four character classes
        $s = [Convert]::ToBase64String($b) -replace '[+/=]', ''
        return 'Kz' + $s.Substring(0, [Math]::Min(22, $s.Length)) + '#7a'
    }
    return -join ($b | ForEach-Object { $_.ToString('x2') })
}

function Read-EnvFile([string]$path = $script:EnvFile) {
    $h = [ordered]@{}
    if (Test-Path $path) {
        foreach ($line in Get-Content -LiteralPath $path) {
            if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
            $k, $v = $line -split '=', 2
            $h[$k.Trim()] = $v.Trim()
        }
    }
    return $h
}

function Write-EnvFile($values, [string]$path = $script:EnvFile) {
    $lines = @('# Kiswa-Zaibus configuration. Written by setup.ps1; edit with care and restart the KZ-API and KZ-Gateway services.',
               "# Generated $(Get-Date -Format 'yyyy-MM-dd HH:mm')", '')
    foreach ($k in $values.Keys) { $lines += "$k=$($values[$k])" }
    # UTF-8 without BOM so Python and Node read it cleanly
    [IO.File]::WriteAllLines($path, $lines, (New-Object Text.UTF8Encoding($false)))
}

function Protect-File([string]$path, [string[]]$readers = @()) {
    # Only Administrators and SYSTEM (plus the given service accounts, read-only) may open the file.
    & icacls.exe $path /inheritance:r /grant:r '*S-1-5-32-544:(F)' '*S-1-5-18:(F)' | Out-Null
    foreach ($r in $readers) { & icacls.exe $path /grant:r "${r}:(R)" | Out-Null }
}

function Invoke-Native([string]$exe, [string[]]$arguments, [string]$what) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

function Invoke-Probe([string]$exe, [string[]]$arguments) {
    # Runs a native command whose failure is acceptable; returns its stdout or $null.
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $o = & $exe @arguments 2>$null; if ($LASTEXITCODE -eq 0) { return $o } return $null }
    catch { return $null } finally { $ErrorActionPreference = $eap }
}

function Get-Download([string]$url, [string]$file, [string]$offlineDir) {
    if ($offlineDir) {
        $local = Join-Path $offlineDir (Split-Path $file -Leaf)
        if (Test-Path $local) { Write-Note "using offline copy $local"; return $local }
    }
    Write-Note "downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
    return $file
}

function Find-Exe([string]$name, [string[]]$candidates) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in $candidates) {
        $hit = Get-ChildItem -Path $p -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Find-MySqlBin {
    $exe = Find-Exe 'mysql.exe' @("$env:ProgramFiles\MySQL\MySQL Server 8*\bin\mysql.exe", "$script:KzRoot\mysql\bin\mysql.exe", "C:\mysql\bin\mysql.exe")
    if ($exe) { return Split-Path $exe -Parent }
    return $null
}

function New-MySqlOptionFile([string]$user, [string]$password, [int]$port = 3306) {
    # Credentials go in a temporary option file, never on the command line (visible in the process list).
    $f = Join-Path $env:TEMP ("kz-my-" + [Guid]::NewGuid().ToString('N') + '.cnf')
    $pw = $password -replace '\\', '\\\\' -replace '"', '\"'
    [IO.File]::WriteAllLines($f, @('[client]', "user=$user", "password=`"$pw`"", 'host=127.0.0.1', "port=$port", 'default-character-set=utf8mb4'),
                             (New-Object Text.UTF8Encoding($false)))
    Protect-File $f
    return $f
}

function Invoke-MySql([string]$mysqlBin, [string]$optionFile, [string]$sql, [string]$database = '') {
    $args2 = @("--defaults-extra-file=$optionFile", '--batch', '--skip-column-names', '-e', $sql)
    if ($database) { $args2 += $database }
    # PowerShell 5.1 turns redirected native stderr into errors; collect it as text instead.
    $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $out = & (Join-Path $mysqlBin 'mysql.exe') @args2 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0) { throw "MySQL command failed: $($out -join ' ')" }
    return $out
}
