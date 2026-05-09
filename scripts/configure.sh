#!/usr/bin/env bash
# Errander-AI interactive configuration script
#
# Prompts for LLM credentials, target VMs, SSH key, and optional Slack,
# then writes .env + inventory.yaml and verifies the LLM connection.
#
# Prerequisites:
#   - bootstrap.sh must have already run (uv, Python 3.12, repo cloned)
#   - Have your LLM endpoint URL, model name, and API key ready
#
# Usage (from inside the errander/ repo root):
#   bash scripts/configure.sh

set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}▶${NC} $*"; }
fail() { echo -e "\n  ${RED}✗ ERROR:${NC} $*\n"; exit 1; }
step() { echo -e "\n${BOLD}[$1]${NC} $2"; }

# prompt_val "label" "default"  →  result in REPLY
prompt_val() {
    local label="$1" default="${2:-}"
    if [ -n "$default" ]; then
        printf "    %s [%s]: " "$label" "$default"
    else
        printf "    %s: " "$label"
    fi
    read -r REPLY || true          # || true: prevent set -e exit on EOF/signal
    REPLY="${REPLY%$'\r'}"         # strip trailing \r (Windows clipboard paste)
    REPLY="${REPLY:-$default}"     # use default if empty (safe: always exits 0)
}

# prompt_secret "label"  →  result in REPLY (no echo)
prompt_secret() {
    printf "    %s: " "$1"
    read -rs REPLY || true
    REPLY="${REPLY%$'\r'}"
    echo
}

# ── banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Errander-AI — Interactive Setup${NC}"
echo "═══════════════════════════════════════════"
echo ""
echo "  This script will:"
echo "    1. Collect your LLM credentials and verify the connection"
echo "    2. Add your target VMs"
echo "    3. Generate an SSH key pair (or reuse existing)"
echo "    4. Optionally configure Slack notifications"
echo "    5. Write .env and inventory.yaml"
echo ""
echo "  Have your LLM endpoint URL, model name, and API key ready."
echo ""

# ── 0. Sanity checks ──────────────────────────────────────────────────────────
step "0/5" "Checking prerequisites"

[ -f "errander/__init__.py" ] \
    || fail "Run this from the repo root: cd errander && bash scripts/configure.sh"
command -v uv &>/dev/null \
    || fail "uv not found — run scripts/bootstrap.sh first"

ok "Running from repo root"
ok "uv found  ($(uv --version))"

# ── 1. LLM ───────────────────────────────────────────────────────────────────
step "1/5" "LLM configuration"
echo ""
echo "  Which LLM provider are you using?"
echo "    1) Azure AI Foundry"
echo "    2) OpenAI"
echo "    3) Groq  (free tier at console.groq.com)"
echo "    4) Ollama  (self-hosted — any machine, CPU or GPU)"
echo "    5) vLLM  (self-hosted — dedicated NVIDIA GPU)"
echo "    6) Other OpenAI-compatible API"
echo ""
printf "  Choice [1-6]: "
read -r LLM_CHOICE
echo ""

case "$LLM_CHOICE" in
  1)
    warn "Azure AI Foundry"
    echo "    Find these at: Azure portal → AI Foundry resource → Keys and Endpoint"
    echo "    The portal gives you the base domain only — append /openai/v1/ yourself."
    echo "    e.g. https://<resource>.cognitiveservices.azure.com/openai/v1/"
    echo ""
    prompt_val "Endpoint URL (must end with /openai/v1/)"
    LLM_BASE_URL="$REPLY"
    prompt_val "Deployment name (e.g. gpt-4o-mini-deploy — NOT the model ID)"
    LLM_MODEL="$REPLY"
    prompt_secret "API key"
    LLM_API_KEY="$REPLY"
    ;;
  2)
    warn "OpenAI"
    LLM_BASE_URL="https://api.openai.com/v1"
    ok "Base URL set to $LLM_BASE_URL"
    prompt_val "Model" "gpt-4o-mini"
    LLM_MODEL="$REPLY"
    prompt_secret "API key (sk-...)"
    LLM_API_KEY="$REPLY"
    ;;
  3)
    warn "Groq"
    LLM_BASE_URL="https://api.groq.com/openai/v1"
    ok "Base URL set to $LLM_BASE_URL"
    prompt_val "Model" "llama-3.3-70b-versatile"
    LLM_MODEL="$REPLY"
    prompt_secret "API key (gsk_...)"
    LLM_API_KEY="$REPLY"
    ;;
  4)
    warn "Ollama"
    prompt_val "Ollama URL" "http://localhost:11434/v1"
    LLM_BASE_URL="$REPLY"
    prompt_val "Model" "qwen3:8b"
    LLM_MODEL="$REPLY"
    LLM_API_KEY="ollama"
    ok "API key set to 'ollama'  (Ollama ignores this value)"
    ;;
  5)
    warn "vLLM"
    prompt_val "vLLM URL (e.g. http://<gpu-vm-private-ip>:8000/v1)"
    LLM_BASE_URL="$REPLY"
    prompt_val "Model" "Qwen/Qwen3-8B-AWQ"
    LLM_MODEL="$REPLY"
    prompt_val "API key  (press Enter to skip for unauthenticated vLLM)" ""
    LLM_API_KEY="${REPLY:-not-needed}"
    ;;
  *)
    warn "Custom OpenAI-compatible API"
    prompt_val "Base URL"
    LLM_BASE_URL="$REPLY"
    prompt_val "Model"
    LLM_MODEL="$REPLY"
    prompt_val "API key  (press Enter to skip)" ""
    LLM_API_KEY="${REPLY:-not-needed}"
    ;;
esac

ok "LLM: $LLM_BASE_URL  model=$LLM_MODEL"

# ── 2. Target VMs ─────────────────────────────────────────────────────────────
step "2/5" "Target VMs"
echo ""

KEEP_INVENTORY=false
TARGETS_YAML=""
VM_COUNT=0

# If inventory.yaml exists, silently reuse env/SSH settings and skip those prompts
if [ -f "inventory.yaml" ]; then
    _existing_vms=$(grep -c "^\s*- host:" inventory.yaml 2>/dev/null || echo 0)
    ENV_NAME=$(grep -m1 "^environments:" -A1 inventory.yaml | tail -1 | tr -d ' :')
    ENV_NAME="${ENV_NAME:-dev}"
    SSH_USER=$(grep -m1 "ssh_user:" inventory.yaml | awk '{print $2}')
    SSH_USER="${SSH_USER:-errander}"
    SSH_KEY_PATH=$(grep -m1 "ssh_key_path:" inventory.yaml | awk '{print $2}')
    SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/errander_prod}"
    VM_COUNT=$_existing_vms
    ok "Reusing settings from existing inventory.yaml  (env=${ENV_NAME}, ssh_user=${SSH_USER})"
    if [ "$_existing_vms" -gt 0 ]; then
        echo "  Current VMs:"
        grep -E "host:|name:" inventory.yaml | grep -v "ssh_" | sed 's/^/    /'
        echo ""
        printf "  Keep existing VMs and just add more? (Y/n): "
        read -r _keep || true
        echo ""
        case "${_keep,,}" in
          n|no) KEEP_INVENTORY=false; VM_COUNT=0; TARGETS_YAML="" ;;
          *)    KEEP_INVENTORY=true ;;
        esac
    fi
else
    prompt_val "Environment name" "dev"
    ENV_NAME="$REPLY"

    prompt_val "SSH user on target VMs" "errander"
    SSH_USER="$REPLY"

    prompt_val "SSH key path" "~/.ssh/errander_prod"
    SSH_KEY_PATH="$REPLY"
fi

echo ""
printf "  Do you want to add target VMs now? (Y/n): "
read -r _add_vms || true
echo ""

case "${_add_vms,,}" in
  n|no)
    warn "No VMs added — you can add them later by editing inventory.yaml"
    ;;
  *)
    while true; do
        prompt_val "  VM hostname or private IP" ""
        VM_HOST="$REPLY"
        [ -z "$VM_HOST" ] && break

        VM_COUNT=$((VM_COUNT + 1))
        DEFAULT_NAME="${ENV_NAME}-vm-$(printf '%02d' $VM_COUNT)"

        prompt_val "  VM name" "$DEFAULT_NAME"
        VM_NAME="$REPLY"

        prompt_val "  OS family  (ubuntu / debian / rhel)" "ubuntu"
        VM_OS="$REPLY"

        TARGETS_YAML="${TARGETS_YAML}      - host: ${VM_HOST}
        name: ${VM_NAME}
        os_family: ${VM_OS}
"
        ok "Added $VM_NAME  ($VM_HOST, $VM_OS)"
        echo ""

        printf "  Add another VM? (y/N): "
        read -r _more || true
        echo ""
        case "${_more,,}" in
          y|yes) continue ;;
          *) break ;;
        esac
    done
    ;;
esac

[ "$VM_COUNT" -gt 0 ] && ok "$VM_COUNT VM(s) in environment '${ENV_NAME}'"

# ── 3. SSH key ────────────────────────────────────────────────────────────────
step "3/5" "SSH key pair"

SSH_KEY_EXPANDED="${SSH_KEY_PATH/#\~/$HOME}"

_key_is_new=false
if [ -f "$SSH_KEY_EXPANDED" ]; then
    ok "Key already exists at $SSH_KEY_EXPANDED — reusing"
else
    warn "Generating new key pair at $SSH_KEY_EXPANDED ..."
    mkdir -p "$(dirname "$SSH_KEY_EXPANDED")"
    ssh-keygen -t ed25519 -f "$SSH_KEY_EXPANDED" -C "errander-agent" -N ""
    ok "Key pair generated"
    _key_is_new=true
fi

SSH_PUBKEY="$(cat "$SSH_KEY_EXPANDED.pub")"

if $_key_is_new; then
    echo ""
    echo -e "  ${BOLD}Public key — install this on every target VM:${NC}"
    echo "  ┌────────────────────────────────────────────────────────────────────┐"
    echo "  │ $SSH_PUBKEY"
    echo "  └────────────────────────────────────────────────────────────────────┘"
    echo ""
    echo "  On each Target VM (SETUP.md Step 2 for the full sequence):"
    echo "    sudo useradd -m -s /bin/bash $SSH_USER"
    echo "    sudo mkdir -p /home/$SSH_USER/.ssh && sudo chmod 700 /home/$SSH_USER/.ssh"
    echo "    echo \"$SSH_PUBKEY\" | sudo tee /home/$SSH_USER/.ssh/authorized_keys"
    echo "    sudo chmod 600 /home/$SSH_USER/.ssh/authorized_keys"
    echo "    sudo chown -R $SSH_USER:$SSH_USER /home/$SSH_USER/.ssh"
    echo ""
    warn "Complete SETUP.md Steps 2-3 (SSH + sudo) on each Target VM before running the agent."
fi

# ── 4. Slack ──────────────────────────────────────────────────────────────────
step "4/5" "Slack  (optional)"
echo ""
echo "  When Slack is not configured, approvals go to the web UI at"
echo "  http://<master-vm-ip>:9090/ui/approvals instead."
echo ""
printf "  Enable Slack notifications? (y/N): "
read -r SLACK_CHOICE
echo ""

SLACK_BOT_TOKEN=""
SLACK_CHANNEL_ID=""

case "${SLACK_CHOICE,,}" in
  y|yes)
    echo "  Get these from api.slack.com/apps → your app → OAuth & Permissions"
    echo ""
    prompt_secret "Bot User OAuth Token  (xoxb-...)"
    SLACK_BOT_TOKEN="$REPLY"
    prompt_val "Channel ID  (C...)"
    SLACK_CHANNEL_ID="$REPLY"
    ok "Slack configured"
    ;;
  *)
    ok "Skipping Slack — web UI approval mode will be active"
    ;;
esac

# ── 5. Write files ────────────────────────────────────────────────────────────
step "5/5" "Writing .env and inventory.yaml"

# .env
{
    echo "# Errander-AI — generated by configure.sh  (do not commit)"
    echo ""
    echo "ERRANDER_LLM_BASE_URL=${LLM_BASE_URL}"
    echo "ERRANDER_LLM_MODEL=${LLM_MODEL}"
    echo "ERRANDER_LLM_API_KEY=${LLM_API_KEY}"
    echo ""
    echo "ERRANDER_AUDIT_DB_URL=errander.sqlite"
    echo ""
    if [ -n "$SLACK_BOT_TOKEN" ]; then
        echo "ERRANDER_SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}"
        echo "ERRANDER_SLACK_CHANNEL_ID=${SLACK_CHANNEL_ID}"
    else
        echo "# Slack not configured — web UI approval mode active"
        echo "# ERRANDER_SLACK_BOT_TOKEN=xoxb-..."
        echo "# ERRANDER_SLACK_CHANNEL_ID=C..."
    fi
    echo ""
    echo "ERRANDER_UI_USER=admin"
    echo "ERRANDER_UI_PASSWORD=changeme"
} > .env

ok ".env written"

# inventory.yaml
if $KEEP_INVENTORY; then
    ok "inventory.yaml unchanged (kept existing)"
else
    {
        echo "# Errander-AI inventory — generated by configure.sh"
        echo "environments:"
        echo "  ${ENV_NAME}:"
        echo "    ssh_user: ${SSH_USER}"
        echo "    ssh_key_path: ${SSH_KEY_PATH}"
        echo "    approval_policy: relaxed"
        echo "    maintenance_window: \"08:00-20:00\""
        echo "    maintenance_days: [monday, tuesday, wednesday, thursday, friday]"
        echo "    maintenance_timezone: UTC"
        echo "    targets:"
        printf '%s' "$TARGETS_YAML"
    } > inventory.yaml
    ok "inventory.yaml written"
fi

# Verify LLM
echo ""
warn "Verifying LLM connection..."
if ERRANDER_LLM_BASE_URL="$LLM_BASE_URL" \
   ERRANDER_LLM_MODEL="$LLM_MODEL" \
   ERRANDER_LLM_API_KEY="$LLM_API_KEY" \
   uv run python -m errander --check-llm 2>&1; then
    ok "LLM connection verified"
else
    warn "LLM check failed — the agent will use hardcoded fallback logic until this is fixed"
    warn "Edit .env and re-run:  uv run python -m errander --check-llm"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo -e "${GREEN} Setup complete!${NC}"
echo ""
echo "  Files written:"
echo "    .env            — LLM credentials + UI auth"
if $KEEP_INVENTORY; then
    echo "    inventory.yaml  — kept existing (${VM_COUNT} VM(s) in '${ENV_NAME}' environment)"
else
    echo "    inventory.yaml  — ${VM_COUNT} VM(s) in '${ENV_NAME}' environment"
fi
echo ""

if [ "$VM_COUNT" -eq 0 ]; then
    echo -e "  ${YELLOW}▶ No VMs configured yet. Add VMs to inventory.yaml before running the agent:${NC}"
    echo ""
    echo "    nano inventory.yaml"
    echo ""
    echo "  Add each VM under the 'targets:' key:"
    echo ""
    echo "    targets:"
    echo "      - host: 10.0.0.10          # private IP or hostname"
    echo "        name: ${ENV_NAME}-vm-01  # friendly name"
    echo "        os_family: ubuntu        # ubuntu / debian / rhel"
    echo "      - host: 10.0.0.11"
    echo "        name: ${ENV_NAME}-vm-02"
    echo "        os_family: ubuntu"
    echo ""
fi

echo -e "  ${BOLD}Before running the agent:${NC}"
echo "  Complete SETUP.md Steps 2-3 on each target VM (errander user + sudo)."
echo ""
echo "  Then run a dry-run:"
echo "    export \$(grep -v '^#' .env | xargs)"
echo "    uv run python -m errander --run-now --env ${ENV_NAME} --inventory inventory.yaml --dry-run"
echo ""
echo "  Web UI (once the agent is running):"
echo "    http://<master-vm-ip>:9090/ui"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
