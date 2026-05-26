#!/usr/bin/env bash
# add-target.sh — Add new target VMs to an existing Errander-AI inventory
#
# Use this instead of re-running the full configure.sh wizard when you only
# need to add VMs to an existing installation.  Your .env and all other
# settings remain untouched.
#
# Prerequisites:
#   - configure.sh has already run (inventory.yaml must exist)
#   - uv is installed (bootstrap.sh handles this)
#
# Usage (from inside the errander/ repo root):
#   bash scripts/add-target.sh
#
# After adding VMs, complete per-target setup on each new VM:
#   1. Create errander user + install SSH public key (SETUP.md Step 2)
#   2. Configure passwordless sudo (SETUP.md Step 3)
#   3. Install optional wrappers if the env uses Docker hygiene or service restart
#   4. Verify:  uv run python -m errander --check-targets <env>
#   5. Pin host key: uv run python -m errander --bootstrap-known-hosts <env>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

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
    echo "  Run configure.sh first to create the initial inventory:"
    echo "    bash scripts/configure.sh"
    echo ""
    exit 1
fi

exec uv run python -m errander.config.add_target "$@"
