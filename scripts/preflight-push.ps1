[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or -not $root) { throw "Execute este script dentro do repositório Git." }

$scanner = Get-Command gitleaks -ErrorAction SilentlyContinue
if (-not $scanner) {
    $packagesRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    $package = Get-ChildItem -LiteralPath $packagesRoot -Directory -Filter "Gitleaks.Gitleaks_*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($package) {
        $found = Get-ChildItem -LiteralPath $package.FullName -File -Filter "gitleaks.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $scanner = $found.FullName }
    }
}
if (-not $scanner) { throw "Gitleaks não encontrado. Rode scripts/install.ps1." }
$scannerPath = if ($scanner -is [string]) { $scanner } else { $scanner.Source }

Push-Location $root
try {
    # Scan the staged snapshot so ignored local state, environments, and build outputs
    # are not treated as release content. Stage every intended change before this check.
    & $scannerPath git --redact --staged .
    if ($LASTEXITCODE -ne 0) { throw "Gitleaks encontrou segredo em arquivos locais." }
    & $scannerPath git --redact .
    if ($LASTEXITCODE -ne 0) { throw "Gitleaks encontrou segredo no histórico Git." }

    $forbidden = git ls-files | Where-Object {
        $_ -match '(^|/)(state|staging|backups|private|exports)(/|$)|(^|/)metadata\.db($|[-/])|\.sdr(/|$)|hardcover_config\.lua|(^|/)\.env($|\.)'
    }
    if ($forbidden) {
        $forbidden | ForEach-Object { Write-Error "Arquivo privado rastreado: $_" }
        throw "Remova arquivos privados do índice antes do push."
    }
}
finally { Pop-Location }
