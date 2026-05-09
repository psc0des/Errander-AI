# Errander-AI — Setup Guide

Step-by-step instructions for getting Errander-AI running from scratch.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│            Azure VNet / Private Network              │
│                                                      │
│  ┌──────────────────────┐                            │
│  │  Master VM            │                            │
│  │  (Linux controller)   │                            │
│  │  Errander-AI agent    │                            │
│  │  APScheduler          │                            │
│  │  SQLite audit DB      │                            │
│  │  Web UI :9090         │                            │
│  └──────────┬────────────┘                            │
│             │ SSH port 22 (key-based, private IP)     │
│    ┌────────▼─────────────────┐                       │
│    │  Target VMs              │                       │
│    │  Ubuntu / Debian / RHEL  │                       │
│    └──────────────────────────┘                       │
└─────────────────────────────────────────────────────┘
    Master VM → LLM API      (outbound HTTPS port 443 — cloud or self-hosted)
    Master VM → Slack API    (outbound HTTPS port 443 — optional)
    Your laptop → Master VM  (SSH port 22 + web UI port 9090)
```

**Roles:**

| Machine | What runs there |
|---|---|
| **Master VM / Controller** | Errander-AI agent, SQLite, web UI — a Linux VM or your Windows PC |
| **Target VMs** | The Linux VMs being maintained — Ubuntu 20.04+, Debian 11+, RHEL 8+ |
| **LLM endpoint** *(optional)* | Any OpenAI-compatible API: a cloud API (Azure AI Foundry, OpenAI, Groq, etc.), a local LLM on the controller (Ollama, LM Studio), or a self-hosted vLLM on a dedicated GPU VM (16 GB VRAM recommended) |

The LLM is **optional**. The agent runs fine without one using built-in hardcoded logic. See [Step 4 — Set up an LLM](#step-4--set-up-an-llm) for the three options.

---

## Prerequisites

Before starting, confirm the following are in place.

### Software (on the Master VM / controller)

| Requirement | Check |
|---|---|
| Python 3.12+ | `python3 --version` |
| git | `git --version` |
| pip | `pip3 --version` |

If any are missing:
```bash
sudo apt-get install -y python3-pip git
# Note: python3.12 is not in default apt repos on Ubuntu 22.04.
# Install uv first (below) and run `uv python install 3.12` instead —
# uv manages its own Python and does not need a system-level 3.12.
```

### Network ports

| Connection | Port | Direction | Notes |
|---|---|---|---|
| Master VM → Target VM | 22 (SSH) | Outbound from Master | Azure: same-VNet VMs reach each other on port 22 by default — no NSG rule needed |
| Master VM → LLM API | 443 (HTTPS) | Outbound from Master | Azure Foundry, OpenAI, Groq — outbound HTTPS is allowed by default |
| Your laptop → Master VM | 22 (SSH) | Inbound to Master | Already open if you SSH to it today |
| Your laptop → Master VM | 9090 (Web UI) | Inbound to Master | **Action required** — see Azure NSG note below |

### Azure NSG — open port 9090 on the Master VM

The web UI and metrics endpoint run on port 9090. To access it from your laptop, add an **inbound rule** to the Master VM's Network Security Group in the Azure portal:

| Setting | Value |
|---|---|
| Source | My IP address (or your CIDR) |
| Destination port ranges | 9090 |
| Protocol | TCP |
| Action | Allow |
| Priority | 1010 (any unused priority) |

> **No NSG access? Use an SSH tunnel instead:**
> ```bash
> ssh -L 9090:localhost:9090 <your-admin-user>@<master-vm-public-ip>
> ```
> Then open `http://localhost:9090/ui` on your laptop. No port 9090 rule needed.

---

## Step 1 — Install the agent on the controller

### Windows controller

1. **Install Python 3.12+**

   Download from [python.org](https://www.python.org/downloads/) and install. During install, check **"Add Python to PATH"**.

   Verify:
   ```powershell
   python --version
   # Python 3.12.x
   ```

2. **Install uv** (package manager)

   ```powershell
   pip install uv
   ```

3. **Clone the repo and install dependencies**

   ```powershell
   git clone https://github.com/psc0des/Errander-AI.git errander
   cd errander
   uv sync --extra dev
   ```

4. **Verify the install**

   ```powershell
   uv run python -c "import errander; print('OK')"
   # OK
   ```

### Linux controller

```bash
# Install pip and git
sudo apt-get update && sudo apt-get install -y python3-pip git

# Install uv
pip3 install uv

# Install Python 3.12 via uv (works on Ubuntu 22.04 and later — no PPA needed)
uv python install 3.12

# Clone and install
git clone https://github.com/psc0des/Errander-AI.git errander
cd errander
uv sync

# Verify
uv run python -c "import errander; print('OK')"
```

---

## Step 2 — Set up SSH keys (controller → target VMs)

Errander-AI connects to target VMs using key-based SSH only. No passwords are ever used.

### Windows controller

1. **Generate an SSH key pair**

   Open PowerShell or Command Prompt:
   ```powershell
   ssh-keygen -t ed25519 -f "$HOME\.ssh\errander_prod" -C "errander-agent" -N ""
   ```

   This creates two files:
   - `C:\Users\<you>\.ssh\errander_prod` — private key (never share this)
   - `C:\Users\<you>\.ssh\errander_prod.pub` — public key (goes on target VMs)

2. **Restrict key file permissions** (important — asyncssh warns on overly permissive keys)

   In PowerShell:
   ```powershell
   $keyPath = "$HOME\.ssh\errander_prod"
   $acl = Get-Acl $keyPath
   $acl.SetAccessRuleProtection($true, $false)
   $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
       $env:USERNAME, "FullControl", "Allow"
   )
   $acl.SetAccessRule($rule)
   Set-Acl $keyPath $acl
   ```

   Or right-click the file → Properties → Security → Advanced → Disable inheritance → remove all except your user.

3. **Copy the public key to each target VM**

   ```powershell
   # Print the public key
   Get-Content "$HOME\.ssh\errander_prod.pub"
   ```

   Then on each target VM (SSH in manually first):
   ```bash
   # On the target VM
   sudo useradd -m -s /bin/bash errander
   sudo mkdir -p /home/errander/.ssh
   sudo chmod 700 /home/errander/.ssh

   # Paste the public key from above
   echo "ssh-ed25519 AAAA...your-key... errander-agent" \
     | sudo tee /home/errander/.ssh/authorized_keys
   sudo chmod 600 /home/errander/.ssh/authorized_keys
   sudo chown -R errander:errander /home/errander/.ssh
   ```

4. **Test the connection from Windows**

   ```powershell
   ssh -i "$HOME\.ssh\errander_prod" errander@<target-vm-ip> "echo connected"
   # connected
   ```

### Linux controller

```bash
# Generate key
ssh-keygen -t ed25519 -f ~/.ssh/errander_prod -C "errander-agent" -N ""

# On each target VM — create user and install public key
ssh <your-admin-user>@<target-vm-ip> "
  sudo useradd -m -s /bin/bash errander
  sudo mkdir -p /home/errander/.ssh
  sudo chmod 700 /home/errander/.ssh
  echo '$(cat ~/.ssh/errander_prod.pub)' \
    | sudo tee /home/errander/.ssh/authorized_keys
  sudo chmod 600 /home/errander/.ssh/authorized_keys
  sudo chown -R errander:errander /home/errander/.ssh
"

# Test
ssh -i ~/.ssh/errander_prod errander@<target-vm-ip> "echo connected"
```

---

## Step 3 — Configure target VMs (sudo permissions)

On each **target VM**, grant the `errander` user passwordless sudo for the commands Errander-AI needs:

```bash
sudo tee /etc/sudoers.d/errander << 'EOF'
errander ALL=(ALL) NOPASSWD: \
  /usr/bin/apt-get, \
  /usr/bin/apt-get clean, \
  /usr/bin/apt-get autoremove, \
  /usr/bin/dnf, \
  /usr/bin/yum, \
  /usr/bin/journalctl, \
  /usr/bin/docker, \
  /usr/bin/find, \
  /bin/df, \
  /usr/bin/du
EOF

# Verify it works
sudo -u errander sudo /bin/df -h /
```

---

## Step 4 — Set up an LLM

The agent needs an LLM endpoint that speaks the OpenAI API (`/v1/chat/completions`). Pick **one** of the three options below — Errander-AI works with any OpenAI-compatible endpoint and does not lock you into a particular provider.

| Option | Best for | Trade-offs |
|---|---|---|
| **A. Cloud API** (OpenAI, Anthropic, Groq, Azure AI Foundry, etc.) | Fastest setup, no infrastructure | Data leaves your VPN (Foundry stays in your Azure tenant) |
| **B. Local LLM on the controller** (Ollama, LM Studio) | Privacy + no separate VM | Limited by your controller's RAM/VRAM |
| **C. Self-hosted vLLM on a dedicated GPU VM** | Privacy + production performance | Needs an NVIDIA GPU with **16 GB VRAM** (Tesla T4 reference) |

See `docs/LLM-PROVIDERS.md` for paste-ready `.env` blocks for every supported provider.

---

### Option A — Cloud API (fastest)

Set the following in `.env` on the controller:

```
ERRANDER_LLM_BASE_URL=https://api.openai.com/v1   # or Groq, Anthropic, Azure AI Foundry, etc.
ERRANDER_LLM_MODEL=gpt-4o-mini
ERRANDER_LLM_API_KEY=sk-...
```

For Azure AI Foundry (Azure OpenAI deployment), use the v1-preview endpoint shape:
```
ERRANDER_LLM_BASE_URL=https://<your-resource>.openai.azure.com/openai/v1/
ERRANDER_LLM_MODEL=<your-deployment-name>
ERRANDER_LLM_API_KEY=<key from "Keys and Endpoint" blade>
```

Verify with `uv run python -m errander --check-llm` and **skip to Step 5**. See `docs/LLM-PROVIDERS.md` Option F for non-OpenAI Foundry models (Llama, Phi, Mistral, DeepSeek).

---

### Option B — Local LLM on the controller (Ollama or LM Studio)

Use this if your Windows controller has enough RAM/VRAM to run a model locally. No separate GPU VM needed.

**Ollama** (easiest):

1. Download and install from [ollama.com](https://ollama.com)
2. Pull a model:
   ```powershell
   ollama pull qwen3:8b
   ```
3. Ollama starts automatically and listens on `http://localhost:11434`

Set in `.env`:
```
ERRANDER_LLM_BASE_URL=http://localhost:11434/v1
```

**LM Studio:**

1. Download from [lmstudio.ai](https://lmstudio.ai)
2. Download a model (Qwen3-8B-GGUF recommended)
3. Go to **Local Server** tab → Start server (default port 1234)

Set in `.env`:
```
ERRANDER_LLM_BASE_URL=http://localhost:1234/v1
```

---

### Option C — Self-hosted vLLM on a dedicated GPU VM

Use this if you have a separate Linux VM with an NVIDIA GPU. Recommended hardware: Tesla T4 with **16 GB VRAM**, 4 vCPUs, 16 GB RAM. 16 GB VRAM is what Qwen3-8B-AWQ needs at 8K context with `--gpu-memory-utilization 0.85`.

**On the LLM VM:**

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh

# Install NVIDIA Container Toolkit
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -sL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify GPU is visible
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi
```

```bash
# Start vLLM
cd errander/deploy/vllm
cp .env.example .env

sudo mkdir -p /opt/vllm/model-cache
sudo chown $USER /opt/vllm/model-cache

# First run downloads ~5 GB of model weights (5-10 min)
docker compose up -d
docker compose logs -f
# Wait for: "Application startup complete"
```

Set in `.env` on the **controller**:
```
ERRANDER_LLM_BASE_URL=http://<llm-vm-ip>:8000/v1
```

---

## Step 5 — Create a Slack app *(optional)*

> **You can skip this step.** Slack is optional. When no Slack token is configured, the agent uses **web UI approval mode** instead: maintenance plans that require approval appear at `http://<master-vm-ip>:9090/ui/approvals` where you click Approve or Reject. All other functionality (scheduling, SSH, audit trail, metrics) is unaffected.
>
> Come back to this step later when you want Slack notifications and mobile approval reactions.

If you do want Slack:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Errander-AI`, select your workspace
3. Under **OAuth & Permissions** → **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `reactions:read` — poll for ✅/❌ reactions
4. Click **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)
5. Create a Slack channel `#errander-approvals` and invite the bot to it
6. Copy the **Channel ID** — right-click the channel → View channel details → copy the ID at the bottom (starts with `C`)

---

## Step 6 — Configure the agent

### Create a `.env` file

On the controller, inside the `errander` directory:

**Windows** — create `.env` with a text editor (Notepad, VS Code, etc.):
```
# LLM endpoint — any OpenAI-compatible API (see Step 4)
ERRANDER_LLM_BASE_URL=https://<your-resource>.openai.azure.com/openai/v1/
ERRANDER_LLM_MODEL=<your-deployment-name>
ERRANDER_LLM_API_KEY=<your-api-key>

ERRANDER_AUDIT_DB_URL=errander.sqlite

# Web UI auth (recommended — remove to leave UI open)
ERRANDER_UI_USER=admin
ERRANDER_UI_PASSWORD=changeme

# Slack — optional (remove # to enable; skip for web UI approval mode)
# ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
# ERRANDER_SLACK_CHANNEL_ID=C0123456789
```

**Linux:**
```bash
cat > .env << 'EOF'
# LLM endpoint — any OpenAI-compatible API (see Step 4)
ERRANDER_LLM_BASE_URL=https://<your-resource>.openai.azure.com/openai/v1/
ERRANDER_LLM_MODEL=<your-deployment-name>
ERRANDER_LLM_API_KEY=<your-api-key>

ERRANDER_AUDIT_DB_URL=errander.sqlite

# Web UI auth (recommended — remove to leave UI open)
ERRANDER_UI_USER=admin
ERRANDER_UI_PASSWORD=changeme

# Slack — optional (remove # to enable; skip for web UI approval mode)
# ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
# ERRANDER_SLACK_CHANNEL_ID=C0123456789
EOF
```

> Never commit `.env` — it is already in `.gitignore`.

### Create your inventory

Copy the example and edit it:
```bash
cp example/inventory.yaml inventory.yaml
```

Minimal example for a single dev VM:

```yaml
environments:
  dev:
    ssh_user: errander
    ssh_key_path: ~/.ssh/errander_prod     # Linux / Windows Git Bash path
    # Windows native path also works:
    # ssh_key_path: C:\Users\you\.ssh\errander_prod
    approval_policy: relaxed
    maintenance_window: "08:00-20:00"
    maintenance_days: [monday, tuesday, wednesday, thursday, friday]
    maintenance_timezone: UTC
    targets:
      - host: 192.168.1.10
        name: dev-vm-01
        os_family: ubuntu
```

Optionally copy and edit settings:
```bash
cp example/settings.yaml settings.yaml
```

### Web UI

The agent exposes a web UI at `http://<master-vm-ip>:9090/ui` (port 9090 — see Prerequisites for NSG setup). The `ERRANDER_UI_USER` / `ERRANDER_UI_PASSWORD` env vars in the `.env` template above enable HTTP Basic Auth on it.

The UI covers:
- `/ui/` — batch run history, event log, pending approvals
- `/ui/approvals` — approve or reject pending maintenance plans (replaces Slack when no token is set)
- `/ui/settings` — change LLM/approval settings at runtime (no restart required)
- `/ui/inventory` — disable YAML VMs or add ad-hoc VMs before the next run

Settings changed via the UI are stored in SQLite. Precedence chain:
```
env var  >  DB (UI)  >  settings.yaml  >  built-in default
```

---

## Step 7 — Verify everything

### Load env vars

**Windows PowerShell:**
```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
```

**Windows Command Prompt / Git Bash / Linux:**
```bash
export $(grep -v '^#' .env | xargs)
```

### Run checks

```bash
# 1. Verify inventory parses correctly
uv run python -c "
from errander.config.schema import validate_inventory
from pathlib import Path
inv = validate_inventory(Path('inventory.yaml'))
print('Environments:', list(inv.environments.keys()))
print('Targets:', sum(len(e.targets) for e in inv.environments.values()))
"

# 2. Run the full test suite
uv run pytest
# Expected: all tests pass
```

---

## Step 8 — First run (dry-run)

**Windows PowerShell** (load env first, see Step 7):
```powershell
uv run python -m errander --run-now --env dev --inventory inventory.yaml --dry-run
```

**Linux / Git Bash:**
```bash
export $(grep -v '^#' .env | xargs)
uv run python -m errander --run-now --env dev --inventory inventory.yaml --dry-run
```

What happens:
1. SSH connects to each target VM and detects OS + disk state
2. Plans actions (disk cleanup, etc.)
3. Simulates all commands — prints `[DRY-RUN]` output, nothing is changed on the VMs
4. Writes results to `errander.sqlite`

---

## Step 9 — Live run

Once dry-run looks correct:

```bash
uv run python -m errander --run-now --env dev --inventory inventory.yaml --live
```

Real commands execute on the target VMs.

---

## Step 10 — Run as a scheduled service (production)

### Linux controller — systemd

```bash
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

### Windows controller — Task Scheduler

1. Open **Task Scheduler** → **Create Basic Task**
2. Name: `Errander-AI`
3. Trigger: **At startup** (or a specific time)
4. Action: **Start a program**
   - Program: `C:\Users\<you>\errander\.venv\Scripts\python.exe`
   - Arguments: `-m errander --inventory C:\Users\<you>\errander\inventory.yaml --config C:\Users\<you>\errander\settings.yaml`
   - Start in: `C:\Users\<you>\errander`
5. Under **Properties** → **General** → check **Run whether user is logged on or not**
6. Add env vars under **Properties** → create a wrapper `.bat` file that sets them first:

```bat
@echo off
set ERRANDER_LLM_BASE_URL=http://10.0.1.5:8000/v1
set ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
set ERRANDER_SLACK_CHANNEL_ID=C0123456789
set ERRANDER_AUDIT_DB_URL=C:\Users\<you>\errander\errander.sqlite
C:\Users\<you>\errander\.venv\Scripts\python.exe -m errander ^
  --inventory C:\Users\<you>\errander\inventory.yaml ^
  --config C:\Users\<you>\errander\settings.yaml
```

Point the Task Scheduler action at this `.bat` file instead.

---

## Troubleshooting

**SSH connection fails**
- Test manually: `ssh -i ~/.ssh/errander_prod errander@<vm-ip> "echo ok"`
- Confirm the public key is in `~errander/.ssh/authorized_keys` on the target
- Check `sshd` is running: `sudo systemctl status sshd`
- On Windows: confirm key file permissions are restricted to your user only

**`--check-llm` says UNREACHABLE** *(only relevant if you set up the optional LLM VM)*
- Confirm vLLM is running: `docker compose -f deploy/vllm/docker-compose.yml ps`
- Check logs: `docker compose -f deploy/vllm/docker-compose.yml logs --tail 50`
- Confirm the controller can reach the LLM VM: `curl http://<llm-vm-ip>:8000/health`
- Check firewall: port 8000 must be open between controller and LLM VM

**`Outside maintenance window` error**
- Add `--force --force-reason "manual test"` to bypass the window check

**vLLM container exits immediately**
- GPU not found: `docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi`
- VRAM OOM: reduce `GPU_MEM_UTIL` to `0.80` in `deploy/vllm/.env`

**Agent falls back to hardcoded logic**
- Expected and correct — Errander-AI never blocks on LLM availability
- To use LLM-powered decisions, confirm `--check-llm` passes first

---

## Environment variables reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ERRANDER_LLM_BASE_URL` | Yes | — | LLM endpoint — any OpenAI-compatible API (cloud, Ollama, LM Studio, or vLLM) |
| `ERRANDER_LLM_MODEL` | Yes | — | Model ID for the chosen provider, e.g. `gpt-4o-mini`, `qwen3:8b`, `Qwen/Qwen3-8B-AWQ` |
| `ERRANDER_LLM_API_KEY` | No | `not-needed` | API key if your LLM server requires auth (required for Azure Foundry, OpenAI, Groq, etc.) |
| `ERRANDER_AUDIT_DB_URL` | No | `errander.sqlite` | SQLite file path |
| `ERRANDER_SLACK_BOT_TOKEN` | No | — | Slack bot token (`xoxb-...`). If omitted, approval falls back to web UI at `/ui/approvals` |
| `ERRANDER_SLACK_CHANNEL_ID` | No | — | Slack approvals channel ID (`C...`). Required if `ERRANDER_SLACK_BOT_TOKEN` is set |
| `ERRANDER_LLM_TEMPERATURE` | No | `0.1` | Sampling temperature (0.0–2.0; keep low for JSON output) |
| `ERRANDER_UI_USER` | No | — | If set together with `ERRANDER_UI_PASSWORD`, enables HTTP Basic Auth on the Web UI |
| `ERRANDER_UI_PASSWORD` | No | — | Password for the Web UI (compared with `secrets.compare_digest`) |

---

## Monitoring

Once the agent is running, the following endpoints are exposed on the metrics port (default `9090`):

| URL | What it shows |
|---|---|
| `http://<controller>:9090/ui` | Dashboard — recent batches, event counts, VM history |
| `http://<controller>:9090/ui/batches` | Full batch history |
| `http://<controller>:9090/ui/approvals` | Pending approvals — Approve/Reject buttons |
| `http://<controller>:9090/ui/settings` | Runtime LLM/approval settings (no restart required) |
| `http://<controller>:9090/ui/inventory` | Disable YAML VMs or add ad-hoc VMs before the next run |
| `http://<controller>:9090/metrics` | Prometheus metrics (scrape this with Prometheus) |
| `http://<controller>:9090/health` | Liveness check — `{"status":"ok"}` |
