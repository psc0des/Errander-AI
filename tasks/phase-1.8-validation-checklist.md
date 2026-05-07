# Phase 1.8 Validation Checklist

Goal: prove the agent works end-to-end against real iron. Cut scope to **disk cleanup + log rotation only** — no patching, no docker prune.

---

## Pre-flight

- [ ] Pick one Ubuntu LTS VM (target #1)
- [ ] Pick one RHEL-family VM (target #2) — defer if not available
- [ ] Pick the LLM path: cloud API (fastest) or self-hosted vLLM (16 GB VRAM)
- [ ] Slack app created, bot in `#errander-approvals`, channel ID copied
- [ ] `git pull` on the controller — confirm `8a7c65e` is the latest commit
- [ ] `uv sync --extra dev` succeeds
- [ ] `uv run pytest` — all green before touching real VMs

## Controller setup

- [ ] `.env` populated (`ERRANDER_LLM_BASE_URL`, `ERRANDER_LLM_MODEL`, `ERRANDER_LLM_API_KEY` if needed, `ERRANDER_SLACK_BOT_TOKEN`, `ERRANDER_SLACK_CHANNEL_ID`, `ERRANDER_AUDIT_DB_URL`)
- [ ] `uv run python -m errander --check-llm` → `Status: OK`
- [ ] `inventory.yaml` written with real VM IPs, **only disk cleanup + log rotation enabled**
- [ ] `settings.yaml` reviewed — confirm maintenance window covers test time (or use `--force`)

## Target VM prep (each VM)

- [ ] `errander` user created, key-based SSH only
- [ ] `/etc/sudoers.d/errander` allows: `apt-get`/`dnf`, `journalctl`, `find`, `df`
- [ ] `ssh -i ~/.ssh/errander_prod errander@<vm> "echo ok"` works from controller
- [ ] `sudo -u errander sudo /bin/df -h /` works on the VM

## Dry-run (Ubuntu first)

- [ ] `uv run python -m errander --run-now --env dev --inventory inventory.yaml --dry-run`
- [ ] Run completes without errors
- [ ] Slack approval message posted (reply ✅ or ❌)
- [ ] `[DRY-RUN]` lines visible for every shell command — nothing actually executed
- [ ] `uv run python -m errander --audit --batches` shows the batch
- [ ] `uv run python -m errander --audit --batch-id <id>` shows full event chain
- [ ] Copy `errander.sqlite` aside as evidence

## Live run (Ubuntu)

- [ ] Same command with `--live` instead of `--dry-run`
- [ ] Approve via Slack ✅
- [ ] Disk cleanup actually freed bytes (compare `df -h /` before/after)
- [ ] Log rotation produced rotated files (`ls -la /var/log/*.gz` or equivalent)
- [ ] No errors in audit trail
- [ ] VM is still healthy (`systemctl is-system-running`, can SSH, services up)

## Repeat on RHEL

- [ ] Dry-run passes
- [ ] Live run passes
- [ ] OS detection picked `dnf` not `apt-get`

## Sign-off evidence to capture

- [ ] `errander.sqlite` from dry-run + live run (rename per environment)
- [ ] Screenshot of Slack approval flow
- [ ] `df -h` before/after on at least one VM
- [ ] Note any surprises in `tasks/lessons.md`

## Known gaps to document (NOT fix in this pass)

- No approver allowlist — anyone in the channel can ✅
- No CSRF / rate limiting on Web UI — keep `ERRANDER_UI_USER`/`PASSWORD` set, don't expose externally
- LLM prompts not in audit DB
- Singleton enforcement is per-VM only — run exactly one agent process during validation
- Rollback for patching is untested — patching is out of scope this round, don't enable it

## Only after Ubuntu + RHEL pass

- [ ] Expand inventory to add docker prune
- [ ] Then patching, with one VM at a time and a known rollback target
- [ ] Then induced-failure tests (SSH drop mid-action, dpkg lock, disk full)
