#!/usr/bin/env bash
# Errander-AI — Application install
#
# Phase 2 of 2: installs Python dependencies, optionally installs Prometheus,
# then runs configure.sh interactively.
#
# Run as errander-agent from inside the repo:
#   sudo su - errander-agent
#   git clone https://github.com/psc0des/Errander-AI.git ~/errander
#   cd ~/errander
#   bash scripts/install.sh
#
# Re-run safe — uv sync is idempotent, configure.sh handles existing .env.
# Prometheus step is skipped if already running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }

echo ""
echo -e "${BOLD}Errander-AI — Application Install${NC}"
echo "═══════════════════════════════════════════"

# ── Sanity checks ─────────────────────────────────────────────────────────────
[ -f "errander/__init__.py" ] \
    || fail "Run this from the repo root: cd ~/errander && bash scripts/install.sh"
command -v uv &>/dev/null \
    || fail "uv not found — run scripts/bootstrap.sh first (as your admin user)"

if [ "$(whoami)" != "errander-agent" ]; then
    warn "Running as '$(whoami)', not errander-agent — that is fine if you chose a custom layout"
fi

# ── 1. Python dependencies ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[1/3]${NC} Python dependencies"
warn "running uv sync --extra dev..."
uv sync --extra dev
uv run python -c "import errander; print('OK')" \
    || fail "import check failed — check errors above"
ok "import check passed"

# ── 2. Prometheus ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/3]${NC} Prometheus  (optional)"

if systemctl is-active prometheus &>/dev/null 2>&1; then
    ok "Prometheus is already running — skipping"
elif [ ! -f "${SCRIPT_DIR}/install-prometheus.sh" ]; then
    warn "install-prometheus.sh not found — skipping (install later: sudo bash scripts/install-prometheus.sh)"
else
    echo "  Installs Prometheus on THIS controller node (port 9091) to scrape the"
    echo "  agent's own /metrics on port 9090. Skip if you already run monitoring"
    echo "  elsewhere or want to set it up later."
    echo ""
    if [ -t 0 ]; then
        read -r -p "  Install Prometheus on this controller node? [y/N] " _prom_ans || _prom_ans=""
    else
        _prom_ans=""
    fi

    if echo "${_prom_ans:-N}" | grep -qiE '^y'; then
        warn "installing Prometheus (requires sudo)..."
        sudo bash "${SCRIPT_DIR}/install-prometheus.sh" \
            && ok "Prometheus installed" \
            || warn "Prometheus install failed — run later: sudo bash scripts/install-prometheus.sh"
    else
        ok "skipped — run later: sudo bash scripts/install-prometheus.sh"
    fi
fi

# ── 3. Configure ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[3/3]${NC} Interactive configuration"
echo ""
exec bash "${SCRIPT_DIR}/configure.sh"
