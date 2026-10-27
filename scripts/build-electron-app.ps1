param(
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$desktopDirectory = Join-Path $repositoryRoot "desktop"
$desktopOutputDirectory = Join-Path $desktopDirectory "out"
$desktopManifest = Get-Content -LiteralPath (Join-Path $desktopDirectory "package.json") -Raw |
    ConvertFrom-Json
$releaseOutputDirectory = Join-Path $desktopOutputDirectory "v$($desktopManifest.version)-release"
$nodeCommand = Get-Command "node.exe" -ErrorAction Stop
$npmCommand = Get-Command "npm.cmd" -ErrorAction Stop

if ([version]((& $nodeCommand.Source --version).Trim().TrimStart("v")) -lt [version]"24.0") {
    throw "Node.js 24 or newer is required to build the Electron app."
}

Push-Location -LiteralPath $repositoryRoot
try {
    & $npmCommand.Source --prefix $desktopDirectory ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed for the desktop project." }

    & $npmCommand.Source --prefix $desktopDirectory test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Electron renderer tests failed." }

    & $npmCommand.Source --prefix $desktopDirectory run typecheck
    if ($LASTEXITCODE -ne 0) { throw "Electron TypeScript validation failed." }

    & $npmCommand.Source --prefix $desktopDirectory run lint
    if ($LASTEXITCODE -ne 0) { throw "Electron lint failed." }

    & $npmCommand.Source --prefix $desktopDirectory run make
    if ($LASTEXITCODE -ne 0) { throw "Electron Forge failed to build the Windows installer." }
}
finally {
    Pop-Location
}

$installerCandidates = @(
    Get-ChildItem -LiteralPath (Join-Path $releaseOutputDirectory "make\squirrel.windows") `
        -Filter "*Setup.exe" -File -Recurse |
        Where-Object { $_.FullName -match "(?i)squirrel\.windows" }
)
if ($installerCandidates.Count -ne 1) {
    throw "Expected exactly one Squirrel.Windows installer, found $($installerCandidates.Count)."
}
$installer = $installerCandidates[0].FullName
$installerHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$hashFile = "$installer.sha256"
"$installerHash  $([System.IO.Path]::GetFileName($installer))" |
    Set-Content -LiteralPath $hashFile -Encoding ascii

if ($SmokeTest) {
    $applicationCandidates = @(
        Get-ChildItem -LiteralPath $releaseOutputDirectory -Filter "KOReaderEcosystem.exe" -File -Recurse |
            Where-Object { $_.FullName -notmatch "(?i)[\\/]make[\\/]" }
    )
    if ($applicationCandidates.Count -ne 1) {
        throw "Expected one packaged Electron executable for the smoke test."
    }

    $applicationDirectory = Split-Path -Parent $applicationCandidates[0].FullName
    $smokeProcess = Start-Process `
        -FilePath $applicationCandidates[0].FullName `
        -ArgumentList "--smoke-test" `
        -WorkingDirectory $applicationDirectory `
        -WindowStyle Hidden `
        -PassThru `
        -Wait
    if ($smokeProcess.ExitCode -ne 0) {
        throw "The packaged Electron app failed its status-only smoke test."
    }

    $outputRoot = [System.IO.Path]::GetFullPath($releaseOutputDirectory)
    $orphanedSidecars = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "KOReaderEcosystem-Service.exe" -and
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($outputRoot, [System.StringComparison]::OrdinalIgnoreCase)
        }
    if ($orphanedSidecars) {
        throw "The smoke test left a KOReaderEcosystem-Service.exe process running."
    }
}

Write-Output "Instalador: $installer"
Write-Output "SHA-256: $installerHash"
Write-Output "Hash salvo: $hashFile"
