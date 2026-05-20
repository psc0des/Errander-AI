#!/usr/bin/env bash
# configure.sh — Errander-AI interactive setup
#
# Runs the Node Exporter configuration flow for every VM in inventory.yaml:
#   - Checks SSH connectivity
#   - Detects whether Node Exporter is already running on :9100
#   - Offers to install it where absent (default: Y)
#   - Writes node_exporter: true/false into inventory.yaml
#
# Prerequisites:
#   cp example/inventory.yaml inventory.yaml   # edit with your VM IPs + keys
#   uv sync --extra dev                         # install Python deps
#
# Usage:
#   bash configure.sh
#
# Re-run at any time to update the configuration (e.g. after adding new VMs).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv &>/dev/null; then
    echo ""
    echo "  Error: 'uv' not found."
    echo "  Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    exit 1
fi

if [ ! -f "inventory.yaml" ]; then
    echo ""
    echo "  Error: inventory.yaml not found."
    echo "  Copy the example first:"
    echo "    cp example/inventory.yaml inventory.yaml"
    echo "  Then edit it with your VM hostnames and SSH keys."
    echo ""
    exit 1
fi

exec uv run python -m errander.config.configure "$@"
