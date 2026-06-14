#!/usr/bin/env bash
# Errander-AI — System bootstrap
#
# Phase 1 of 2: installs system prerequisites, creates the errander-agent
# service user and clones the repo.
# No repo needed — safe to run via curl | bash.
#
#   curl -fsSL https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/bootstrap.sh | bash
#
# Or inspect first, then run:
#   git clone https://github.com/psc0des/Errander-AI.git errander-setup
#   bash errander-setup/scripts/bootstrap.sh
#
# When it finishes, Phase 2 runs as the service user (no sudo required):
#   sudo su - errander-agent
#   cd ~/errander
#   bash scripts/configure.sh
#
# Supported distros: Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora
# Idempotent — safe to re-run.

set -euo pipefail

SERVICE_USER="errander-agent"
SERVICE_HOME="/home/${SERVICE_USER}"
REPO_URL="https://github.com/psc0des/Errander-AI.git"
REPO_DIR="${SERVICE_HOME}/errander"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }
step() { echo -e "\n${BOLD}[$1]${NC} $2"; }

echo ""
echo -e "${BOLD}Errander-AI — System Bootstrap${NC}"
echo "═══════════════════════════════════════════"

# ── 0. Distro detection ───────────────────────────────────────────────────────
step "0/8" "Detecting Linux distribution"

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
        apt) sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" 2>&1 \
                 | grep -v "^Reading\|^Building\|^Unpacking\|^Setting\|^Processing" || true ;;
        dnf) sudo dnf install -y -q "$@" ;;
        yum) sudo yum install -y -q "$@" ;;
    esac
}

# ── 1. git ────────────────────────────────────────────────────────────────────
step "1/8" "git"
if command -v git &>/dev/null; then
    ok "already installed  ($(git --version | awk '{print $3}'))"
else
    warn "not found — installing..."
    [ "$PKG_MANAGER" = "apt" ] && sudo apt-get update -q
    _install git
    ok "installed  ($(git --version | awk '{print $3}'))"
fi

# ── 2. curl ───────────────────────────────────────────────────────────────────
step "2/8" "curl"
if command -v curl &>/dev/null; then
    ok "already installed"
else
    warn "not found — installing..."
    _install curl
    ok "installed"
fi

# ── 3. uv ─────────────────────────────────────────────────────────────────────
step "3/8" "uv  (Python package + version manager)"
export PATH="$HOME/.local/bin:$PATH"

if command -v uv &>/dev/null; then
    ok "already installed  ($(uv --version))"
else
    warn "installing via official installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || fail "uv install succeeded but binary not found — check $HOME/.local/bin"
    ok "installed  ($(uv --version))"
fi

if [ ! -f /usr/local/bin/uv ]; then
    sudo cp "$HOME/.local/bin/uv" /usr/local/bin/uv \
        && ok "uv copied to /usr/local/bin  (available to all users)" \
        || warn "could not copy uv to /usr/local/bin — service user may need PATH config"
else
    ok "uv already present at /usr/local/bin"
fi

# ── 4. Python 3.12 ────────────────────────────────────────────────────────────
step "4/8" "Python 3.12"
warn "installing via uv  (idempotent — safe to run again)..."
uv python install 3.12
ok "Python 3.12 ready"

# ── 5. Service user ───────────────────────────────────────────────────────────
step "5/8" "Service user  (${SERVICE_USER})"

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

_svc_bashrc="${SERVICE_HOME}/.bashrc"
_path_line='export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"'
if ! sudo grep -qF '.local/bin' "$_svc_bashrc" 2>/dev/null; then
    echo "$_path_line" | sudo tee -a "$_svc_bashrc" > /dev/null
    ok "PATH configured in ${SERVICE_USER}'s .bashrc"
else
    ok "PATH already configured in ${SERVICE_USER}'s .bashrc"
fi

# ── 6. Docker + Docker Compose ────────────────────────────────────────────────
step "6/8" "Docker + Docker Compose"

if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    ok "already installed  ($(docker --version | awk '{print $3}' | tr -d ','))"
else
    warn "not found — installing via get.docker.com..."
    curl -fsSL https://get.docker.com | sudo sh
    ok "Docker Engine + Compose plugin installed"
fi

sudo systemctl enable --now docker
ok "docker.service enabled and running"

if id -nG "$SERVICE_USER" | grep -qw docker; then
    ok "${SERVICE_USER} already in docker group"
else
    sudo usermod -aG docker "$SERVICE_USER"
    ok "${SERVICE_USER} added to docker group (takes effect on next login — Step B already re-logs in)"
fi

# ── 7. Web service user (R3 process split) ────────────────────────────────────
step "7/8" "Web service user  (errander-web)"

WEB_USER="errander-web"
WEB_HOME="/home/${WEB_USER}"

if id "$WEB_USER" &>/dev/null; then
    ok "${WEB_USER} already exists"
else
    # Create web user without shell, no home SSH access (nologin or /sbin/nologin)
    sudo useradd -r -s /sbin/nologin -d "$WEB_HOME" "$WEB_USER" 2>/dev/null || true
    ok "created system user ${WEB_USER}  (no SSH/shell access)"
fi

sudo mkdir -p "${WEB_HOME}" 2>/dev/null || true
sudo chown "${WEB_USER}:${WEB_USER}" "${WEB_HOME}"
ok "home directory ready at ${WEB_HOME}"

# ── 8. Clone repo as service user ──────────────────────────────────────────────
step "8/8" "Clone repo  →  ${REPO_DIR}"

_clone_repo() {
    sudo -u "$SERVICE_USER" git clone "$REPO_URL" "$REPO_DIR" \
        || fail "git clone failed — check network access and that the repo is public"
}

if [ -d "${REPO_DIR}/.git" ]; then
    warn "repo present — updating to latest..."
    _updated=false
    if sudo -H -u "$SERVICE_USER" git -C "$REPO_DIR" fetch origin main 2>&1 \
    && sudo -H -u "$SERVICE_USER" git -C "$REPO_DIR" reset --hard origin/main 2>&1; then
        _updated=true
        ok "repo updated to latest at ${REPO_DIR}"
    fi

    if ! $_updated; then
        warn "git update failed — re-cloning (preserving .env + inventory.yaml)..."
        sudo cp "${REPO_DIR}/.env"           /tmp/_errander_env.bak 2>/dev/null || true
        sudo cp "${REPO_DIR}/inventory.yaml" /tmp/_errander_inv.bak 2>/dev/null || true
        sudo rm -rf "$REPO_DIR"
        _clone_repo
        sudo mv /tmp/_errander_env.bak       "${REPO_DIR}/.env"           2>/dev/null || true
        sudo mv /tmp/_errander_inv.bak       "${REPO_DIR}/inventory.yaml" 2>/dev/null || true
        ok "re-cloned at ${REPO_DIR}"
    fi
else
    warn "cloning as ${SERVICE_USER}..."
    _clone_repo
    ok "cloned to ${REPO_DIR}"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} System bootstrap complete!${NC}"
echo ""
echo "  Service user : ${SERVICE_USER}"
echo "  Repo         : ${REPO_DIR}"
echo ""
echo "  Phase 2 — switch to the service user and configure the app:"
echo ""
echo -e "    ${BOLD}sudo su - ${SERVICE_USER}${NC}"
echo -e "    ${BOLD}cd ~/errander${NC}"
echo -e "    ${BOLD}bash scripts/configure.sh${NC}"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
