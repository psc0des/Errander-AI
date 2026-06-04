#!/usr/bin/env bash
# Errander-AI — Application install
#
# Phase 2 of 2: installs Python dependencies then runs interactive
# configuration. No sudo required — runs entirely as errander-agent.
#
#   sudo su - errander-agent
#   cd ~/errander
#   bash scripts/install.sh
#
# Re-run safe — uv sync is idempotent, configure.sh handles existing .env.

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
    warn "Running as '$(whoami)', not errander-agent — fine if using a custom layout"
fi

# ── 1. Python dependencies ────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[1/2]${NC} Python dependencies"
warn "running uv sync --extra dev..."
uv sync --extra dev
uv run python -c "import errander; print('OK')" \
    || fail "import check failed — check errors above"
ok "import check passed"

# ── 2. Configure ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}[2/2]${NC} Interactive configuration"
echo ""
exec bash "${SCRIPT_DIR}/configure.sh"
