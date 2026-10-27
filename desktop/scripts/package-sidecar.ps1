$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$buildDirectory = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "build\electron-sidecar"))
$allowedBuildDirectory = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot "build\electron-sidecar"))
$entryPoint = Join-Path $repositoryRoot "src\koreader_ecosystem\ipc_service.py"
$sidecarDirectory = Join-Path $buildDirectory "sidecar"

if (-not $buildDirectory.Equals($allowedBuildDirectory, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean an unexpected sidecar build directory."
}
if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
    throw "The desktop sidecar currently targets 64-bit Windows."
}
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "The Python JSONL service entry point is missing."
}

$uvCommand = Get-Command "uv.exe" -ErrorAction Stop
if (Test-Path -LiteralPath $buildDirectory) {
    Remove-Item -LiteralPath $buildDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null

$nuitkaArguments = @(
    "run",
    "--locked",
    "--project",
    $repositoryRoot,
    "--group",
    "build",
    "python",
    "-m",
    "nuitka",
    "--mode=standalone",
    "--assume-yes-for-downloads",
    "--experimental=force-dependencies-pefile",
    "--output-dir=$buildDirectory",
    "--output-filename=KOReaderEcosystem-Service.exe",
    $entryPoint
)
Push-Location -LiteralPath $repositoryRoot
try {
    & $uvCommand.Source @nuitkaArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka failed to build the standalone reading service."
    }
}
finally {
    Pop-Location
}

$distributionDirectories = @(
    Get-ChildItem -LiteralPath $buildDirectory -Directory -Filter "*.dist" |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "KOReaderEcosystem-Service.exe") -PathType Leaf }
)
if ($distributionDirectories.Count -ne 1) {
    throw "Nuitka did not produce exactly one standalone KOReader Ecosystem service distribution."
}

New-Item -ItemType Directory -Path $sidecarDirectory -Force | Out-Null
Get-ChildItem -LiteralPath $distributionDirectories[0].FullName -Force |
    Copy-Item -Destination $sidecarDirectory -Recurse -Force

$runtimeDirectory = Join-Path $sidecarDirectory "runtime"
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
. (Join-Path $repositoryRoot "scripts\restic-release.ps1")
$resticSource = Get-VerifiedResticExecutable -CacheDirectory $buildDirectory
Copy-Item -LiteralPath $resticSource -Destination (Join-Path $runtimeDirectory "restic.exe")
foreach ($runtimeName in @("stage-kindle.ps1", "WpdReadback.cs", "send-kindle.ps1", "install-hardcover-plugin.ps1")) {
    Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\$runtimeName") `
        -Destination $runtimeDirectory
}
Copy-Item -LiteralPath (Join-Path $repositoryRoot "patches\hardcoverapp-koplugin\0.4.0.patch") `
    -Destination (Join-Path $runtimeDirectory "hardcoverapp-0.4.0.patch")

$sidecarExecutable = Join-Path $sidecarDirectory "KOReaderEcosystem-Service.exe"
if (-not (Test-Path -LiteralPath $sidecarExecutable -PathType Leaf)) {
    throw "The standalone sidecar executable is missing from the package directory."
}

$forbiddenPayload = Get-ChildItem -LiteralPath $sidecarDirectory -File -Recurse |
    Where-Object { $_.Name -match "(?i)(metadata\.db|\.sdr|\.epub$|\.azw\d*$|\.mobi$|\.pdf$|^\.env|credentials|hardcover_config\.lua)" }
if ($forbiddenPayload) {
    throw "The standalone sidecar unexpectedly contains a user-data or credential file."
}

Write-Output "Sidecar pronto: $sidecarExecutable"
