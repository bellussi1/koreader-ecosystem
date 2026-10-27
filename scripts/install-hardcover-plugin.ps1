[CmdletBinding()]
param(
    [ValidateSet("install", "update")]
    [string]$Mode = "install",

    [Parameter(Mandatory = $true)]
    [string]$SourceDirectory,

    [string]$RollbackDirectory,
    [string]$UpdateManifestJson
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

    $matches = @(
        $Folder.Items() |
            Where-Object { [string]$_.Name -ieq $Name }
    )
    if ($matches.Count -eq 0) {
        if ($AllowMissing) { return $null }
        throw $MissingReason
    }
    if ($matches.Count -gt 1) {
        throw $MultipleReason
    }
    return $matches[0]
}

function Get-LocalFiles {
    param([string]$Root)

    $files = @(Get-ChildItem -LiteralPath $Root -File -Force -Recurse)
    if ($files.Count -eq 0) {
        throw "package_invalid"
    }

    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $entries = [System.Collections.Generic.List[object]]::new()
    $totalBytes = 0L
    foreach ($file in $files) {
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "package_invalid"
        }
        $relative = $file.FullName.Substring($Root.Length).TrimStart([char[]]@('\', '/')).Replace('/', '\')
        $parts = $relative.Split('\')
        $invalidParts = @(
            $parts | Where-Object {
                [string]::IsNullOrWhiteSpace($_) -or $_ -in @('.', '..') -or $_ -match '[<>:"|?*]'
            }
        )
        if ([string]::IsNullOrWhiteSpace($relative) -or $invalidParts.Count -gt 0) {
            throw "package_invalid"
        }
        if (-not $seen.Add($relative)) {
            throw "package_invalid"
        }
        $size = [long]$file.Length
        if ($size -lt 0) { throw "package_invalid" }
        $totalBytes += $size
        [void]$entries.Add([pscustomobject]@{
            FullName = $file.FullName
            Name = $file.Name
            RelativePath = $relative
            ParentPath = if ($parts.Count -gt 1) { ($parts[0..($parts.Count - 2)] -join '\') } else { '' }
            Size = $size
        })
    }

    $directories = @(Get-ChildItem -LiteralPath $Root -Directory -Force -Recurse)
    $directoryPaths = [System.Collections.Generic.List[string]]::new()
    foreach ($directory in $directories) {
        if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "package_invalid"
        }
        $relative = $directory.FullName.Substring($Root.Length).TrimStart([char[]]@('\', '/')).Replace('/', '\')
        $directoryPaths.Add($relative)
    }

    return @{
        Files = $entries
        Directories = $directoryPaths
        TotalBytes = $totalBytes
    }
}

function Get-RemoteFolder {
    param([object]$RootFolder, [string]$RelativePath)

    $current = $RootFolder
    if ([string]::IsNullOrEmpty($RelativePath)) { return $current }
    foreach ($component in $RelativePath.Split('\')) {
        $item = Get-SingleItem -Folder $current -Name $component -MissingReason "copy_failed" -MultipleReason "copy_failed"
        if (-not [bool]$item.IsFolder) { throw "copy_failed" }
        $current = $item.GetFolder
        if ($null -eq $current) { throw "copy_failed" }
    }
    return $current
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

function Test-FileCanBeOpenedExclusively {
    param([string]$Path)

    $stream = $null
    try {
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
        return $true
    }
    catch [IO.IOException] {
        return $false
    }
    catch [UnauthorizedAccessException] {
        return $false
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Wait-RemoteFile {
    param(
        [object]$Folder,
        [string]$Name,
        [long]$ExpectedSize,
        [int]$TimeoutSeconds = 300
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $stableSamples = 0
    while ([DateTime]::UtcNow -lt $deadline) {
        $items = @($Folder.Items() | Where-Object { [string]$_.Name -ieq $Name -and -not [bool]$_.IsFolder })
        if ($items.Count -gt 1) { throw "copy_failed" }
        if ($items.Count -eq 1 -and (Get-RemoteSize -Item $items[0]) -eq $ExpectedSize) {
            $stableSamples++
            if ($stableSamples -ge 2) { return $items[0] }
        }
        else {
            $stableSamples = 0
        }
        Start-Sleep -Milliseconds 250
    }
    throw "copy_failed"
}

function Wait-LocalFile {
    param([string]$Path, [long]$ExpectedSize, [int]$TimeoutSeconds = 300)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $stableSamples = 0
    $lastSize = -1L
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $size = [long](Get-Item -LiteralPath $Path).Length
            $complete = $size -eq $ExpectedSize -and (Test-FileCanBeOpenedExclusively -Path $Path)
            if ($complete -and $size -eq $lastSize) { $stableSamples++ }
            elseif ($complete) { $stableSamples = 1 }
            else { $stableSamples = 0 }
            $lastSize = $size
            if ($stableSamples -ge 2) { return }
        }
        else {
            $stableSamples = 0
            $lastSize = -1L
        }
        Start-Sleep -Milliseconds 250
    }
    throw "copy_failed"
}

function Get-RemoteFileSha256 {
    param(
        [object]$Shell,
        [object]$Folder,
        [object]$Item,
        [string]$FileName,
        [long]$ExpectedSize,
        [string]$TemporaryRoot
    )

    $readbackDirectory = Join-Path $TemporaryRoot ([guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $readbackDirectory)
    try {
        $readbackFolder = $Shell.Namespace($readbackDirectory)
        if ($null -eq $readbackFolder) { throw "copy_failed" }
        $readbackFolder.CopyHere($Item, 0x414)
        $readbackPath = Join-Path $readbackDirectory $FileName
        Wait-LocalFile -Path $readbackPath -ExpectedSize $ExpectedSize
        return (Get-FileHash -LiteralPath $readbackPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    finally {
        if (Test-Path -LiteralPath $readbackDirectory) {
            Remove-Item -LiteralPath $readbackDirectory -Recurse -Force
        }
    }
}

function Invoke-SafePluginUpdate {
    param(
        [object]$Shell,
        [object]$PluginFolder,
        [string]$SourceDirectory,
        [string]$RollbackDirectory,
        [object[]]$Manifest
    )

    $updateVerifyRoot = Join-Path (Split-Path -Parent $SourceDirectory) (".mtp-update-verify-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $updateVerifyRoot)
    $records = [System.Collections.Generic.List[object]]::new()
    try {
        foreach ($entry in $Manifest) {
            $parts = ([string]$entry.path).Split('/')
            $leaf = $parts[-1]
            $parentPath = if ($parts.Count -gt 1) { $parts[0..($parts.Count - 2)] -join '\' } else { '' }
            $folder = Get-RemoteFolder -RootFolder $PluginFolder -RelativePath $parentPath
            $item = Get-SingleItem -Folder $folder -Name $leaf -MissingReason "plugin_version_mismatch" -MultipleReason "plugin_version_mismatch"
            if ([bool]$item.IsFolder) { throw "plugin_version_mismatch" }
            $size = Get-RemoteSize -Item $item
            if ($size -le 0) { throw "plugin_version_mismatch" }
            $currentHash = Get-RemoteFileSha256 -Shell $Shell -Folder $folder -Item $item -FileName $leaf -ExpectedSize $size -TemporaryRoot $updateVerifyRoot
            $acceptedHashes = @([string]$entry.original_sha256, [string]$entry.patched_sha256)
            if (-not [string]::IsNullOrWhiteSpace([string]$entry.legacy_sha256)) {
                $acceptedHashes += [string]$entry.legacy_sha256
            }
            if (-not ($acceptedHashes -ccontains $currentHash)) {
                throw "plugin_version_mismatch"
            }
            [void]$records.Add([pscustomobject]@{
                Path = [string]$entry.path
                ParentPath = $parentPath
                Leaf = $leaf
                Folder = $folder
                Item = $item
                CurrentSize = $size
                CurrentHash = $currentHash
                PatchedHash = [string]$entry.patched_sha256
                PatchedSize = [long]$entry.source_size
                RemoteBackupName = ([IO.Path]::GetFileNameWithoutExtension($leaf) + ".kre-backup-" + [guid]::NewGuid().ToString("N") + [IO.Path]::GetExtension($leaf))
                RemoteFailedName = ([IO.Path]::GetFileNameWithoutExtension($leaf) + ".kre-failed-" + [guid]::NewGuid().ToString("N") + [IO.Path]::GetExtension($leaf))
                LocalBackupPath = (Join-Path $RollbackDirectory ($entry.path.Replace('/', '\')))
                Touched = $false
            })
        }

        $totalBytes = [long](($records | Measure-Object -Property PatchedSize -Sum).Sum)
        if (@($records | Where-Object { $_.CurrentHash -ne $_.PatchedHash }).Count -eq 0) {
            return @{
                Action = "already_current"
                RollbackBackupId = $null
                RollbackBackupHashes = @()
                TotalBytes = $totalBytes
            }
        }

        # Capture every current file locally before changing any file on the Kindle.
        foreach ($record in $records) {
            $localParent = Split-Path -Parent $record.LocalBackupPath
            if (-not (Test-Path -LiteralPath $localParent -PathType Container)) {
                [void](New-Item -ItemType Directory -Path $localParent -Force)
            }
            $localFolder = $Shell.Namespace($localParent)
            if ($null -eq $localFolder) { throw "rollback_failed" }
            $localFolder.CopyHere($record.Item, 0x414)
            Wait-LocalFile -Path $record.LocalBackupPath -ExpectedSize $record.CurrentSize
            $localHash = (Get-FileHash -LiteralPath $record.LocalBackupPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($localHash -cne $record.CurrentHash) { throw "rollback_failed" }
        }

        # Keep a file-level rollback copy on the Kindle and verify every replacement.
        foreach ($record in $records) {
            if ($record.CurrentHash -ceq $record.PatchedHash) { continue }
            $record.Touched = $true
            if ($null -ne (Get-SingleItem -Folder $record.Folder -Name $record.RemoteBackupName -MissingReason "copy_failed" -MultipleReason "copy_failed" -AllowMissing)) {
                throw "copy_failed"
            }
            $record.Item.Name = $record.RemoteBackupName
            $remoteBackup = Wait-RemoteFile -Folder $record.Folder -Name $record.RemoteBackupName -ExpectedSize $record.CurrentSize
            $backupHash = Get-RemoteFileSha256 -Shell $Shell -Folder $record.Folder -Item $remoteBackup -FileName $record.RemoteBackupName -ExpectedSize $record.CurrentSize -TemporaryRoot $updateVerifyRoot
            if ($backupHash -cne $record.CurrentHash) { throw "copy_failed" }

            $sourceFolderRoot = $Shell.Namespace($SourceDirectory)
            if ($null -eq $sourceFolderRoot) { throw "copy_failed" }
            $sourceFolder = Get-RemoteFolder -RootFolder $sourceFolderRoot -RelativePath $record.ParentPath
            $sourceItem = Get-SingleItem -Folder $sourceFolder -Name $record.Leaf -MissingReason "copy_failed" -MultipleReason "copy_failed"
            if ([bool]$sourceItem.IsFolder) { throw "copy_failed" }
            $record.Folder.CopyHere($sourceItem, 0x414)
            $updatedRemote = Wait-RemoteFile -Folder $record.Folder -Name $record.Leaf -ExpectedSize $record.PatchedSize
            $updatedHash = Get-RemoteFileSha256 -Shell $Shell -Folder $record.Folder -Item $updatedRemote -FileName $record.Leaf -ExpectedSize $record.PatchedSize -TemporaryRoot $updateVerifyRoot
            if ($updatedHash -cne $record.PatchedHash) { throw "source_changed" }
        }

        $backupHashes = @(
            foreach ($record in $records) {
                [ordered]@{ path = $record.Path; sha256 = $record.CurrentHash }
            }
        )
        return @{
            Action = "updated"
            RollbackBackupId = (Split-Path -Leaf $RollbackDirectory)
            RollbackBackupHashes = $backupHashes
            TotalBytes = $totalBytes
        }
    }
    catch {
        try {
            for ($index = $records.Count - 1; $index -ge 0; $index--) {
                $record = $records[$index]
                if (-not $record.Touched) { continue }
                $current = Get-SingleItem -Folder $record.Folder -Name $record.Leaf -MissingReason "copy_failed" -MultipleReason "copy_failed" -AllowMissing
                $remoteBackup = Get-SingleItem -Folder $record.Folder -Name $record.RemoteBackupName -MissingReason "copy_failed" -MultipleReason "copy_failed" -AllowMissing
                if ($null -ne $current) {
                    $currentSize = Get-RemoteSize -Item $current
                    $currentHash = if ($currentSize -gt 0) {
                        Get-RemoteFileSha256 -Shell $Shell -Folder $record.Folder -Item $current -FileName $record.Leaf -ExpectedSize $currentSize -TemporaryRoot $updateVerifyRoot
                    } else { $null }
                    if ($currentHash -ceq $record.CurrentHash) { continue }
                    $current.Name = $record.RemoteFailedName
                    [void](Wait-RemoteFile -Folder $record.Folder -Name $record.RemoteFailedName -ExpectedSize $currentSize)
                }

                if ($null -ne $remoteBackup) {
                    $backupSize = Get-RemoteSize -Item $remoteBackup
                    $backupHash = Get-RemoteFileSha256 -Shell $Shell -Folder $record.Folder -Item $remoteBackup -FileName $record.RemoteBackupName -ExpectedSize $backupSize -TemporaryRoot $updateVerifyRoot
                    if ($backupHash -cne $record.CurrentHash) { throw "rollback_failed" }
                    $remoteBackup.Name = $record.Leaf
                }
                else {
                    $localParent = Split-Path -Parent $record.LocalBackupPath
                    $localFolder = $Shell.Namespace($localParent)
                    if ($null -eq $localFolder) { throw "rollback_failed" }
                    $localBackup = $localFolder.ParseName($record.Leaf)
                    if ($null -eq $localBackup) { throw "rollback_failed" }
                    $record.Folder.CopyHere($localBackup, 0x414)
                }
                $restored = Wait-RemoteFile -Folder $record.Folder -Name $record.Leaf -ExpectedSize $record.CurrentSize
                $restoredHash = Get-RemoteFileSha256 -Shell $Shell -Folder $record.Folder -Item $restored -FileName $record.Leaf -ExpectedSize $record.CurrentSize -TemporaryRoot $updateVerifyRoot
                if ($restoredHash -cne $record.CurrentHash) { throw "rollback_failed" }
            }
        }
        catch {
            throw "rollback_failed"
        }
        throw "copy_failed"
    }
    finally {
        if (Test-Path -LiteralPath $updateVerifyRoot) {
            Remove-Item -LiteralPath $updateVerifyRoot -Recurse -Force
        }
    }
}

$reasonCode = "copy_failed"
$status = "error"
$fileCount = 0
$totalBytes = 0L
$contentVerified = $false
$verifyRoot = $null
$action = if ($Mode -eq "update") { "updated" } else { "installed" }
$rollbackBackupId = $null
$rollbackBackupHashes = @()

try {
    if (-not (Test-FullyQualifiedWindowsPath -Path $SourceDirectory)) { throw "package_invalid" }
    $SourceDirectory = (Resolve-Path -LiteralPath $SourceDirectory).Path
    if (-not (Test-Path -LiteralPath $SourceDirectory -PathType Container)) { throw "package_invalid" }
    $source = Get-LocalFiles -Root $SourceDirectory
    if ($Mode -eq "update") {
        if (-not (Test-FullyQualifiedWindowsPath -Path $RollbackDirectory) -or
            [string]::IsNullOrWhiteSpace($UpdateManifestJson) -or
            -not (Test-Path -LiteralPath $RollbackDirectory -PathType Container)) {
            throw "package_invalid"
        }
        $rollbackItem = Get-Item -LiteralPath $RollbackDirectory -Force
        if (($rollbackItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            @(Get-ChildItem -LiteralPath $RollbackDirectory -Force).Count -ne 0) {
            throw "package_invalid"
        }
        try { $rawManifest = @(ConvertFrom-Json -InputObject $UpdateManifestJson) }
        catch { throw "package_invalid" }
        if ($rawManifest.Count -lt 1 -or $rawManifest.Count -gt 32) { throw "package_invalid" }
        $sourceByPath = @{}
        foreach ($sourceEntry in $source.Files) {
            $sourceByPath[$sourceEntry.RelativePath.Replace('\', '/')] = $sourceEntry
        }
        $seenManifestPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        $updateManifest = [System.Collections.Generic.List[object]]::new()
        $totalBytes = 0L
        foreach ($rawEntry in $rawManifest) {
            $relative = [string]$rawEntry.path
            $parts = $relative.Split('/')
            if ([string]::IsNullOrWhiteSpace($relative) -or $relative.Contains('\') -or
                $relative.StartsWith('/') -or $parts.Count -lt 1 -or
                @($parts | Where-Object { [string]::IsNullOrWhiteSpace($_) -or $_ -in @('.', '..') -or $_ -match '[<>:"|?*]' }).Count -gt 0 -or
                [IO.Path]::GetExtension($relative) -ine '.lua' -or
                -not $seenManifestPaths.Add($relative) -or
                [string]$rawEntry.original_sha256 -notmatch '^[0-9a-f]{64}$' -or
                [string]$rawEntry.patched_sha256 -notmatch '^[0-9a-f]{64}$' -or
                (-not [string]::IsNullOrWhiteSpace([string]$rawEntry.legacy_sha256) -and
                    [string]$rawEntry.legacy_sha256 -notmatch '^[0-9a-f]{64}$')) {
                throw "package_invalid"
            }
            $sourceEntry = $sourceByPath[$relative]
            if ($null -eq $sourceEntry) { throw "package_invalid" }
            $patchedHash = (Get-FileHash -LiteralPath $sourceEntry.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($patchedHash -cne ([string]$rawEntry.patched_sha256).ToLowerInvariant()) { throw "package_invalid" }
            [void]$updateManifest.Add([pscustomobject]@{
                path = $relative
                original_sha256 = ([string]$rawEntry.original_sha256).ToLowerInvariant()
                patched_sha256 = $patchedHash
                legacy_sha256 = if ([string]::IsNullOrWhiteSpace([string]$rawEntry.legacy_sha256)) { $null } else { ([string]$rawEntry.legacy_sha256).ToLowerInvariant() }
                source_size = [long]$sourceEntry.Size
            })
            $totalBytes += [long]$sourceEntry.Size
        }
        $fileCount = $updateManifest.Count
    }
    else {
        $fileCount = $source.Files.Count
        $totalBytes = [long]$source.TotalBytes
    }

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

    $koreader = Get-SingleItem -Folder $storageFolder -Name "koreader" -MissingReason "koreader_not_found" -MultipleReason "koreader_not_found"
    if (-not [bool]$koreader.IsFolder) { throw "koreader_not_found" }
    $koreaderFolder = $koreader.GetFolder
    if ($null -eq $koreaderFolder) { throw "koreader_not_found" }
    $plugins = Get-SingleItem -Folder $koreaderFolder -Name "plugins" -MissingReason "plugins_not_found" -MultipleReason "plugins_not_found"
    if (-not [bool]$plugins.IsFolder) { throw "plugins_not_found" }
    $pluginsFolder = $plugins.GetFolder
    if ($null -eq $pluginsFolder) { throw "plugins_not_found" }

    $existing = Get-SingleItem -Folder $pluginsFolder -Name "hardcoverapp.koplugin" -MissingReason "copy_failed" -MultipleReason "copy_failed" -AllowMissing
    if ($Mode -eq "update") {
        if ($null -eq $existing) { throw "plugin_not_installed" }
        if (-not [bool]$existing.IsFolder) { throw "plugin_version_mismatch" }
        $pluginFolder = $existing.GetFolder
        if ($null -eq $pluginFolder) { throw "plugin_version_mismatch" }
        $update = Invoke-SafePluginUpdate -Shell $shell -PluginFolder $pluginFolder -SourceDirectory $SourceDirectory -RollbackDirectory $RollbackDirectory -Manifest $updateManifest.ToArray()
        $action = [string]$update.Action
        $rollbackBackupId = $update.RollbackBackupId
        $rollbackBackupHashes = @($update.RollbackBackupHashes)
        $totalBytes = [long]$update.TotalBytes
    }
    else {
        if ($null -ne $existing) { throw "destination_exists" }
        [void]$pluginsFolder.NewFolder("hardcoverapp.koplugin")
        $targetItem = Get-SingleItem -Folder $pluginsFolder -Name "hardcoverapp.koplugin" -MissingReason "copy_failed" -MultipleReason "copy_failed"
        if (-not [bool]$targetItem.IsFolder) { throw "copy_failed" }
        $targetFolder = $targetItem.GetFolder
        if ($null -eq $targetFolder) { throw "copy_failed" }

        $remoteFolders = @{ "" = $targetFolder }
        foreach ($relativeDirectory in @($source.Directories | Sort-Object { $_.Split('\').Count }, { $_.ToLowerInvariant() })) {
            $parts = $relativeDirectory.Split('\')
            $parentPath = if ($parts.Count -gt 1) { $parts[0..($parts.Count - 2)] -join '\' } else { '' }
            $leaf = $parts[-1]
            $parentFolder = $remoteFolders[$parentPath]
            if ($null -eq $parentFolder) { throw "copy_failed" }
            [void]$parentFolder.NewFolder($leaf)
            $directoryItem = Get-SingleItem -Folder $parentFolder -Name $leaf -MissingReason "copy_failed" -MultipleReason "copy_failed"
            if (-not [bool]$directoryItem.IsFolder) { throw "copy_failed" }
            $remoteFolders[$relativeDirectory] = $directoryItem.GetFolder
            if ($null -eq $remoteFolders[$relativeDirectory]) { throw "copy_failed" }
        }

        $verifyRoot = Join-Path (Split-Path -Parent $SourceDirectory) (".mtp-verify-" + [guid]::NewGuid().ToString("N"))
        [void](New-Item -ItemType Directory -Path $verifyRoot)
        foreach ($entry in $source.Files) {
            $destinationFolder = $remoteFolders[$entry.ParentPath]
            if ($null -eq $destinationFolder) { throw "copy_failed" }
            $localParent = Split-Path -Parent $entry.FullName
            $localFolder = $shell.Namespace($localParent)
            if ($null -eq $localFolder) { throw "copy_failed" }
            $localItem = $localFolder.ParseName($entry.Name)
            if ($null -eq $localItem) { throw "copy_failed" }
            $destinationFolder.CopyHere($localItem, 0x414)
            $remoteItem = Wait-RemoteFile -Folder $destinationFolder -Name $entry.Name -ExpectedSize $entry.Size

            $readbackDirectory = Join-Path $verifyRoot ([guid]::NewGuid().ToString("N"))
            [void](New-Item -ItemType Directory -Path $readbackDirectory)
            try {
                $readbackFolder = $shell.Namespace($readbackDirectory)
                if ($null -eq $readbackFolder) { throw "copy_failed" }
                $readbackFolder.CopyHere($remoteItem, 0x414)
                $readbackPath = Join-Path $readbackDirectory $entry.Name
                Wait-LocalFile -Path $readbackPath -ExpectedSize $entry.Size
                $sourceHash = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash
                $readbackHash = (Get-FileHash -LiteralPath $readbackPath -Algorithm SHA256).Hash
                if ($sourceHash -cne $readbackHash) { throw "source_changed" }
            }
            finally {
                if (Test-Path -LiteralPath $readbackDirectory) {
                    Remove-Item -LiteralPath $readbackDirectory -Recurse -Force
                }
            }
        }
    }

    $contentVerified = $true
    $status = "ok"
    $reasonCode = $null
}
catch {
    $allowedReasons = @(
        "device_not_found", "multiple_devices", "storage_not_found", "multiple_storages",
        "koreader_not_found", "plugins_not_found", "destination_exists", "package_invalid",
        "copy_failed", "source_changed", "plugin_not_installed", "plugin_version_mismatch",
        "rollback_failed"
    )
    $candidate = [string]$_.Exception.Message
    if ($candidate -in $allowedReasons) { $reasonCode = $candidate }
    else { $reasonCode = "copy_failed" }
}
finally {
    if ($null -ne $verifyRoot -and (Test-Path -LiteralPath $verifyRoot)) {
        try { Remove-Item -LiteralPath $verifyRoot -Recurse -Force }
        catch {
            $status = "error"
            $reasonCode = "copy_failed"
            $contentVerified = $false
        }
    }
}

$response = [ordered]@{
    schema_version = if ($Mode -eq "update") { 2 } else { 1 }
    status = $status
    reason_code = $reasonCode
    action = $action
    rollback_backup_id = $rollbackBackupId
    rollback_backup_hashes = @($rollbackBackupHashes)
    file_count = $fileCount
    total_bytes = $totalBytes
    content_verified = $contentVerified
}
[Console]::Out.WriteLine((ConvertTo-Json -InputObject $response -Compress))
if ($status -eq "ok") { exit 0 }
exit 1
