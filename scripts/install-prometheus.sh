#!/usr/bin/env bash
# Errander-AI — install Prometheus on a dedicated monitoring VM
#
# Run this on a SEPARATE monitoring VM, not on the agent VM. Point it at the
# agent VM's /metrics endpoint via AGENT_METRICS_PORT below.
# This is the "Prometheus → Errander" direction — monitoring the agent from
# outside. It is NOT the agent-reads-target-node_exporter direction
# (that is configured separately; see example/Prometheus/).
#
# Distro-agnostic: uses the upstream release tarball, so it works the same on
# Ubuntu, Debian, RHEL, Rocky, Alma, Fedora, etc. — no apt/dnf package needed.
#
# Port layout:
#   agent UI + /metrics : 9090 on the AGENT VM   (ERRANDER_METRICS_PORT)
#   prometheus          : 9091 on THIS monitoring VM
#
# Usage (run as a user with sudo):
#   bash scripts/install-prometheus.sh
#
# Overridable via env vars:
#   PROM_VERSION=2.53.2   AGENT_METRICS_PORT=9090   PROM_PORT=9091
#
# Idempotent — safe to re-run (overwrites binaries/config and restarts).

set -euo pipefail

PROM_VERSION="${PROM_VERSION:-2.53.2}"        # LTS line; override if you want newer
AGENT_METRICS_PORT="${AGENT_METRICS_PORT:-9090}"
PROM_PORT="${PROM_PORT:-9091}"
PROM_USER="prometheus"
PROM_CONF_DIR="/etc/prometheus"
PROM_DATA_DIR="/var/lib/prometheus"

# ── colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }

echo ""
echo -e "${BOLD}Errander-AI — Prometheus (monitoring VM)${NC}"
echo "═══════════════════════════════════════════"

command -v curl &>/dev/null || fail "curl is required but not installed"
command -v sudo &>/dev/null || fail "sudo is required but not installed"

# ── arch detection ──────────────────────────────────────────────────────────
case "$(uname -m)" in
    x86_64)  ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    armv7l)  ARCH="armv7" ;;
    *) fail "unsupported CPU architecture: $(uname -m)" ;;
esac
ok "architecture: ${ARCH}  ·  Prometheus v${PROM_VERSION}"

# ── download + extract ────────────────────────────────────────────────────────
TARBALL="prometheus-${PROM_VERSION}.linux-${ARCH}.tar.gz"
URL="https://github.com/prometheus/prometheus/releases/download/v${PROM_VERSION}/${TARBALL}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

warn "downloading ${URL}"
curl -fLsS "$URL" -o "${TMP_DIR}/${TARBALL}" || fail "download failed — check PROM_VERSION=${PROM_VERSION} and network access to github.com"
tar -xzf "${TMP_DIR}/${TARBALL}" -C "$TMP_DIR"
SRC_DIR="${TMP_DIR}/prometheus-${PROM_VERSION}.linux-${ARCH}"
[ -x "${SRC_DIR}/prometheus" ] || fail "prometheus binary not found in extracted tarball"
ok "downloaded and extracted"

# ── system user + dirs ─────────────────────────────────────────────────────────
if id "$PROM_USER" &>/dev/null; then
    ok "user ${PROM_USER} already exists"
else
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$PROM_USER" 2>/dev/null \
        || sudo useradd -rs /bin/false "$PROM_USER"
    ok "created system user ${PROM_USER}"
fi

sudo mkdir -p "$PROM_CONF_DIR" "$PROM_DATA_DIR"
sudo chown -R "${PROM_USER}:${PROM_USER}" "$PROM_DATA_DIR"
ok "config dir ${PROM_CONF_DIR} · data dir ${PROM_DATA_DIR}"

# ── binaries ────────────────────────────────────────────────────────────────────
sudo install -m 0755 "${SRC_DIR}/prometheus" /usr/local/bin/prometheus
sudo install -m 0755 "${SRC_DIR}/promtool"   /usr/local/bin/promtool
ok "installed prometheus + promtool to /usr/local/bin"

# ── scrape config ───────────────────────────────────────────────────────────────
# INVARIANT: the agent's /metrics is unauthenticated by design (it stays open even
# when the UI requires login), so no credentials are needed in this scrape job.
sudo tee "${PROM_CONF_DIR}/prometheus.yml" > /dev/null <<EOF
# Managed by scripts/install-prometheus.sh — dedicated monitoring VM.
# Scrapes the Errander-AI agent's /metrics endpoint (set static_configs target below).
global:
  scrape_interval: 15s

scrape_configs:
  # The Errander-AI agent (this host). UI + /metrics live on ${AGENT_METRICS_PORT}.
  - job_name: "errander-agent"
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:${AGENT_METRICS_PORT}"]

  # Prometheus scraping itself (health/meta).
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:${PROM_PORT}"]
EOF
sudo chown "${PROM_USER}:${PROM_USER}" "${PROM_CONF_DIR}/prometheus.yml"
sudo -u "$PROM_USER" /usr/local/bin/promtool check config "${PROM_CONF_DIR}/prometheus.yml" >/dev/null \
    || fail "prometheus.yml failed promtool validation"
ok "wrote + validated ${PROM_CONF_DIR}/prometheus.yml  (scrapes agent on :${AGENT_METRICS_PORT})"

# ── systemd unit ─────────────────────────────────────────────────────────────────
sudo tee /etc/systemd/system/prometheus.service > /dev/null <<EOF
[Unit]
Description=Prometheus (Errander-AI monitoring VM)
Wants=network-online.target
After=network-online.target

[Service]
User=${PROM_USER}
Group=${PROM_USER}
Type=simple
ExecStart=/usr/local/bin/prometheus \\
  --config.file=${PROM_CONF_DIR}/prometheus.yml \\
  --storage.tsdb.path=${PROM_DATA_DIR} \\
  --web.listen-address=:${PROM_PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
ok "wrote /etc/systemd/system/prometheus.service  (listen :${PROM_PORT})"

sudo systemctl daemon-reload
sudo systemctl enable --now prometheus
ok "prometheus service enabled + started"

# ── verify ────────────────────────────────────────────────────────────────────
sleep 2
if curl -fsS "http://localhost:${PROM_PORT}/-/healthy" >/dev/null 2>&1; then
    ok "prometheus is healthy on :${PROM_PORT}"
else
    warn "health check did not pass yet — give it a few seconds, then: systemctl status prometheus"
fi

echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Prometheus installed on this monitoring VM.${NC}"
echo ""
echo "  Prometheus UI   : http://<controller-ip>:${PROM_PORT}"
echo "  Scrape target   : localhost:${AGENT_METRICS_PORT}/metrics  (the agent)"
echo "  Targets page    : http://<controller-ip>:${PROM_PORT}/targets"
echo ""
echo "  Note: the agent must be running for its target to show UP."
echo "  Point Grafana (anywhere) at this Prometheus for dashboards."
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
