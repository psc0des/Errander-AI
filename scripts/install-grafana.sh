#!/usr/bin/env bash
# Errander-AI — install Grafana on the controller node
#
# Downloads the official Grafana OSS release tarball — no package manager,
# zero interactive prompts, works identically on Ubuntu, Debian, RHEL,
# Rocky, Fedora, and any other Linux distro.
#
# Usage (run as a user with sudo):
#   sudo bash scripts/install-grafana.sh
#
# Overridable env vars:
#   GF_VERSION=11.4.0   GF_PORT=3000
#
# Idempotent — safe to re-run.

set -euo pipefail

GF_VERSION="${GF_VERSION:-11.4.0}"
GF_PORT="${GF_PORT:-3000}"
GF_USER="grafana"
GF_HOME="/usr/share/grafana"           # web assets + default conf
GF_CONF_DIR="/etc/grafana"             # custom config + provisioning
GF_DATA_DIR="/var/lib/grafana"         # SQLite DB, dashboards, plugins
GF_LOGS_DIR="/var/log/grafana"

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

[ -d "$DEPLOY_DIR" ] || fail "deploy/grafana/ not found at ${DEPLOY_DIR} — run from inside the repo"

# ── already running? ──────────────────────────────────────────────────────────
if systemctl is-active grafana-server &>/dev/null 2>&1; then
    ok "Grafana already running — skipping"
    exit 0
fi

# ── remove any partial apt/rpm install that may have been interrupted ─────────
if command -v dpkg &>/dev/null && dpkg -s grafana &>/dev/null 2>&1; then
    warn "removing interrupted apt-installed Grafana..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get remove -y grafana 2>/dev/null || true
elif command -v rpm &>/dev/null && rpm -q grafana &>/dev/null 2>&1; then
    warn "removing interrupted rpm-installed Grafana..."
    sudo rpm -e grafana 2>/dev/null || true
fi
sudo systemctl stop  grafana-server 2>/dev/null || true
sudo systemctl reset-failed grafana-server 2>/dev/null || true

# ── arch detection ────────────────────────────────────────────────────────────
case "$(uname -m)" in
    x86_64)        ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    armv7l)        ARCH="armv7" ;;
    *) fail "unsupported CPU architecture: $(uname -m)" ;;
esac
ok "architecture: ${ARCH}  ·  Grafana v${GF_VERSION}"

# ── download + extract ────────────────────────────────────────────────────────
TARBALL="grafana-${GF_VERSION}.linux-${ARCH}.tar.gz"
URL="https://dl.grafana.com/oss/release/${TARBALL}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

warn "downloading ${URL}"
curl -fLsS "$URL" -o "${TMP_DIR}/${TARBALL}" \
    || fail "download failed — check GF_VERSION=${GF_VERSION} and network access to dl.grafana.com"
tar -xzf "${TMP_DIR}/${TARBALL}" -C "$TMP_DIR"

SRC_DIR=$(find "$TMP_DIR" -maxdepth 1 -type d -name "grafana-*" | head -1)
[ -d "$SRC_DIR" ] || fail "grafana directory not found in extracted tarball"
ok "downloaded and extracted"

# ── system user + dirs ────────────────────────────────────────────────────────
if id "$GF_USER" &>/dev/null; then
    ok "user ${GF_USER} already exists"
else
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$GF_USER" 2>/dev/null \
        || sudo useradd -rs /bin/false "$GF_USER"
    ok "created system user ${GF_USER}"
fi

sudo mkdir -p "${GF_HOME}" "${GF_CONF_DIR}/provisioning" "${GF_DATA_DIR}" "${GF_LOGS_DIR}"
sudo chown -R "${GF_USER}:${GF_USER}" "${GF_DATA_DIR}" "${GF_LOGS_DIR}"
ok "dirs ready: ${GF_HOME}  ${GF_CONF_DIR}  ${GF_DATA_DIR}"

# ── install binary + web assets ───────────────────────────────────────────────
# Grafana 10+ ships a single 'grafana' binary (grafana server / grafana cli).
# Grafana 9.x and below ships 'grafana-server' + 'grafana-cli' separately.
if [ -f "${SRC_DIR}/bin/grafana" ]; then
    sudo install -m 0755 "${SRC_DIR}/bin/grafana" /usr/sbin/grafana
    GF_SERVER_CMD="/usr/sbin/grafana server"
    GF_CLI_CMD="/usr/sbin/grafana cli"
elif [ -f "${SRC_DIR}/bin/grafana-server" ]; then
    sudo install -m 0755 "${SRC_DIR}/bin/grafana-server" /usr/sbin/grafana-server
    sudo install -m 0755 "${SRC_DIR}/bin/grafana-cli"    /usr/sbin/grafana-cli 2>/dev/null || true
    GF_SERVER_CMD="/usr/sbin/grafana-server"
    GF_CLI_CMD="/usr/sbin/grafana-cli"
else
    fail "grafana binary not found in ${SRC_DIR}/bin/"
fi

# Web assets and default config must live at GF_HOME for the UI to work
sudo cp -r "${SRC_DIR}/public" "${GF_HOME}/"
sudo cp -r "${SRC_DIR}/conf"   "${GF_HOME}/"
ok "installed binary + web assets to ${GF_HOME}"

# ── grafana.ini ───────────────────────────────────────────────────────────────
sudo tee "${GF_CONF_DIR}/grafana.ini" > /dev/null <<EOF
[server]
protocol  = http
http_port = ${GF_PORT}

[paths]
data         = ${GF_DATA_DIR}
logs         = ${GF_LOGS_DIR}
plugins      = ${GF_DATA_DIR}/plugins
provisioning = ${GF_CONF_DIR}/provisioning

[users]
allow_sign_up = false

[log]
mode  = console
level = warn
EOF
sudo chown "${GF_USER}:${GF_USER}" "${GF_CONF_DIR}/grafana.ini"
ok "wrote ${GF_CONF_DIR}/grafana.ini  (listen :${GF_PORT})"

# ── provisioning: datasource ──────────────────────────────────────────────────
sudo mkdir -p "${GF_CONF_DIR}/provisioning/datasources"
sudo cp "${DEPLOY_DIR}/provisioning/datasources/errander.yml" \
        "${GF_CONF_DIR}/provisioning/datasources/errander.yml"
sudo chown -R "${GF_USER}:${GF_USER}" "${GF_CONF_DIR}/provisioning"
ok "provisioned datasource: Prometheus → http://localhost:9091"

# ── provisioning: dashboard provider ─────────────────────────────────────────
sudo mkdir -p "${GF_CONF_DIR}/provisioning/dashboards"
sudo cp "${DEPLOY_DIR}/provisioning/dashboards/errander.yml" \
        "${GF_CONF_DIR}/provisioning/dashboards/errander.yml"
ok "provisioned dashboard provider"

# ── dashboard JSON ─────────────────────────────────────────────────────────────
sudo mkdir -p "${GF_DATA_DIR}/dashboards"
sudo cp "${DEPLOY_DIR}/dashboards/errander.json" "${GF_DATA_DIR}/dashboards/errander.json"
sudo chown -R "${GF_USER}:${GF_USER}" "${GF_DATA_DIR}/dashboards"
ok "dashboard JSON installed: Errander-AI Fleet Operations"

# ── systemd unit ──────────────────────────────────────────────────────────────
sudo tee /etc/systemd/system/grafana-server.service > /dev/null <<EOF
[Unit]
Description=Grafana (Errander-AI controller node)
Documentation=https://grafana.com/docs/grafana/latest/
Wants=network-online.target
After=network-online.target

[Service]
User=${GF_USER}
Group=${GF_USER}
Type=simple
ExecStart=${GF_SERVER_CMD} \\
  --config=${GF_CONF_DIR}/grafana.ini \\
  --homepath=${GF_HOME}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
ok "wrote /etc/systemd/system/grafana-server.service"

sudo systemctl daemon-reload
sudo systemctl enable --now grafana-server
ok "grafana-server enabled + started"

# ── wait for startup ──────────────────────────────────────────────────────────
warn "waiting for Grafana to initialise..."
_tries=0
until curl -fsS "http://localhost:${GF_PORT}/api/health" >/dev/null 2>&1; do
    _tries=$((_tries + 1))
    [ "$_tries" -gt 30 ] && fail "Grafana did not start after 30s — check: sudo journalctl -u grafana-server --no-pager -n 30"
    sleep 1
done
ok "Grafana is up on :${GF_PORT}"

# ── set admin password ────────────────────────────────────────────────────────
GF_ADMIN_PASS="$(set +o pipefail; LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 20)"
[ -z "$GF_ADMIN_PASS" ] && GF_ADMIN_PASS="$(date +%s | sha256sum | head -c 20)"

sudo -u "$GF_USER" ${GF_CLI_CMD} \
    --config="${GF_CONF_DIR}/grafana.ini" \
    --homepath="${GF_HOME}" \
    admin reset-admin-password "$GF_ADMIN_PASS" >/dev/null 2>&1 \
    || { warn "password reset failed — default admin/admin is active, change after login"
         GF_ADMIN_PASS="admin"; }

ok "admin password set"

# ── verify ────────────────────────────────────────────────────────────────────
if curl -fsS -u "admin:${GF_ADMIN_PASS}" \
        "http://localhost:${GF_PORT}/api/org" >/dev/null 2>&1; then
    ok "Grafana API authenticated successfully"
else
    warn "API auth check failed — service is up, try logging in with admin / ${GF_ADMIN_PASS}"
fi

# ── done ──────────────────────────────────────────────────────────────────────
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
