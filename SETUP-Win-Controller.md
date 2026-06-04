# Errander-AI — Windows Controller Setup

Running the Errander-AI controller on a Windows PC or Windows VM.

> **Target VMs are always Linux.** This doc covers the *controller* only — the machine running the agent. Steps 2–3 (SSH keys, sudo permissions) and the optional sections (Docker hygiene, service restart wrappers) run on your Linux target VMs. Follow [SETUP.md](SETUP.md) for those.

---

## Step 1 — Install the agent

**Recommended — run the bootstrap script from PowerShell.**
Installs git (via winget), uv, and Python 3.12, clones the repo, and verifies the install. No admin rights required.

```powershell
git clone https://github.com/psc0des/Errander-AI.git errander
powershell -ExecutionPolicy Bypass -File errander\scripts\bootstrap.ps1
```

Once complete, skip to Step 2 in [SETUP.md](SETUP.md) — the script handles everything in Step 1.

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

---

## Steps 2–4 — SSH, target VMs, and LLM

These steps are identical on Windows. Follow them in [SETUP.md](SETUP.md):

- [Step 2 — Set up SSH keys](SETUP.md#step-2--set-up-ssh-keys) — run commands in **Git Bash** (installed with Git for Windows), not PowerShell
- [Step 3 — Configure target VMs](SETUP.md#step-3--configure-target-vms-sudo-permissions) — SSH into each Linux target VM
- [Step 4 — Set up an LLM](SETUP.md#step-4--set-up-an-llm)

> **Git Bash for SSH steps:** `ssh-keygen` works natively in Git Bash. Open Git Bash (Start → Git Bash) and follow the Step 2 commands as written.

---

## Step 5 — Configure the agent

### Create `.env`

Create a file named `.env` in the `errander\` directory using Notepad, VS Code, or any text editor:

```
# LLM — paste the values from whichever Step 4 option you chose
ERRANDER_LLM_BASE_URL=<base-url-from-step-4>
ERRANDER_LLM_MODEL=<model-from-step-4>
ERRANDER_LLM_API_KEY=<api-key-from-step-4>

ERRANDER_AUDIT_DB_URL=errander.sqlite

ERRANDER_INVENTORY_PATH=inventory.yaml

# Web UI auth — change all three before exposing to a network
ERRANDER_UI_USERNAME=admin
ERRANDER_UI_PASSWORD=changeme
ERRANDER_UI_SECRET=change-this-to-a-random-32-char-string

ERRANDER_UI_DATA_MODE=fixture

# Slack — optional
# ERRANDER_SLACK_BOT_TOKEN=xoxb-your-token-here
# ERRANDER_SLACK_CHANNEL_ID=C0123456789

# Signed-URL HMAC secret — required for docker_hygiene web approval (v1.1)
# ERRANDER_SIGNING_SECRET=paste-base64-output-here
# ERRANDER_WEB_BASE_URL=http://10.0.0.5:9090

ERRANDER_SSH_STRICT_HOST_KEYS=false
# ERRANDER_SSH_KNOWN_HOSTS=~/.ssh/errander_known_hosts

# LangSmith — optional, Layer A tracing only (dev/staging)
# Sends Layer-A prompt contents off-network — do NOT enable in no-egress prod.
# LANGCHAIN_TRACING_V2=true
# LANGCHAIN_API_KEY=lsv2_pt_...
# LANGCHAIN_PROJECT=errander-ai
```

> Never commit `.env` — it is already in `.gitignore`.

### Create your inventory

Same as Linux — follow [SETUP.md Step 5](SETUP.md#step-5--configure-the-agent) for inventory, Slack, LangSmith, and ELK setup.

> **SSH key path in inventory.yaml:** Use the Git Bash-style path (`~/.ssh/errander_prod`) or the Windows native path (`C:\Users\you\.ssh\errander_prod`) — both work.

> **configure.sh on Windows:** `configure.sh` runs in **Git Bash**. Open Git Bash and run `bash scripts/configure.sh` from inside the `errander/` folder. Alternatively, create `.env` and `inventory.yaml` manually as shown above.

### Monitoring (Prometheus + Grafana)

`scripts/install-prometheus.sh` and `scripts/install-grafana.sh` are Linux Bash scripts — they do not run natively on Windows.

The agent still exposes `/metrics` on port 9090 (cross-platform). To monitor a Windows controller, point an existing Prometheus instance at `http://<controller-ip>:9090/metrics`, then point Grafana at that Prometheus. The pre-built dashboard JSON is at `deploy/grafana/dashboards/errander.json` — import it manually into your Grafana instance.

If you want the full automated stack, the simplest path is to run Prometheus + Grafana on a small Linux VM (or WSL2) and scrape the Windows controller's `:9090` endpoint from there.

---

## Step 6 — Verify everything

The agent loads `.env` automatically at startup. In PowerShell, if you need env vars visible to the shell itself (not just inside `uv run`), load them first:

```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
```

Then run the same verify commands as Linux:

```powershell
uv run python -m errander --check-inventory
uv run python -m errander --check-llm
uv run python -m errander --bootstrap-known-hosts <your-env-name>
uv run python -m errander --check-targets <your-env-name>
```

See [SETUP.md Step 6](SETUP.md#step-6--verify-everything) for what each command checks.

---

## Step 7 — First run (dry-run)

Load env vars if needed (see Step 6 above), then:

```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
uv run python -m errander --run-now --env <your-env-name> --inventory inventory.yaml --dry-run --force --force-reason "initial dry-run validation"
```

> `--force` bypasses the maintenance window for this first validation run. Remove it once setup is confirmed.

---

## Step 8 — Live run

```powershell
Get-Content .env | Where-Object { $_ -notmatch "^#" -and $_ -ne "" } | ForEach-Object {
    $parts = $_ -split "=", 2
    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
}
uv run python -m errander --run-now --env <your-env-name> --inventory inventory.yaml --live
```

---

## Step 9 — Run as a scheduled service

Use **Task Scheduler** to run the agent as a background service.

1. Open **Task Scheduler** → **Create Basic Task**
2. Name: `Errander-AI`
3. Trigger: **At startup** (or a specific time)
4. Action: **Start a program**
   - Program: `C:\Users\<you>\errander\.venv\Scripts\python.exe`
   - Arguments: `-m errander --inventory C:\Users\<you>\errander\inventory.yaml --config C:\Users\<you>\errander\settings.yaml`
   - Start in: `C:\Users\<you>\errander`
5. Under **Properties** → **General** → check **Run whether user is logged on or not**
6. For env vars, create a wrapper `.bat` file that sets them, then point the task at the `.bat` instead:

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

**Access the web UI** once the service is running:
```
http://localhost:9090/ui
```

---

## Starting fresh

`scripts/teardown.sh` is Linux-only and does not apply to a Windows controller. To start fresh on Windows, delete the cloned folder and re-run Step 1:

```powershell
Remove-Item -Recurse -Force errander
git clone https://github.com/psc0des/Errander-AI.git errander
powershell -ExecutionPolicy Bypass -File errander\scripts\bootstrap.ps1
```

If you also want to remove uv and Python 3.12: uninstall Python from **Add or remove programs**, and delete `%USERPROFILE%\.local\bin\uv.exe` and `%USERPROFILE%\.local\share\uv\`.

---

## Troubleshooting

**SSH connection fails**
- Test in Git Bash: `ssh -i ~/.ssh/errander_prod errander@<vm-ip> "echo ok"`
- Confirm the public key is in `~errander/.ssh/authorized_keys` on the target
- Confirm key file permissions on `~/.ssh/errander_prod` are restricted to your user only (right-click → Properties → Security → remove Everyone/Users)

For all other issues see [SETUP.md Troubleshooting](SETUP.md#troubleshooting).
