#!/usr/bin/env bash
# Errander-AI — Application install
#
# Phase 2 of 2: installs Python dependencies only. No sudo required.
# Configuration (LLM, VMs, Slack) is a separate step — run configure.sh
# once you have your credentials and target VM details ready.
#
#   sudo su - errander-agent
#   cd ~/errander
#   bash scripts/install.sh

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }

echo ""
echo -e "${BOLD}Errander-AI — Application Install${NC}"
echo "═══════════════════════════════════════════"

# ── sanity checks ─────────────────────────────────────────────────────────────
[ -f "errander/__init__.py" ] \
    || fail "Run this from the repo root: cd ~/errander && bash scripts/install.sh"
command -v uv &>/dev/null \
    || fail "uv not found — run scripts/bootstrap.sh first (as your admin user)"

if [ "$(whoami)" != "errander-agent" ]; then
    warn "Running as '$(whoami)', not errander-agent — fine if using a custom layout"
fi

# ── Python dependencies ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[1/1]${NC} Python dependencies"
warn "running uv sync --extra dev..."
uv sync --extra dev
uv run python -c "import errander; print('OK')" \
    || fail "import check failed — check errors above"
ok "import check passed"

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Install complete.${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "  2. Set up SSH keys on this controller (Step 2 in SETUP.md)"
echo "  3. Configure sudo permissions on each target VM (Step 3)"
echo "  4. When ready, run the configuration wizard:"
echo ""
echo -e "     ${BOLD}bash scripts/configure.sh${NC}"
echo ""
echo "     configure.sh will ask for: LLM endpoint, target VMs,"
echo "     Slack token (optional), and write .env + inventory.yaml."
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
