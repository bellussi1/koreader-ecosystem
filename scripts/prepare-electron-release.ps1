param(
    [Parameter(Mandatory)][ValidatePattern('^v\d+\.\d+\.\d+$')]
    [string]$Tag
)

$ErrorActionPreference = "Stop"
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$version = $Tag.Substring(1)
$packageManifestPath = Join-Path $repositoryRoot "desktop\package.json"
$releaseNotesPath = Join-Path $repositoryRoot "docs\releases\v$version.md"
$releaseDirectory = Join-Path $repositoryRoot "desktop\out\v$version-release"
$assetsDirectory = Join-Path $repositoryRoot "desktop\out\release-assets"

$packageManifest = Get-Content -LiteralPath $packageManifestPath -Raw | ConvertFrom-Json
if ($packageManifest.version -ne $version) {
    throw "Release tag $Tag does not match desktop/package.json version $($packageManifest.version)."
}
if (-not (Test-Path -LiteralPath $releaseNotesPath -PathType Leaf)) {
    throw "Release notes are missing: docs/releases/v$version.md."
}
if (-not (Test-Path -LiteralPath $releaseDirectory -PathType Container)) {
    throw "The packaged build is missing: desktop/out/v$version-release."
}

$rendererSource = Get-Content -LiteralPath (Join-Path $repositoryRoot "desktop\src\renderer\App.tsx") -Raw
$rendererConfig = Get-Content -LiteralPath (Join-Path $repositoryRoot "desktop\vite.renderer.config.ts") -Raw
if (
    $rendererSource -notmatch "__APP_VERSION__" -or
    $rendererConfig -notmatch "__APP_VERSION__" -or
    $rendererConfig -notmatch "packageManifest\.version"
) {
    throw "The visible application version is not connected to desktop/package.json."
}

$installerCandidates = @(
    Get-ChildItem -LiteralPath (Join-Path $releaseDirectory "make\squirrel.windows\x64") `
        -Filter "*Setup.exe" -File -ErrorAction Stop
)
$zipCandidates = @(
    Get-ChildItem -LiteralPath (Join-Path $releaseDirectory "make\zip\win32\x64") `
        -Filter "*$version.zip" -File -ErrorAction Stop
)
if ($installerCandidates.Count -ne 1 -or $zipCandidates.Count -ne 1) {
    throw "Expected exactly one Windows installer and one versioned portable ZIP."
}

New-Item -ItemType Directory -Path $assetsDirectory -Force | Out-Null
$installerPath = Join-Path $assetsDirectory "KOReaderEcosystem-Setup.exe"
$portablePath = Join-Path $assetsDirectory "KOReaderEcosystem-Windows-x64.zip"
Copy-Item -LiteralPath $installerCandidates[0].FullName -Destination $installerPath -Force
Copy-Item -LiteralPath $zipCandidates[0].FullName -Destination $portablePath -Force

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($portablePath)
try {
    $entryNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    foreach ($requiredEntry in @(
        "KOReaderEcosystem.exe",
        "resources/app.asar",
        "resources/sidecar/KOReaderEcosystem-Service.exe",
        "resources/sidecar/runtime/restic.exe"
    )) {
        if ($requiredEntry -notin $entryNames) {
            throw "The portable ZIP is missing required file $requiredEntry."
        }
    }
    $forbiddenEntries = @(
        $entryNames | Where-Object {
            $_ -match '(?i)(^|/)metadata\.db(?:-.*)?(?:/|$)' -or
            $_ -match '(?i)(^|/)[^/]+\.sdr(?:/|$)' -or
            $_ -match '(?i)(^|/)\.env(?:\.[^/]*)?(?:/|$)' -or
            $_ -match '(?i)(^|/)hardcover_config\.lua$' -or
            $_ -match '(?i)\.(epub|azw\d*|mobi|pdf)$' -or
            $_ -match '(?i)(^|/)(credentials|backups|exports)(/|$)'
        }
    )
    if ($forbiddenEntries.Count -gt 0) {
        throw "The portable ZIP contains a user-data path and cannot be published."
    }
}
finally {
    $archive.Dispose()
}

$checksums = @($installerPath, $portablePath) | Sort-Object
$checksumLines = foreach ($file in $checksums) {
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([System.IO.Path]::GetFileName($file))"
}
$checksumPath = Join-Path $assetsDirectory "SHA256SUMS.txt"
$checksumLines | Set-Content -LiteralPath $checksumPath -Encoding ascii

Write-Output "Release $Tag preparada em desktop/out/release-assets."
Get-ChildItem -LiteralPath $assetsDirectory -File | Select-Object Name, Length
