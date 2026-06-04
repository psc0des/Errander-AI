#!/usr/bin/env bash
# Errander-AI — teardown / uninstall
#
# Reverses everything bootstrap.sh, install-prometheus.sh, and
# install-grafana.sh did. Intended for dev/test re-runs on a clean slate.
#
# Run as your admin user (needs sudo):
#   sudo bash scripts/teardown.sh
#
# Or without cloning first:
#   curl -fsSL https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/teardown.sh | bash
#
# Does NOT remove: git, curl, Python 3.12 (may have pre-existed or are harmless to keep).

set -uo pipefail   # note: no -e so partial removal keeps going

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
skip() { echo -e "  · $*  (not found — skipping)"; }

echo ""
echo -e "${BOLD}Errander-AI — Teardown${NC}"
echo "═══════════════════════════════════════════"
echo ""
echo -e "  ${RED}This will permanently remove:${NC}"
echo "    • Grafana    — service, binary, assets, config, data"
echo "    • Prometheus — service, binaries, config, data"
echo "    • errander-agent user + /home/errander-agent (repo, .env, inventory)"
echo "    • uv from /usr/local/bin"
echo ""
echo "  NOT removed: git, curl, Python 3.12"
echo ""

_confirm=""
if [ -e /dev/tty ]; then
    read -r -p "  Type 'yes' to confirm: " _confirm </dev/tty || _confirm=""
else
    echo "  Non-interactive — pass CONFIRM=yes to skip this prompt"
    _confirm="${CONFIRM:-}"
fi

if [ "${_confirm}" != "yes" ]; then
    echo "  Aborted."
    exit 0
fi

echo ""

# ── Errander agent service (if user created it in Step 9) ─────────────────────
warn "checking for errander agent service..."
if systemctl list-unit-files errander.service &>/dev/null 2>&1; then
    systemctl stop    errander.service 2>/dev/null || true
    systemctl disable errander.service 2>/dev/null || true
    rm -f /etc/systemd/system/errander.service
    ok "errander.service stopped + removed"
else
    skip "errander.service"
fi

# ── Grafana ────────────────────────────────────────────────────────────────────
warn "removing Grafana..."
systemctl stop    grafana-server 2>/dev/null || true
systemctl disable grafana-server 2>/dev/null || true
rm -f /etc/systemd/system/grafana-server.service
rm -f /usr/sbin/grafana /usr/sbin/grafana-server /usr/sbin/grafana-cli
rm -rf /usr/share/grafana /etc/grafana /var/lib/grafana /var/log/grafana
if id grafana &>/dev/null 2>&1; then
    userdel grafana 2>/dev/null || true
fi
# Clean up any partial apt-installed Grafana
if command -v dpkg &>/dev/null && dpkg -s grafana &>/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get remove -y grafana 2>/dev/null || true
fi
ok "Grafana removed"

# ── Prometheus ─────────────────────────────────────────────────────────────────
warn "removing Prometheus..."
systemctl stop    prometheus 2>/dev/null || true
systemctl disable prometheus 2>/dev/null || true
rm -f /etc/systemd/system/prometheus.service
rm -f /usr/local/bin/prometheus /usr/local/bin/promtool
rm -rf /etc/prometheus /var/lib/prometheus
if id prometheus &>/dev/null 2>&1; then
    userdel prometheus 2>/dev/null || true
fi
ok "Prometheus removed"

# ── systemd cleanup ────────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true
ok "systemd reloaded"

# ── errander-agent user + home ─────────────────────────────────────────────────
warn "removing errander-agent user + home..."
pkill -u errander-agent 2>/dev/null || true
sleep 1
if id errander-agent &>/dev/null 2>&1; then
    userdel -r errander-agent 2>/dev/null || {
        rm -rf /home/errander-agent
        userdel errander-agent 2>/dev/null || true
    }
    ok "errander-agent user + /home/errander-agent removed"
else
    skip "errander-agent user"
fi

# ── uv from /usr/local/bin ─────────────────────────────────────────────────────
if [ -f /usr/local/bin/uv ]; then
    rm -f /usr/local/bin/uv
    ok "uv removed from /usr/local/bin"
else
    skip "uv (not in /usr/local/bin)"
fi

# ── leftovers ──────────────────────────────────────────────────────────────────
rm -f /etc/needrestart/conf.d/50-errander.conf 2>/dev/null || true
rm -f /etc/apt/sources.list.d/grafana.list 2>/dev/null || true
rm -f /etc/apt/keyrings/grafana.gpg 2>/dev/null || true
rm -f /etc/yum.repos.d/grafana.repo 2>/dev/null || true

# ── done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Teardown complete. System is back to a clean slate.${NC}"
echo ""
echo "  To start fresh:"
echo -e "    ${BOLD}curl -fsSL https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/bootstrap.sh | bash${NC}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
