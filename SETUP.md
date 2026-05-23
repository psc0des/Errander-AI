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

### Supported target operating systems

| OS family | Versions officially supported |
|---|---|
| Ubuntu | 20.04 LTS, 22.04 LTS, 24.04 LTS |
| Debian | 11 (Bullseye), 12 (Bookworm) |
| RHEL / Rocky / Alma | 8.x, 9.x |

Older distros may work, but Errander expects FHS-compliant absolute binary paths
(`/usr/bin/apt-get`, `/usr/sbin/logrotate`, etc.). Older distros that have not
adopted the `/usr → /` merge may need a runtime path resolver. That's tracked
as a separate compatibility project — `--check-targets <env>` will report any
missing binaries.

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

> **git not installed yet?** Download it from [git-scm.com/download/win](https://git-scm.com/download/win), install, reopen PowerShell, then run the commands above.

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

Once complete, **cd into the repo directory** before continuing:

```bash
cd errander
```

**Skip to Step 2** — the script handles everything else in Step 1.

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

## Optional: Docker hygiene

> **Skip this entire section if you are not enabling Docker hygiene.**
> When you create `inventory.yaml` in Step 5, leave `actions.docker_hygiene.enabled: false` (the default). Continue to the next section.

The Docker group is effectively root: a user in it can mount the host filesystem via `docker run`. Do **not** add `errander` to the docker group, and do not grant raw `sudo /usr/bin/docker` in production.

Errander supports two Docker modes per environment (`actions.docker_hygiene.command_mode` in `inventory.yaml`):

| Mode | Description | Use case |
|---|---|---|
| `wrapper` | Root-owned wrapper scripts at `/usr/local/sbin/errander-docker-*` | Production and all deployments — per-object validation requires the wrapper |
| `disabled` (default) | No Docker hygiene planned or executed | Envs without Docker or not yet set up |

### Part 1 — Install wrapper on each target VM *(do this now, after Step 3)*

Two steps — copy from the controller, then run on the target:

**From the controller** — copy using the errander key (no admin key needed on the controller):

```bash
scp -i ~/.ssh/errander_prod scripts/install-docker-wrappers-v2.sh errander@<target>:/tmp/
```

**On the target** — SSH in as your admin user and run as root:

```bash
ssh <admin-user>@<target>
sudo bash /tmp/install-docker-wrappers-v2.sh
```

### Part 2 — Configure inventory and verify *(complete this during Step 5 and Step 6)*

In your `inventory.yaml`, enable docker_hygiene and set the command mode:

```yaml
actions:
  docker_hygiene:
    enabled: true
    command_mode: wrapper
```

Then verify from the **controller VM** (where Errander is installed — not on the targets):

```bash
uv run python -m errander --check-targets <env>
```

> `uv` runs only on the controller. Errander SSHes into target VMs internally — no agent software is needed on the targets.

**Execution scope** — what Errander will propose for removal vs show for visibility only:

| Resource class | Classification | Eligible for removal? |
|---|---|---|
| `image_dangling` | `cleanup_candidate` | ✓ Always |
| `image_unused` (age > 30 days, unreferenced) | `cleanup_candidate` | ✓ Yes |
| `image_unused` (age ≤ 30 days) | `report_only` | No — shown for visibility |
| `container_stopped` (exit 0, age > 7 days) | `cleanup_candidate` | ✓ Yes |
| `container_stopped` (exit 137/139 — OOM/SIGSEGV) | `investigate` | No — operator decides |
| `container_stopped` (other) | `report_only` | No — shown for visibility |
| `volume_unreferenced` (not mounted > threshold, `volume_deletion_enabled: true`) | `cleanup_candidate` | ✓ Explicit-only — must name by index; `approve all` skips volumes |
| `volume_unreferenced` (default — `volume_deletion_enabled: false`) | `report_only` | No — shown for visibility |
| `build_cache` (`build_cache_deletion_enabled: true`) | `cleanup_candidate` | ✓ Yes — selectable by `approve all` |
| `build_cache` (default — `build_cache_deletion_enabled: false`) | `report_only` | No — shown for visibility |

The Slack approval message shows `✓` next to each `cleanup_candidate` finding and `(report-only)` next to others. `approve all cleanup_candidate` selects only eligible objects (volumes are **explicit-only** — they are never selected by `approve all` even when classified as cleanup_candidate; name them by index instead).

**v1.5 volume and build cache config fields** (all default-off; set under `actions.docker_hygiene` in `inventory.yaml`):

| Field | Default | Description |
|---|---|---|
| `volume_deletion_enabled` | `false` | Enable volume removal proposals. When `false`, volumes are always `report_only` regardless of age. |
| `volume_last_mount_days_threshold` | `90` | Minimum days since last mount for a volume to become a `cleanup_candidate`. Must be ≥ 1. |
| `build_cache_deletion_enabled` | `false` | Enable build cache removal proposals. When `false`, build cache is always `report_only`. |

> **Volumes require explicit approval.** Even when `volume_deletion_enabled: true`, the operator cannot use `approve all` to select volumes — they must name each one by its 1-based index in the approval message (e.g., `approve volumes 1,3`). This is intentional: volume data is permanently lost on removal.

> **Wrapper reinstall required.** v1.5 replaces the `volume_unreferenced|build_cache` catch-all branch in `errander-docker-remove-v2` with two separate branches that each perform a drift re-check before removing. Re-run `scripts/install-docker-wrappers-v2.sh` on each target VM after upgrading to v1.5.

**Assess wrapper output format** (parsed by `parse_assess_v2_output()` in `docker_hygiene.py`):

```
reachable=yes|no
error=<optional message>
docker_hygiene_begin
class=image_dangling
  id=<sha256> size_bytes=N age_days=N last_tag=<none>
class=image_unused
  id=<sha256> size_bytes=N age_days=N last_tag=<repo:tag>
class=container_stopped
  id=<sha256> name=<name> exit_code=N stopped_age_hours=N
class=volume_unreferenced
  name=<vol> size_bytes=N last_mount_days=N
class=build_cache
  reclaimable_bytes=N
docker_hygiene_end
```

---

## Optional: Service restart {#optional-service-restart}

> **Skip this entire section if you are not enabling service_restart.**
> When you create `inventory.yaml` in Step 5, leave `actions.service_restart.enabled: false` (the default). Continue to Step 4.

Service restart is operator-triggered only — Errander does not auto-restart services. The operator runs `--restart-service` after seeing a failed unit in the Slack probe digest, and must approve the restart in Slack before it executes. Risk tier: **HIGH** — Slack approval is always required.

### Part 1 — Install wrapper on each target VM *(do this now, after Step 3)*

The restart wrapper enforces a per-VM allowlist so Errander can only restart pre-approved units. Two steps — copy from the controller, then run on the target:

**From the controller** — copy using the errander key (no admin key needed on the controller):

```bash
scp -i ~/.ssh/errander_prod scripts/install-systemctl-restart-wrapper.sh errander@<target>:/tmp/
```

**On the target** — SSH in as your admin user and run as root:

```bash
ssh <admin-user>@<target>
sudo bash /tmp/install-systemctl-restart-wrapper.sh nginx.service gunicorn.service redis-server.service
```

Replace `nginx gunicorn redis-server` with the units this VM is allowed to restart. The allowlist is written to `/etc/errander/restart-allowlist` (one unit per line, mode 644).

### Part 2 — Configure inventory and verify *(complete this during Step 5 and Step 6)*

Add a `service_restart` block to each environment in your `inventory.yaml`. `restartable_units` is required when `enabled: true`:

```yaml
actions:
  service_restart:
    enabled: true
    restartable_units:
      - nginx.service
      - gunicorn.service
      - redis-server.service
```

Then verify from the **controller VM** (where Errander is installed — not on the targets):

```bash
uv run python -m errander --check-targets <env>
```

> `uv` runs only on the controller. Errander SSHes into target VMs internally — no agent software is needed on the targets.

This verifies the wrapper exists on each VM, the sudoers entry is correct, and the on-target allowlist matches your inventory `restartable_units`. Drift in either direction is reported as a warning.

### Trigger a restart *(once Step 5 and Step 6 are complete)*

```bash
uv run python -m errander --restart-service production --unit nginx.service --vm prod-web-01 --dry-run
# Review plan, then live:
uv run python -m errander --restart-service production --unit nginx.service --vm prod-web-01
```

Approve the plan in `#errander-approvals` with ✅. The wrapper captures pre/post status + journal; a verify step confirms the unit reached `active` state. If verification fails, a `SERVICE_RESTART_VERIFY_FAILED` event is logged and Slack is notified — no automatic re-restart attempt.

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

# Inventory path (used by the web UI in live mode)
ERRANDER_INVENTORY_PATH=inventory.yaml

# Web UI auth — change all three before exposing to a network
ERRANDER_UI_USERNAME=admin
ERRANDER_UI_PASSWORD=changeme
ERRANDER_UI_SECRET=change-this-to-a-random-32-char-string

# Web UI data mode: "fixture" (demo/default) or "live" (real backend stores)
# Set to "live" when running against real VMs
ERRANDER_UI_DATA_MODE=fixture

# Slack — optional (see "Slack notifications" below; remove # to enable)
# ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
# ERRANDER_SLACK_CHANNEL_ID=C0123456789

# Signed-URL HMAC secret — required for docker_hygiene web approval (v1.1).
# Generate with: head -c 32 /dev/urandom | base64
# ERRANDER_SIGNING_SECRET=paste-base64-output-here

# Base URL for agent VM web UI — used to build signed web-approval URLs
# in Slack messages (docker_hygiene). Empty = web URL omitted from Slack.
# ERRANDER_WEB_BASE_URL=http://10.0.0.5:9090
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

The agent exposes a web UI at `http://<master-vm-ip>:9090/ui` (port 9090 — see Prerequisites for NSG setup). The `ERRANDER_UI_USERNAME` / `ERRANDER_UI_PASSWORD` env vars in the `.env` template above enable HTTP Basic Auth.

> **Change all three UI secrets** — `ERRANDER_UI_PASSWORD` and `ERRANDER_UI_SECRET` are placeholders. Set both to unique values in `.env` before exposing the UI on any network. `ERRANDER_UI_SECRET` signs the 8-hour session cookie; if left as default, any attacker who knows the default can forge a session.

#### Data mode: fixture vs live

| `ERRANDER_UI_DATA_MODE` | Behaviour |
|---|---|
| `fixture` (default) | Shows realistic demo data — safe for demos and CI. No real stores needed. |
| `live` | Shows real data from your AuditStore, inventory, and ApprovalManager. Missing stores render "Unavailable" — never shows fake data. |

Set `ERRANDER_UI_DATA_MODE=live` in `.env` when running against real VMs. Also set `ERRANDER_INVENTORY_PATH` to the path of your `inventory.yaml` so the web UI can read VM identity.

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

### Prometheus metrics *(optional)*

> **Skip if you don't have Prometheus.** The agent runs fully without it. Prometheus adds VM CPU, memory, and disk metrics to the `--ask` fleet analysis and daily probe digest.

If you have a Prometheus instance scraping your VMs via `node_exporter`:

```
ERRANDER_PROMETHEUS_BASE_URL=http://<prometheus-host>:9090
```

Add this to your `.env`. Leave it blank or omit it entirely to disable.

**Per-environment override:** If each environment has its own Prometheus cluster, set `prometheus_url:` under the environment block in `inventory.yaml`. The env-level value takes priority over the global `.env` setting:

```yaml
# inventory.yaml
environments:
  production:
    prometheus_url: http://10.0.1.100:9090   # overrides ERRANDER_PROMETHEUS_BASE_URL
  staging:
    # no prometheus_url → uses global ERRANDER_PROMETHEUS_BASE_URL
```

### ELK / Elasticsearch log aggregation *(optional)*

> **Skip if you don't use ELK.** Without it the agent reads `journalctl` directly from each VM via SSH. ELK adds aggregated log error counts and error summaries to the probe digest and `--ask` analysis.

#### Check if auth is required

```bash
curl http://<elasticsearch-host>:9200/_cluster/health
```

- **Returns JSON** — no auth required, skip the API key steps below and leave `ERRANDER_ELK_API_KEY` blank.
- **Returns 401** — security is enabled, follow the steps below to create an API key.

#### Create a scoped API key (if auth is required)

First, find your `elastic` user password — check your docker-compose file or the container's first-startup logs:

```bash
docker logs <elasticsearch-container> 2>&1 | grep -i password
# or
grep -i elastic_password /path/to/docker-compose.yml
```

Then create a read-only API key scoped to Errander:

```bash
curl -u elastic:<password> -X POST http://<elasticsearch-host>:9200/_security/api_key \
  -H "Content-Type: application/json" \
  -d '{
    "name": "errander-ai",
    "role_descriptors": {
      "errander": {
        "cluster": ["monitor"],
        "indices": [{"names": ["*"], "privileges": ["read"]}]
      }
    }
  }'
```

Copy the `encoded` field from the response — that is your API key.

#### Add to `.env`

```
ERRANDER_ELK_BASE_URL=http://<elasticsearch-host>:9200
ERRANDER_ELK_API_KEY=<encoded value from above>   # omit entirely if no auth required
ERRANDER_ELK_INDEX_PATTERN=filebeat-*,logstash-*  # default; change if needed
```

Add these to your `.env`. Leave `ERRANDER_ELK_BASE_URL` blank or omit it to disable.

**Per-environment override:** Each ELK field can be overridden independently in `inventory.yaml`. Fields not set in the env block fall back to the global `.env` values:

```yaml
# inventory.yaml
environments:
  production:
    elk_url: http://10.0.1.101:9200          # overrides ERRANDER_ELK_BASE_URL
    elk_index_pattern: prod-logs-*           # overrides ERRANDER_ELK_INDEX_PATTERN
    # elk_api_key not set → uses global ERRANDER_ELK_API_KEY
  staging:
    # no elk_* → all use global .env values
```

---

## Step 6 — Verify everything

Run these in order:

**1. Validate inventory parses correctly** *(no env vars needed)*

```bash
uv run python -m errander --check-inventory
```

**2. Load env vars** *(required for all remaining commands)*

```bash
# Linux / Git Bash
export $(grep -v '^#' .env | xargs)
```

```powershell
# Windows PowerShell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
```

**3. Verify LLM connectivity**

If you ran `configure.sh`, this was already done. Re-verify any time with:

```bash
uv run python -m errander --check-llm
# Expected: Status: OK, Latency: <Xms>, Response: 'OK'
```

**4. Pin SSH host keys** *(run once per environment — prevents host key prompts during batches)*

```bash
uv run python -m errander --bootstrap-known-hosts <your-env-name>
```

This pins the host keys and automatically adds `ERRANDER_SSH_KNOWN_HOSTS` to your `.env`. Reload env vars before continuing:

```bash
export $(grep -v '^#' .env | xargs)
```

**5. Verify target VM readiness**

```bash
uv run python -m errander --check-targets <your-env-name>
```

This confirms SSH access, sudo permissions, OS detection, and binary paths for every VM in the environment.

**5b. If you installed Docker wrappers** — add this to each environment block in `inventory.yaml`, then re-run `--check-targets`:

```yaml
actions:
  docker_hygiene:
    enabled: true
    command_mode: wrapper
```

```bash
uv run python -m errander --check-targets <your-env-name>
```

**5c. If you installed the service restart wrapper** — add this to each environment block in `inventory.yaml`, then re-run `--check-targets`:

```yaml
actions:
  service_restart:
    enabled: true
    restartable_units:
      - nginx        # replace with your actual units
      - gunicorn
```

```bash
uv run python -m errander --check-targets <your-env-name>
```

`--check-targets` verifies wrapper presence, sudoers entry, and (for service restart) that the on-target allowlist matches your `restartable_units`. Any drift is reported as a warning.

**5d. Configure Node Exporter for richer VM metrics** *(optional but recommended)*

The Operations Hub VM Detail page can show CPU/MEM/disk trends from either SSH probes or [Prometheus Node Exporter](https://github.com/prometheus/node_exporter) on each target VM. Node Exporter is preferred: it runs as a lightweight systemd service on `:9100` and avoids auth-log noise from per-minute SSH logins.

Run the interactive setup script once after editing `inventory.yaml`:

```bash
bash configure.sh
```

For each VM it will:
1. Check SSH connectivity
2. Check if Node Exporter is already running on `:9100`
3. If not found — prompt **"Install Node Exporter? [Y/n]"** (default Y)
4. Install Node Exporter via SSH (curl from GitHub, systemd unit, enable + start)
5. Write `node_exporter: true/false` into `inventory.yaml`

Skip this step to use SSH-probe fallback for all VMs (slightly higher SSH auth-log activity). Re-run `configure.sh` at any time after adding new VMs.

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

**Linux / Git Bash:**
```bash
export $(grep -v '^#' .env | xargs)
uv run python -m errander --run-now --env <your-env-name> --inventory inventory.yaml --live
```

**Windows PowerShell:**
```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
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

**`--check-llm` says UNREACHABLE**
- Cloud API (OpenAI / Groq / Azure): confirm your API key is correct and `ERRANDER_LLM_BASE_URL` matches the provider's endpoint exactly
- Ollama: confirm it's running (`systemctl status ollama` or `curl http://localhost:11434/api/tags`)
- vLLM: confirm the container is up (`docker compose -f deploy/vllm/docker-compose.yml ps`), check logs (`docker compose -f deploy/vllm/docker-compose.yml logs --tail 50`), and confirm port 8000 is reachable from the controller (`curl http://<llm-vm-ip>:8000/health`)

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
| `ERRANDER_UI_USERNAME` | No | `admin` | Web UI login username |
| `ERRANDER_UI_PASSWORD` | No | `errander` | Web UI login password — **change before any network exposure** |
| `ERRANDER_UI_SECRET` | No | dev default | HMAC key for 8-hour session cookie — **change before any network exposure** |
| `ERRANDER_UI_DATA_MODE` | No | `fixture` | UI data source: `fixture` (demo) or `live` (real stores) |
| `ERRANDER_INVENTORY_PATH` | No | `inventory.yaml` | Path to inventory file — used by the web UI in live mode to read VM identity |

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
