[CmdletBinding()]
param(
    [Parameter()]
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$uvCommand = Get-Command 'uv' -ErrorAction SilentlyContinue
$uvPath = if ($null -ne $uvCommand) { $uvCommand.Source } else { $null }
if ($null -eq $uvPath) {
    $uvPackage = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\astral-sh.uv_*'
    $uvPackageFile = Get-ChildItem -Path $uvPackage -Filter 'uv.exe' -File -ErrorAction SilentlyContinue |
        Sort-Object -Property LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -ne $uvPackageFile) {
        $uvPath = $uvPackageFile.FullName
    }
}
if ($null -eq $uvPath) {
    throw 'uv is not installed; run scripts/install.ps1 first.'
}

$doctorLines = & $uvPath run --project $ProjectRoot reading doctor
$doctorExitCode = $LASTEXITCODE
try {
    $doctor = ($doctorLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
} catch {
    throw 'reading doctor returned an invalid report.'
}
if ($doctorExitCode -gt 1 -or $null -eq $doctor.PSObject.Properties['checks']) {
    throw 'reading doctor could not complete its checks.'
}

foreach ($name in @('calibre', 'kindle', 'restic', 'backup_space')) {
    if ($doctor.checks.$name.status -ne 'ok') {
        throw "Preflight failed: $name. Resolve the diagnostic before backing up."
    }
}
if (-not $doctor.checks.credentials.windows_credential_manager) {
    throw 'Windows Credential Manager is unavailable.'
}

$backupLines = & $uvPath run --project $ProjectRoot reading backup --verify-restore
$backupExitCode = $LASTEXITCODE
try {
    $backup = ($backupLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
} catch {
    throw 'reading backup returned an invalid report.'
}
if ($backupExitCode -ne 0 -or $backup.status -ne 'ok' -or -not $backup.restore_verified) {
    $reasonCode = if ($null -ne $backup.PSObject.Properties['reason_code']) {
        $backup.reason_code
    } else {
        'unknown'
    }
    throw "Live backup and restore verification failed: $reasonCode"
}

$reportDirectory = Join-Path $env:LOCALAPPDATA 'KOReaderEcosystem\reports'
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$reportPath = Join-Path $reportDirectory "$($backup.snapshot_id).json"
$report = [ordered]@{
    schema_version = 1
    status = 'ok'
    snapshot_id = $backup.snapshot_id
    verified_at_utc = [DateTime]::UtcNow.ToString('o')
    restore_verified = $backup.restore_verified
    file_count = $backup.file_count
    total_bytes = $backup.total_bytes
    calibre_file_count = $backup.calibre_file_count
    kindle_file_count = $backup.kindle_file_count
    preflight = [ordered]@{
        calibre = $doctor.checks.calibre.status
        kindle = $doctor.checks.kindle
        restic = $doctor.checks.restic.status
        credential_manager_available = $doctor.checks.credentials.windows_credential_manager
        free_bytes_before_backup = $doctor.checks.backup_space.available_bytes
    }
}
$json = $report | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText($reportPath, $json, [Text.UTF8Encoding]::new($false))

Write-Output "Backup e restauração verificados. Snapshot: $($backup.snapshot_id)"
Write-Output "Arquivos: $($backup.file_count); bytes: $($backup.total_bytes)."
Write-Output 'Relatório agregado salvo em LocalAppData.'
