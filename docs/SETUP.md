# Errander-AI — Setup Guide

Step-by-step instructions for getting Errander-AI running from scratch.

---

## Overview

Errander-AI has three components, each running on its own VM (or the same VM for dev):

```
┌─────────────────────────────────────────┐
│              Private VPN                │
│                                         │
│  ┌──────────────┐   ┌────────────────┐  │
│  │  LLM VM       │   │  Agent VM      │  │
│  │  vLLM +       │◄──│  Errander-AI     │  │
│  │  Qwen3-8B-AWQ │   │  + SQLite      │  │
│  └──────────────┘   └───────┬────────┘  │
│                              │ SSH       │
│                     ┌────────▼────────┐  │
│                     │  Target VMs      │  │
│                     │  (Ubuntu/RHEL)   │  │
│                     └─────────────────┘  │
└─────────────────────────────────────────┘
        Agent VM → Slack API (outbound HTTPS only)
```

**Minimum hardware:**
- Agent VM: any Linux VM, 2 vCPUs, 4GB RAM
- LLM VM: Linux VM with NVIDIA Tesla T4 (16GB VRAM), 4 vCPUs, 16GB RAM
- Target VMs: any Linux VM (Ubuntu 20.04+, Debian 11+, or RHEL 8+)

---

## Step 1 — Install the agent

On the **agent VM**:

```bash
# Clone the repo
git clone https://github.com/psc0des/Errander-AI.git errander
cd errander

# Install uv (package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # adds uv to PATH — or open a new terminal

# Install all dependencies
uv sync --extra dev

# Verify everything imported correctly
uv run python -c "import errander; print('OK')"
```

---

## Step 2 — Set up an LLM

Errander-AI works with any OpenAI-compatible API. Choose one:

### Option A: Cloud API (skip to Step 3 after this)

If you're using OpenAI, Groq, Anthropic, or another cloud provider:

```bash
# Add to .env
ERRANDER_LLM_BASE_URL=https://api.openai.com/v1   # or Groq, Anthropic, etc.
ERRANDER_LLM_MODEL=gpt-4o-mini
ERRANDER_LLM_API_KEY=sk-...
```

Then verify with `uv run python -m errander --check-llm` and **skip to Step 3**.

See `docs/LLM-PROVIDERS.md` for ready-to-paste configs for every supported provider.

---

### Option B: Self-hosted vLLM (Qwen3-8B-AWQ)

On the **LLM VM** (requires Docker + NVIDIA Container Toolkit):

### 2a. Install prerequisites

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh

# Install NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify GPU is visible to Docker
docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu20.04 nvidia-smi
```

### 2b. Start vLLM

```bash
cd deploy/vllm

# Configure
cp .env.example .env
# Edit .env if needed (defaults work for Qwen3-8B-AWQ on T4)
# Only required change: set HF_TOKEN if you have a private HuggingFace account
# (Qwen3-8B-AWQ is public, so HF_TOKEN can be left blank)

# Create model cache directory
sudo mkdir -p /opt/vllm/model-cache
sudo chown $USER /opt/vllm/model-cache

# Start vLLM (first run downloads ~5GB model weights — takes 5-10 min)
docker-compose up -d

# Watch the logs until you see "Application startup complete"
docker-compose logs -f
```

**Expected output when ready:**
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Model load takes ~2-3 minutes on a T4 after the weights are downloaded.

### 2c. Verify vLLM is working

```bash
# From the LLM VM
curl http://localhost:8000/health
# → {"status":"ok"}

curl http://localhost:8000/v1/models
# → {"object":"list","data":[{"id":"qwen3-8b",...}]}
```

---

## Step 3 — Configure SSH access to target VMs

> **Single-VM setup?** If you only have one VM, the agent and target are the same machine. Run all commands below on that VM and use `localhost` wherever `<target-vm-ip>` appears.

On each **target VM**:

```bash
# Create the errander user (no password, key-based auth only)
sudo useradd -m -s /bin/bash errander

# Grant passwordless sudo for the commands Errander-AI needs
sudo tee /etc/sudoers.d/errander << 'EOF'
errander ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/dnf, \
  /usr/bin/journalctl, /usr/bin/docker, /usr/bin/find, /bin/df
EOF
```

On the **agent VM**:

```bash
# Generate a dedicated SSH key for Errander-AI
ssh-keygen -t ed25519 -f ~/.ssh/errander_prod -C "errander-agent" -N ""

# Copy the public key to each target VM
ssh-copy-id -i ~/.ssh/errander_prod.pub errander@<target-vm-ip>

# Test connectivity
ssh -i ~/.ssh/errander_prod errander@<target-vm-ip> "echo ok"
```

---

## Step 4 — Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Errander-AI`, pick your workspace
3. Under **OAuth & Permissions** → **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `reactions:read` — poll for ✅/❌ approvals
4. **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)
5. Create a channel `#errander-approvals`, invite the bot to it
6. Copy the **Channel ID** (right-click channel → View channel details → copy ID at bottom)

---

## Step 5 — Configure the agent

On the **agent VM**, create a `.env` file (never commit this):

```bash
cat > .env << 'EOF'
# LLM endpoint — see docs/LLM-PROVIDERS.md for all provider options
ERRANDER_LLM_BASE_URL=http://10.0.1.5:8000/v1
ERRANDER_LLM_MODEL=Qwen/Qwen3-8B-AWQ
# ERRANDER_LLM_API_KEY=not-needed      # set for cloud APIs (sk-..., gsk_..., etc.)
# ERRANDER_LLM_TEMPERATURE=0.1         # optional, default 0.1

# Slack
ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
ERRANDER_SLACK_CHANNEL_ID=C0123456789

# Audit DB (SQLite — leave as default or set an absolute path)
ERRANDER_AUDIT_DB_URL=errander.sqlite
EOF
```

Copy and edit the inventory:

```bash
cp example/inventory.yaml inventory.yaml
```

Edit `inventory.yaml` — replace the example IPs, names, and key paths with your real VMs:

```yaml
environments:
  dev:
    ssh_user: errander
    ssh_key_path: ~/.ssh/errander_prod
    approval_policy: relaxed
    maintenance_window: "02:00-06:00"
    maintenance_days: [tuesday, thursday]
    maintenance_timezone: UTC
    targets:
      - host: 10.0.1.10      # ← your VM's IP
        name: dev-web-01      # ← human-readable name
        os_family: ubuntu     # ubuntu | debian | rhel
```

Optionally copy and edit settings:

```bash
cp example/settings.yaml settings.yaml
# Adjust ssh_command_timeout_seconds, approval_timeout_seconds, etc. if needed
```

### Step 5b — Secure the Web UI (optional but recommended)

The agent exposes a web UI at `http://<agent-ip>:<metrics-port>/ui/`. By default it is **open** (no auth). To protect it with HTTP Basic Auth, add two env vars:

```bash
# Add to .env
ERRANDER_UI_USER=admin
ERRANDER_UI_PASSWORD=choose-a-strong-password
```

The middleware uses `secrets.compare_digest()` (constant-time comparison) so it is safe against timing attacks. The UI covers three areas:

- `/ui/` — batch run history and pending approvals
- `/ui/settings` — runtime LLM/approval settings (no restart required)
- `/ui/inventory` — disable YAML VMs or add ad-hoc VMs before the next run

**Settings precedence reminder:** changes made via the UI write to the `settings_overrides` table in SQLite. The full precedence chain is:

```
env var  >  DB (UI)  >  settings.yaml  >  built-in default
```

So if `ERRANDER_LLM_MODEL` is set as an env var, the UI field will be locked (shown in red) — you cannot override it from the UI.

---

## Step 6 — Verify everything before first run

```bash
# Load env vars
export $(grep -v '^#' .env | xargs)

# 1. Check vLLM is reachable from the agent VM
uv run python -m errander --check-llm
# Expected:
#   Status   : OK
#   Models   : qwen3-8b
#   Latency  : ~1200 ms

# 2. Verify inventory parses correctly
uv run python -c "
from errander.config.schema import validate_inventory
from pathlib import Path
inv = validate_inventory(Path('inventory.yaml'))
print('Environments:', list(inv.environments.keys()))
print('Targets:', sum(len(e.targets) for e in inv.environments.values()))
"

# 3. Run the full test suite
uv run pytest
# Expected: 844 passed
```

---

## Step 7 — First run (dry-run)

Always run dry-run first. No commands are executed on target VMs — everything is simulated.

```bash
export $(grep -v '^#' .env | xargs)

uv run python -m errander \
  --run-now \
  --env dev \
  --inventory inventory.yaml \
  --dry-run
```

**What happens:**
1. Agent validates the maintenance window (passes automatically in `--run-now` mode unless `--force` is needed)
2. SSH-connects to each target VM and detects OS, disk usage, Docker state
3. Plans actions (disk cleanup, etc.)
4. Simulates all commands — prints `[DRY-RUN]` output, touches nothing
5. Writes results to `errander.sqlite`
6. Logs a report

**Check the results:**

```bash
# View recent batches
uv run python -m errander --audit --batches

# View events from that batch
uv run python -m errander --audit --batch-id <batch-id-from-above>

# Open the web UI
# Start the agent again (it serves the UI while running)
# Then open: http://localhost:9090/ui
```

---

## Step 8 — Live run

Once dry-run looks correct:

```bash
uv run python -m errander \
  --run-now \
  --env dev \
  --inventory inventory.yaml \
  --live
```

The `--live` flag disables dry-run mode. Real commands run on the target VMs.

---

## Step 9 — Run as a scheduled service (production)

For continuous operation with APScheduler-driven cron runs:

```bash
# Create a systemd service on the agent VM
sudo tee /etc/systemd/system/errander.service << 'EOF'
[Unit]
Description=Errander-AI autonomous maintenance agent
After=network.target

[Service]
Type=simple
User=errander
WorkingDirectory=/home/errander/errander
EnvironmentFile=/home/errander/errander/.env
ExecStart=/home/errander/errander/.venv/bin/python -m errander \
  --inventory /home/errander/errander/inventory.yaml \
  --config /home/errander/errander/settings.yaml
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable errander
sudo systemctl start errander
sudo systemctl status errander
```

The agent runs continuously, triggering maintenance batches on the schedule defined in `settings.yaml`.

---

## Monitoring

| URL | What it shows |
|---|---|
| `http://<agent-vm>:9090/ui` | Dashboard — recent batches, event counts, VM history |
| `http://<agent-vm>:9090/ui/batches` | Full batch history |
| `http://<agent-vm>:9090/ui/approvals` | Pending approvals — Approve/Reject buttons |
| `http://<agent-vm>:9090/metrics` | Prometheus metrics (scrape this with Prometheus) |
| `http://<agent-vm>:9090/health` | Liveness check — `{"status":"ok"}` |

---

## Environment variables reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ERRANDER_LLM_BASE_URL` | Yes | — | API endpoint, e.g. `http://10.0.1.5:8000/v1` or `https://api.openai.com/v1` |
| `ERRANDER_LLM_MODEL` | Yes | — | Model ID for the provider, e.g. `Qwen/Qwen3-8B-AWQ` or `gpt-4o-mini` |
| `ERRANDER_SLACK_BOT_TOKEN` | Yes | — | Slack bot token (`xoxb-...`) |
| `ERRANDER_SLACK_CHANNEL_ID` | Yes | — | Approvals channel ID (`C...`) |
| `ERRANDER_AUDIT_DB_URL` | No | `errander.sqlite` | SQLite file path |
| `ERRANDER_LLM_API_KEY` | No | `not-needed` | API key for cloud providers (`sk-...`, `gsk_...`) |
| `ERRANDER_LLM_TEMPERATURE` | No | `0.1` | Sampling temperature (0.0–2.0; keep low for JSON output) |

---

## Troubleshooting

**`--check-llm` says UNREACHABLE**
- Confirm vLLM is running: `docker-compose -f deploy/vllm/docker-compose.yml ps`
- Check logs: `docker-compose -f deploy/vllm/docker-compose.yml logs --tail 50`
- Confirm the agent VM can reach the LLM VM: `curl http://<llm-vm-ip>:8000/health`
- Check firewall: port 8000 must be open between agent VM and LLM VM

**SSH connection fails on target VMs**
- Test manually: `ssh -i ~/.ssh/errander_prod errander@<vm-ip> "echo ok"`
- Confirm the public key is in `~errander/.ssh/authorized_keys` on the target VM
- Check `sshd` is running on the target: `sudo systemctl status sshd`

**`Outside maintenance window` error with `--run-now`**
- Add `--force --force-reason "manual test run"` to bypass the window check

**vLLM container exits immediately**
- GPU not found: run `docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi`
- VRAM OOM: reduce `GPU_MEM_UTIL` to `0.80` in `deploy/vllm/.env`
- Check Docker logs: `docker-compose -f deploy/vllm/docker-compose.yml logs`

**Agent falls back to hardcoded logic (LLM not used)**
- This is expected and correct — Errander-AI never blocks on LLM availability
- If you want LLM-powered decisions, confirm `--check-llm` passes first
