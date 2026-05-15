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

> **These are handled automatically by the bootstrap script in Step 1.** You only need to verify them if you are skipping the bootstrap and doing a manual install.

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

**Recommended — run the bootstrap script from PowerShell.**
It installs git (via winget), uv, and Python 3.12, clones the repo, and verifies the install. No admin rights required.

```powershell
git clone https://github.com/psc0des/Errander-AI.git errander
powershell -ExecutionPolicy Bypass -File errander\scripts\bootstrap.ps1
```

Once complete, **skip to Step 2** — the script handles everything in Step 1.

> **git not installed yet?** Download it from [git-scm.com/download/win](https://git-scm.com/download/win), install, then run the command above.

<details>
<summary>Manual installation (reference / fallback)</summary>

```powershell
# 1. Install Python 3.12 from python.org (check "Add Python to PATH" during install)
# 2. Install uv
pip install uv

# 3. Clone and install
git clone https://github.com/psc0des/Errander-AI.git errander
cd errander
uv sync --extra dev

# 4. Verify
uv run python -c "import errander; print('OK')"
```

</details>

### Linux controller

**Recommended — run the bootstrap script.**
It detects your distro (Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora),
installs all prerequisites, and verifies the install.

```bash
git clone https://github.com/psc0des/Errander-AI.git errander
bash errander/scripts/bootstrap.sh
```

Once complete, **skip to Step 2** — the script handles everything in Step 1.

<details>
<summary>Manual installation (reference / fallback)</summary>

```bash
# Install pip and git
sudo apt-get update && sudo apt-get install -y python3-pip git   # Ubuntu/Debian
# sudo dnf install -y python3-pip git                            # RHEL/CentOS/Oracle

# Install uv (official installer — no PPA, works on all distros)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# Install Python 3.12 via uv
uv python install 3.12

# Clone and install
git clone https://github.com/psc0des/Errander-AI.git errander
cd errander
uv sync --extra dev

# Verify
uv run python -c "import errander; print('OK')"
```

</details>

---

## Step 2 — Set up SSH keys

Errander-AI connects to target VMs using key-based SSH only. No passwords are ever used.

The SSH key is generated **on the Master VM (controller)** — that is the machine running the agent, which makes outbound SSH connections to target VMs.

```
Your laptop → (SSH) → Master VM → (SSH, private IP) → Target VM
                       ↑ key lives here
```

### On the Master VM

**1. Generate the key pair**

```bash
# Run this on the Master VM
ssh-keygen -t ed25519 -f ~/.ssh/errander_prod -C "errander-agent" -N ""
```

This creates two files on the Master VM:
- `~/.ssh/errander_prod` — private key (stays on Master VM, never shared)
- `~/.ssh/errander_prod.pub` — public key (installed on each target VM)

**2. Print the public key — you will need it in the next step**

```bash
# Run this on the Master VM
cat ~/.ssh/errander_prod.pub
```

Copy the output to your clipboard.

**3. On each Target VM — create the errander user and install the public key**

SSH into your Target VM from your laptop, then run:

```bash
# Run these on the Target VM
sudo useradd -m -s /bin/bash errander
sudo mkdir -p /home/errander/.ssh
sudo chmod 700 /home/errander/.ssh

# Paste the public key output from step 2
echo "ssh-ed25519 AAAA...paste-your-key-here... errander-agent" \
  | sudo tee /home/errander/.ssh/authorized_keys

sudo chmod 600 /home/errander/.ssh/authorized_keys
sudo chown -R errander:errander /home/errander/.ssh
```

**4. Test the connection — from Master VM to Target VM (private IP)**

```bash
# Run this on the Master VM
ssh -i ~/.ssh/errander_prod errander@<target-vm-private-ip> "echo connected"
# Expected output: connected
```

---

## Step 3 — Configure target VMs (sudo permissions)

**All commands in this step run on the Target VM.**
SSH into your Target VM from your laptop before starting.

Grant the `errander` user passwordless sudo for the commands Errander-AI needs.

> **Important:** A syntax error in a sudoers file can lock you out of `sudo` entirely.
> Follow the backup → write → validate → verify sequence below exactly.
> Keep your current SSH session open throughout — do not close it until the verify step passes.

**1. Back up existing sudoers files** *(Target VM)*

```bash
sudo cp /etc/sudoers /etc/sudoers.bak.$(date +%Y%m%d)
sudo cp -r /etc/sudoers.d /etc/sudoers.d.bak.$(date +%Y%m%d)
```

**2. Create the errander sudoers file** *(Target VM)*

> **sudo -n model:** The agent calls all privileged commands as `sudo -n /absolute/path ...` (e.g. `sudo -n /usr/bin/apt-get upgrade -y`). `sudo -n` fails immediately with exit 1 if passwordless sudo is not configured — it never hangs waiting for a password. Absolute paths match sudoers entries predictably and produce clean audit logs in `/var/log/auth.log` on the target VM.

```bash
sudo tee /etc/sudoers.d/errander << 'EOF'
errander ALL=(root) NOPASSWD: \
  /usr/bin/apt-get, \
  /usr/bin/apt-mark, \
  /usr/bin/dnf, \
  /usr/bin/yum, \
  /usr/bin/journalctl, \
  /usr/sbin/logrotate, \
  /usr/bin/gzip, \
  /usr/bin/truncate, \
  /usr/bin/cp, \
  /usr/bin/needs-restarting, \
  /usr/bin/systemctl
EOF
```

> **Docker — production hardening (see below):** Do NOT add `/usr/bin/docker` to the sudoers above for production. Use root-owned wrapper scripts instead (see "Docker hardening" section below). Raw `sudo docker` is root-equivalent — any `docker run` command can mount the host filesystem. The wrapper approach restricts the agent to exactly the prune operations it is allowed to perform.

> **Read-only commands** — these run without sudo (no sudoers entry needed): `df`, `du`, `dpkg-query`, `rpm -q`, `apt list`, `dnf check-update`, `find` (listing only), `stat`, `systemctl is-active`, `journalctl --disk-usage`.

**3. Set correct permissions and validate syntax** *(Target VM)*

```bash
# sudoers.d files must be mode 440 — world-readable but not writable
sudo chmod 440 /etc/sudoers.d/errander

# Validate syntax — must print OK before you proceed
sudo visudo -c -f /etc/sudoers.d/errander
# Expected: /etc/sudoers.d/errander: parsed OK
```

If `visudo -c` reports an error, restore from backup:

```bash
# Only run this if visudo reported an error
sudo rm /etc/sudoers.d/errander
sudo cp -r /etc/sudoers.d.bak.$(date +%Y%m%d) /etc/sudoers.d
```

**4. Verify the errander user can use sudo -n** *(Target VM)*

```bash
sudo -u errander sudo -n /usr/bin/apt-get --version
# Expected: apt-get version output — no password prompt, exits 0

# Confirm sudo -n fails fast when not configured (sanity check):
sudo -u errander sudo -n /usr/bin/id  # should FAIL — /usr/bin/id is not in sudoers
# Expected: exit 1 immediately, no hanging
```

---

## Docker hardening *(Target VM — production)*

The Docker group is effectively root: a user in it can mount the host filesystem via `docker run`. Do **not** add `errander` to the docker group, and do not grant raw `sudo /usr/bin/docker` in production.

Errander supports three Docker modes per environment (set `docker_command_mode` in `inventory.yaml`):

| Mode | Description | Use case |
|---|---|---|
| `wrapper` (default) | Root-owned wrapper scripts at `/usr/local/sbin/errander-docker-*` | Production — narrow sudoers, no raw `sudo docker` |
| `direct_sudo` | `sudo -n /usr/bin/docker ...` directly | Lab / pre-prod only. Logs a warning every batch. |
| `disabled` | No docker_prune actions planned or executed | Envs without Docker |

### Wrapper scripts *(production mode — required when `docker_command_mode: wrapper`)*

Create three root-owned wrapper scripts. Each supports `--check` for sudo capability probing.

```bash
# /usr/local/sbin/errander-docker-assess
sudo tee /usr/local/sbin/errander-docker-assess << 'EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

if ! /usr/bin/docker info >/dev/null 2>&1; then
    echo "reachable=no"
    echo "error=docker daemon not reachable"
    exit 0
fi

echo "reachable=yes"
dangling=$(/usr/bin/docker images -f dangling=true -q 2>/dev/null | wc -l)
echo "dangling_images=${dangling}"
stopped=$(/usr/bin/docker ps -a -f status=exited -q 2>/dev/null | wc -l)
echo "stopped_containers=${stopped}"
echo "error="
echo "system_df_begin"
/usr/bin/docker system df 2>/dev/null || true
echo "system_df_end"
EOF

# /usr/local/sbin/errander-docker-prune-safe
sudo tee /usr/local/sbin/errander-docker-prune-safe << 'EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

# Dangling images + stopped containers only. No -a flag.
/usr/bin/docker image prune -f 2>&1
/usr/bin/docker container prune -f 2>&1
EOF

# /usr/local/sbin/errander-docker-prune-aggressive
sudo tee /usr/local/sbin/errander-docker-prune-aggressive << 'EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

# Full system prune including ALL unused images.
# Only used when aggressive=true is approved in the maintenance plan.
/usr/bin/docker system prune -af 2>&1
EOF

sudo chmod 755 /usr/local/sbin/errander-docker-assess
sudo chmod 755 /usr/local/sbin/errander-docker-prune-safe
sudo chmod 755 /usr/local/sbin/errander-docker-prune-aggressive
sudo chown root:root /usr/local/sbin/errander-docker-assess \
  /usr/local/sbin/errander-docker-prune-safe \
  /usr/local/sbin/errander-docker-prune-aggressive
```

**Assess wrapper output format** (parsed by `parse_assess_output()` in `docker_prune.py`):

```
reachable=yes|no
dangling_images=N
stopped_containers=N
error=<optional message>
system_df_begin
<raw docker system df output>
system_df_end
```

Then add all three wrappers to sudoers:

```bash
sudo tee /etc/sudoers.d/errander-docker << 'EOF'
errander ALL=(root) NOPASSWD: \
  /usr/local/sbin/errander-docker-assess, \
  /usr/local/sbin/errander-docker-prune-safe, \
  /usr/local/sbin/errander-docker-prune-aggressive
EOF
sudo chmod 440 /etc/sudoers.d/errander-docker
sudo visudo -c -f /etc/sudoers.d/errander-docker
```

> **Lab / pre-prod shortcut only:** Set `docker_command_mode: direct_sudo` in `inventory.yaml`. You may then add `/usr/bin/docker` to the main sudoers file. The agent will log a warning every batch. Do not use `direct_sudo` in production.

---

## Steps 4–6 — Quick path (recommended)

Once you have your LLM endpoint URL, model name, and API key ready, run the interactive setup script from inside the `errander/` directory:

**Linux / Git Bash (Windows):**
```bash
bash scripts/configure.sh
```

It will prompt you for LLM credentials, target VMs, verify your SSH key path (create it first in Step 2 if you haven't), and optional Slack — then write `.env` and `inventory.yaml` and verify the LLM connection. Skip to [Step 6 — Verify everything](#step-6--verify-everything) when done.

> **Windows note:** `configure.sh` runs in Git Bash (installed with Git for Windows). Open **Git Bash** (not PowerShell) and run the command above from inside the `errander\` folder.
>
> **Prefer to configure manually?** Follow Steps 4–6 below instead.

---

## Step 4 — Set up an LLM *(Master VM)*

The LLM powers maintenance decisions and report generation. It is **optional** — the agent falls back to built-in hardcoded logic if no LLM is configured and will never block on LLM availability.

There are two fundamentally different approaches:

| | **Option A — Cloud API** | **Option B — Self-hosted** |
|---|---|---|
| **Where it runs** | Provider's servers (OpenAI, Azure, Groq…) | Your own VM |
| **Setup effort** | Minutes — just an API key | More work — install and run a model |
| **Data privacy** | Leaves your network (except Azure Foundry, which stays in your Azure tenant) | Never leaves your network |
| **Cost** | Pay per token (Groq has a free tier) | Free after setup (hardware cost only) |
| **Performance** | Fast | Depends on hardware (see below) |

**Pick Option A if:** you have a cloud account or just want the fastest setup.
**Pick Option B if:** data must stay on your own infrastructure.

If you pick Option B, choose the right tool:

| | **Ollama** | **vLLM** |
|---|---|---|
| **Hardware** | CPU or GPU — any machine | NVIDIA GPU required (16 GB VRAM) |
| **Where it runs** | Any VM, including Master VM | Dedicated GPU VM recommended |
| **Setup** | Single install command | Docker + NVIDIA Container Toolkit |
| **Performance** | Fast on GPU, slow on CPU-only | High throughput, production-grade |
| **Best for** | Getting started, dev/testing | Production self-hosted deployment |

Pick **Ollama** if you want the simpler path — it works on CPU or GPU, and you can run it on the Master VM or any other machine.
Pick **vLLM** if you have a dedicated GPU VM and need production-grade throughput.

Each option below gives you three values (`BASE_URL`, `MODEL`, `API_KEY`) and a verify command.
Note them down — you'll paste them into your `.env` in Step 5.

> For full provider config reference, see `docs/LLM-PROVIDERS.md`.

---

### Option A — Cloud API *(no extra infrastructure)*

**All commands run on the Master VM.**

#### Azure AI Foundry *(if you have an Azure subscription)*

1. In the Azure portal, go to your **AI Foundry resource** → **Keys and Endpoint**
2. Copy the **Endpoint URL** and one of the **Keys**
3. Note your **deployment name** (the name you gave the model in Foundry — e.g. `gpt-4o-mini-deploy`, not the model ID)

Your three values:
```
BASE_URL  = https://<your-resource>.cognitiveservices.azure.com/openai/v1/   ← trailing / required
MODEL     = <your-deployment-name>                                             ← deployment name, not model ID
API_KEY   = <key from Keys and Endpoint blade>
```

#### OpenAI

Your three values:
```
BASE_URL  = https://api.openai.com/v1
MODEL     = gpt-4o-mini
API_KEY   = sk-...
```

#### Groq *(free tier available at console.groq.com)*

Your three values:
```
BASE_URL  = https://api.groq.com/openai/v1
MODEL     = llama-3.3-70b-versatile
API_KEY   = gsk_...
```

**Verify** — test the connection before moving on *(Master VM, inside `errander/` directory)*:
```bash
ERRANDER_LLM_BASE_URL=<your-base-url> \
ERRANDER_LLM_MODEL=<your-model> \
ERRANDER_LLM_API_KEY=<your-key> \
uv run python -m errander --check-llm
# Expected: Status: OK, Latency: <Xms>, Response: 'OK'
```

---

### Option B — Self-hosted

#### B1 — Ollama *(runs on any VM — CPU or GPU)*

Ollama is the easiest self-hosted path. It runs on the Master VM or any other machine,
uses whatever hardware is available (CPU or NVIDIA/AMD/Apple GPU), and needs no Docker setup.
Needs 8 GB+ RAM minimum. Inference is GPU-fast when a GPU is present, CPU-slow without one.

**On the Master VM:**

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model (~5 GB download)
ollama pull qwen3:8b

# Ollama starts automatically and listens on port 11434
```

Your three values:
```
BASE_URL  = http://localhost:11434/v1
MODEL     = qwen3:8b
API_KEY   = ollama
```

**Verify** *(Master VM)*:
```bash
ERRANDER_LLM_BASE_URL=http://localhost:11434/v1 \
ERRANDER_LLM_MODEL=qwen3:8b \
ERRANDER_LLM_API_KEY=ollama \
uv run python -m errander --check-llm
# Expected: Status: OK, Latency: <Xms>, Response: 'OK'
```

---

#### B2 — vLLM *(GPU, dedicated VM)*

Requires a separate Linux VM with an NVIDIA GPU.
Recommended hardware: Tesla T4, 16 GB VRAM, 4 vCPUs, 16 GB RAM.

**On the GPU VM** *(not the Master VM — this is a separate dedicated machine)*:

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

# Verify GPU is visible to Docker
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi
```

```bash
# Start vLLM (from your cloned repo on the GPU VM)
cd errander/deploy/vllm
cp .env.example .env

sudo mkdir -p /opt/vllm/model-cache
sudo chown $USER /opt/vllm/model-cache

# First run downloads ~5 GB of model weights (5–10 min)
docker compose up -d
docker compose logs -f
# Wait for: "Application startup complete"
```

Your three values *(on the Master VM)*:
```
BASE_URL  = http://<gpu-vm-private-ip>:8000/v1
MODEL     = Qwen/Qwen3-8B-AWQ
API_KEY   = (leave blank — unauthenticated vLLM needs no key)
```

**Verify** *(Master VM)*:
```bash
ERRANDER_LLM_BASE_URL=http://<gpu-vm-private-ip>:8000/v1 \
ERRANDER_LLM_MODEL=Qwen/Qwen3-8B-AWQ \
uv run python -m errander --check-llm
# Expected: Status: OK, Latency: <Xms>, Response: 'OK'
```

---

## Step 5 — Configure the agent

### Create `.env`  *(Master VM)*

Inside the `errander/` directory. Paste the `BASE_URL`, `MODEL`, and `API_KEY` values you noted in Step 4.

**Linux:**
```bash
cat > .env << 'EOF'
# LLM — paste the values from whichever Step 4 option you chose
ERRANDER_LLM_BASE_URL=<base-url-from-step-4>
ERRANDER_LLM_MODEL=<model-from-step-4>
ERRANDER_LLM_API_KEY=<api-key-from-step-4>

ERRANDER_AUDIT_DB_URL=errander.sqlite

# Web UI auth — change these before going to production
ERRANDER_UI_USER=admin
ERRANDER_UI_PASSWORD=changeme

# Slack — optional (see "Slack notifications" below; remove # to enable)
# ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
# ERRANDER_SLACK_CHANNEL_ID=C0123456789
EOF
```

**Windows** — create `.env` with a text editor (Notepad, VS Code, etc.) using the same content as above.

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

> **Change the default password** — `changeme` is a placeholder. Update `ERRANDER_UI_PASSWORD` in `.env` before exposing the UI on any network.

The UI covers:
- `/ui/` — batch run history, event log, pending approvals
- `/ui/approvals` — approve or reject pending maintenance plans (replaces Slack when no token is set)
- `/ui/settings` — change LLM/approval settings at runtime (no restart required)
- `/ui/inventory` — disable YAML VMs or add ad-hoc VMs before the next run

Settings changed via the UI are stored in SQLite. Precedence chain:
```
env var  >  DB (UI)  >  settings.yaml  >  built-in default
```

### Slack notifications *(optional)*

> **You can skip this section entirely.** When no Slack token is configured, the agent uses **web UI approval mode**: maintenance plans that require approval appear at `http://<master-vm-ip>:9090/ui/approvals`. All other functionality (scheduling, SSH, audit trail, metrics) is unaffected.
>
> Come back here when you want Slack notifications and mobile approval reactions.

To enable Slack:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Errander-AI`, select your workspace
3. Under **OAuth & Permissions** → **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `reactions:read` — poll for ✅/❌ reactions
4. Click **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)
5. Create a Slack channel `#errander-approvals` and invite the bot to it
6. Copy the **Channel ID** — right-click the channel → View channel details → copy the ID at the bottom (starts with `C`)

Then **uncomment and fill in** the Slack lines in your `.env`:
```
ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
ERRANDER_SLACK_CHANNEL_ID=C0123456789
```

---

## Step 6 — Verify everything

```bash
# Verify inventory parses correctly (no env vars needed)
uv run python -m errander --check-inventory
```

If you ran `configure.sh`, the LLM connection was already verified as part of setup. To re-verify manually:

```bash
# Load env vars first — --check-llm needs ERRANDER_LLM_API_KEY
[ -f ~/.errander.key ] && source ~/.errander.key   # load secrets key if encryption is enabled
export $(grep -v '^#' .env | xargs)
uv run python -m errander --check-llm
```

---

## Step 7 — First run (dry-run)

Replace `<your-env-name>` with the environment name from your `inventory.yaml` (e.g. `dev`, `dr`, `production`).

The agent reads credentials from environment variables — load `.env` first, then run.

**Linux / Git Bash:**
```bash
export $(grep -v '^#' .env | xargs)
uv run python -m errander --run-now --env <your-env-name> --inventory inventory.yaml --dry-run --force --force-reason "initial dry-run validation"
```

**Windows PowerShell:**
```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
uv run python -m errander --run-now --env <your-env-name> --inventory inventory.yaml --dry-run --force --force-reason "initial dry-run validation"
```

> `--force` bypasses the maintenance window so this first validation run always succeeds regardless of day or time. Remove it once you've confirmed the setup works and set your maintenance window in `inventory.yaml`.

What happens:
1. SSH connects to each target VM and detects OS + disk state
2. Plans actions (disk cleanup, etc.)
3. Simulates all commands — prints `[DRY-RUN]` output, nothing is changed on the VMs
4. Writes results to `errander.sqlite`

---

## Step 8 — Live run

Once dry-run looks correct (replace `<your-env-name>` as above):

```bash
uv run python -m errander --run-now --env <your-env-name> --inventory inventory.yaml --live
```

Real commands execute on the target VMs.

---

## Step 9 — Run as a scheduled service (production)

### Linux controller — systemd

Run this from inside the `errander/` directory — it auto-fills your username and install path:

```bash
# Run from inside the errander/ directory
INSTALL_DIR=$(pwd)
INSTALL_USER=$(whoami)

sudo tee /etc/systemd/system/errander.service << EOF
[Unit]
Description=Errander-AI supervised agentic AI SRE platform
After=network.target

[Service]
Type=simple
User=${INSTALL_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m errander \\
  --inventory ${INSTALL_DIR}/inventory.yaml \\
  --config ${INSTALL_DIR}/settings.yaml
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

---

## For developers (contributing / running tests)

End users deploying the agent do not need these steps.

```bash
# Install dev tools (pytest, ruff, mypy, playwright)
uv sync --extra dev

# Install Chromium browser binary for UI tests (~150 MB, one-time)
uv run playwright install chromium

# Run the full test suite
# Do NOT export .env before this — exported secrets leak into tests and cause failures
uv run pytest

# Lint and type-check
uv run ruff check .
uv run mypy .
```
