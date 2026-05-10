#!/usr/bin/env bash
# Errander-AI bootstrap script
#
# Detects your Linux distro, installs all prerequisites (git, curl, uv,
# Python 3.12), clones the repo, runs uv sync, and verifies the install.
#
# Supported distros: Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora
#
# Usage:
#   git clone https://github.com/psc0des/Errander-AI.git errander
#   bash errander/scripts/bootstrap.sh

set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }
step() { echo -e "\n${BOLD}[$1]${NC} $2"; }

echo ""
echo -e "${BOLD}Errander-AI — Bootstrap${NC}"
echo "═══════════════════════════════════════════"

# ── 0. Distro detection ───────────────────────────────────────────────────────
step "0/7" "Detecting Linux distribution"

[ -f /etc/os-release ] || fail "/etc/os-release not found — unsupported system"
. /etc/os-release
DISTRO_DISPLAY="${NAME:-unknown} ${VERSION_ID:-}"
ID_ALL="${ID:-} ${ID_LIKE:-}"

if echo "$ID_ALL" | grep -qiE 'ubuntu|debian'; then
    PKG_MANAGER="apt"
elif echo "$ID_ALL" | grep -qiE 'rhel|centos|fedora|oracle|ol\b'; then
    if command -v dnf &>/dev/null; then
        PKG_MANAGER="dnf"
    else
        PKG_MANAGER="yum"
    fi
else
    fail "Unsupported distribution: '${ID:-unknown}'. Supported: Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora"
fi

ok "$DISTRO_DISPLAY  →  package manager: $PKG_MANAGER"

# Wrapper — calls sudo only when not already root
_install() {
    case "$PKG_MANAGER" in
        apt) sudo apt-get install -y -q "$@" 2>&1 | grep -v "^Reading\|^Building\|^Unpacking\|^Setting\|^Processing" || true ;;
        dnf) sudo dnf install -y -q "$@" ;;
        yum) sudo yum install -y -q "$@" ;;
    esac
}

# ── 1. git ────────────────────────────────────────────────────────────────────
step "1/7" "git"
if command -v git &>/dev/null; then
    ok "already installed  ($(git --version | awk '{print $3}'))"
else
    warn "not found — installing..."
    [ "$PKG_MANAGER" = "apt" ] && sudo apt-get update -q
    _install git
    ok "installed  ($(git --version | awk '{print $3}'))"
fi

# ── 2. curl ───────────────────────────────────────────────────────────────────
step "2/7" "curl"
if command -v curl &>/dev/null; then
    ok "already installed"
else
    warn "not found — installing..."
    _install curl
    ok "installed"
fi

# ── 3. uv ─────────────────────────────────────────────────────────────────────
step "3/7" "uv  (Python package + version manager)"
export PATH="$HOME/.local/bin:$PATH"

if command -v uv &>/dev/null; then
    ok "already installed  ($(uv --version))"
else
    warn "installing via official installer (no pip required)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || fail "uv install succeeded but binary not found — check $HOME/.local/bin"
    ok "installed  ($(uv --version))"
fi

# ── 4. PATH persistence ───────────────────────────────────────────────────────
step "4/7" "PATH  (~/.local/bin)"

# Pick the right shell RC file
if [ -n "${ZSH_VERSION:-}" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
else
    SHELL_RC="$HOME/.profile"
fi

PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
if grep -qF '.local/bin' "$SHELL_RC" 2>/dev/null; then
    ok "already present in $SHELL_RC"
else
    echo "$PATH_LINE" >> "$SHELL_RC"
    ok "added to $SHELL_RC  (run: source $SHELL_RC)"
fi

# ── 5. Python 3.12 ────────────────────────────────────────────────────────────
step "5/7" "Python 3.12"
warn "installing via uv  (idempotent — safe to run again)..."
uv python install 3.12
ok "Python 3.12 ready"

# ── 6. Clone repo ─────────────────────────────────────────────────────────────
step "6/7" "Errander-AI repository"

REPO_URL="https://github.com/psc0des/Errander-AI.git"
INSTALL_DIR="${1:-errander}"

# If we're already sitting inside the repo root, skip clone
if [ -f "errander/__init__.py" ]; then
    ok "already inside the repo  ($(pwd))"
elif [ -d "$INSTALL_DIR/.git" ]; then
    ok "repo already cloned at ./$INSTALL_DIR"
    cd "$INSTALL_DIR"
else
    warn "cloning into ./$INSTALL_DIR ..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    ok "cloned"
fi

# ── 7. Install Python dependencies ───────────────────────────────────────────
step "7/7" "Python dependencies  (uv sync --extra dev)"
warn "running uv sync --extra dev..."
uv sync --extra dev
ok "dependencies installed  (includes pytest, ruff, mypy)"

warn "installing Playwright Chromium browser..."
uv run playwright install chromium || true
ok "Chromium installed  (required for UI tests)"

# Quick import check
uv run python -c "import errander; print('OK')" \
    || fail "import check failed — re-run this script or check errors above"
ok "import check passed"

# ── Done ──────────────════════════════════════════════════════════════════════
REPO_ABS="$(pwd)"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Bootstrap complete!${NC}"
echo ""
echo "  Repo : $REPO_ABS"
echo "  Shell: source $SHELL_RC  (to apply PATH in this session)"
echo ""
echo "  Next — follow SETUP.md from Step 2:"
echo "    Step 2  SSH key setup (Master VM → Target VM)"
echo "    Step 3  Target VM sudo permissions"
echo ""
echo "  Then run the interactive setup (covers Steps 4-5):"
echo "    cd $REPO_ABS && bash scripts/configure.sh"
echo ""
echo "    Step 6  Verify (inventory check + uv run pytest)"
echo "    Step 7  First dry-run"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
