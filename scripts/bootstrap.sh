#!/usr/bin/env bash
# Errander-AI — System bootstrap
#
# Phase 1 of 2: installs system prerequisites, creates the errander-agent
# service user, clones the repo, and optionally installs Prometheus.
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
#   bash scripts/install.sh
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
step "0/6" "Detecting Linux distribution"

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
step "1/6" "git"
if command -v git &>/dev/null; then
    ok "already installed  ($(git --version | awk '{print $3}'))"
else
    warn "not found — installing..."
    [ "$PKG_MANAGER" = "apt" ] && sudo apt-get update -q
    _install git
    ok "installed  ($(git --version | awk '{print $3}'))"
fi

# ── 2. curl ───────────────────────────────────────────────────────────────────
step "2/6" "curl"
if command -v curl &>/dev/null; then
    ok "already installed"
else
    warn "not found — installing..."
    _install curl
    ok "installed"
fi

# ── 3. uv ─────────────────────────────────────────────────────────────────────
step "3/6" "uv  (Python package + version manager)"
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
step "4/6" "Python 3.12"
warn "installing via uv  (idempotent — safe to run again)..."
uv python install 3.12
ok "Python 3.12 ready"

# ── 5. Service user ───────────────────────────────────────────────────────────
step "5/6" "Service user  (${SERVICE_USER})"

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

# ── 6. Clone repo as service user ─────────────────────────────────────────────
step "6/6" "Clone repo  →  ${REPO_DIR}"

if [ -d "${REPO_DIR}/.git" ]; then
    ok "repo already present at ${REPO_DIR}"
else
    warn "cloning as ${SERVICE_USER}..."
    sudo -u "$SERVICE_USER" git clone "$REPO_URL" "$REPO_DIR" \
        || fail "git clone failed — check network access and that the repo is public"
    ok "cloned to ${REPO_DIR}"
fi

# ── Prometheus  (optional) ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[optional]${NC} Prometheus  (controller-node monitoring)"

if systemctl is-active prometheus &>/dev/null 2>&1; then
    ok "Prometheus already running — skipping"
else
    echo "  Installs Prometheus on THIS controller (port 9091) to scrape the"
    echo "  agent's own /metrics (port 9090). Skip if you already run"
    echo "  monitoring elsewhere or want to set it up later."
    echo ""
    _prom_ans=""
    if [ -e /dev/tty ]; then
        read -r -p "  Install Prometheus now? [y/N] " _prom_ans </dev/tty || _prom_ans=""
    else
        warn "non-interactive session — skipping. Run later: sudo bash ${REPO_DIR}/scripts/install-prometheus.sh"
    fi

    if echo "${_prom_ans:-N}" | grep -qiE '^y'; then
        warn "installing Prometheus..."
        bash "${REPO_DIR}/scripts/install-prometheus.sh" \
            && ok "Prometheus installed — open http://localhost:9091/targets after the agent starts" \
            || warn "install failed — run later: sudo bash ${REPO_DIR}/scripts/install-prometheus.sh"
    else
        ok "skipped — run later: sudo bash ${REPO_DIR}/scripts/install-prometheus.sh"
    fi
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
echo -e "    ${BOLD}bash scripts/install.sh${NC}"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
