param(
    [switch]$SkipSmokeTest,
    [ValidateSet("KOReaderEcosystem", "KOReaderEcosystem-next", "KOReaderEcosystem-compact")]
    [string]$DistributionDirectoryName = "KOReaderEcosystem-next"
)

$ErrorActionPreference = "Stop"

$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$buildDirectory = Join-Path $repositoryRoot "build\windows"
$compiledDirectory = Join-Path $buildDirectory "KOReaderEcosystem.dist"
$compiledExecutable = Join-Path $compiledDirectory "KOReaderEcosystem.exe"
$distributionDirectory = Join-Path $repositoryRoot "dist\$DistributionDirectoryName"
$pythonExecutable = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$deployExecutable = Join-Path $repositoryRoot ".venv\Scripts\pyside6-deploy.exe"
$deploymentSpecSource = Join-Path $repositoryRoot "pysidedeploy.spec"
$applicationAssets = Join-Path $repositoryRoot "assets"
$applicationIcon = Join-Path $applicationAssets "KOReaderEcosystem.ico"
. (Join-Path $repositoryRoot "scripts\restic-release.ps1")

function Remove-GeneratedDirectory {
    param([string]$TargetPath)

    $rootWithSeparator = $repositoryRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    $absoluteTarget = [System.IO.Path]::GetFullPath($TargetPath)
    if (-not $absoluteTarget.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the repository build area."
    }

    $relativeTarget = $absoluteTarget.Substring($rootWithSeparator.Length).Replace("/", "\")
    if (
        $relativeTarget -notin @(
            "build\windows",
            "dist\KOReaderEcosystem",
            "dist\KOReaderEcosystem-next",
            "dist\KOReaderEcosystem-compact"
        )
    ) {
        throw "Refusing to remove an unrecognized generated directory."
    }

    if (Test-Path -LiteralPath $absoluteTarget) {
        Remove-Item -LiteralPath $absoluteTarget -Recurse -Force
    }
}

if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
    throw "This build currently targets 64-bit Windows."
}
foreach (
    $required in @($pythonExecutable, $deployExecutable, $deploymentSpecSource, $applicationIcon)
) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Build prerequisite is missing. Run uv sync --all-groups first."
    }
}

Remove-GeneratedDirectory $buildDirectory
New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null
$entryPoint = Join-Path $buildDirectory "KOReaderEcosystem.py"
@'
from koreader_ecosystem.ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
'@ | Set-Content -LiteralPath $entryPoint -Encoding UTF8
$deploymentSpec = Join-Path $buildDirectory "pysidedeploy.spec"
$specContents = (Get-Content -LiteralPath $deploymentSpecSource -Raw).Replace(
    "project_dir = .",
    "project_dir = ../.."
)
$specContents = $specContents.Replace(
    "icon = assets/KOReaderEcosystem.ico",
    "icon = $applicationIcon"
)
Set-Content -LiteralPath $deploymentSpec -Value $specContents -Encoding UTF8

$resticSource = Get-VerifiedResticExecutable -CacheDirectory $buildDirectory

$originalPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $repositoryRoot "src"
try {
    & $deployExecutable -c $deploymentSpec --no-install --no-warn --force
    if ($LASTEXITCODE -ne 0) {
        throw "pyside6-deploy failed to compile the Windows application."
    }
}
finally {
    $env:PYTHONPATH = $originalPythonPath
}
if (-not (Test-Path -LiteralPath $compiledDirectory -PathType Container) -or
    -not (Test-Path -LiteralPath $compiledExecutable -PathType Leaf)) {
    throw "The compiler output did not contain the expected standalone application."
}

Remove-GeneratedDirectory $distributionDirectory
New-Item -ItemType Directory -Path $distributionDirectory -Force | Out-Null
Get-ChildItem -LiteralPath $compiledDirectory -Force | Copy-Item -Destination $distributionDirectory -Recurse -Force

$runtimeDirectory = Join-Path $distributionDirectory "runtime"
$licenseDirectory = Join-Path $distributionDirectory "licenses"
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $licenseDirectory -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $distributionDirectory "assets") -Force | Out-Null
Copy-Item -LiteralPath $applicationIcon `
    -Destination (Join-Path $distributionDirectory "assets\KOReaderEcosystem.ico")
$fontSourceDirectory = Join-Path $applicationAssets "fonts"
$fontDestinationDirectory = Join-Path $distributionDirectory "assets\fonts"
$fontLicenseDirectory = Join-Path $licenseDirectory "fonts"
New-Item -ItemType Directory -Path $fontDestinationDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $fontLicenseDirectory -Force | Out-Null
foreach ($fontName in @("Inter.ttf", "Lora.ttf")) {
    Copy-Item -LiteralPath (Join-Path $fontSourceDirectory $fontName) `
        -Destination $fontDestinationDirectory
}
foreach ($licenseName in @("Inter-OFL.txt", "Lora-OFL.txt")) {
    Copy-Item -LiteralPath (Join-Path $fontSourceDirectory $licenseName) `
        -Destination $fontLicenseDirectory
}
Copy-Item -LiteralPath $resticSource -Destination (Join-Path $runtimeDirectory "restic.exe")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\stage-kindle.ps1") `
    -Destination (Join-Path $runtimeDirectory "stage-kindle.ps1")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\WpdReadback.cs") `
    -Destination (Join-Path $runtimeDirectory "WpdReadback.cs")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\send-kindle.ps1") `
    -Destination (Join-Path $runtimeDirectory "send-kindle.ps1")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "scripts\install-hardcover-plugin.ps1") `
    -Destination (Join-Path $runtimeDirectory "install-hardcover-plugin.ps1")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "patches\hardcoverapp-koplugin\0.4.0.patch") `
    -Destination (Join-Path $runtimeDirectory "hardcoverapp-0.4.0.patch")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "docs\LEIA-ME-EXECUTAVEL.txt") `
    -Destination (Join-Path $distributionDirectory "LEIA-ME-EXECUTAVEL.txt")
Copy-Item -LiteralPath (Join-Path $repositoryRoot "licenses\LGPL-3.0.txt") -Destination $licenseDirectory
Copy-Item -LiteralPath (Join-Path $repositoryRoot "licenses\RESTIC-LICENSE.txt") -Destination $licenseDirectory
Copy-Item -LiteralPath (Join-Path $repositoryRoot "licenses\THIRD-PARTY-NOTICES.txt") -Destination $licenseDirectory

$pythonBase = (& $pythonExecutable -c "import sys; print(sys.base_prefix)").Trim()
$pythonLicense = Join-Path $pythonBase "LICENSE.txt"
if (-not (Test-Path -LiteralPath $pythonLicense -PathType Leaf)) {
    throw "The Python distribution license could not be located."
}
Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenseDirectory "PYTHON-LICENSE.txt")

& $pythonExecutable (Join-Path $repositoryRoot "scripts\validate-portable-bundle.py") $distributionDirectory
if ($LASTEXITCODE -ne 0) {
    throw "The generated directory failed the portable bundle checks."
}

if (-not $SkipSmokeTest) {
    $smokeProcess = Start-Process `
        -FilePath (Join-Path $distributionDirectory "KOReaderEcosystem.exe") `
        -ArgumentList "--smoke-test" `
        -WorkingDirectory $distributionDirectory `
        -PassThru `
        -Wait
    if ($smokeProcess.ExitCode -ne 0) {
        throw "The packaged application did not pass its startup smoke test."
    }
}

Write-Output "Build verificado: dist\$DistributionDirectoryName\KOReaderEcosystem.exe"
