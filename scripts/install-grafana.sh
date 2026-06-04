#!/usr/bin/env bash
# Errander-AI — install Grafana on the controller node
#
# Installs Grafana OSS via the official Grafana package repo (APT or YUM),
# provisions a Prometheus datasource pointing at localhost:9091, and loads
# the pre-built Errander-AI Fleet Operations dashboard.
#
# This script is called automatically by scripts/bootstrap.sh when the operator
# opts into the monitoring stack. It can also be run standalone at any time:
#
#   sudo bash scripts/install-grafana.sh
#
# Requires install-prometheus.sh to have been run first (Prometheus on :9091).
# Distro-agnostic: Ubuntu, Debian, RHEL, CentOS, Rocky, Alma, Fedora.
# Idempotent — safe to re-run.
#
# Overridable env vars:
#   GF_PORT=3000        (Grafana listen port)

set -euo pipefail

GF_PORT="${GF_PORT:-3000}"
GF_USER="grafana"
GF_CONF_DIR="/etc/grafana"
GF_DATA_DIR="/var/lib/grafana"
GF_DASHBOARD_DIR="${GF_DATA_DIR}/dashboards"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_DIR="${REPO_ROOT}/deploy/grafana"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }

echo ""
echo -e "${BOLD}Errander-AI — Grafana (controller node)${NC}"
echo "═══════════════════════════════════════════"

command -v curl &>/dev/null || fail "curl is required but not installed"
command -v sudo &>/dev/null || fail "sudo is required but not installed"

[ -d "$DEPLOY_DIR" ] || fail "deploy/grafana/ not found at ${DEPLOY_DIR} — run from inside the Errander-AI repo"

# ── distro detection ──────────────────────────────────────────────────────────
[ -f /etc/os-release ] || fail "/etc/os-release not found — unsupported system"
. /etc/os-release
ID_ALL="${ID:-} ${ID_LIKE:-}"

if echo "$ID_ALL" | grep -qiE 'ubuntu|debian'; then
    PKG_MANAGER="apt"
elif echo "$ID_ALL" | grep -qiE 'rhel|centos|fedora|oracle|ol\b'; then
    PKG_MANAGER=$(command -v dnf &>/dev/null && echo "dnf" || echo "yum")
else
    fail "Unsupported distribution: '${ID:-unknown}'"
fi
ok "distro: ${NAME:-unknown}  →  package manager: ${PKG_MANAGER}"

# ── install grafana via official package repo ─────────────────────────────────
if command -v grafana-server &>/dev/null && systemctl is-enabled grafana-server &>/dev/null 2>&1; then
    ok "Grafana already installed — skipping package install"
else
    warn "adding Grafana OSS package repo..."

    if [ "$PKG_MANAGER" = "apt" ]; then
        export DEBIAN_FRONTEND=noninteractive
        sudo mkdir -p /etc/needrestart/conf.d
        echo "\$nrconf{restart} = 'a';" \
            | sudo tee /etc/needrestart/conf.d/50-errander.conf > /dev/null 2>&1 || true
        sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a \
            apt-get install -y -q apt-transport-https software-properties-common wget gnupg 2>/dev/null || true
        sudo mkdir -p /etc/apt/keyrings
        wget -q -O - https://apt.grafana.com/gpg.key \
            | gpg --dearmor \
            | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
        echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
            | sudo tee /etc/apt/sources.list.d/grafana.list > /dev/null
        sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
        sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -q grafana
    else
        sudo tee /etc/yum.repos.d/grafana.repo > /dev/null <<'EOF'
[grafana]
name=grafana
baseurl=https://rpm.grafana.com
repo_gpgcheck=1
enabled=1
gpgcheck=1
gpgkey=https://rpm.grafana.com/gpg.key
sslverify=1
sslcacert=/etc/pki/tls/certs/ca-bundle.crt
EOF
        sudo "$PKG_MANAGER" install -y grafana
    fi

    ok "grafana package installed"
fi

# ── configure listen port (if non-default) ────────────────────────────────────
if [ "$GF_PORT" != "3000" ]; then
    sudo sed -i "s/^;*http_port = .*/http_port = ${GF_PORT}/" "${GF_CONF_DIR}/grafana.ini"
    ok "Grafana listen port set to :${GF_PORT}"
else
    ok "Grafana listen port: :${GF_PORT} (default)"
fi

# ── provisioning: datasource ──────────────────────────────────────────────────
sudo mkdir -p "${GF_CONF_DIR}/provisioning/datasources"
sudo cp "${DEPLOY_DIR}/provisioning/datasources/errander.yml" \
        "${GF_CONF_DIR}/provisioning/datasources/errander.yml"
sudo chown -R "${GF_USER}:${GF_USER}" "${GF_CONF_DIR}/provisioning/datasources/" 2>/dev/null || \
    sudo chown -R root:root "${GF_CONF_DIR}/provisioning/datasources/"
ok "provisioned datasource: Prometheus → http://localhost:9091"

# ── provisioning: dashboard provider ─────────────────────────────────────────
sudo mkdir -p "${GF_CONF_DIR}/provisioning/dashboards"
sudo cp "${DEPLOY_DIR}/provisioning/dashboards/errander.yml" \
        "${GF_CONF_DIR}/provisioning/dashboards/errander.yml"
sudo chown -R "${GF_USER}:${GF_USER}" "${GF_CONF_DIR}/provisioning/dashboards/" 2>/dev/null || \
    sudo chown -R root:root "${GF_CONF_DIR}/provisioning/dashboards/"
ok "provisioned dashboard provider: /var/lib/grafana/dashboards"

# ── dashboard JSON ─────────────────────────────────────────────────────────────
sudo mkdir -p "$GF_DASHBOARD_DIR"
sudo cp "${DEPLOY_DIR}/dashboards/errander.json" "${GF_DASHBOARD_DIR}/errander.json"
sudo chown -R "${GF_USER}:${GF_USER}" "$GF_DASHBOARD_DIR" 2>/dev/null || \
    sudo chown -R root:root "$GF_DASHBOARD_DIR"
ok "dashboard JSON installed: Errander-AI Fleet Operations"

# ── start service ─────────────────────────────────────────────────────────────
sudo systemctl daemon-reload
sudo systemctl enable --now grafana-server
ok "grafana-server enabled + started"

# ── wait for startup and set admin password ───────────────────────────────────
warn "waiting for Grafana to initialise..."
_tries=0
until curl -fsS "http://localhost:${GF_PORT}/api/health" >/dev/null 2>&1; do
    _tries=$((_tries + 1))
    [ "$_tries" -gt 20 ] && fail "Grafana did not start after 20 seconds — check: sudo systemctl status grafana-server"
    sleep 1
done
ok "Grafana is up on :${GF_PORT}"

GF_ADMIN_PASS="$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 20 || true)"
[ -z "$GF_ADMIN_PASS" ] && GF_ADMIN_PASS="$(date +%s | sha256sum | head -c 20)"

sudo grafana-cli --homepath /usr/share/grafana admin reset-admin-password "$GF_ADMIN_PASS" >/dev/null 2>&1 \
    || sudo grafana-cli admin reset-admin-password "$GF_ADMIN_PASS" >/dev/null 2>&1 \
    || { warn "grafana-cli password reset failed — default admin/admin is active, change it immediately"; GF_ADMIN_PASS="admin"; }

ok "admin password set"

# ── verify ─────────────────────────────────────────────────────────────────────
if curl -fsS -u "admin:${GF_ADMIN_PASS}" "http://localhost:${GF_PORT}/api/org" >/dev/null 2>&1; then
    ok "Grafana API authenticated successfully"
else
    warn "API auth check failed — service is up but password may need manual reset"
fi

# ── done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Grafana installed on the controller node.${NC}"
echo ""
echo "  Grafana UI      : http://<controller-ip>:${GF_PORT}"
echo "  Dashboard       : Errander-AI Fleet Operations  (auto-provisioned)"
echo "  Datasource      : Prometheus → http://localhost:9091  (auto-provisioned)"
echo ""
echo -e "  ${BOLD}Admin credentials:${NC}"
echo -e "  Username        : admin"
echo -e "  ${BOLD}Password        : ${GF_ADMIN_PASS}${NC}"
echo ""
echo "  ⚠  Save this password — it will not be shown again."
echo "     Change it after first login: Profile → Change Password."
echo ""
echo "  Access via SSH tunnel (no firewall rule needed):"
echo "    ssh -L ${GF_PORT}:localhost:${GF_PORT} <user>@<controller-ip>"
echo "    then open http://localhost:${GF_PORT}"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
