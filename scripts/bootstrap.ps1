#Requires -Version 5.1
# Errander-AI bootstrap script for Windows
#
# Installs git, uv, Python 3.12, clones the repo, runs uv sync,
# and verifies the install. No admin rights required.
#
# Usage (from PowerShell):
#   git clone https://github.com/psc0des/Errander-AI.git errander
#   powershell -ExecutionPolicy Bypass -File errander\scripts\bootstrap.ps1
#
# Or if you already cloned and are inside the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

$ErrorActionPreference = 'Stop'

function Write-Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [>>] $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "`n  [ERR] $msg`n" -ForegroundColor Red; exit 1 }
function Write-Step { param($num, $msg) Write-Host "`n[$num] $msg" -ForegroundColor White }

Write-Host ""
Write-Host "Errander-AI - Bootstrap (Windows)" -ForegroundColor White
Write-Host "==========================================="

# ── 1. git ────────────────────────────────────────────────────────────────────
Write-Step "1/6" "git"

if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Ok "already installed  ($(git --version))"
} else {
    Write-Warn "not found"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Warn "installing via winget..."
        winget install --id Git.Git --source winget --silent `
            --accept-package-agreements --accept-source-agreements
        # Reload PATH from registry so git is visible immediately
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") `
                  + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Write-Fail "git installed but not found in PATH. Close and reopen PowerShell, then re-run this script."
        }
        Write-Ok "installed  ($(git --version))"
    } else {
        Write-Fail "winget not available. Install git from https://git-scm.com/download/win then re-run this script."
    }
}

# ── 2. uv ─────────────────────────────────────────────────────────────────────
Write-Step "2/6" "uv  (Python package + version manager)"

# uv installs to %USERPROFILE%\.local\bin on Windows
$uvBin = Join-Path $env:USERPROFILE ".local\bin"
$env:PATH = "$uvBin;$env:PATH"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Ok "already installed  ($(uv --version))"
} else {
    Write-Warn "installing via official installer..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # Re-add to current session PATH (installer updates registry but not this session)
    $env:PATH = "$uvBin;$env:PATH"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Fail "uv installed but binary not found. Expected: $uvBin\uv.exe"
    }
    Write-Ok "installed  ($(uv --version))"
}

# ── 3. PATH persistence ───────────────────────────────────────────────────────
Write-Step "3/6" "PATH  (~\.local\bin)"

$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -and $userPath -like "*.local\bin*") {
    Write-Ok "already in user PATH"
} else {
    $newPath = if ($userPath) { "$uvBin;$userPath" } else { $uvBin }
    [System.Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Ok "added to user PATH  (takes effect in new PowerShell sessions)"
}

# ── 4. Python 3.12 ────────────────────────────────────────────────────────────
Write-Step "4/6" "Python 3.12"
Write-Warn "installing via uv  (idempotent - safe to re-run)..."
uv python install 3.12
Write-Ok "Python 3.12 ready"

# ── 5. Clone repo ─────────────────────────────────────────────────────────────
Write-Step "5/6" "Errander-AI repository"

$repoUrl    = "https://github.com/psc0des/Errander-AI.git"
$installDir = if ($args.Count -gt 0) { $args[0] } else { "errander" }

if (Test-Path "errander\__init__.py") {
    Write-Ok "already inside the repo  ($(Get-Location))"
} elseif (Test-Path "$installDir\.git") {
    Write-Ok "repo already cloned at .\$installDir"
    Set-Location $installDir
} else {
    Write-Warn "cloning into .\$installDir ..."
    git clone $repoUrl $installDir
    Set-Location $installDir
    Write-Ok "cloned"
}

# ── 6. Install dependencies ───────────────────────────────────────────────────
Write-Step "6/6" "Python dependencies  (uv sync)"
Write-Warn "running uv sync..."
uv sync
Write-Ok "dependencies installed"

# Quick import check
$check = uv run python -c "import errander; print('OK')" 2>&1
if ($check -ne "OK") {
    Write-Fail "import check failed - re-run this script or check errors above`n  Output: $check"
}
Write-Ok "import check passed"

# ── Done ──────────────────────────────────────────────────────────────────────
$repoAbs = Get-Location
Write-Host ""
Write-Host "==========================================="
Write-Host " Bootstrap complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Repo : $repoAbs"
Write-Host ""
Write-Host "  Next - follow SETUP.md from Step 2:"
Write-Host "    Step 2  SSH key: Master VM -> Target VM"
Write-Host "    Step 3  Target VM sudo permissions"
Write-Host "    Step 4  LLM endpoint (Azure AI Foundry / Ollama / vLLM)"
Write-Host "    Step 5  Run configure.sh (Git Bash) to set up .env + inventory"
Write-Host "==========================================="
Write-Host ""
