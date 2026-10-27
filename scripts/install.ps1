[CmdletBinding()]
param([switch]$SkipSecretScanner)

$ErrorActionPreference = "Stop"

function Install-WinGetPackage {
    param([Parameter(Mandatory)][string]$Id)

    $installed = winget list --id $Id --exact 2>$null | Out-String
    if ($installed -match [regex]::Escape($Id)) { return }

    winget install --id $Id --exact --scope user --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o pacote $Id pelo WinGet." }
}

function Find-UserExecutable {
    param([Parameter(Mandatory)][string]$PackagePrefix, [Parameter(Mandatory)][string]$ExecutableName)

    $fromPath = Get-Command $ExecutableName -ErrorAction SilentlyContinue
    if ($fromPath) { return $fromPath.Source }

    $packagesRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $package = Get-ChildItem -LiteralPath $packagesRoot -Directory -Filter "$PackagePrefix*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($package) {
        $found = Get-ChildItem -LiteralPath $package.FullName -File -Filter $ExecutableName -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "WinGet não está disponível. Instale uv e Gitleaks manualmente e rode uv sync --locked."
}

Install-WinGetPackage -Id "astral-sh.uv"
if (-not $SkipSecretScanner) { Install-WinGetPackage -Id "Gitleaks.Gitleaks" }

$uv = Find-UserExecutable -PackagePrefix "astral-sh.uv_" -ExecutableName "uv.exe"
if (-not $uv) { throw "uv não foi encontrado após a instalação." }

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & $uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw "uv sync --locked falhou." }
}
finally { Pop-Location }
