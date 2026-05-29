#!/usr/bin/env bash
# Errander-AI bootstrap script
#
# Detects your Linux distro, installs all prerequisites (git, curl, uv,
# Python 3.12), clones the repo, creates the errander-agent service user,
# and hands off the repo to that user.
#
# Supported distros: Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora
#
# Usage (run as your admin user — needs sudo):
#   git clone https://github.com/psc0des/Errander-AI.git errander
#   bash errander/scripts/bootstrap.sh
#
# After bootstrap completes, switch to the service user:
#   sudo su - errander-agent
#   cd ~/errander
#   bash scripts/configure.sh

set -euo pipefail

SERVICE_USER="errander-agent"
SERVICE_HOME="/home/${SERVICE_USER}"
SERVICE_REPO="${SERVICE_HOME}/errander"

# Resolve this script's own directory now, before any `cd`, so we can call
# sibling scripts (e.g. install-prometheus.sh) even after changing directories.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
step "0/9" "Detecting Linux distribution"

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
step "1/9" "git"
if command -v git &>/dev/null; then
    ok "already installed  ($(git --version | awk '{print $3}'))"
else
    warn "not found — installing..."
    [ "$PKG_MANAGER" = "apt" ] && sudo apt-get update -q
    _install git
    ok "installed  ($(git --version | awk '{print $3}'))"
fi

# ── 2. curl ───────────────────────────────────────────────────────────────────
step "2/9" "curl"
if command -v curl &>/dev/null; then
    ok "already installed"
else
    warn "not found — installing..."
    _install curl
    ok "installed"
fi

# ── 3. uv ─────────────────────────────────────────────────────────────────────
step "3/9" "uv  (Python package + version manager)"
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

# Copy uv to /usr/local/bin so the service user can run it without PATH magic
if [ ! -f /usr/local/bin/uv ]; then
    sudo cp "$HOME/.local/bin/uv" /usr/local/bin/uv \
        && ok "uv copied to /usr/local/bin  (available to all users)" \
        || warn "could not copy uv to /usr/local/bin — continuing, but step 8 may fail"
else
    ok "uv already present at /usr/local/bin"
fi

# ── 4. PATH persistence ───────────────────────────────────────────────────────
step "4/9" "PATH  (~/.local/bin)"

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
step "5/9" "Python 3.12"
warn "installing via uv  (idempotent — safe to run again)..."
uv python install 3.12
ok "Python 3.12 ready"

# ── 6. Clone repo ─────────────────────────────────────────────────────────────
step "6/9" "Errander-AI repository"

REPO_URL="https://github.com/psc0des/Errander-AI.git"
INSTALL_DIR="${1:-errander}"

if [ -f "errander/__init__.py" ]; then
    ok "already inside the repo  ($(pwd))"
elif [ -d "$SERVICE_REPO/errander/__init__.py" ] || [ -d "$SERVICE_REPO/.git" ]; then
    ok "repo already at $SERVICE_REPO — skipping clone"
elif [ -d "$INSTALL_DIR/.git" ]; then
    ok "repo already cloned at ./$INSTALL_DIR"
    cd "$INSTALL_DIR"
else
    warn "cloning into ./$INSTALL_DIR ..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    ok "cloned"
fi

REPO_ABS="$(pwd)"

# ── 7. Install Python dependencies (as admin, caches packages) ───────────────
step "7/9" "Python dependencies  (uv sync --extra dev)"

if [ "$REPO_ABS" = "$SERVICE_REPO" ]; then
    # Already owned by service user — sync as service user
    warn "repo already at service location — syncing as $SERVICE_USER..."
    sudo -u "$SERVICE_USER" /usr/local/bin/uv sync --extra dev
else
    # Sync as admin to pre-warm the package download (avoids re-downloading later)
    warn "running uv sync --extra dev..."
    uv sync --extra dev
fi

uv run python -c "import errander; print('OK')" \
    || fail "import check failed — re-run this script or check errors above"
ok "import check passed"

# ── 8. Prometheus (optional, controller-node monitoring of the agent) ─────────
step "8/9" "Prometheus  (optional — scrapes the agent on this controller node)"

if [ -t 0 ] && [ -f "${SCRIPT_DIR}/install-prometheus.sh" ]; then
    echo "  Installs Prometheus on THIS node (port 9091) to scrape the agent's"
    echo "  own /metrics on port 9090. Skip if you already run monitoring."
    read -r -p "  Install Prometheus on this controller node? [y/N] " _prom_ans || _prom_ans=""
    if echo "${_prom_ans:-N}" | grep -qiE '^y'; then
        bash "${SCRIPT_DIR}/install-prometheus.sh" \
            && ok "Prometheus installed" \
            || warn "Prometheus install failed — re-run later: bash scripts/install-prometheus.sh"
    else
        ok "skipped — run later with: bash scripts/install-prometheus.sh"
    fi
else
    # Non-interactive shell (piped/CI) — never block; point to the standalone script.
    ok "non-interactive — skipping. Install later: bash scripts/install-prometheus.sh"
fi

# ── 9. Service user setup ─────────────────────────────────────────────────────
step "9/9" "Service user  (${SERVICE_USER})"

if [ "$REPO_ABS" = "$SERVICE_REPO" ]; then
    ok "repo already at $SERVICE_REPO — nothing to move"
else
    # Create the service user if it doesn't exist
    if id "$SERVICE_USER" &>/dev/null; then
        ok "$SERVICE_USER already exists"
    else
        sudo useradd -m -s /bin/bash "$SERVICE_USER"
        ok "created user $SERVICE_USER"
    fi

    # Set up .ssh directory for the service user
    sudo mkdir -p "${SERVICE_HOME}/.ssh"
    sudo chmod 700 "${SERVICE_HOME}/.ssh"
    sudo chown "${SERVICE_USER}:${SERVICE_USER}" "${SERVICE_HOME}/.ssh"
    ok ".ssh directory ready"

    # Move the repo into the service user's home
    if [ -d "$SERVICE_REPO" ]; then
        warn "$SERVICE_REPO already exists — skipping move"
    else
        sudo mv "$REPO_ABS" "$SERVICE_REPO"
        ok "repo moved to $SERVICE_REPO"
    fi
    sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "$SERVICE_REPO"
    ok "ownership set to $SERVICE_USER"

    # Remove the venv created by admin — its symlinks point to root's Python path,
    # which the service user cannot follow. uv will create a fresh one.
    sudo rm -rf "$SERVICE_REPO/.venv"

    # Rebuild the virtualenv as the service user
    warn "rebuilding virtualenv as $SERVICE_USER..."
    sudo -u "$SERVICE_USER" /usr/local/bin/uv sync --extra dev \
        --project "$SERVICE_REPO"
    ok "virtualenv ready for $SERVICE_USER"
fi

# ── Done ──────────────════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Bootstrap complete!${NC}"
echo ""
echo "  Service user : $SERVICE_USER"
echo "  Repo         : $SERVICE_REPO"
echo ""
echo "  Switch to the service user and continue setup:"
echo ""
echo -e "    ${BOLD}sudo su - ${SERVICE_USER}${NC}"
echo -e "    ${BOLD}cd ~/errander${NC}"
echo ""
echo "  Then run the interactive setup wizard:"
echo -e "    ${BOLD}bash scripts/configure.sh${NC}"
echo ""
echo "  (configure.sh covers Steps 2-5 of SETUP.md)"
echo ""
echo "  Monitoring (optional): if you skipped Prometheus above, install it"
echo "  on this controller node anytime with:"
echo -e "    ${BOLD}bash scripts/install-prometheus.sh${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
