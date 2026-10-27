[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [string]$TargetName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Test-FullyQualifiedWindowsPath {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    return $Path -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

function Get-SingleItem {
    param(
        [object]$Folder,
        [string]$Name,
        [string]$MissingReason,
        [string]$MultipleReason,
        [switch]$AllowMissing
    )
    $matches = @($Folder.Items() | Where-Object { [string]$_.Name -ieq $Name })
    if ($matches.Count -eq 0) {
        if ($AllowMissing) { return $null }
        throw $MissingReason
    }
    if ($matches.Count -gt 1) { throw $MultipleReason }
    return $matches[0]
}

function Get-RemoteSize {
    param([object]$Item)
    try {
        $raw = $Item.ExtendedProperty("System.Size")
        if ($null -eq $raw) { $raw = $Item.Size }
        return [long]$raw
    }
    catch {
        return -1L
    }
}

function Wait-NewRemoteFile {
    param([object]$Folder, [object]$ExistingNames, [long]$ExpectedSize)
    $deadline = [DateTime]::UtcNow.AddSeconds(600)
    $stableSamples = 0
    $lastName = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $newItems = @(
            $Folder.Items() |
                Where-Object {
                    -not [bool]$_.IsFolder -and
                    -not $ExistingNames.Contains([string]$_.Name) -and
                    (Get-RemoteSize -Item $_) -eq $ExpectedSize
                }
        )
        if ($newItems.Count -gt 1) { throw "copy_failed" }
        if ($newItems.Count -eq 1) {
            $currentName = [string]$newItems[0].Name
            if ($currentName -ceq $lastName) { $stableSamples++ }
            else { $stableSamples = 1 }
            $lastName = $currentName
            if ($stableSamples -ge 3) { return $newItems[0] }
        }
        else {
            $stableSamples = 0
            $lastName = $null
        }
        Start-Sleep -Milliseconds 350
    }
    throw "copy_failed"
}

function Wait-LocalFile {
    param([string]$Path, [long]$ExpectedSize)
    $deadline = [DateTime]::UtcNow.AddSeconds(600)
    $stableSamples = 0
    $lastSize = -1L
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $currentSize = [long](Get-Item -LiteralPath $Path).Length
            if ($currentSize -eq $ExpectedSize -and $currentSize -eq $lastSize) {
                $stableSamples++
            }
            elseif ($currentSize -eq $ExpectedSize) {
                $stableSamples = 1
            }
            else {
                $stableSamples = 0
            }
            $lastSize = $currentSize
            if ($stableSamples -ge 3) { return }
        }
        else {
            $stableSamples = 0
            $lastSize = -1L
        }
        Start-Sleep -Milliseconds 350
    }
    throw "copy_failed"
}

function Get-RemoteHash {
    param([object]$Shell, [object]$Item, [string]$Name, [long]$ExpectedSize, [string]$Root)
    $readbackDirectory = Join-Path $Root ([guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $readbackDirectory)
    try {
        $readbackFolder = $Shell.Namespace($readbackDirectory)
        if ($null -eq $readbackFolder) { throw "copy_failed" }
        $readbackFolder.CopyHere($Item, 0x414)
        $readbackPath = Join-Path $readbackDirectory $Name
        Wait-LocalFile -Path $readbackPath -ExpectedSize $ExpectedSize
        return (Get-FileHash -LiteralPath $readbackPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    finally {
        if (Test-Path -LiteralPath $readbackDirectory) {
            Remove-Item -LiteralPath $readbackDirectory -Recurse -Force
        }
    }
}

$exitCode = 1
$copied = $false
$temporaryRoot = $null
$response = $null
try {
    if (-not (Test-FullyQualifiedWindowsPath -Path $SourcePath) -or
        -not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "invalid_source"
    }
    $sourceItem = Get-Item -LiteralPath $SourcePath -Force
    if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $sourceItem.Length -le 0) {
        throw "invalid_source"
    }
    $sourcePathResolved = [System.IO.Path]::GetFullPath($sourceItem.FullName)
    $extension = [System.IO.Path]::GetExtension($sourcePathResolved).ToLowerInvariant()
    $allowedExtensions = @(".azw", ".azw3", ".cbz", ".epub", ".fb2", ".mobi", ".pdf", ".txt")
    if ($extension -notin $allowedExtensions) { throw "invalid_source" }

    $invalidName = [string]::IsNullOrWhiteSpace($TargetName) -or
        $TargetName.Length -gt 180 -or
        $TargetName -match '[<>:"/\\|?*\x00-\x1f\x7f]' -or
        $TargetName -match '[ .]$' -or
        $TargetName -in @(".", "..") -or
        [System.IO.Path]::GetExtension($TargetName).ToLowerInvariant() -cne $extension -or
        [System.IO.Path]::GetFileNameWithoutExtension($TargetName).ToUpperInvariant() -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$'
    if ($invalidName) { throw "invalid_name" }

    $sourceHash = (Get-FileHash -LiteralPath $sourcePathResolved -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceSize = [long]$sourceItem.Length
    $shell = New-Object -ComObject Shell.Application
    $computer = $shell.Namespace(17)
    if ($null -eq $computer) { throw "device_not_found" }
    $devices = @($computer.Items() | Where-Object { ([string]$_.Name) -match '(?i)kindle' })
    if ($devices.Count -eq 0) { throw "device_not_found" }
    if ($devices.Count -ne 1) { throw "multiple_devices" }

    $deviceFolder = $devices[0].GetFolder
    if ($null -eq $deviceFolder) { throw "storage_not_found" }
    $storage = Get-SingleItem -Folder $deviceFolder -Name "Internal Storage" -MissingReason "storage_not_found" -MultipleReason "multiple_storages"
    if (-not [bool]$storage.IsFolder) { throw "storage_not_found" }
    $storageFolder = $storage.GetFolder
    if ($null -eq $storageFolder) { throw "storage_not_found" }

    $documents = Get-SingleItem -Folder $storageFolder -Name "documents" -MissingReason "copy_failed" -MultipleReason "copy_failed" -AllowMissing
    if ($null -eq $documents) {
        [void]$storageFolder.NewFolder("documents")
        $documents = Get-SingleItem -Folder $storageFolder -Name "documents" -MissingReason "copy_failed" -MultipleReason "copy_failed"
    }
    if (-not [bool]$documents.IsFolder) { throw "copy_failed" }
    $documentsFolder = $documents.GetFolder
    if ($null -eq $documentsFolder) { throw "copy_failed" }

    $books = Get-SingleItem -Folder $documentsFolder -Name "Books" -MissingReason "copy_failed" -MultipleReason "copy_failed" -AllowMissing
    if ($null -eq $books) {
        [void]$documentsFolder.NewFolder("Books")
        $books = Get-SingleItem -Folder $documentsFolder -Name "Books" -MissingReason "copy_failed" -MultipleReason "copy_failed"
    }
    if (-not [bool]$books.IsFolder) { throw "destination_exists" }
    $destinationFolder = $books.GetFolder
    if ($null -eq $destinationFolder) { throw "copy_failed" }

    $existingNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($item in $destinationFolder.Items()) { [void]$existingNames.Add([string]$item.Name) }
    $existing = Get-SingleItem -Folder $destinationFolder -Name $TargetName -MissingReason "copy_failed" -MultipleReason "destination_exists" -AllowMissing
    $temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("kre-send-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $temporaryRoot)
    $stagedSource = Join-Path $temporaryRoot $TargetName
    [System.IO.File]::Copy($sourcePathResolved, $stagedSource, $false)
    if ((Get-FileHash -LiteralPath $stagedSource -Algorithm SHA256).Hash.ToLowerInvariant() -cne $sourceHash) {
        throw "invalid_source"
    }
    if ($null -ne $existing) {
        if ([bool]$existing.IsFolder -or (Get-RemoteSize -Item $existing) -ne $sourceSize) {
            throw "destination_exists"
        }
        $existingHash = Get-RemoteHash -Shell $shell -Item $existing -Name $TargetName -ExpectedSize $sourceSize -Root $temporaryRoot
        if ($existingHash -cne $sourceHash) { throw "destination_exists" }
        $response = [ordered]@{
            schema_version = 1; mode = "send"; status = "ok"; reason_code = $null
            filename = $TargetName; size = $sourceSize; sha256 = $sourceHash
            verified = $true; already_present = $true; copied = $false
        }
        $exitCode = 0
    }
    else {
        $localFolder = $shell.Namespace($temporaryRoot)
        if ($null -eq $localFolder) { throw "copy_failed" }
        $localItem = $localFolder.ParseName($TargetName)
        if ($null -eq $localItem) { throw "copy_failed" }
        $copied = $true
        $destinationFolder.CopyHere($localItem, 0x41C)
        $remoteItem = Wait-NewRemoteFile -Folder $destinationFolder -ExistingNames $existingNames -ExpectedSize $sourceSize
        if ([string]$remoteItem.Name -ine $TargetName) { throw "destination_exists" }
        $actualHash = Get-RemoteHash -Shell $shell -Item $remoteItem -Name $TargetName -ExpectedSize $sourceSize -Root $temporaryRoot
        if ($actualHash -cne $sourceHash) { throw "content_mismatch" }
        $response = [ordered]@{
            schema_version = 1; mode = "send"; status = "ok"; reason_code = $null
            filename = $TargetName; size = $sourceSize; sha256 = $sourceHash
            verified = $true; already_present = $false; copied = $true
        }
        $exitCode = 0
    }
}
catch {
    $reasonCode = [string]$_.Exception.Message
    $allowedReasons = @("device_not_found", "multiple_devices", "storage_not_found", "multiple_storages", "destination_exists", "invalid_source", "invalid_name", "copy_failed", "content_mismatch")
    if ($reasonCode -notin $allowedReasons) { $reasonCode = "copy_failed" }
    $response = [ordered]@{
        schema_version = 1; mode = "send"; status = "error"; reason_code = $reasonCode
        filename = $TargetName; size = 0; sha256 = $null; verified = $false
        already_present = $false; copied = $copied
    }
}
finally {
    if ($temporaryRoot -and (Test-Path -LiteralPath $temporaryRoot)) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

[Console]::Out.WriteLine(($response | ConvertTo-Json -Depth 5 -Compress))
exit $exitCode
