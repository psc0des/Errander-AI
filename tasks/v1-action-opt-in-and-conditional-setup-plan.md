# v1 — Action Opt-In + Conditional Setup Plan

**Owner:** Sonnet (implementation), Opus (architecture/review)
**Status:** Approved by SRE, ready for implementation
**Scope freeze:** This is the last architectural change before v1 ship.

**Phase 1 (Tier 1 + Tier 2):** setup ergonomics, config schema, preflight — no new infrastructure actions.
**Phase 2 (Service Restart):** adds the 6th built-in action (`service_restart`), HIGH risk tier, operator-triggered with mandatory Slack approval. Closes the credibility gap of "Errander tells me the service is down but cannot restart it." Phase 2 depends on Phase 1's manifest + registry + schema landing first.

---

## Context

The current SETUP.md funnels every user through Docker hardening even when their fleet has no Docker. The deeper issue: setup, preflight, and inventory all conflate "things Errander knows how to do" with "things this particular environment uses." This plan introduces per-action opt-in (`actions.<name>.enabled` per env) backed by a lightweight per-sub-graph manifest, generalizing the existing `docker_command_mode` pattern (Phase A, 2026-05-15) to all actions.

**Reference:** SRE decision thread in conversation history (Q1–Q9 + final approvals). All decisions below are locked — do not re-litigate during implementation.

---

## Locked decisions (no re-litigation)

1. **Schema break + clean migrate.** New nested `actions:` block per env. Old flat `docker_command_mode` field is removed from runtime — its presence triggers a fail-fast error directing the operator to `--migrate-inventory`.
2. **Manifest with each sub-graph + central registry.** Each `agent/subgraphs/<action>.py` exports a `MANIFEST: ActionManifest`. A central `BUILTIN_ACTIONS` dict in `agent/subgraphs/__init__.py` imports each one explicitly. **No dynamic filesystem discovery** (no `pkgutil.iter_modules`). This is the explicit-allowlist guardrail vs. a plugin system.
3. **v1 scope = Linux host maintenance only.** No K8s, VictoriaMetrics, Tomcat, app runtimes. CLAUDE.md gets an explicit non-goals note.
4. **Failure taxonomy.**
   - Env-level config contradiction (e.g. `docker_prune.enabled: true` + `command_mode: disabled`) → **batch-fail at config-load time**, before SSH.
   - Per-VM prerequisite drift (e.g. missing wrapper on one node) → **VM-skip** + `TARGET_PREFLIGHT_FAILED` audit + Slack alert + batch continues + final batch status `PARTIAL_FAILED`.
   - Zero eligible VMs after preflight → **batch-fail** (never silently succeed with nothing done).
5. **Migration helper: full synthesis.** Reads old shape, writes `<file>.migrated` with every known action explicit using new defaults. Never overwrites original. Prints diff.
6. **Tier 1 + Tier 2 only.** Tier 3 (`--remediate-targets <env> --apply`) deferred to v1.1.

---

## Per-action defaults (locked)

| Action          | `enabled` default | `command_mode` default | Rationale |
|-----------------|-------------------|------------------------|-----------|
| `patching`      | `true`            | n/a                    | Headline feature; HITL approval + maintenance window still gate live execution. |
| `disk_cleanup`  | `true`            | n/a                    | Bounded by CLAUDE.md whitelist + age threshold; non-destructive outside whitelist. |
| `log_rotation`  | `true`            | n/a                    | logrotate compresses, does not delete payload data. |
| `docker_prune`  | `false`           | `disabled`             | Root-equivalent control plane; requires explicit operator opt-in + wrapper install. |
| `backup_verify` | `false`           | n/a                    | Requires explicit `backup:` config section to be useful; off when unconfigured. |

**Invariant:** `patching`/`disk_cleanup`/`log_rotation` defaulting on is only safe because their existing safety guards hold (HITL Slack approval, maintenance windows, whitelist + age bounds, non-destructive compress/rotate). This plan must not weaken any of those guards. If implementation reveals any guard is weaker than claimed, **stop and surface to user before continuing.** Do not silently build new config structure on top of weakened safety.

---

## Target schema

### New `example/inventory.yaml` shape

```yaml
environments:
  prod:
    vms:
      - id: prod-web-01
        host: 10.0.0.10
        user: errander
        ssh_key: ~/.ssh/errander_prod
        os_family: ubuntu
    actions:
      patching:
        enabled: true
      disk_cleanup:
        enabled: true
      log_rotation:
        enabled: true
      docker_prune:
        enabled: false
        command_mode: disabled
      backup_verify:
        enabled: false
```

### Migration error (fail-fast on legacy field at config-load)

```
Legacy inventory field 'docker_command_mode' detected at environments.prod
This field was removed in v1. Run:
  uv run python -m errander --migrate-inventory inventory.yaml
A new file inventory.yaml.migrated will be written for review.
```

---

## ActionManifest model

Lean — no over-specification. Lives at `errander/models/manifest.py`.

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class ActionManifest:
    name: str
    default_enabled: bool
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    command_modes: tuple[str, ...] | None        # None = action has no mode concept (patching, etc.)
    required_binaries: tuple[str, ...]           # absolute paths checked via `command -v`
    required_wrappers: tuple[str, ...]           # absolute paths to root-owned wrapper scripts (empty if none)
    setup_doc: str                                # anchor in SETUP.md, e.g. "SETUP.md#optional-docker-cleanup"
    requires_config_section: str | None = None   # name of settings.yaml block that must be present (e.g. "backup")
```

**Do not add:**
- `supported_os_families` — duplicates `target_validation.py` logic
- `required_sudoers` — sudoers entries are documented in SETUP.md, not enforced by manifest
- `version` / `last_updated` — premature

---

## Tier 1 — three commits

### Commit 1.1 — Manifest model + per-sub-graph manifests + central registry + schema reader

**Goal:** introduce the new schema and manifest plumbing. Runtime execution unchanged — only config-load and preflight-validation paths shift.

**Files:**
- **NEW** `errander/models/manifest.py` — `ActionManifest` dataclass (above)
- **NEW** export `MANIFEST` from each of:
  - `errander/agent/subgraphs/patching.py`
  - `errander/agent/subgraphs/disk_cleanup.py`
  - `errander/agent/subgraphs/log_rotation.py`
  - `errander/agent/subgraphs/docker_prune.py`
  - `errander/agent/subgraphs/backup_verify.py`
- **MODIFY** `errander/agent/subgraphs/__init__.py` — explicit `BUILTIN_ACTIONS: dict[str, ActionManifest]` importing each MANIFEST by name. No globbing.
- **MODIFY** `errander/config/schema.py` — accept new `actions:` block; reject legacy `docker_command_mode` flat field with the fail-fast error message above.
- **MODIFY** `errander/config/inventory.py` — surface `actions` config to downstream consumers via `InventoryConfig`.
- **MODIFY** `example/inventory.yaml` — switch to new nested shape (above). Annotated comments explain every default.
- **MODIFY** existing call sites that read `docker_command_mode` (vm_graph.py, graph.py, main.py, docker_prune.py preflight) → read from `env_config.actions["docker_prune"].command_mode` instead.

**Manifest values (locked — copy verbatim):**

```python
# patching.py
MANIFEST = ActionManifest(
    name="patching",
    default_enabled=True,
    risk_tier="MEDIUM",
    command_modes=None,
    required_binaries=("/usr/bin/apt-get", "/usr/bin/dnf", "/usr/bin/yum"),  # any-of (matched by os_family at preflight)
    required_wrappers=(),
    setup_doc="SETUP.md#step-3--target-vm-setup",
)

# disk_cleanup.py
MANIFEST = ActionManifest(
    name="disk_cleanup",
    default_enabled=True,
    risk_tier="LOW",
    command_modes=None,
    required_binaries=("/usr/bin/find", "/usr/bin/journalctl"),
    required_wrappers=(),
    setup_doc="SETUP.md#step-3--target-vm-setup",
)

# log_rotation.py
MANIFEST = ActionManifest(
    name="log_rotation",
    default_enabled=True,
    risk_tier="LOW",
    command_modes=None,
    required_binaries=("/usr/sbin/logrotate", "/usr/bin/gzip", "/usr/bin/truncate"),
    required_wrappers=(),
    setup_doc="SETUP.md#step-3--target-vm-setup",
)

# docker_prune.py
MANIFEST = ActionManifest(
    name="docker_prune",
    default_enabled=False,
    risk_tier="MEDIUM",
    command_modes=("disabled", "wrapper", "direct_sudo"),
    required_binaries=("/usr/bin/docker",),
    required_wrappers=(
        "/usr/local/sbin/errander-docker-assess",
        "/usr/local/sbin/errander-docker-prune-safe",
        "/usr/local/sbin/errander-docker-prune-aggressive",
    ),
    setup_doc="SETUP.md#optional-docker-cleanup",
)

# backup_verify.py
MANIFEST = ActionManifest(
    name="backup_verify",
    default_enabled=False,
    risk_tier="LOW",
    command_modes=None,
    required_binaries=("/usr/bin/stat",),
    required_wrappers=(),
    setup_doc="SETUP.md#optional-backup-verify",
    requires_config_section="backup",
)
```

**Tests (new):**
- `tests/models/test_manifest.py` — ActionManifest fields immutable; tuple types enforced.
- `tests/agent/subgraphs/test_registry.py` — `BUILTIN_ACTIONS` has exactly 5 entries with expected names; `BUILTIN_ACTIONS["docker_prune"].default_enabled is False`; `BUILTIN_ACTIONS["patching"].default_enabled is True`.
- `tests/config/test_schema_actions.py` — accepts new nested shape; rejects legacy `docker_command_mode` with the canonical error message; missing `actions:` block applies defaults from `BUILTIN_ACTIONS`; env-level contradiction (`enabled: true` + `command_mode: disabled` for docker_prune) raises `ConfigError` at load time.
- `tests/config/test_schema_actions.py` — manifest-derived defaults apply when a known action is absent from inventory.
- Existing `tests/agent/subgraphs/test_docker_prune_modes.py` — update reads to new schema path; behavior unchanged.

**Acceptance:**
- All 5 sub-graphs expose `MANIFEST`.
- `BUILTIN_ACTIONS` is a plain dict, not lazy-loaded.
- Runtime SSH/execute behavior unchanged from before this commit (verify by running the full existing test suite minus mechanical schema-rename touch-ups).

---

### Commit 1.2 — Migration helper + `--migrate-inventory` CLI

**Goal:** give the operator a one-command upgrade path from the old flat schema to the new nested schema.

**Files:**
- **NEW** `errander/config/migrate.py`:
  - `migrate_inventory(path: Path) -> Path` — reads old YAML, returns path to `<file>.migrated`.
  - Full synthesis: every env gets a complete `actions:` block with **all 5 known actions** explicit, defaults from `BUILTIN_ACTIONS`.
  - Old `docker_command_mode: X` translates to `actions.docker_prune.command_mode: X` and overrides `default_enabled=False` to `enabled: true` when mode is `wrapper` or `direct_sudo` (operator-intended Docker work shouldn't silently disappear).
  - Old `docker_command_mode: disabled` → `actions.docker_prune.enabled: false, command_mode: disabled`.
  - Preserves all other inventory fields verbatim (vms, ssh, policy, schedule, etc.).
  - Comment preservation: best-effort via ruamel.yaml; correctness over comment fidelity.
  - Refuses to overwrite if `<file>.migrated` already exists (operator must delete first).
  - Prints unified diff to stdout.
- **MODIFY** `errander/main.py` — add `--migrate-inventory <path>` CLI handler; exits 0 on success, 1 on error.

**Tests (new):**
- `tests/config/test_migrate.py`:
  - Migrates `docker_command_mode: wrapper` → `actions.docker_prune.enabled: true, command_mode: wrapper`.
  - Migrates `docker_command_mode: disabled` → `actions.docker_prune.enabled: false, command_mode: disabled`.
  - Synthesizes full `actions:` block including the 4 non-Docker actions with their defaults.
  - Preserves `vms`, `policy`, `schedule`, and other unrelated fields verbatim.
  - Writes `<path>.migrated`, never touches original.
  - Refuses when `.migrated` already exists.
  - Idempotent on already-new-schema files (no-op + diff is empty).
  - Diff output contains both the removal line and the additions.
- `tests/test_main.py` — `--migrate-inventory <path>` exits 0; missing path exits 1.

**Acceptance:**
- An operator with the existing `example/inventory.yaml` (pre-this-plan) can run `uv run python -m errander --migrate-inventory inventory.yaml` and get a working v1 file without manual edits.

---

### Commit 1.3 — `--check-targets` registry-driven + SETUP.md restructure + CLAUDE.md scope note

**Goal:** make preflight only validate what the operator has actually enabled, and clean up the setup waterfall so Docker is conditional.

**Files:**
- **MODIFY** `errander/main.py` (`--check-targets <env>`) — iterate `BUILTIN_ACTIONS`, filter to actions where `env.actions[name].enabled is True`, validate only those (required binaries + required wrappers per manifest). Skip disabled actions silently. Warn-on-missing-prerequisite for enabled actions (do not fail this readiness check).
- **MODIFY** `errander/agent/sudo_preflight.py` (or wherever `REQUIRED_BINARIES_BY_ACTION` lives) — replace hardcoded table with `BUILTIN_ACTIONS[action].required_binaries`. Same for wrappers.
- **MODIFY** runtime preflight (vm_graph) — when an enabled action's wrapper is missing on a target VM, emit `TARGET_PREFLIGHT_FAILED` for that VM/action, skip the VM, set batch terminal status to `PARTIAL_FAILED` if any VM was skipped. Do not batch-fail. (Config contradictions are already caught at config-load in 1.1.)
- **MODIFY** `errander/models/reports.py` — add `BatchStatus.PARTIAL_FAILED` enum value if not already present; ensure CLI exit code and Slack final report reflect it.
- **MODIFY** `SETUP.md`:
  - Restructure into clearly labeled sections: **Part 1 — Controller setup** (always), **Part 2 — Target VM base setup** (always, OS-maintenance sudoers only), **Part 3 — Optional modules** (Docker cleanup, backup verify — each clearly marked optional with "skip this entire section unless..." callout).
  - Move current "Docker hardening" section to `## Optional: Docker cleanup` with the decision-tree callout at the top:
    ```
    > **Skip this entire section if you are not enabling docker_prune.**
    > In your inventory.yaml, leave `actions.docker_prune.enabled: false` and continue to Step 4.
    ```
  - Update all section anchors to match manifest `setup_doc` URLs (anchors are checked at preflight via `BUILTIN_ACTIONS[...].setup_doc`).
- **MODIFY** `CLAUDE.md` — add explicit "## v1 Scope" subsection under Domain Rules:
  ```
  v1 supports Linux host maintenance only: OS patching (non-kernel), disk cleanup,
  log rotation, Docker prune, backup verification. Kubernetes, app runtimes
  (Tomcat/Nginx/Java GC), database management, network/firewall changes, and
  arbitrary user-supplied commands are explicitly out of scope. Adding new
  actions requires a new sub-graph + manifest + risk-tier classification +
  rollback strategy — not a config flag.
  ```
- **MODIFY** `README.md` — capability matrix paragraph reflecting what is opt-in vs default.

**Tests (new):**
- `tests/test_main.py` — `--check-targets` skips disabled actions (no binary check for docker when disabled).
- `tests/test_main.py` — `--check-targets` warns (not fails) when enabled action lacks binary on a target.
- `tests/agent/test_vm_graph.py` — runtime missing-wrapper for enabled docker_prune emits `TARGET_PREFLIGHT_FAILED`, skips that VM, other VMs continue, batch terminal status is `PARTIAL_FAILED`.
- `tests/agent/test_vm_graph.py` — all VMs failing preflight → batch-fail (not silent success).
- Existing `tests/agent/test_sudo_preflight.py` — update to read from registry rather than hardcoded table.

**Acceptance:**
- A new user reading SETUP.md who is not enabling Docker can finish setup without reading or running any Docker-related step.
- `uv run python -m errander --check-targets dev` against a dev env with `docker_prune.enabled: false` produces zero Docker-related output.
- `BatchStatus.PARTIAL_FAILED` is visible in CLI exit code (non-zero, distinct from `FAILED`), Slack final report, and `--audit --batches` listing.

---

## Tier 2 — one commit

### Commit 2.1 — `scripts/install-docker-wrappers.sh` + SETUP.md Docker section collapse

**Goal:** remove the ~90-line heredoc copy-paste from SETUP.md so wrapper installation is one command, and ensure wrapper output format stays in lockstep with `parse_assess_output()` via a drift test.

**Files:**
- **NEW** `scripts/install-docker-wrappers.sh`:
  - Runs as root (`#!/bin/bash`, `set -euo pipefail`, `[[ $EUID -eq 0 ]] || exit 1`).
  - Writes the three wrapper scripts (assess, prune-safe, prune-aggressive) using heredocs.
  - Sets `chmod 755` and `chown root:root` on each.
  - Writes `/etc/sudoers.d/errander-docker` with `chmod 440`.
  - Validates with `visudo -c -f /etc/sudoers.d/errander-docker`; aborts and removes the file on failure.
  - Idempotent — re-running overwrites cleanly without error.
  - Final message: "Wrapper install complete. Run `uv run python -m errander --check-targets <env>` from the controller to verify."
- **MODIFY** `SETUP.md` — the entire "Wrapper scripts" subsection (lines ~326-419) collapses to:
  ```
  Copy the install script to the target and run it as root:

  scp scripts/install-docker-wrappers.sh errander@<target>:/tmp/
  ssh errander@<target> "sudo bash /tmp/install-docker-wrappers.sh"

  Then verify from the controller:

  uv run python -m errander --check-targets <env>
  ```
- **NEW** `tests/scripts/test_install_docker_wrappers.py`:
  - Extract each wrapper's body from the install script (regex on the heredoc markers).
  - For the assess wrapper: mock `/usr/bin/docker` outputs, run the extracted bash in a subprocess, pipe stdout through `parse_assess_output()`, assert it parses without error and returns expected fields.
  - For prune-safe / prune-aggressive: assert each invokes only the documented docker subcommands (image prune, container prune, system prune -af).
  - Single drift test ensures the wrapper output contract and the parser stay aligned. If the parser changes, this test breaks.

**Acceptance:**
- SETUP.md "Optional: Docker cleanup" section is under 30 lines.
- New target VM can be set up for Docker cleanup with one scp + one ssh, no copy-pasted heredocs.
- Editing the install script's assess output format without updating the parser breaks `test_install_docker_wrappers.py`.

---

## Phase 2 — Service Restart Module

**Goal:** add `service_restart` as the 6th built-in action. Operator-triggered (no auto-detection in v1), HIGH risk tier, always requires Slack approval, executes only against a per-env allowlist of restartable units, captures pre/post restart context for the audit trail.

**Depends on:** all of Phase 1 (Tier 1.1 → 1.3 + Tier 2.1) landing first. This module uses the new manifest model, registry, nested schema, and wrapper install-script pattern.

### Trigger model — operator-triggered only in v1 (locked)

Failed-service detection already exists in the daily probe digest — when a unit in `systemctl --failed` is detected, the digest escalates it to Slack. v1 does **not** auto-propose a restart from probe output. Reason: distinguishing transient from persistent failures (deploy churn, dependency cascades, GC pauses) requires trigger logic that is easy to get wrong; a wrong auto-restart during a deploy can cause cascade damage. Operator-triggered keeps a human in the loop on every restart.

**v1 flow:**
1. Operator sees failed unit in Slack digest.
2. Operator runs `uv run python -m errander --restart-service <env> --unit <name> --vm <vm-id>` (or `--vms vm-1,vm-2` for multiple).
3. Errander constructs a one-action batch through the existing graph.
4. Plan posted to `#errander-approvals` with pre-restart context snapshot (status, recent journal lines).
5. Operator approves ✅ in Slack within the configured timeout (default 30 min).
6. Restart wrapper executes; post-restart verification runs.
7. Report posted with success/failure and post-restart context.

**v1.1 deferred:** detect-and-propose mode — daily probe queues a restart proposal for failed allowlisted units, operator approves to execute. Deferred until v1 trigger logic has been battle-tested.

### Privilege model — wrapper + on-target allowlist (locked)

Same pattern as Docker: one root-owned wrapper script + one sudoers entry. The wrapper consults a plain-text allowlist file installed alongside it. Two layers of "is this unit allowed?":
1. **Inventory-side:** `actions.service_restart.restartable_units: [...]` per env (operator declares intent in `inventory.yaml`).
2. **Target-side:** `/etc/errander/restart-allowlist` on each VM (root-owned, written by the install script from operator-supplied arguments).

`--check-targets <env>` validates these two stay in sync per VM. Drift → warning at check time, fail-closed at run time (per Phase 1 failure taxonomy: per-VM drift = VM-skip + `PARTIAL_FAILED`).

### Commits (S.1 → S.4, strict order)

#### Commit S.1 — Sub-graph + manifest + state model + audit events

**Files:**
- **NEW** `errander/agent/subgraphs/service_restart.py` — full sub-graph: `validate_node` → `snapshot_node` → `execute_node` → `verify_node`; `MANIFEST` export; `parse_restart_output(stdout) -> RestartContext` helper.
- **NEW** `errander/models/service_restart.py` — `RestartContext` dataclass (pre_status, pre_journal, post_active, post_status, post_journal); `ServiceRestartState` LangGraph state.
- **MODIFY** `errander/agent/subgraphs/__init__.py` — add `service_restart` MANIFEST to `BUILTIN_ACTIONS`.
- **MODIFY** `errander/models/events.py` — add audit event types:
  - `SERVICE_RESTART_REQUESTED` (CLI invocation logged)
  - `SERVICE_RESTART_UNIT_NOT_ALLOWED` (inventory-side allowlist rejection)
  - `SERVICE_RESTART_APPROVED` / `SERVICE_RESTART_REJECTED` (Slack outcome)
  - `SERVICE_RESTART_EXECUTED`
  - `SERVICE_RESTART_VERIFY_OK` / `SERVICE_RESTART_VERIFY_FAILED`

**Manifest (verbatim):**

```python
MANIFEST = ActionManifest(
    name="service_restart",
    default_enabled=False,
    risk_tier="HIGH",
    command_modes=None,
    required_binaries=("/bin/systemctl", "/bin/journalctl"),
    required_wrappers=("/usr/local/sbin/errander-systemctl-restart",),
    setup_doc="SETUP.md#optional-service-restart",
)
```

**Sub-graph rules:**
- `validate_node`: fail closed if requested unit is not in env's `restartable_units` (emit `SERVICE_RESTART_UNIT_NOT_ALLOWED`); fail closed if `sudo -n /usr/local/sbin/errander-systemctl-restart --check` returns non-zero.
- `snapshot_node`: invoke wrapper without arguments to capture `pre_status` and `pre_journal` only — DO NOT execute restart in snapshot phase. (See wrapper design below: `--snapshot-only` mode.)
- `execute_node`: invoke wrapper with unit name; capture full pre/post output; parse via `parse_restart_output`.
- `verify_node`: post-restart `systemctl is-active` must report `active`. If not, emit `SERVICE_RESTART_VERIFY_FAILED` and trigger Slack escalation. **No automatic re-restart attempt** — humans take it from there. A failed restart is a paging event, not a retry loop.

**Tests (new):**
- `tests/agent/subgraphs/test_service_restart.py` — validate/snapshot/execute/verify happy path with mocked SSH; verify fails on `is-active=inactive`; inventory-allowlist rejection emits correct event.
- `tests/agent/subgraphs/test_service_restart_manifest.py` — manifest fields, registry registration, risk_tier=HIGH.
- `tests/agent/subgraphs/test_service_restart_parser.py` — `parse_restart_output` correctly extracts all 5 sections from sample wrapper output; handles malformed output gracefully.

#### Commit S.2 — Wrapper script + sudoers + install script + drift test

**Files:**
- **NEW** `scripts/install-systemctl-restart-wrapper.sh` — installs `/usr/local/sbin/errander-systemctl-restart`, writes `/etc/errander/restart-allowlist` from script arguments, writes `/etc/sudoers.d/errander-systemctl-restart`, validates with `visudo -c`. Usage: `sudo bash install-systemctl-restart-wrapper.sh nginx gunicorn redis-server`. Re-runnable — overwrites allowlist cleanly.
- **NEW** `tests/scripts/test_install_systemctl_restart_wrapper.py` — extract wrapper body from install script, run against mocked systemctl, pipe output through `parse_restart_output()`, assert it parses; assert allowlist enforcement (unit not in allowlist → exit code 4).

**Wrapper script (verbatim, written by install script to `/usr/local/sbin/errander-systemctl-restart`):**

```bash
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

if [ "${1:-}" = "--snapshot-only" ]; then
    UNIT="${2:-}"
    if [ -z "$UNIT" ]; then
        echo "ERROR: no unit specified" >&2
        exit 2
    fi
    echo "pre_status_begin"
    /bin/systemctl status "$UNIT" --no-pager 2>&1 || true
    echo "pre_status_end"
    echo "pre_journal_begin"
    /bin/journalctl -u "$UNIT" --since "5 minutes ago" --no-pager 2>&1 || true
    echo "pre_journal_end"
    exit 0
fi

UNIT="${1:-}"
if [ -z "$UNIT" ]; then
    echo "ERROR: no unit specified" >&2
    exit 2
fi

ALLOWLIST="/etc/errander/restart-allowlist"
if [ ! -r "$ALLOWLIST" ]; then
    echo "ERROR: allowlist $ALLOWLIST not readable" >&2
    exit 3
fi

if ! grep -qFx "$UNIT" "$ALLOWLIST"; then
    echo "ERROR: unit '$UNIT' not in allowlist" >&2
    exit 4
fi

echo "pre_status_begin"
/bin/systemctl status "$UNIT" --no-pager 2>&1 || true
echo "pre_status_end"

echo "pre_journal_begin"
/bin/journalctl -u "$UNIT" --since "5 minutes ago" --no-pager 2>&1 || true
echo "pre_journal_end"

/bin/systemctl restart "$UNIT"

sleep 2

echo "post_active_begin"
/bin/systemctl is-active "$UNIT" 2>&1 || true
echo "post_active_end"

echo "post_status_begin"
/bin/systemctl status "$UNIT" --no-pager 2>&1 || true
echo "post_status_end"

echo "post_journal_begin"
/bin/journalctl -u "$UNIT" --since "10 seconds ago" --no-pager 2>&1 || true
echo "post_journal_end"
```

**Sudoers entry (written by install script):**
```
errander ALL=(root) NOPASSWD: /usr/local/sbin/errander-systemctl-restart
```

**Allowlist file (`/etc/errander/restart-allowlist`):** one unit name per line, no comments, mode 644 (world-readable so the wrapper running as root can read; sensitive contents = none).

#### Commit S.3 — CLI + schema validation + check-targets wiring + approval test

**Files:**
- **MODIFY** `errander/main.py` — add flags:
  - `--restart-service <env>` (required)
  - `--unit <name>` (required)
  - `--vm <vm-id>` OR `--vms <comma,separated,ids>` (one of, required)
  - Optional: `--dry-run` (produces plan + posts to Slack but does not execute on approval)
  - Constructs one-action batch through existing graph; hooks into existing approval flow.
- **MODIFY** `errander/config/schema.py` — when `service_restart.enabled: true`, require non-empty `restartable_units: list[str]` in the same block. Fail-fast config error with the message:
  ```
  service_restart.enabled is true for environment <env>, but restartable_units is empty.
  Add restartable_units: [unit1, unit2, ...] under actions.service_restart, or set enabled: false.
  ```
- **MODIFY** `errander/main.py` (`--check-targets`) — for envs with `service_restart.enabled: true`:
  - Verify `/usr/local/sbin/errander-systemctl-restart` exists on each VM (per manifest `required_wrappers`).
  - SSH read `/etc/errander/restart-allowlist`, compare to inventory `restartable_units`. Warn on drift in either direction (units in inventory but missing from on-target file; units on target file but not in inventory).
- **CONFIRM** `errander/safety/approval.py` — HIGH tier already forces Slack approval per existing logic. No code changes; add explicit test that `service_restart` cannot be auto-approved under any policy tier (relaxed/moderate/strict).

**Schema extension (added to example/inventory.yaml in S.4):**

```yaml
environments:
  prod:
    actions:
      service_restart:
        enabled: true
        restartable_units:
          - nginx
          - gunicorn
          - redis-server
```

**Tests:**
- `tests/test_main.py` — `--restart-service` happy path (dry-run); rejects when env unknown; rejects when unit unknown; rejects when neither `--vm` nor `--vms` provided; allowlist mismatch surfaces clearly.
- `tests/config/test_schema_actions.py` — `service_restart.enabled: true` with empty `restartable_units` fails at config-load with the canonical error.
- `tests/agent/test_approval.py` — `service_restart` action always routes through Slack approval regardless of policy tier (strict/moderate/relaxed); cannot be auto-approved even if some future change to policy code tried.
- `tests/test_main.py` — `--check-targets` reports allowlist drift between inventory `restartable_units` and on-target `/etc/errander/restart-allowlist`.

#### Commit S.4 — SETUP.md + CLAUDE.md + README + docs/learning

**Files:**
- **MODIFY** `SETUP.md` — new section `## Optional: Service restart` after the Docker section. Same shape: decision-tree callout ("skip unless enabling service_restart"), one-line install command (`scp ... && ssh ... "sudo bash /tmp/install-systemctl-restart-wrapper.sh <units...>"`), explanation of inventory vs on-target allowlist, verification with `--check-targets`.
- **MODIFY** `CLAUDE.md` —
  - Update v1 scope note: 6 actions instead of 5.
  - Update risk-tier table: service restart now listed as HIGH with "Human approval required" — actually built, not aspirational.
  - Add explicit note: "Service restart is operator-triggered only in v1. Auto-detection from probe output is v1.1."
- **MODIFY** `README.md` —
  - Capability matrix: `service_restart` now ✅ (opt-in, HITL required, operator-triggered).
  - Add new CLI examples block showing `--restart-service`.
- **MODIFY** `example/inventory.yaml` — add `service_restart` block (disabled by default, with commented-out `restartable_units` example).
- **NEW** `docs/learning/XX-service-restart-module.md` — design walkthrough: sub-graph node responsibilities, why operator-triggered vs detect-and-propose, two-layer allowlist rationale, parser drift test.

### Per-action defaults table — full v1 picture (replaces Phase 1 table)

| Action          | `enabled` default | `command_mode` default | Required inventory fields when enabled | Risk tier |
|-----------------|-------------------|------------------------|----------------------------------------|-----------|
| `patching`      | `true`            | n/a                    | —                                      | MEDIUM    |
| `disk_cleanup`  | `true`            | n/a                    | —                                      | LOW       |
| `log_rotation`  | `true`            | n/a                    | —                                      | LOW       |
| `docker_prune`  | `false`           | `disabled`             | —                                      | MEDIUM    |
| `backup_verify` | `false`           | n/a                    | `backup` section in settings.yaml      | LOW       |
| `service_restart` | `false`         | n/a                    | `restartable_units: list[str]`         | **HIGH**  |

### Phase 2 acceptance criteria

- [ ] `uv run python -m errander --restart-service production --unit nginx --vm prod-web-01 --dry-run` produces a plan with pre-restart context
- [ ] Live run requires Slack ✅ regardless of policy tier (relaxed/moderate/strict)
- [ ] Inventory-side allowlist enforcement: requesting a unit not in `restartable_units` rejected at CLI parse time with clear error
- [ ] On-target allowlist enforcement: wrapper rejects units not in `/etc/errander/restart-allowlist` (exit code 4)
- [ ] `--check-targets <env>` reports allowlist drift in either direction
- [ ] Post-restart verify failure emits `SERVICE_RESTART_VERIFY_FAILED` and Slack escalation; **no automatic re-restart attempt**
- [ ] Audit trail contains the 6 new event types in chronological order
- [ ] One operator command can install the wrapper + allowlist on a new target VM (single scp + single ssh)

---

## Runtime behavior changes (summary table)

| Trigger | Today | After this plan |
|---|---|---|
| Config has legacy `docker_command_mode: wrapper` | Works | **Fail at config-load** with migration instructions |
| Config has `docker_prune.enabled: true` + `command_mode: disabled` | n/a | **Fail at config-load** (contradiction) |
| Run-time: VM is missing required wrapper for enabled action | Whole batch fails or unclear | **VM-skip** + `TARGET_PREFLIGHT_FAILED` + batch continues + `PARTIAL_FAILED` final status |
| Run-time: zero eligible VMs after preflight | Empty success | **Batch-fail** |
| `--check-targets` with `docker_prune.enabled: false` | Docker checks run anyway | Docker checks skipped |
| Wrappers installed but action disabled | n/a | **No warning** (harmless dead config) |
| `--restart-service <env> --unit <name>` (Phase 2) | Did not exist | One-action batch through approval; pre/post context captured; HIGH-tier approval always required |
| Post-restart verify fails (Phase 2) | n/a | `SERVICE_RESTART_VERIFY_FAILED` + Slack escalation; no auto-retry |
| Inventory ↔ on-target allowlist drift (Phase 2) | n/a | Warn at `--check-targets`; fail-closed (VM-skip + `PARTIAL_FAILED`) at run time |

---

## What this plan will NOT touch

- Live execution invariants (HITL approval gate, maintenance windows, autonomous mode gate `autonomous_live_apply_enabled = False`, disk cleanup whitelist, log rotation safety)
- Action sub-graph internals (validate → snapshot → execute → verify → rollback flow per action)
- Audit DB schema or event types beyond adding `TARGET_PREFLIGHT_FAILED` if not present and `BatchStatus.PARTIAL_FAILED` if not present
- Web UI (no settings exposed for `actions:` block in v1; operator edits inventory.yaml directly)
- LLM / Operator Assistant (Layer A) code
- Prometheus / ELK adapters
- Slack approval polling code

---

## Test budget

- Current: 1707 passing, 0 skipped, 0 regressions.
- Target after Phase 1: ~1750 passing.
- Target after Phase 2: ~1820 passing.
- New tests by commit:
  - Phase 1: 1.1 ≈ +12, 1.2 ≈ +10, 1.3 ≈ +10, 2.1 ≈ +8
  - Phase 2: S.1 ≈ +20, S.2 ≈ +10, S.3 ≈ +25, S.4 ≈ +5
- Mechanical updates to existing tests (schema rename in fixtures, registry adoption): ~30 tests touched; behavior unchanged.

---

## Acceptance criteria (whole plan)

- [ ] `uv run pytest` — green, ~1820 tests, 0 regressions
- [ ] `uv run ruff check .` — clean
- [ ] `uv run mypy .` — strict clean (zero new errors)
- [ ] `uv run python -m errander --migrate-inventory <pre-v1-inventory>.yaml` produces a working v1 file
- [ ] `uv run python -m errander --check-targets <env>` with `docker_prune.enabled: false` produces zero Docker output
- [ ] `uv run python -m errander --restart-service <env> --unit <name> --vm <vm-id>` flows through Slack approval and produces audit trail
- [ ] SETUP.md base-setup path (Part 1 + Part 2) reads cleanly without any Docker or service-restart mentions
- [ ] One commit per Tier 1 sub-section + one commit for Tier 2 + one commit per S.x sub-section (8 commits total)
- [ ] Each commit's message follows CLAUDE.md format: `type: short description (under 72 chars)`
- [ ] STATUS.md updated each session per CLAUDE.md Doc Sync Rule
- [ ] `docs/learning/XX-action-opt-in-and-manifests.md` created walking through Phase 1 design
- [ ] `docs/learning/XX-service-restart-module.md` created walking through Phase 2 design
- [ ] `tasks/lessons.md` updated with any surprises encountered
- [ ] `docs/command-log.md` updated with every shell command used

---

## Out of scope (deferred to v1.1 / v2)

- `--remediate-targets <env> --apply` — auto-SSH install of wrappers/sudoers (v1.1)
- Action packs / declarative action manifest YAML / plugin system (v2)
- Auto-detect-and-propose for service restart from probe digest (v1.1 — operator-triggered is v1; auto-propose is hardened later)
- K8s, VictoriaMetrics, Tomcat, app runtime management (v2+)
- Arbitrary user-supplied command actions (deliberately never — violates Layer B safety invariant)
- PostgreSQL audit store (v2)
- Valkey for VM locking (v2)
- HashiCorp Vault for secrets (v2)

---

## Implementation order (strict)

Do not interleave. Each commit must land green before starting the next.

**Phase 1:**
1. **Commit 1.1** (manifest + registry + nested schema reader + reject legacy)
2. **Commit 1.2** (migration helper + `--migrate-inventory` CLI)
3. **Commit 1.3** (check-targets registry-driven + SETUP.md restructure + CLAUDE.md scope note + README capability paragraph)
4. **Commit 2.1** (install-docker-wrappers.sh + SETUP.md Docker section collapse + drift test)

**Phase 2:**
5. **Commit S.1** (service_restart sub-graph + manifest + state model + audit events)
6. **Commit S.2** (wrapper script + install script + drift test)
7. **Commit S.3** (CLI `--restart-service` + schema validation + check-targets allowlist drift + approval test)
8. **Commit S.4** (SETUP.md service-restart section + CLAUDE.md update + README capability matrix + learning doc + example/inventory.yaml addition)

After S.4: tag v1 release candidate. No more changes before freeze unless a regression or invariant violation is discovered.
