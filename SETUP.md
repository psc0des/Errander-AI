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
| **Master VM / Controller** | Errander-AI agent, SQLite, web UI — a Linux VM (Windows controller: see [SETUP-Win-Controller.md](SETUP-Win-Controller.md)) |
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

> **These are handled automatically by the bootstrap script in Step 1.** You only need to verify them if you are skipping the bootstrap and doing a manual install.

### Network ports

Everything the controller needs to reach, and what needs to reach the controller:

**Outbound from the controller (open by default on most cloud VMs)**

| Destination | Port | Required? | Notes |
|---|---|---|---|
| Target VMs (private IP) | 22 (SSH) | **Required** | Same-VNet: allowed by default. Cross-VNet/peered: may need NSG rule |
| Slack API (`slack.com`) | 443 (HTTPS) | Optional | Only if Slack notifications are enabled. Outbound HTTPS is allowed by default |
| Cloud LLM API (OpenAI / Groq / Azure AI Foundry) | 443 (HTTPS) | Optional | Only if using a cloud LLM provider. Outbound HTTPS is allowed by default |
| Self-hosted vLLM (private IP, GPU VM) | 8000 (HTTP) | Optional | Only if using self-hosted vLLM. Same-VNet: needs NSG allow rule on the GPU VM inbound |
| Your existing Prometheus server (private IP) | varies (HTTP) | Optional | Errander reads metrics from a Prometheus instance you already have — it does not install or run Prometheus itself. Set `ERRANDER_PROMETHEUS_BASE_URL` to point at it (commonly 9090, but use whatever port your Prometheus runs on) |
| Your existing ELK / Elasticsearch server (private IP) | 9200 (HTTP) | Optional | Errander reads logs from an Elasticsearch instance you already have — it does not install or run ELK itself. Set `ERRANDER_ELK_BASE_URL` to point at it |

**Inbound to the controller**

| Source | Port | Required? | Notes |
|---|---|---|---|
| Your laptop | 22 (SSH) | **Required** | Already open if you SSH to the controller today |
| Your laptop | 9090 (Web UI) | **Required** | Must be opened — see NSG note below |

> **Summary for Azure NSG / firewall rules:** The only port you typically need to open is **inbound 9090** on the controller. Outbound is unrestricted by default. The only non-standard outbound rule needed is **TCP 8000 on the GPU VM's inbound** if using self-hosted vLLM.

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

> **Windows controller?** See [SETUP-Win-Controller.md](SETUP-Win-Controller.md).

> **Two users, two machines — don't confuse them:**
> - **Controller:** `errander-agent` — the service account bootstrap creates; runs the agent, holds the SSH private key
> - **Each target VM:** `errander` — a separate account you create in Step 2 that receives SSH connections from the controller
>
> These are unrelated accounts on different machines.

### Linux controller

Two steps: **admin** handles all system-level setup (including cloning the repo), then **errander-agent** installs dependencies. No sudo required after Step A.

**Step A — System setup** (run as your admin user — needs sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/bootstrap.sh | bash
```

`bootstrap.sh` is self-contained — no repo needed before running. It:
- Detects your distro (Ubuntu, Debian, RHEL, CentOS, Oracle Linux, Fedora)
- Installs git, curl, uv (at `/usr/local/bin`), Python 3.12
- Creates the `errander-agent` service user with a `.ssh` directory
- Clones the repo into `/home/errander-agent/errander` (pulls latest on re-runs)

**Step B — Install dependencies** (run as the service user — no sudo required):

```bash
sudo su - errander-agent
cd ~/errander
uv sync --extra dev
uv run python -c "import errander; print('OK')"
```

**All remaining steps run as `errander-agent`.** Continue to Step 2 — `configure.sh` comes later in Step 5 once SSH keys and target VMs are set up.

> **Prefer to inspect bootstrap.sh before running it?** Clone it first — bootstrap.sh skips any step already done:
> ```bash
> git clone https://github.com/psc0des/Errander-AI.git errander-setup
> bash errander-setup/scripts/bootstrap.sh
> ```

> **Prefer to install manually?** → [Appendix C: Manual installation](#appendix-c-manual-installation)

---

## Step 2 — Set up SSH keys

Errander-AI connects to target VMs using key-based SSH only. No passwords are ever used.

The SSH key is generated **on the Master VM (controller)** — that is the machine running the agent, which makes outbound SSH connections to target VMs.

```
Your laptop → (SSH) → Master VM → (SSH, private IP) → Target VM
                       ↑ key lives here
```

### On the Master VM

> **Which user?** On **Linux**: run all commands in this step as `errander-agent` — the service user `bootstrap.sh` created. On **Windows**: run in Git Bash as your own user (see [SETUP-Win-Controller.md](SETUP-Win-Controller.md)). Do **not** run as `root`. The `~` in `~/.ssh/errander_prod` expands to that user's home directory. If you generate the key as a different user than the one running the agent, the agent cannot read the key and every SSH connection will fail.

**1. Generate the key pair**

```bash
# Run this on the Master VM — as the user who will run the Errander-AI agent (not root)
ssh-keygen -t ed25519 -f ~/.ssh/errander_prod -C "errander-agent" -N ""
```

This creates two files in that user's home directory on the Master VM:
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

> **sudo -n model:** The agent calls all privileged commands as `sudo -n /absolute/path ...` (e.g. `sudo -n /usr/bin/apt-get upgrade -y` on Ubuntu, `sudo -n /usr/bin/dnf upgrade -y` on RHEL-family). `sudo -n` fails immediately with exit 1 if passwordless sudo is not configured — it never hangs waiting for a password. Absolute paths match sudoers entries predictably and produce clean audit logs in `/var/log/auth.log` on the target VM.

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

> **Which entries apply to which OS:**
>
> | Entry | Ubuntu / Debian | RHEL / AlmaLinux / Rocky |
> |---|---|---|
> | `/usr/bin/apt-get`, `/usr/bin/apt-mark` | ✓ Used | Harmless — binary does not exist, never called |
> | `/usr/bin/dnf`, `/usr/bin/yum` | Harmless — not called | ✓ Used (`dnf` on RHEL 8/9+) |
> | `/usr/bin/needs-restarting` | Not used | ✓ Used — requires `dnf-utils` installed: `sudo dnf install -y dnf-utils` |
> | All other entries | ✓ Same path on both | ✓ Same path on both |
>
> The template is intentionally cross-distro — paste it on any supported OS and Errander will only call the binaries that exist.

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

Use the package manager that matches the target OS:

```bash
# Ubuntu / Debian
sudo -u errander sudo -n /usr/bin/apt-get --version
# Expected: apt-get version output — no password prompt, exits 0

# RHEL / AlmaLinux / Rocky / CentOS
sudo -u errander sudo -n /usr/bin/dnf --version
# Expected: dnf version output — no password prompt, exits 0
```

> **AlmaLinux / Rocky / RHEL note:** `/usr/bin/apt-get` does not exist on these distros — always use `/usr/bin/dnf` to verify. Listing `apt-get` in the sudoers file is harmless (the binary simply doesn't exist), but using it for verification will produce `command not found`.

```bash
# Confirm sudo -n fails fast when not configured (sanity check — any OS):
sudo -u errander sudo -n /usr/bin/id  # should FAIL — /usr/bin/id is not in sudoers
# Expected: "sudo: a password is required" — exit 1 immediately, no hanging
```

---

## Optional: Docker hygiene

> **Skip this entire section if you are not enabling Docker hygiene.**
> When you create `inventory.yaml` in Step 5, leave `actions.docker_hygiene.enabled: false` (the default). Continue to the next section.

> **Why not `sudo docker` in sudoers?** Raw `sudo docker` is root-equivalent — any `docker run` command can mount the host filesystem. The wrapper approach restricts the agent to exactly the prune operations it is allowed to perform. Do NOT add `/usr/bin/docker` to `/etc/sudoers.d/errander`.

> **Read-only commands** — these run without sudo (no sudoers entry needed): `df`, `du`, `dpkg-query`, `rpm -q`, `apt list`, `dnf check-update`, `find` (listing only), `stat`, `systemctl is-active`, `journalctl --disk-usage`.

The Docker group is effectively root: a user in it can mount the host filesystem via `docker run`. Do **not** add `errander` to the docker group, and do not grant raw `sudo /usr/bin/docker` in production.

Errander supports two Docker modes per environment (`actions.docker_hygiene.command_mode` in `inventory.yaml`):

| Mode | Description | Use case |
|---|---|---|
| `wrapper` | Root-owned wrapper scripts at `/usr/local/sbin/errander-docker-*` | Production and all deployments — per-object validation requires the wrapper |
| `disabled` (default) | No Docker hygiene planned or executed | Envs without Docker or not yet set up |

### Part 1 — Install wrapper on each target VM *(do this now, after Step 3)*

Two steps — copy from the controller, then run on the target:

**From the controller** — run from the repo directory (`~/errander`), not from `~/.ssh`:

```bash
cd ~/errander
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

> **Full annotated example:** `example/inventory.yaml` → `dev` environment shows a complete `docker_hygiene` block including the v1.5 volume and build cache fields.

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

Service restart is operator-triggered only — Errander does not auto-restart services. The operator runs `--restart-service` after seeing a failed unit in the probe digest, and must approve the restart before it executes. Risk tier: **HIGH** — human approval is always required (via Slack reaction or web UI at `/ui/approvals`).

### Part 1 — Install wrapper on each target VM *(do this now, after Step 3)*

The restart wrapper enforces a per-VM allowlist so Errander can only restart pre-approved units. Two steps — copy from the controller, then run on the target:

**From the controller** — run from the repo directory (`~/errander`), not from `~/.ssh`:

```bash
cd ~/errander
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

> **Full annotated example:** `example/inventory.yaml` → `dev` environment shows a complete `service_restart` block with real unit names.

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

Approve the plan via Slack reaction ✅ in `#errander-approvals`, or via the web UI at `http://<controller>:9090/ui/approvals`. The wrapper captures pre/post status + journal; a verify step confirms the unit reached `active` state. If verification fails, a `SERVICE_RESTART_VERIFY_FAILED` event is logged — no automatic re-restart attempt.

---


## Step 4 — Set up an LLM *(Master VM)*

`configure.sh` (Step 5) will prompt you for three values: your **Endpoint URL**, **Model name**, and **API key**. Have them ready before the next step.

> **Where do I find these?** → [Appendix A: LLM provider reference](#appendix-a-llm-provider-reference) — exact endpoint URLs, model names, and key locations for Azure AI Foundry, OpenAI, Groq, Ollama, and vLLM.

The LLM is optional — the agent falls back to built-in hardcoded logic if no LLM is configured.

---

## Step 5 — Configure the agent

> **Want Docker hygiene or service restart?** Install wrapper scripts first (see Optional sections above), then come back here.

Run the interactive setup script from inside the `errander/` directory:

**Linux** — run from the repo directory (`~/errander`):
```bash
cd ~/errander
bash scripts/configure.sh
```

`configure.sh` prompts for LLM credentials, SSH key path, target VMs, and optional Slack — then writes `.env` and `inventory.yaml`, tests the LLM connection, and optionally pins SSH host keys. Skip to [Step 6 — Verify everything](#step-6--verify-everything) when done.

**After configure.sh, set these in `.env` if needed:**

| Variable | Default | When to change |
|---|---|---|
| `ERRANDER_UI_PASSWORD` | `errander` | Before any network exposure |
| `ERRANDER_UI_SECRET` | dev default | Before any network exposure — signs session cookies |
| `ERRANDER_UI_DATA_MODE` | `fixture` | Set to `live` when running against real VMs |
| `ERRANDER_SIGNING_SECRET` | unset | Required for docker_hygiene web approval |
| `ERRANDER_WEB_BASE_URL` | unset | e.g. `http://10.0.0.5:9090` — enables signed approval URL in Slack |

> **Full reference** (complete `.env` template, web UI, Slack, Prometheus, LangSmith, ELK) → [Appendix B: Agent configuration reference](#appendix-b-agent-configuration-reference)

---

## Adding target VMs after initial setup

Once Errander-AI is configured, use `scripts/add-target.sh` to add new VMs without touching `.env` or re-running the full setup wizard.

```bash
bash scripts/add-target.sh
```

The script reads your existing `inventory.yaml`, shows your current environments and VMs, and walks you through adding new ones:

1. Choose which environment to add to (shown with current VM count)
2. Enter hostname/IP, VM name, and OS family for each new VM
3. Optionally verify SSH connectivity immediately
4. Writes the new VM block into `inventory.yaml` under the correct environment — your `.env` is never touched

**After running the script**, complete the usual per-target setup on each new VM:

| Step | Command / action |
|---|---|
| SSH setup | Create `errander` user + install public key — [Step 2](#step-2--set-up-ssh-keys) |
| Sudo permissions | Grant passwordless sudo — [Step 3](#step-3--configure-target-vms-sudo-permissions) |
| Docker hygiene *(if env uses it)* | Install wrappers — [Optional: Docker hygiene](#optional-docker-hygiene) |
| Service restart *(if env uses it)* | Install wrapper + update allowlist — [Optional: Service restart](#optional-service-restart) |
| Verify | `uv run python -m errander --check-targets <env>` |
| Pin host key | `uv run python -m errander --bootstrap-known-hosts <env>` |

> **When to use `configure.sh` vs `add-target.sh`**
>
> | Situation | Use |
> |---|---|
> | Adding VMs to an existing install | `bash scripts/add-target.sh` |
> | Changing LLM, Slack, or UI credentials | `bash scripts/configure.sh` |
> | First-time setup from scratch | `bash scripts/configure.sh` |

---

## Step 6 — Verify everything

Run these in order:

**1. Validate inventory parses correctly** *(no env vars needed)*

```bash
uv run python -m errander --check-inventory
```

**2. Load env vars**

The agent loads `.env` automatically at startup — no manual sourcing needed. The only exception is `--check-inventory`, which runs before the app initialises (step 1 above, already covered).

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

This pins the host keys and automatically adds `ERRANDER_SSH_KNOWN_HOSTS` to your `.env`. The next errander command picks it up automatically.

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
      - nginx.service        # replace with your actual units
      - gunicorn.service
```

```bash
uv run python -m errander --check-targets <your-env-name>
```

`--check-targets` verifies wrapper presence, sudoers entry, and (for service restart) that the on-target allowlist matches your `restartable_units`. Any drift is reported as a warning.

---

## Step 7 — First run (dry-run)

Replace `<your-env-name>` with the environment name from your `inventory.yaml` (e.g. `dev`, `dr`, `production`).

The agent loads `.env` automatically.

```bash
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

Once dry-run looks correct, run live for each environment you want to execute against.
`--env` targets one environment at a time — run once per env, or in parallel across terminals:

```bash
# Single environment
uv run python -m errander --run-now --env dr --inventory inventory.yaml --live

# Multiple environments (run in separate terminals, or background each)
uv run python -m errander --run-now --env prod --inventory inventory.yaml --live
uv run python -m errander --run-now --env staging --inventory inventory.yaml --live
```

Real commands execute on the target VMs. Each environment gets its own Slack approval message — approve or reject them independently.

---

## Step 9 — Run as a scheduled service (production)

In service mode (no `--run-now`, no `--env`), Errander runs as a long-lived daemon and executes **all environments** defined in `inventory.yaml` according to each environment's `maintenance_window` schedule. No `--env` flag needed — the scheduler picks up every environment automatically.

### systemd service *(Linux)*

> **Switch back to your admin user for this step** — `sudo` is required to write the systemd unit file and manage services. `errander-agent` has no sudo access.

Run this from inside the errander directory (`/home/errander-agent/errander`):

```bash
# Run as your admin user from /home/errander-agent/errander
INSTALL_DIR=/home/errander-agent/errander

sudo tee /etc/systemd/system/errander.service << EOF
[Unit]
Description=Errander-AI supervised agentic AI SRE platform
After=network.target

[Service]
Type=simple
User=errander-agent
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-/home/errander-agent/.errander.key
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m errander \\
  --inventory ${INSTALL_DIR}/inventory.yaml
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

**Access the web UI**

Once the service is running, the web UI is available on port 9090 of the controller VM:

```
http://<controller-ip>:9090/ui
```

If you can't reach port 9090 directly (e.g., Azure NSG blocks it), use an SSH tunnel from your laptop:

```bash
ssh -L 9090:localhost:9090 <your-admin-user>@<controller-ip>
```

Then open `http://localhost:9090/ui` in your browser. No firewall changes needed.

**Tail the service logs**

```bash
journalctl -u errander -f
```

---

## Starting fresh / teardown

To fully uninstall everything bootstrap installed (for re-testing or decommissioning):

```bash
sudo bash scripts/teardown.sh
# or without a local copy:
curl -fsSL https://raw.githubusercontent.com/psc0des/Errander-AI/main/scripts/teardown.sh | bash
```

Type `yes` at the prompt. Removes: the `errander-agent` user + home (repo, `.env`, `inventory.yaml`) and `uv` from `/usr/local/bin`. Does **not** remove git, curl, or Python 3.12. Prometheus and Grafana run on a separate monitoring VM — use your normal process to remove them there.

---

## Troubleshooting

**SSH connection fails**
- Test manually: `ssh -i ~/.ssh/errander_prod errander@<vm-ip> "echo ok"`
- Confirm the public key is in `~errander/.ssh/authorized_keys` on the target
- Check `sshd` is running: `sudo systemctl status sshd`

**`--check-llm` says UNREACHABLE**
- Cloud API (OpenAI / Groq / Azure): confirm your API key is correct and `ERRANDER_LLM_BASE_URL` matches the provider's endpoint exactly
- Ollama: confirm it's running (`systemctl status ollama` or `curl http://localhost:11434/api/tags`)
- vLLM: confirm the container is up (`docker compose -f deploy/vllm/docker-compose.yml ps`), check logs (`docker compose -f deploy/vllm/docker-compose.yml logs --tail 50`), and confirm port 8000 is reachable from the controller (`curl http://<llm-vm-ip>:8000/health`)

**`Outside maintenance window` error**
- Add `--force --force-reason "manual test"` to bypass the window check

**vLLM container exits immediately**
- GPU not found: `docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi` (works on any Linux GPU VM — the image OS doesn't need to match the host)
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
| `ERRANDER_SSH_STRICT_HOST_KEYS` | No | `true` | SSH host key policy. `false` = TOFU mode (WARNING logged per connection). Set `true` after running `--bootstrap-known-hosts` to enable strict verification. |
| `ERRANDER_SSH_KNOWN_HOSTS` | No | — | Path to known_hosts file for SSH host key pinning. Set automatically by `--bootstrap-known-hosts` (default: `~/.ssh/errander_known_hosts`). Required when `ERRANDER_SSH_STRICT_HOST_KEYS=true`. |
| `LANGCHAIN_TRACING_V2` | No | unset (off) | Set to `true` to enable LangSmith tracing of Layer-A LangGraph reasoning. **Dev/staging only** — sends prompt contents to LangChain cloud. Never enable in no-egress prod. |
| `LANGCHAIN_API_KEY` | No | — | LangSmith API key (`lsv2_pt_...`). Required when `LANGCHAIN_TRACING_V2=true`. |
| `LANGCHAIN_PROJECT` | No | `default` | LangSmith project name to group traces under (e.g. `errander-ai`). |

---

## Monitoring

Once the agent is running, the following endpoints are exposed on the metrics port (default `9090`):

| URL | What it shows |
|---|---|
| `http://<controller>:9090/ui` | Dashboard — recent batches, event counts, VM history |
| `http://<controller>:9090/ui/monitoring` | **Built-in monitoring** — action trends, approval funnel, safety signals, duration averages |
| `http://<controller>:9090/ui/batches` | Full batch history |
| `http://<controller>:9090/ui/approvals` | Pending approvals — Approve/Reject buttons |
| `http://<controller>:9090/ui/settings` | Runtime LLM/approval settings (no restart required) |
| `http://<controller>:9090/ui/inventory` | Disable YAML VMs or add ad-hoc VMs before the next run |
| `http://<controller>:9090/ui/ai-decisions` | AI decision log — LLM calls, outcomes, latencies |
| `http://<controller>:9090/metrics` | Prometheus metrics endpoint (scrape with external Prometheus) |
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

---

## Appendix A: LLM provider reference

Endpoint URL, model name, and API key for each supported provider. You will be prompted for these by `configure.sh` (Step 5). For deeper per-provider config options see `docs/LLM-PROVIDERS.md`.

The LLM is **optional** — the agent falls back to built-in hardcoded logic if no LLM is configured.

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

---

### Option A — Cloud API *(no extra infrastructure)*

**All commands run on the Master VM.**

#### Azure AI Foundry *(if you have an Azure subscription)*

Azure AI Foundry has two URL formats depending on how your resource was created:

**New Foundry project endpoint** (Azure AI Foundry portal — recommended):
1. Open your project in [Azure AI Foundry portal](https://ai.azure.com)
2. Go to **Settings → API keys** — copy a key
3. The endpoint is shown on the project overview page

```
BASE_URL  = https://<hub>.services.ai.azure.com/api/projects/<project>/v1/   ← trailing / required
MODEL     = <your-deployment-name>                                              ← name you gave the deployment
API_KEY   = <key from project Settings → API keys>
```

**Classic Azure OpenAI resource** (Azure portal → Azure OpenAI service):
1. Go to your **Azure OpenAI resource** → **Keys and Endpoint**
2. Copy the **Endpoint URL** and one of the **Keys**

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

**Verify** — test the connection *(Master VM, inside `errander/` directory)*:
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

# Ubuntu / Debian:
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -sL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit

# RHEL / AlmaLinux / Rocky (use this block instead of the Ubuntu block above):
# curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
#   | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
# sudo dnf install -y nvidia-container-toolkit

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify GPU is visible to Docker (image works on both Ubuntu and RHEL GPU VMs)
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
```

```bash
# Clone the repo on the GPU VM (only deploy/vllm is needed)
git clone https://github.com/psc0des/Errander-AI.git errander
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

## Appendix B: Agent configuration reference

Full `.env` template and optional service configuration. Use this if you prefer to configure
manually instead of using `configure.sh`, or to set options `configure.sh` does not cover
(web UI auth, LangSmith, ELK, Prometheus).

### Create `.env` manually *(Master VM)*

Inside the `errander/` directory:

```bash
cat > .env << 'EOF'
# LLM — paste the values from Appendix A
ERRANDER_LLM_BASE_URL=<base-url>
ERRANDER_LLM_MODEL=<model>
ERRANDER_LLM_API_KEY=<api-key>

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

# SSH host key verification
# Run --bootstrap-known-hosts <env> to pin host keys, then set this to true
ERRANDER_SSH_STRICT_HOST_KEYS=false
# ERRANDER_SSH_KNOWN_HOSTS=~/.ssh/errander_known_hosts

# LangSmith — optional, Layer A tracing only (dev/staging).
# Sends Layer-A prompt contents off-network — do NOT enable in no-egress prod.
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=lsv2_pt_...
# LANGCHAIN_PROJECT=errander-ai
EOF
```

> Never commit `.env` — it is already in `.gitignore`.

### Create your inventory manually

```bash
cp example/inventory.yaml inventory.yaml
```

Minimal example for a single dev VM:

```yaml
environments:
  dev:
    ssh_user: errander
    ssh_key_path: ~/.ssh/errander_prod
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

The agent exposes a web UI at `http://<master-vm-ip>:9090/ui`. The `ERRANDER_UI_USERNAME` / `ERRANDER_UI_PASSWORD` env vars enable HTTP Basic Auth.

> **Change all three UI secrets** before exposing the UI on any network. `ERRANDER_UI_SECRET` signs the 8-hour session cookie — if left as default any attacker who knows the default can forge a session.

#### Data mode: fixture vs live

| `ERRANDER_UI_DATA_MODE` | Behaviour |
|---|---|
| `fixture` (default) | Shows realistic demo data — safe for demos and CI. No real stores needed. |
| `live` | Shows real data from your AuditStore, inventory, and ApprovalManager. Missing stores render "Unavailable" — never shows fake data. |

Set `ERRANDER_UI_DATA_MODE=live` when running against real VMs. Also set `ERRANDER_INVENTORY_PATH` to the path of your `inventory.yaml`.

The UI covers:
- `/ui/` — batch run history, event log, pending approvals
- `/ui/monitoring` — **built-in monitoring dashboard**: action trends, approval funnel, safety signals, duration averages, live Prometheus counters
- `/ui/approvals` — approve or reject pending maintenance plans (replaces Slack when no token is set)
- `/ui/settings` — change LLM/approval settings at runtime (no restart required)
- `/ui/inventory` — disable YAML VMs or add ad-hoc VMs before the next run
- `/ui/ai-decisions` — AI decision log (LLM calls, outcomes, latencies)

Settings changed via the UI are stored in SQLite. Precedence chain:
```
env var  >  DB (UI)  >  settings.yaml  >  built-in default
```

### Slack notifications *(optional)*

> **Skip this entirely** if you are happy with web UI approval at `/ui/approvals`. All other functionality is unaffected.

To enable Slack:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it `Errander-AI`, select your workspace
3. Under **OAuth & Permissions** → **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `reactions:read` — poll for ✅/❌ reactions
4. Click **Install to Workspace** → copy the **Bot User OAuth Token** (`xoxb-...`)
5. Create a Slack channel `#errander-approvals` and invite the bot to it
6. Copy the **Channel ID** — right-click the channel → View channel details → copy the ID at the bottom (starts with `C`)

Uncomment and fill in the Slack lines in `.env`:
```
ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
ERRANDER_SLACK_CHANNEL_ID=C0123456789
```

### Prometheus metrics *(optional — reads existing Prometheus)*

> **Two different Prometheus relationships.** This section is **Errander → Prometheus**: the agent *reads* target-VM metrics from a Prometheus *you already run*. The monitoring stack section below is the reverse: Prometheus *scrapes the agent itself*.

> **Skip if you don't have Prometheus.** The agent runs fully without it.

```
ERRANDER_PROMETHEUS_BASE_URL=http://<prometheus-host>:9090
```

Add to `.env`. Per-environment override in `inventory.yaml`:

```yaml
environments:
  production:
    prometheus_url: http://10.0.1.100:9090
  staging:
    # no prometheus_url → uses global ERRANDER_PROMETHEUS_BASE_URL
```

### Monitoring stack: Prometheus + Grafana *(optional — dedicated external VM only)*

> **Built-in first — no external stack needed on the agent VM.** `/ui/monitoring` already shows action trends, approval funnel, safety signals, and duration averages. It reads from the audit DB (authoritative, survives restarts) and in-process Prometheus counters.
>
> **Only install Prometheus + Grafana if you have a dedicated monitoring VM** and need time-series history spanning multiple agent restarts, or alertmanager-based paging. Running them on the same server as the agent adds RAM pressure and disk growth with no meaningful observability gain over the built-in page.

On a **separate dedicated monitoring VM**:

```bash
sudo bash scripts/install-prometheus.sh   # Prometheus on :9091 — scrapes agent :9090/metrics
sudo bash scripts/install-grafana.sh      # Grafana on :3000 with pre-built dashboard
```

**Point Prometheus at the agent** — edit `/etc/prometheus/prometheus.yml` on the monitoring VM:
```yaml
scrape_configs:
  - job_name: errander-agent
    static_configs:
      - targets: ['<agent-vm-ip>:9090']
```

**Access via SSH tunnel** (no firewall rules needed):
```bash
ssh -L 9091:localhost:9091 -L 3000:localhost:3000 <user>@<monitoring-vm-ip>
```

```bash
# Override defaults if needed:
PROM_PORT=9091 AGENT_METRICS_PORT=9090 PROM_VERSION=2.53.2 bash scripts/install-prometheus.sh
GF_PORT=3000 bash scripts/install-grafana.sh
```

### LangSmith tracing *(optional — Layer A only, dev/staging)*

> **Skip for production.** LangSmith sends Layer-A prompt contents off-network. The built-in `/ui/ai-decisions` log is always-on and in-network — use that in prod.

Add three env vars to `.env`:

```bash
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_...
LANGCHAIN_PROJECT=errander-ai
```

Sign up at [smith.langchain.com](https://smith.langchain.com), create a project, copy your API key. LangGraph auto-detects `LANGCHAIN_TRACING_V2=true` at startup — no code changes needed.

### ELK / Elasticsearch log aggregation *(optional)*

> **Skip if you don't use ELK.** Without it the agent reads `journalctl` directly from each VM via SSH.

```bash
# Check if auth is required:
curl http://<elasticsearch-host>:9200/_cluster/health
# Returns JSON → no auth. Returns 401 → create an API key (see below).
```

**Create a read-only API key** (if auth is required):

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

Copy the `encoded` field. Then add to `.env`:

```
ERRANDER_ELK_BASE_URL=http://<elasticsearch-host>:9200
ERRANDER_ELK_API_KEY=<encoded value>   # omit entirely if no auth required
ERRANDER_ELK_INDEX_PATTERN=filebeat-*,logstash-*
```

Per-environment override in `inventory.yaml`:

```yaml
environments:
  production:
    elk_url: http://10.0.1.101:9200
    elk_index_pattern: prod-logs-*
  staging:
    # no elk_* → uses global .env values
```

---

## Appendix C: Manual installation

> **Normally you do not need this.** `bootstrap.sh` handles everything in Step 1A automatically. Use these commands only if `bootstrap.sh` fails or you need to inspect each step individually.

```bash
# As admin: install system deps and create service user
sudo apt-get update && sudo apt-get install -y git curl   # Ubuntu/Debian
# sudo dnf install -y git curl                            # RHEL/CentOS/Oracle

# Install uv (official installer — no PPA, works on all distros)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
sudo cp ~/.local/bin/uv /usr/local/bin/uv

# Install Python 3.12 via uv
uv python install 3.12

# Create service user and .ssh directory
sudo useradd -m -s /bin/bash errander-agent
sudo mkdir -p /home/errander-agent/.ssh
sudo chmod 700 /home/errander-agent/.ssh
sudo chown -R errander-agent:errander-agent /home/errander-agent/.ssh

# Clone repo as service user
sudo -u errander-agent git clone https://github.com/psc0des/Errander-AI.git /home/errander-agent/errander

# As errander-agent: install deps and verify
sudo su - errander-agent
cd ~/errander
uv sync --extra dev
uv run python -c "import errander; print('OK')"
```

Once these commands succeed, continue from **Step 2** — all remaining steps are identical to the `bootstrap.sh` path.
