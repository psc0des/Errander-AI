#!/usr/bin/env bash
# Errander-AI — System bootstrap
#
# Phase 1 of 2: sets up system-level prerequisites and creates the
# errander-agent service user. Does NOT clone the repo, install Python
# dependencies, or move any files.
#
# Run as your admin user (needs sudo):
#   git clone https://github.com/psc0des/Errander-AI.git errander
#   bash errander/scripts/bootstrap.sh
#
# When it finishes, Phase 2 (app install) runs as the service user:
#   sudo su - errander-agent
#   git clone https://github.com/psc0des/Errander-AI.git ~/errander
#   cd ~/errander
#   bash scripts/install.sh
#
# Supported distros: Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora
# Idempotent — safe to re-run.

set -euo pipefail

SERVICE_USER="errander-agent"
SERVICE_HOME="/home/${SERVICE_USER}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }
step() { echo -e "\n${BOLD}[$1]${NC} $2"; }

echo ""
echo -e "${BOLD}Errander-AI — System Bootstrap${NC}"
echo "═══════════════════════════════════════════"

# ── 0. Distro detection ───────────────────────────────────────────────────────
step "0/5" "Detecting Linux distribution"

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

_install() {
    case "$PKG_MANAGER" in
        apt) sudo apt-get install -y -q "$@" 2>&1 | grep -v "^Reading\|^Building\|^Unpacking\|^Setting\|^Processing" || true ;;
        dnf) sudo dnf install -y -q "$@" ;;
        yum) sudo yum install -y -q "$@" ;;
    esac
}

# ── 1. git ────────────────────────────────────────────────────────────────────
step "1/5" "git"
if command -v git &>/dev/null; then
    ok "already installed  ($(git --version | awk '{print $3}'))"
else
    warn "not found — installing..."
    [ "$PKG_MANAGER" = "apt" ] && sudo apt-get update -q
    _install git
    ok "installed  ($(git --version | awk '{print $3}'))"
fi

# ── 2. curl ───────────────────────────────────────────────────────────────────
step "2/5" "curl"
if command -v curl &>/dev/null; then
    ok "already installed"
else
    warn "not found — installing..."
    _install curl
    ok "installed"
fi

# ── 3. uv ─────────────────────────────────────────────────────────────────────
step "3/5" "uv  (Python package + version manager)"
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
        || warn "could not copy uv to /usr/local/bin — service user may need PATH config"
else
    ok "uv already present at /usr/local/bin"
fi

# ── 4. Python 3.12 ────────────────────────────────────────────────────────────
step "4/5" "Python 3.12"
warn "installing via uv  (idempotent — safe to run again)..."
uv python install 3.12
ok "Python 3.12 ready"

# ── 5. Service user ───────────────────────────────────────────────────────────
step "5/5" "Service user  (${SERVICE_USER})"

if id "$SERVICE_USER" &>/dev/null; then
    ok "${SERVICE_USER} already exists"
else
    sudo useradd -m -s /bin/bash "$SERVICE_USER"
    ok "created user ${SERVICE_USER}"
fi

sudo mkdir -p "${SERVICE_HOME}/.ssh"
sudo chmod 700 "${SERVICE_HOME}/.ssh"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${SERVICE_HOME}/.ssh"
ok ".ssh directory ready at ${SERVICE_HOME}/.ssh"

# Wire uv + local bin into the service user's .bashrc so it works in new sessions
_svc_bashrc="${SERVICE_HOME}/.bashrc"
_path_line='export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"'
if ! sudo grep -qF '.local/bin' "$_svc_bashrc" 2>/dev/null; then
    echo "$_path_line" | sudo tee -a "$_svc_bashrc" > /dev/null
    ok "PATH configured in ${SERVICE_USER}'s .bashrc"
else
    ok "PATH already configured in ${SERVICE_USER}'s .bashrc"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} System bootstrap complete!${NC}"
echo ""
echo "  Service user : ${SERVICE_USER}"
echo "  Home         : ${SERVICE_HOME}"
echo ""
echo "  Phase 2 — switch to the service user and install the app:"
echo ""
echo -e "    ${BOLD}sudo su - ${SERVICE_USER}${NC}"
echo -e "    ${BOLD}git clone https://github.com/psc0des/Errander-AI.git ~/errander${NC}"
echo -e "    ${BOLD}cd ~/errander${NC}"
echo -e "    ${BOLD}bash scripts/install.sh${NC}"
echo ""
echo "  (You can delete this admin clone afterwards — it was only needed"
echo "   to run bootstrap.sh.)"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
