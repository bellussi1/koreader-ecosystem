[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("presence", "inventory", "stage", "stage-readings")]
    [string]$Mode,

    [string]$Destination
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$script:SchemaVersion = 4

function ConvertTo-AsciiJson {
    param([object]$InputObject)

    $json = ConvertTo-Json -InputObject $InputObject -Depth 5 -Compress
    $escaped = [System.Text.StringBuilder]::new()
    foreach ($character in $json.ToCharArray()) {
        if ([int]$character -le 0x7F) {
            [void]$escaped.Append($character)
        }
        else {
            [void]$escaped.AppendFormat('\u{0:X4}', [int]$character)
        }
    }
    return $escaped.ToString()
}

function New-Response {
    param(
        [string]$Mode,
        [string]$Status,
        [string]$ReasonCode,
        [int]$DeviceCount = 0,
        [int]$StorageCount = 0,
        [string[]]$Roots = @(),
        [long]$FileCount = 0,
        [long]$DirectoryCount = 0,
        [long]$TotalBytes = 0,
        [bool]$ContentVerified = $false,
        [string]$ContentVerificationMethod = $null,
        [bool]$SourcePathsVerified = $false,
        [hashtable]$SourceHashes = @{},
        [string[]]$SourceDirectories = @(),
        [hashtable]$SourcePathMap = @{}
    )

    return [ordered]@{
        schema_version = $script:SchemaVersion
        mode = $Mode
        status = $Status
        reason_code = $ReasonCode
        device_count = $DeviceCount
        storage_count = $StorageCount
        roots = @($Roots | Sort-Object { $_.ToLowerInvariant() })
        file_count = $FileCount
        directory_count = $DirectoryCount
        total_bytes = $TotalBytes
        content_verified = $ContentVerified
        content_verification_method = $ContentVerificationMethod
        source_paths_verified = $SourcePathsVerified
        source_sha256 = $SourceHashes
        source_directories = @($SourceDirectories)
        source_path_map = $SourcePathMap
    }
}

function Test-FullyQualifiedWindowsPath {
    param([AllowNull()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    return $Path -match '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))'
}

function Test-SafeComponent {
    param([string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name) -or $Name -in @(".", "..")) {
        return $false
    }
    return $true
}

function ConvertTo-SafeComponent {
    param([string]$Name)

    $escaped = [System.Text.StringBuilder]::new()
    $trimmedLength = $Name.TrimEnd(".", " ").Length
    for ($index = 0; $index -lt $Name.Length; $index++) {
        $character = $Name[$index]
        $code = [int]$character
        $mustEscape = $character -eq "~" -or $code -lt 32 -or $code -in @(34, 42, 47, 58, 60, 62, 63, 92, 124)
        if ($index -ge $trimmedLength) {
            $mustEscape = $true
        }
        if ($mustEscape) {
            [void]$escaped.AppendFormat("~u{0:X4}~", $code)
        }
        else {
            [void]$escaped.Append($character)
        }
    }

    $safeName = $escaped.ToString()
    if ($Name -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
        $first = [string]$safeName[0]
        $safeName = ([System.Text.StringBuilder]::new()).AppendFormat("~u{0:X4}~", [int]$first[0]).Append($safeName.Substring(1)).ToString()
    }
    return $safeName
}

function Add-MtpEntries {
    param(
        [object]$Folder,
        [string]$RelativeParent,
        [string]$SafeRelativeParent,
        [int]$Depth,
        [System.Collections.Generic.List[object]]$Entries,
        [System.Collections.Generic.HashSet[string]]$SeenPaths
    )

    if ($Depth -gt 100) {
        throw "source_changed"
    }

    foreach ($item in @($Folder.Items())) {
        $name = [string]$item.Name
        if (-not (Test-SafeComponent -Name $name)) {
            throw "invalid_component"
        }

        $relativePath = if ($RelativeParent) { "$RelativeParent\$name" } else { $name }
        $safeComponent = ConvertTo-SafeComponent -Name $name
        $safeRelativePath = if ($SafeRelativeParent) { "$SafeRelativeParent\$safeComponent" } else { $safeComponent }
        if (-not $SeenPaths.Add($safeRelativePath)) {
            throw "name_collision"
        }

        $isDirectory = [bool]$item.IsFolder
        if ($isDirectory) {
            [void]$Entries.Add([pscustomobject]@{
                RelativePath = $relativePath
                SafeRelativePath = $safeRelativePath
                RequiresEscaping = ($safeComponent -cne $name)
                IsDirectory = $true
                Size = [long]0
                Item = $item
            })
            $childFolder = $item.GetFolder
            if ($null -eq $childFolder) {
                throw "source_changed"
            }
            Add-MtpEntries -Folder $childFolder -RelativeParent $relativePath -SafeRelativeParent $safeRelativePath -Depth ($Depth + 1) -Entries $Entries -SeenPaths $SeenPaths
        }
        else {
            try {
                $rawSize = $item.ExtendedProperty("System.Size")
                if ($null -eq $rawSize) {
                    $rawSize = $item.Size
                }
                $size = [long]$rawSize
            }
            catch {
                throw "source_changed"
            }
            if ($size -lt 0) {
                throw "source_changed"
            }
            [void]$Entries.Add([pscustomobject]@{
                RelativePath = $relativePath
                SafeRelativePath = $safeRelativePath
                RequiresEscaping = ($safeComponent -cne $name)
                IsDirectory = $false
                Size = $size
                Item = $item
            })
        }
    }
}

function Get-TreeSummary {
    param([System.Collections.Generic.List[object]]$Entries)

    $files = @($Entries | Where-Object { -not $_.IsDirectory })
    $directories = @($Entries | Where-Object { $_.IsDirectory })
    $maximumFileBytes = if ($files.Count -gt 0) {
        [long](($files | Measure-Object -Property Size -Maximum).Maximum)
    }
    else { 0L }
    $roots = @(
        $directories |
            Where-Object { $_.SafeRelativePath.IndexOf("\") -lt 0 } |
            ForEach-Object { $_.SafeRelativePath }
    )

    return @{
        roots = $roots
        file_count = [long]$files.Count
        directory_count = [long]$directories.Count
        total_bytes = [long](($files | Measure-Object -Property Size -Sum).Sum)
        maximum_file_bytes = $maximumFileBytes
    }
}

function Select-KoreaderReadingEntries {
    param([System.Collections.Generic.List[object]]$Entries)

    $files = @($Entries | Where-Object { -not $_.IsDirectory })
    $databases = @(
        $files | Where-Object { (Split-Path -Leaf $_.RelativePath) -ieq "statistics.sqlite3" }
    )
    $companions = @(
        $files | Where-Object {
            (Split-Path -Leaf $_.RelativePath) -ieq "statistics.sqlite3-wal" -or
            (Split-Path -Leaf $_.RelativePath) -ieq "statistics.sqlite3-shm"
        }
    )

    if ($databases.Count -gt 1) {
        throw "reading_data_ambiguous"
    }
    if ($databases.Count -eq 0 -and $companions.Count -gt 0) {
        throw "reading_wal_incomplete"
    }

    $selectedPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    if ($databases.Count -eq 1) {
        $databasePath = [string]$databases[0].RelativePath
        $walPath = "$databasePath-wal"
        $shmPath = "$databasePath-shm"
        $wal = @($companions | Where-Object { $_.RelativePath -ieq $walPath })
        $shm = @($companions | Where-Object { $_.RelativePath -ieq $shmPath })
        if (($wal.Count -gt 0) -ne ($shm.Count -gt 0)) {
            throw "reading_wal_incomplete"
        }
        foreach ($companion in $companions) {
            if ($companion.RelativePath -ine $walPath -and $companion.RelativePath -ine $shmPath) {
                throw "reading_wal_incomplete"
            }
        }

        [void]$selectedPaths.Add($databasePath)
        if ($wal.Count -eq 1 -and $shm.Count -eq 1) {
            [void]$selectedPaths.Add($walPath)
            [void]$selectedPaths.Add($shmPath)
        }
    }

    $requiredDirectories = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $files) {
        $name = Split-Path -Leaf $file.RelativePath
        $parts = $file.RelativePath.Split("\")
        $insideSidecarDirectory = $false
        if ($name -match '(?i)^metadata\..+\.lua$') {
            for ($index = 0; $index -lt ($parts.Length - 1); $index++) {
                if ($parts[$index].EndsWith(".sdr", [System.StringComparison]::OrdinalIgnoreCase)) {
                    $insideSidecarDirectory = $true
                    break
                }
            }
        }
        if ($insideSidecarDirectory) {
            [void]$selectedPaths.Add([string]$file.RelativePath)
        }
        if ($selectedPaths.Contains([string]$file.RelativePath)) {
            $parent = Split-Path -Parent $file.RelativePath
            while ($parent) {
                [void]$requiredDirectories.Add($parent)
                $parent = Split-Path -Parent $parent
            }
        }
    }

    $selectedEntries = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $Entries) {
        if ((-not $entry.IsDirectory -and $selectedPaths.Contains([string]$entry.RelativePath)) -or
            ($entry.IsDirectory -and $requiredDirectories.Contains([string]$entry.RelativePath))) {
            [void]$selectedEntries.Add($entry)
        }
    }
    return ,$selectedEntries
}

function Test-SameSourceTree {
    param(
        [System.Collections.Generic.List[object]]$Before,
        [System.Collections.Generic.List[object]]$After
    )

    if ($Before.Count -ne $After.Count) {
        return $false
    }

    $beforeMap = [System.Collections.Generic.Dictionary[string, string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in $Before) {
        if ($beforeMap.ContainsKey($entry.RelativePath)) {
            throw "source_changed"
        }
        $beforeMap.Add($entry.RelativePath, "$($entry.IsDirectory)|$($entry.Size)")
    }
    $matchedPaths = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($entry in $After) {
        if (-not $matchedPaths.Add($entry.RelativePath)) {
            return $false
        }
        if (-not $beforeMap.ContainsKey($entry.RelativePath)) {
            return $false
        }
        if ($beforeMap[$entry.RelativePath] -ne "$($entry.IsDirectory)|$($entry.Size)") {
            return $false
        }
    }
    return $matchedPaths.Count -eq $beforeMap.Count
}

function Test-FileCanBeOpenedExclusively {
    param([string]$Path)

    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::None
        )
        return $true
    }
    catch [System.IO.IOException] {
        return $false
    }
    catch [System.UnauthorizedAccessException] {
        return $false
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Wait-ForSingleCopiedFile {
    param(
        [string]$Directory,
        [long]$ExpectedSize,
        [int]$TimeoutSeconds = 300
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $files = @(Get-ChildItem -LiteralPath $Directory -File -Force -ErrorAction SilentlyContinue)
        if ($files.Count -gt 1) {
            throw "copy_failed"
        }
        if ($files.Count -eq 1) {
            $currentSize = [long]$files[0].Length
            $complete = $currentSize -eq $ExpectedSize -and (Test-FileCanBeOpenedExclusively -Path $files[0].FullName)
            if ($complete) {
                return $files[0].FullName
            }
        }
        Start-Sleep -Milliseconds 100
    }
    throw "copy_failed"
}

function Get-ShellReadbackHash {
    param(
        [object]$Entry,
        [string]$MappedDestination,
        [object]$Shell
    )

    $stagedPath = Join-Path $MappedDestination $Entry.SafeRelativePath
    if (-not (Test-Path -LiteralPath $stagedPath -PathType Leaf)) {
        $script:WpdDiagnosticCode = "staged_file_missing"
        throw "source_changed"
    }
    if ((Get-Item -LiteralPath $stagedPath).Length -ne [long]$Entry.Size) {
        $script:WpdDiagnosticCode = "staged_size_mismatch"
        throw "source_changed"
    }
    if ([long]$Entry.Size -eq 0) {
        $script:UsedEmptyFileSizeVerification = $true
        return (Get-FileHash -LiteralPath $stagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $sampleDirectory = Join-Path $MappedDestination (".kre-verify-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $sampleDirectory)
    try {
        $sampleFolder = $Shell.Namespace($sampleDirectory)
        if ($null -eq $sampleFolder) {
            throw "copy_failed"
        }
        $sampleFolder.CopyHere($Entry.Item, 0x414)
        $samplePath = Wait-ForSingleCopiedFile -Directory $sampleDirectory `
            -ExpectedSize ([long]$Entry.Size) -TimeoutSeconds 3600
        $script:UsedShellContentReadback = $true
        return (Get-FileHash -LiteralPath $samplePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    finally {
        if (Test-Path -LiteralPath $sampleDirectory) {
            Remove-Item -LiteralPath $sampleDirectory -Recurse -Force
        }
    }
}

function Test-MtpContentMatchesStage {
    param(
        [System.Collections.Generic.List[object]]$Entries,
        [string]$MappedDestination,
        [object]$Shell
    )

    $verifiedHashes = @{}
    $helperPath = Join-Path $PSScriptRoot "WpdReadback.cs"
    if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        throw "copy_failed"
    }
    if ($null -eq ([System.Management.Automation.PSTypeName]'KreWpdReadback').Type) {
        Add-Type -Path $helperPath -ErrorAction Stop
    }

    $files = @($Entries | Where-Object { -not $_.IsDirectory })
    $emptyFiles = @($files | Where-Object { [long]$_.Size -eq 0 })
    $shellReadbackFiles = @($files | Where-Object {
        [long]$_.Size -gt 0 -and [KreWpdReadback]::RequiresShellReadback([string]$_.RelativePath)
    })
    $directFiles = @($files | Where-Object {
        [KreWpdReadback]::RequiresWpdReadback([string]$_.RelativePath, [UInt64]$_.Size)
    })

    foreach ($entry in $emptyFiles) {
        $stagedPath = Join-Path $MappedDestination $entry.SafeRelativePath
        if (-not (Test-Path -LiteralPath $stagedPath -PathType Leaf)) {
            $script:WpdDiagnosticCode = "empty_file_stage_missing"
            throw "source_changed"
        }
        if ((Get-Item -LiteralPath $stagedPath).Length -ne 0) {
            $script:WpdDiagnosticCode = "empty_file_stage_mismatch"
            throw "source_changed"
        }
        $emptyHash = (Get-FileHash -LiteralPath $stagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $verifiedHashes[$entry.SafeRelativePath.Replace("\", "/")] = $emptyHash
        $script:UsedEmptyFileSizeVerification = $true
    }

    if ($directFiles.Count -gt 0) {
        [string[]]$relativePaths = @($directFiles | ForEach-Object { [string]$_.RelativePath })
        [System.UInt64[]]$expectedSizes = @($directFiles | ForEach-Object { [System.UInt64]$_.Size })
        $readback = [KreWpdReadback]::ReadFiles($relativePaths, $expectedSizes)
        if ($readback.Status -ne "ok" -or $readback.Sha256.Count -ne $directFiles.Count) {
            $script:WpdDiagnosticCode = [string]$readback.DiagnosticCode
            $script:WpdDiagnosticIndex = [int]$readback.DiagnosticIndex
            $script:WpdDiagnosticDepth = [int]$readback.DiagnosticDepth
            if ([KreWpdReadback]::IsPathResolutionFailure([string]$readback.DiagnosticCode)) {
                foreach ($entry in $directFiles) {
                    $stagedPath = Join-Path $MappedDestination $entry.SafeRelativePath
                    $sourceHash = Get-ShellReadbackHash -Entry $entry `
                        -MappedDestination $MappedDestination -Shell $Shell
                    $stagedHash = (Get-FileHash -LiteralPath $stagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
                    if ($sourceHash -cne $stagedHash) {
                        $script:WpdDiagnosticCode = "shell_fallback_hash_mismatch"
                        throw "source_changed"
                    }
                    $verifiedHashes[$entry.SafeRelativePath.Replace("\", "/")] = $sourceHash
                }
            }
            else {
                $allowedReasons = @(
                    "device_not_found", "multiple_devices", "storage_not_found", "multiple_storages",
                    "source_changed", "copy_failed"
                )
                if ($readback.Status -in $allowedReasons) {
                    throw [string]$readback.Status
                }
                throw "copy_failed"
            }
        }

        if ($readback.Status -eq "ok" -and $readback.Sha256.Count -eq $directFiles.Count) {
            for ($index = 0; $index -lt $directFiles.Count; $index++) {
                $entry = $directFiles[$index]
                $stagedPath = Join-Path $MappedDestination $entry.SafeRelativePath
                if (-not (Test-Path -LiteralPath $stagedPath -PathType Leaf)) {
                    $script:WpdDiagnosticCode = "staged_file_missing"
                    throw "source_changed"
                }

                $sourceHash = [string]$readback.Sha256[$index]
                $stagedHash = (Get-FileHash -LiteralPath $stagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($sourceHash -cnotmatch '^[0-9a-f]{64}$' -or $sourceHash -cne $stagedHash) {
                    $script:WpdDiagnosticCode = "staged_hash_mismatch"
                    throw "source_changed"
                }
                $verifiedHashes[$entry.SafeRelativePath.Replace("\", "/")] = $sourceHash
            }
            $script:UsedWpdContentReadback = $true
        }
    }

    if ($shellReadbackFiles.Count -gt 0) {
        foreach ($entry in $shellReadbackFiles) {
            $stagedPath = Join-Path $MappedDestination $entry.SafeRelativePath
            $script:WpdDiagnosticCode = "shell_content_readback"
            $sourceHash = Get-ShellReadbackHash -Entry $entry `
                -MappedDestination $MappedDestination -Shell $Shell
            $stagedHash = (Get-FileHash -LiteralPath $stagedPath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($sourceHash -cne $stagedHash) {
                $script:WpdDiagnosticCode = "staged_hash_mismatch"
                throw "source_changed"
            }
            $verifiedHashes[$entry.SafeRelativePath.Replace("\", "/")] = $sourceHash
        }
    }

    return $verifiedHashes
}

$deviceCount = 0
$storageCount = 0
$stageDrive = $null
$exitCode = 0
$response = $null
$contentVerified = $false
$sourcePathsVerified = $false
$sourceHashes = @{}
$script:WpdDiagnosticCode = $null
$script:WpdDiagnosticIndex = -1
$script:WpdDiagnosticDepth = -1
$script:UsedShellContentReadback = $false
$script:UsedEmptyFileSizeVerification = $false
$script:UsedWpdContentReadback = $false

try {
    if ($Mode -in @("stage", "stage-readings")) {
        if (-not (Test-FullyQualifiedWindowsPath -Path $Destination)) {
            throw "invalid_destination"
        }
        if (Test-Path -LiteralPath $Destination) {
            throw "destination_exists"
        }
        $parent = Split-Path -Parent $Destination
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            throw "invalid_destination"
        }
    }

    $shell = New-Object -ComObject Shell.Application
    $computer = $shell.Namespace(17)
    if ($null -eq $computer) {
        throw "device_not_found"
    }

    $devices = @(
        $computer.Items() |
            Where-Object { ([string]$_.Name) -match '(?i)kindle' }
    )
    $deviceCount = $devices.Count
    if ($deviceCount -eq 0) {
        throw "device_not_found"
    }
    if ($deviceCount -ne 1) {
        throw "multiple_devices"
    }

    $deviceFolder = $devices[0].GetFolder
    if ($null -eq $deviceFolder) {
        throw "storage_not_found"
    }
    $storages = @(
        $deviceFolder.Items() |
            Where-Object { ([string]$_.Name) -match '(?i)^internal storage$' }
    )
    $storageCount = $storages.Count
    if ($storageCount -eq 0) {
        throw "storage_not_found"
    }
    if ($storageCount -ne 1) {
        throw "multiple_storages"
    }

    $storageFolder = $storages[0].GetFolder
    if ($null -eq $storageFolder) {
        throw "storage_not_found"
    }

    if ($Mode -eq "presence") {
        $summary = @{
            roots = @()
            file_count = 0L
            directory_count = 0L
            total_bytes = 0L
        }
    }
    else {
    $entries = [System.Collections.Generic.List[object]]::new()
    $seenPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    Add-MtpEntries -Folder $storageFolder -RelativeParent "" -SafeRelativeParent "" -Depth 0 -Entries $entries -SeenPaths $seenPaths
    $allEntries = $entries
    if ($Mode -eq "stage-readings") {
        $entries = Select-KoreaderReadingEntries -Entries $allEntries
    }
    $summary = Get-TreeSummary -Entries $entries

    if ($Mode -in @("stage", "stage-readings")) {
        $drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($Destination))
        $headroom = [Math]::Max(64MB, [long]($summary.total_bytes * 0.10))
        if ($drive.AvailableFreeSpace -lt ($summary.total_bytes + $summary.maximum_file_bytes + $headroom)) {
            throw "insufficient_space"
        }

        [void](New-Item -ItemType Directory -Path $Destination)
        $usedDriveLetters = @(
            [System.IO.DriveInfo]::GetDrives() |
                ForEach-Object {
                    if ($_.Name -match '^([A-Za-z]):\\$') {
                        $Matches[1].ToUpperInvariant()
                    }
                }
        )
        for ($letterCode = [int][char]"Z"; $letterCode -ge [int][char]"D"; $letterCode--) {
            $candidate = ([char]$letterCode).ToString() + ":"
            if ($candidate.TrimEnd(":") -notin $usedDriveLetters) {
                $stageDrive = $candidate
                break
            }
        }
        if (-not $stageDrive) {
            throw "short_path_unavailable"
        }

        $subst = Join-Path $env:WINDIR "System32\subst.exe"
        & $subst $stageDrive $Destination *> $null
        if ($LASTEXITCODE -ne 0) {
            $stageDrive = $null
            throw "short_path_unavailable"
        }
        $mappedDestination = $stageDrive + "\"

        $directories = @($entries | Where-Object { $_.IsDirectory } | Sort-Object { $_.SafeRelativePath.Split("\").Count }, SafeRelativePath)
        foreach ($entry in $directories) {
            $localDirectory = Join-Path $mappedDestination $entry.SafeRelativePath
            if ([System.IO.Path]::GetFullPath($localDirectory).Length -gt 259) {
                throw "path_too_long"
            }
            if (Test-Path -LiteralPath $localDirectory) {
                throw "destination_collision"
            }
            [void](New-Item -ItemType Directory -Path $localDirectory)
        }

        $files = @($entries | Where-Object { -not $_.IsDirectory } | Sort-Object SafeRelativePath)
        $targetFolders = @{}
        $pendingFiles = [System.Collections.Generic.List[object]]::new()
        foreach ($entry in $files) {
            $localPath = Join-Path $mappedDestination $entry.SafeRelativePath
            if ([System.IO.Path]::GetFullPath($localPath).Length -gt 259) {
                throw "path_too_long"
            }
            $localParent = Split-Path -Parent $localPath
            if (-not (Test-Path -LiteralPath $localParent -PathType Container) -or (Test-Path -LiteralPath $localPath)) {
                throw "destination_collision"
            }

            if ($entry.Size -eq 0) {
                [System.IO.File]::WriteAllBytes($localPath, [byte[]]@())
            }
            elseif ($entry.RequiresEscaping) {
                $temporaryDirectory = Join-Path $localParent (".kre-" + [guid]::NewGuid().ToString("N"))
                [void](New-Item -ItemType Directory -Path $temporaryDirectory)
                $temporaryFolder = $shell.Namespace($temporaryDirectory)
                if ($null -eq $temporaryFolder) {
                    throw "copy_failed"
                }
                $temporaryFolder.CopyHere($entry.Item, 0x414)
                $copiedPath = Wait-ForSingleCopiedFile -Directory $temporaryDirectory -ExpectedSize $entry.Size
                [System.IO.File]::Move($copiedPath, $localPath)
                [System.IO.Directory]::Delete($temporaryDirectory, $false)
            }
            else {
                if (-not $targetFolders.ContainsKey($localParent)) {
                    $targetFolders[$localParent] = $shell.Namespace($localParent)
                }
                $targetFolder = $targetFolders[$localParent]
                if ($null -eq $targetFolder) {
                    throw "copy_failed"
                }
                $targetFolder.CopyHere($entry.Item, 0x414)
                [void]$pendingFiles.Add([pscustomobject]@{
                    Path = $localPath
                    ExpectedSize = [long]$entry.Size
                    LastSize = -1L
                    StableSamples = 0
                })
            }
        }

        $copyDeadline = [DateTime]::UtcNow.AddHours(1)
        while ($pendingFiles.Count -gt 0 -and [DateTime]::UtcNow -lt $copyDeadline) {
            for ($index = $pendingFiles.Count - 1; $index -ge 0; $index--) {
                $pending = $pendingFiles[$index]
                if (Test-Path -LiteralPath $pending.Path -PathType Leaf) {
                    $currentSize = (Get-Item -LiteralPath $pending.Path).Length
                    $complete = $currentSize -eq $pending.ExpectedSize -and (Test-FileCanBeOpenedExclusively -Path $pending.Path)
                    if ($complete -and $currentSize -eq $pending.LastSize) {
                        $pending.StableSamples++
                    }
                    elseif ($complete) {
                        $pending.StableSamples = 1
                    }
                    else {
                        $pending.StableSamples = 0
                    }
                    $pending.LastSize = $currentSize
                    if ($pending.StableSamples -ge 2) {
                        $pendingFiles.RemoveAt($index)
                    }
                }
                else {
                    $pending.StableSamples = 0
                    $pending.LastSize = -1L
                }
            }
            if ($pendingFiles.Count -gt 0) {
                Start-Sleep -Milliseconds 400
            }
        }
        if ($pendingFiles.Count -gt 0) {
            throw "copy_failed"
        }

        $afterEntries = [System.Collections.Generic.List[object]]::new()
        $afterSeen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        $freshDeviceFolder = $devices[0].GetFolder
        $freshStorage = @(
            $freshDeviceFolder.Items() |
                Where-Object { ([string]$_.Name) -match '(?i)^internal storage$' }
        )
        if ($freshStorage.Count -ne 1) {
            throw "source_changed"
        }
        Add-MtpEntries -Folder $freshStorage[0].GetFolder -RelativeParent "" -SafeRelativeParent "" -Depth 0 -Entries $afterEntries -SeenPaths $afterSeen
        $expectedSourceEntries = if ($Mode -eq "stage-readings") { $allEntries } else { $entries }
        if (-not (Test-SameSourceTree -Before $expectedSourceEntries -After $afterEntries)) {
            throw "source_changed"
        }

        $verificationEntries = if ($Mode -eq "stage-readings") {
            Select-KoreaderReadingEntries -Entries $afterEntries
        }
        else { $afterEntries }
        if ($Mode -eq "stage-readings" -and -not (Test-SameSourceTree -Before $entries -After $verificationEntries)) {
            throw "source_changed"
        }
        $verifiedHashes = Test-MtpContentMatchesStage -Entries $verificationEntries -MappedDestination $mappedDestination -Shell $shell

        $finalEntries = [System.Collections.Generic.List[object]]::new()
        $finalSeen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        $finalDeviceFolder = $devices[0].GetFolder
        $finalStorage = @(
            $finalDeviceFolder.Items() |
                Where-Object { ([string]$_.Name) -match '(?i)^internal storage$' }
        )
        if ($finalStorage.Count -ne 1) {
            throw "source_changed"
        }
        Add-MtpEntries -Folder $finalStorage[0].GetFolder -RelativeParent "" -SafeRelativeParent "" -Depth 0 -Entries $finalEntries -SeenPaths $finalSeen
        if (-not (Test-SameSourceTree -Before $expectedSourceEntries -After $finalEntries)) {
            throw "source_changed"
        }
        $sourcePathsVerified = $true
        $contentVerified = $true
    }
    }

    $verificationMethods = [System.Collections.Generic.List[string]]::new()
    if ($script:UsedWpdContentReadback) { [void]$verificationMethods.Add("wpd-stream-sha256") }
    if ($script:UsedShellContentReadback) { [void]$verificationMethods.Add("shell-content-readback-sha256") }
    if ($script:UsedEmptyFileSizeVerification) { [void]$verificationMethods.Add("empty-file-size") }
    $verificationMethod = if ($contentVerified) { $verificationMethods -join "+" } else { $null }
    $sourcePathMap = @{}
    $sourceDirectories = if ($Mode -in @("stage", "stage-readings")) {
        @($verificationEntries | Where-Object { $_.IsDirectory } | ForEach-Object { [string]$_.SafeRelativePath })
    }
    else { @() }
    if ($Mode -in @("stage", "stage-readings")) {
        $stageRoot = [System.IO.Path]::GetFullPath($mappedDestination)
        if (-not $stageRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
            $stageRoot += [System.IO.Path]::DirectorySeparatorChar
        }
        $sourceHashes = @{}
        $stagedPathIdentities = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $verificationEntries) {
            $safePath = ([string]$entry.SafeRelativePath).Replace("\", "/")
            $stagedPath = Join-Path $mappedDestination $entry.SafeRelativePath
            if (-not (Test-Path -LiteralPath $stagedPath)) {
                throw "source_changed"
            }
            $stagedItem = Get-Item -LiteralPath $stagedPath -Force
            $actualPath = [System.IO.Path]::GetFullPath($stagedItem.FullName)
            if (-not $actualPath.StartsWith($stageRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "source_changed"
            }
            $actualRelativePath = $actualPath.Substring($stageRoot.Length).Replace("\", "/")
            if ([string]::IsNullOrWhiteSpace($actualRelativePath) -or -not $stagedPathIdentities.Add($actualRelativePath)) {
                throw "source_changed"
            }
            $sourcePathMap[$safePath] = $actualRelativePath
            if (-not $entry.IsDirectory) {
                if (-not $verifiedHashes.ContainsKey($safePath)) {
                    throw "source_changed"
                }
                $sourceHashes[$safePath] = [string]$verifiedHashes[$safePath]
            }
        }
    }
    $response = New-Response -Mode $Mode -Status "ok" -ReasonCode $null -DeviceCount $deviceCount -StorageCount $storageCount -Roots $summary.roots -FileCount $summary.file_count -DirectoryCount $summary.directory_count -TotalBytes $summary.total_bytes -ContentVerified $contentVerified -ContentVerificationMethod $verificationMethod -SourcePathsVerified $sourcePathsVerified -SourceHashes $sourceHashes -SourceDirectories $sourceDirectories -SourcePathMap $sourcePathMap
}
catch {
    $errorRecord = $_
    $reasonCode = [string]$errorRecord.Exception.Message
    $allowedReasons = @(
        "device_not_found", "storage_not_found", "multiple_devices", "multiple_storages",
        "destination_exists", "invalid_destination", "invalid_name", "invalid_component", "name_collision",
        "path_too_long", "destination_collision", "insufficient_space", "short_path_unavailable",
        "copy_failed", "source_changed", "reading_data_ambiguous", "reading_wal_incomplete"
    )
    if ($reasonCode -notin $allowedReasons) {
        $reasonCode = if ($Mode -in @("stage", "stage-readings")) { "copy_failed" } else { "source_changed" }
    }

    $exitCode = 1
    $response = New-Response -Mode $Mode -Status "error" -ReasonCode $reasonCode -DeviceCount $deviceCount -StorageCount $storageCount
    if ($env:READING_MTP_DEBUG -eq "1") {
        $response.debug = @{
            error_type = $errorRecord.Exception.GetType().Name
            line = [int]$errorRecord.InvocationInfo.ScriptLineNumber
            verification_code = $script:WpdDiagnosticCode
            verification_index = $script:WpdDiagnosticIndex
            verification_depth = $script:WpdDiagnosticDepth
        }
    }
}

if ($stageDrive) {
    $subst = Join-Path $env:WINDIR "System32\subst.exe"
    & $subst /D $stageDrive *> $null
}

$json = ConvertTo-AsciiJson -InputObject $response
[Console]::Out.WriteLine($json)
exit $exitCode
