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
echo "    3. Verify your SSH key exists (see SETUP.md Step 2 to create one)"
echo "    4. Optionally configure Slack, Prometheus, and ELK (all optional — press N to skip)"
echo "    5. Set web UI credentials, optionally encrypt secrets, write .env and inventory.yaml"
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
KEEP_INVENTORY=false
TARGETS_YAML=""
VM_COUNT=0
_add_vms="y"

if [ -f "inventory.yaml" ]; then
    # Re-run: read existing settings silently and reuse them
    step "2/5" "Target VMs"
    echo ""
    _existing_vms=$(grep -c "^\s*- host:" inventory.yaml 2>/dev/null || echo 0)
    ENV_NAME=$(grep -m1 "^environments:" -A1 inventory.yaml | tail -1 | tr -d ' :' || true)
    ENV_NAME="${ENV_NAME:-dev}"
    SSH_USER=$(grep -m1 "ssh_user:" inventory.yaml | awk '{print $2}' || true)
    SSH_USER="${SSH_USER:-errander}"
    SSH_KEY_PATH=$(grep -m1 "ssh_key_path:" inventory.yaml | awk '{print $2}' || true)
    SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/errander_prod}"
    VM_COUNT=$_existing_vms
    ok "Reusing settings from existing inventory.yaml  (env=${ENV_NAME}, ssh_user=${SSH_USER})"
    if [ "$_existing_vms" -gt 0 ]; then
        echo "  Current VMs:"
        grep -E "host:|name:" inventory.yaml | grep -v "ssh_" | sed 's/^/    /' || true
        echo ""
        printf "  Keep these VMs? (Y/n): "
        read -r _keep || true
        echo ""
        case "${_keep,,}" in
          n|no) KEEP_INVENTORY=false; VM_COUNT=0; TARGETS_YAML="" ;;
          *)    KEEP_INVENTORY=true ;;
        esac
    fi
    echo ""
    printf "  Add more VMs? (y/N): "
    read -r _add_vms || true
    echo ""
else
    # Fresh install: ask first, show section header only if they say yes
    echo ""
    printf "  [2/5] Do you want to add target VMs now? (Y/n): "
    read -r _add_vms || true
    _add_vms="${_add_vms:-y}"   # Enter = Y (default)
    echo ""

    case "${_add_vms,,}" in
      n|no)
        ENV_NAME="dev"
        prompt_val "SSH user on target VMs" "errander"
        SSH_USER="$REPLY"
        prompt_val "SSH key path" "~/.ssh/errander_prod"
        SSH_KEY_PATH="$REPLY"
        ;;
      *)
        step "2/5" "Target VMs"
        echo ""
        prompt_val "Environment name" "dev"
        ENV_NAME="$REPLY"
        prompt_val "SSH user on target VMs" "errander"
        SSH_USER="$REPLY"
        prompt_val "SSH key path" "~/.ssh/errander_prod"
        SSH_KEY_PATH="$REPLY"
        ;;
    esac
fi

case "${_add_vms,,}" in
  y|yes)
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
  *)
    warn "No VMs added — you can add them later by editing inventory.yaml"
    ;;
esac

[ "$VM_COUNT" -gt 0 ] && ok "$VM_COUNT VM(s) in environment '${ENV_NAME}'"

# ── 3. SSH key — verify only (users set this up in SETUP.md Step 2) ───────────
SSH_KEY_EXPANDED="${SSH_KEY_PATH/#\~/$HOME}"

if [ -f "$SSH_KEY_EXPANDED" ]; then
    ok "[3/5] SSH key found at $SSH_KEY_EXPANDED"
else
    warn "[3/5] SSH key not found at $SSH_KEY_EXPANDED"
    echo "  The agent cannot SSH to target VMs without this key."
    echo "  Follow SETUP.md Step 2 to generate it, then re-run this script:"
    echo "    ssh-keygen -t ed25519 -f $SSH_KEY_EXPANDED -C \"errander-agent\" -N \"\""
    echo ""
    warn "Setup is incomplete — re-run configure.sh after creating the SSH key."
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

# ── 4b. Prometheus (optional) ────────────────────────────────────────────────
echo ""
echo "  Prometheus is optional. This sets the global default URL used by --ask and"
echo "  --probe-now. You can override it per-environment in inventory.yaml via"
echo "  'prometheus_url:' under the environment block."
printf "  Do you have Prometheus running? (y/N): "
read -r PROM_CHOICE
echo ""

PROMETHEUS_BASE_URL=""
case "${PROM_CHOICE,,}" in
  y|yes)
    prompt_val "Prometheus URL (global default)" "http://localhost:9090"
    PROMETHEUS_BASE_URL="$REPLY"
    ok "Prometheus configured: $PROMETHEUS_BASE_URL"
    ;;
  *)
    ok "Skipping Prometheus — live VM metrics will use SSH-only probes"
    ;;
esac

# ── 4c. ELK / Elasticsearch (optional) ───────────────────────────────────────
echo ""
echo "  ELK is optional. This sets the global default used by --ask and --probe-now."
echo "  You can override it per-environment in inventory.yaml via 'elk_url:',"
echo "  'elk_api_key:', and 'elk_index_pattern:' under the environment block."
printf "  Do you use ELK / Elasticsearch for log aggregation? (y/N): "
read -r ELK_CHOICE
echo ""

ELK_BASE_URL=""
ELK_API_KEY=""
ELK_INDEX_PATTERN="filebeat-*,logstash-*"

case "${ELK_CHOICE,,}" in
  y|yes)
    prompt_val "Elasticsearch URL (global default)" "http://localhost:9200"
    ELK_BASE_URL="$REPLY"
    prompt_val "API key  (press Enter to skip for unauthenticated)" ""
    ELK_API_KEY="$REPLY"
    prompt_val "Index pattern" "filebeat-*,logstash-*"
    ELK_INDEX_PATTERN="$REPLY"
    ok "ELK configured: $ELK_BASE_URL  index=$ELK_INDEX_PATTERN"
    ;;
  *)
    ok "Skipping ELK — journal errors will be read via journalctl over SSH"
    ;;
esac

# ── 5. Write files ────────────────────────────────────────────────────────────
step "5/5" "Writing .env and inventory.yaml"

# ── Web UI credentials ────────────────────────────────────────────────────────
echo ""
# On re-run: read existing values as defaults so the user can accept or change them
_existing_ui_user="admin"
_existing_ui_pass=""
if [ -f ".env" ]; then
    _u=$(grep "^ERRANDER_UI_USER=" .env 2>/dev/null | cut -d= -f2- || true)
    _p=$(grep "^ERRANDER_UI_PASSWORD=" .env 2>/dev/null | cut -d= -f2- || true)
    [ -n "$_u" ] && _existing_ui_user="$_u"
    [ -n "$_p" ] && _existing_ui_pass="$_p"
fi

prompt_val "Web UI username" "$_existing_ui_user"
_ui_user="$REPLY"

# Password: if one already exists show a hint rather than requiring re-entry
if [ -n "$_existing_ui_pass" ]; then
    echo ""
    printf "    Web UI password  (Enter to keep existing): "
    read -rs _new_pass || true
    _new_pass="${_new_pass%$'\r'}"
    echo ""
    _ui_pass="${_new_pass:-$_existing_ui_pass}"
else
    while true; do
        prompt_secret "Web UI password"
        _ui_pass="$REPLY"
        if [ -z "$_ui_pass" ]; then
            warn "Password cannot be empty — please enter one"
            continue
        fi
        prompt_secret "Confirm password"
        if [ "$REPLY" != "$_ui_pass" ]; then
            warn "Passwords do not match — try again"
            continue
        fi
        break
    done
fi

# ── Encryption (optional) ─────────────────────────────────────────────────────
echo ""
printf "  Encrypt sensitive values in .env? (y/N): "
read -r _enc_choice || true
echo ""

_encrypt=false
SECRETS_KEY=""
KEY_FILE="${HOME}/.errander.key"

case "${_enc_choice,,}" in
  y|yes)
    # Reuse the existing key if one is already on disk — generating a new key
    # every re-run would make all previously-encrypted .env values unreadable.
    if [ -f "$KEY_FILE" ]; then
        _existing_key_line=$(grep "^ERRANDER_SECRETS_KEY=" "$KEY_FILE" 2>/dev/null || true)
        SECRETS_KEY="${_existing_key_line#ERRANDER_SECRETS_KEY=}"
        if [ -n "$SECRETS_KEY" ]; then
            _encrypt=true
            export ERRANDER_SECRETS_KEY="${SECRETS_KEY}"
            ok "Reusing existing encryption key from ${KEY_FILE}"
        fi
    fi

    if [ -z "$SECRETS_KEY" ]; then
        warn "Generating new encryption key..."
        _key_line=$(uv run python -m errander --generate-secrets-key 2>/dev/null | grep "^ERRANDER_SECRETS_KEY=" || true)
        SECRETS_KEY="${_key_line#ERRANDER_SECRETS_KEY=}"
        if [ -z "$SECRETS_KEY" ]; then
            warn "Key generation failed — writing .env with plaintext values"
        else
            _encrypt=true
            # Write key to a separate file — never stored in .env itself
            echo "ERRANDER_SECRETS_KEY=${SECRETS_KEY}" > "$KEY_FILE"
            chmod 600 "$KEY_FILE"
            ok "Encryption key saved to ${KEY_FILE}  (chmod 600)"

            # Wire key into shell RC so every new session loads it automatically
            SHELL_RC="${HOME}/.bashrc"
            [ -f "${HOME}/.zshrc" ] && SHELL_RC="${HOME}/.zshrc"
            _marker="# errander secrets key"
            if ! grep -q "$_marker" "$SHELL_RC" 2>/dev/null; then
                printf '\n%s\n[ -f "%s" ] && set -a && source "%s" && set +a\n' \
                    "$_marker" "$KEY_FILE" "$KEY_FILE" >> "$SHELL_RC"
                ok "Key auto-load added to ${SHELL_RC}"
            else
                ok "Key auto-load already present in ${SHELL_RC}"
            fi
            # Export into the current session too so the LLM verify step works now
            export ERRANDER_SECRETS_KEY="${SECRETS_KEY}"

            # Wire into systemd service file if already installed
            _svc="/etc/systemd/system/errander.service"
            if [ -f "$_svc" ]; then
                if ! grep -q "$KEY_FILE" "$_svc"; then
                    sudo sed -i "s|EnvironmentFile=.*\.env|EnvironmentFile=${KEY_FILE}\nEnvironmentFile=$(pwd)/.env|" "$_svc"
                    sudo systemctl daemon-reload
                    ok "Systemd service updated — key EnvironmentFile injected"
                else
                    ok "Systemd service already references ${KEY_FILE}"
                fi
            fi

            echo ""
            echo -e "  ${BOLD}Back up this key — losing it means losing all encrypted credentials:${NC}"
            echo "  ERRANDER_SECRETS_KEY=${SECRETS_KEY}"
            echo ""
        fi
    fi
    ;;
esac

# Encrypt a value if encryption is on; pass through plaintext or already-encrypted values unchanged
encrypt_val() {
    local val="$1"
    if $_encrypt && [ -n "$SECRETS_KEY" ]; then
        if [[ "$val" == enc:v1:* ]]; then
            echo "$val"  # already encrypted — don't double-encrypt on re-run
        else
            ERRANDER_SECRETS_KEY="$SECRETS_KEY" \
                uv run python -m errander --encrypt "$val" 2>/dev/null || echo "$val"
        fi
    else
        echo "$val"
    fi
}

_env_llm_api_key=$(encrypt_val "$LLM_API_KEY")
_env_ui_pass=$(encrypt_val "$_ui_pass")
_env_slack_token=""
[ -n "$SLACK_BOT_TOKEN" ] && _env_slack_token=$(encrypt_val "$SLACK_BOT_TOKEN")

# .env
{
    echo "# Errander-AI — generated by configure.sh  (do not commit)"
    echo ""
    echo "ERRANDER_LLM_BASE_URL=${LLM_BASE_URL}"
    echo "ERRANDER_LLM_MODEL=${LLM_MODEL}"
    echo "ERRANDER_LLM_API_KEY=${_env_llm_api_key}"
    echo ""
    echo "ERRANDER_AUDIT_DB_URL=errander.sqlite"
    echo ""
    if [ -n "$SLACK_BOT_TOKEN" ]; then
        echo "ERRANDER_SLACK_BOT_TOKEN=${_env_slack_token}"
        echo "ERRANDER_SLACK_CHANNEL_ID=${SLACK_CHANNEL_ID}"
    else
        echo "# Slack not configured — web UI approval mode active"
        echo "# ERRANDER_SLACK_BOT_TOKEN=xoxb-..."
        echo "# ERRANDER_SLACK_CHANNEL_ID=C..."
    fi
    echo ""
    echo "ERRANDER_UI_USER=${_ui_user}"
    echo "ERRANDER_UI_PASSWORD=${_env_ui_pass}"
    echo ""
    if [ -n "$PROMETHEUS_BASE_URL" ]; then
        echo "ERRANDER_PROMETHEUS_BASE_URL=${PROMETHEUS_BASE_URL}"
    else
        echo "# Prometheus not configured (optional — enables VM metrics in --ask and probe digest)"
        echo "# ERRANDER_PROMETHEUS_BASE_URL=http://localhost:9090"
    fi
    echo ""
    if [ -n "$ELK_BASE_URL" ]; then
        echo "ERRANDER_ELK_BASE_URL=${ELK_BASE_URL}"
        [ -n "$ELK_API_KEY" ] && echo "ERRANDER_ELK_API_KEY=${ELK_API_KEY}"
        echo "ERRANDER_ELK_INDEX_PATTERN=${ELK_INDEX_PATTERN}"
    else
        echo "# ELK not configured (optional — adds log error summaries to --ask and probe digest)"
        echo "# ERRANDER_ELK_BASE_URL=http://localhost:9200"
        echo "# ERRANDER_ELK_API_KEY=your-api-key"
        echo "# ERRANDER_ELK_INDEX_PATTERN=filebeat-*,logstash-*"
    fi
    echo ""
    echo "# SSH host key verification"
    echo "# Run --bootstrap-known-hosts <env> to pin host keys, then set this to true"
    echo "ERRANDER_SSH_STRICT_HOST_KEYS=false"
} > .env

chmod 600 .env
ok ".env written  (permissions: 600)"

# inventory.yaml
if $KEEP_INVENTORY; then
    if [ -n "$TARGETS_YAML" ]; then
        printf '%s' "$TARGETS_YAML" >> inventory.yaml
        ok "inventory.yaml updated — appended new VM(s) to existing"
    else
        ok "inventory.yaml unchanged (kept existing)"
    fi
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
   ERRANDER_SECRETS_KEY="${SECRETS_KEY:-}" \
   uv run python -m errander --check-llm 2>&1; then
    ok "LLM connection verified"
else
    warn "LLM check failed — the agent will use hardcoded fallback logic until this is fixed"
    warn "Edit .env and re-run:  uv run python -m errander --check-llm"
fi

# ── SSH host key bootstrap ─────────────────────────────────────────────────────
if [ "$VM_COUNT" -gt 0 ] && [ -f "$SSH_KEY_EXPANDED" ]; then
    echo ""
    warn "SSH host key bootstrap (recommended)"
    echo "  Pins each VM's host key so Errander verifies identity on every connection."
    echo "  Without this, TOFU mode is active (WARNING logged per connection)."
    echo ""
    printf "  Pin host keys now? (Y/n): "
    read -r _pin || true
    echo ""
    case "${_pin,,}" in
      n|no)
        ok "Skipping — TOFU mode active (ERRANDER_SSH_STRICT_HOST_KEYS=false in .env)"
        ;;
      *)
        if uv run python -m errander --bootstrap-known-hosts "$ENV_NAME" --inventory inventory.yaml; then
            sed -i 's/^ERRANDER_SSH_STRICT_HOST_KEYS=false/ERRANDER_SSH_STRICT_HOST_KEYS=true/' .env
            ok "Host keys pinned — strict mode enabled (ERRANDER_SSH_STRICT_HOST_KEYS=true)"
        else
            warn "Bootstrap failed — TOFU mode remains active (ERRANDER_SSH_STRICT_HOST_KEYS=false)"
        fi
        ;;
    esac
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

echo "  Next steps (continue from SETUP.md Step 6):"
echo ""
echo "  Step 6 — Verify:"
echo "    uv run python -m errander --check-inventory"
echo "    uv run python -m errander --check-targets ${ENV_NAME}"
echo "    (LLM connection already verified above)"
echo ""
echo "  Step 7 — Dry-run:"
echo "    uv run python -m errander --run-now --env ${ENV_NAME} --inventory inventory.yaml --dry-run --force --force-reason \"initial dry-run validation\""
echo "    (--force bypasses the maintenance window for this first run)"
echo ""
echo "  Web UI (once the agent is running):"
echo "    http://<master-vm-ip>:9090/ui"
echo -e "${BOLD}═══════════════════════════════════════════${NC}"
echo ""
