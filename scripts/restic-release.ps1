function Get-VerifiedResticExecutable {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$CacheDirectory)

    $version = "0.19.1"
    $assetName = "restic_$($version)_windows_amd64.zip"
    $assetDigest = "sha256:da948ad707ed690426473aaba2046cd61f8f90f6f0e7dab6be0d5796531de67d"
    $destination = Join-Path $CacheDirectory "restic.exe"

    New-Item -ItemType Directory -Path $CacheDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        $versionOutput = & $destination version 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $versionOutput -match "restic $([regex]::Escape($version))") {
            return $destination
        }
        Remove-Item -LiteralPath $destination -Force
    }

    $temporaryDirectory = Join-Path $CacheDirectory "restic-download-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
    try {
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/restic/restic/releases/tags/v$version" `
            -Headers @{ "User-Agent" = "KOReaderEcosystem-build" }
        $assets = @($release.assets | Where-Object { $_.name -eq $assetName })
        if ($assets.Count -ne 1 -or $assets[0].digest -ne $assetDigest) {
            throw "The pinned Restic release asset or its upstream SHA-256 digest changed."
        }

        $archive = Join-Path $temporaryDirectory $assetName
        Invoke-WebRequest -Uri $assets[0].browser_download_url -OutFile $archive
        $actualDigest = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ("sha256:$actualDigest" -ne $assetDigest) {
            throw "The downloaded Restic archive failed its SHA-256 verification."
        }

        $extractDirectory = Join-Path $temporaryDirectory "extract"
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDirectory
        $restic = Get-ChildItem -LiteralPath $extractDirectory -Filter "restic_*.exe" -File -Recurse |
            Select-Object -First 1
        if ($null -eq $restic) {
            throw "The verified Restic archive does not contain restic.exe."
        }

        $versionOutput = & $restic.FullName version 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch "restic $([regex]::Escape($version))") {
            throw "The downloaded Restic executable did not report the pinned version."
        }
        Copy-Item -LiteralPath $restic.FullName -Destination $destination
        return $destination
    }
    finally {
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
        }
    }
}
