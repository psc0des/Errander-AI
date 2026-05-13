# SRE Recommendations — Implementation Plan

**Audience:** Sonnet (implementer) — Opus has reviewed the SRE recommendations in `ai_sre_recommendations.md` and produced the architectural plan below. Execute phases in order. Each phase ships independently and leaves the agent in a working, testable state.

**Scope discipline:** This plan implements the 8 features Opus agreed with. Explicitly **out of scope** for this plan: numeric VM health score (Phase 3 item 1 in the SRE doc), auto-restart of services, auto-reboot. Do not add them.

**Existing infrastructure to reuse — do NOT duplicate:**
- `errander/execution/commands.py` — `PackageManager` ABC + `AptManager` / `DnfManager`. Extend, don't replace.
- `errander/execution/os_detection.py` — runtime OS detection. Reuse for any new probes.
- `errander/safety/validators.py` — pre-execution validation. Add new validators here; do not create a parallel pre-flight module.
- `errander/safety/drift.py` — **whole-VM-state** baseline (single dict, single hash). Leave this alone. The per-resource drift work below adds a NEW table and helper; the two coexist.
- `errander/safety/audit.py` — `AuditStore` + `AuditEvent` + `EventType`. All new signals must produce audit events.
- `errander/agent/subgraphs/patching.py` — extend, do not rewrite.

---

## Cross-cutting groundwork (do this FIRST — Phases 1 and 2 both depend on it)

### G1. New action / status enums

`errander/models/actions.py`

- Add `ActionStatus.BLOCKED = "blocked"` — used when a pre-flight gate refuses to run an action cleanly (not a failure, not a skip; an operator-visible "we deliberately did not run").
- Add new `EventType` values in `errander/models/events.py`:
  - `PREFLIGHT_LOCK_DETECTED`
  - `PREFLIGHT_LOCK_CLEAR`
  - `REBOOT_REQUIRED_DETECTED`
  - `SERVICE_HEALTH_REGRESSION`
  - `DISK_USAGE_CAPTURED`
  - `DRIFT_KIND_BASELINE_SAVED` (distinct from existing `DRIFT_BASELINE_SAVED`)
  - `DRIFT_KIND_CHANGED`
  - `FAILED_SSH_LOGINS_OBSERVED`

### G2. New `VMTarget` field: critical services

`errander/models/vm.py` — `VMTarget`:

```python
critical_services: tuple[str, ...] = ()
```

Use `tuple`, not `list`, because `VMTarget` is `frozen=True`. Update `errander/config/schema.py` (`TargetSchema`) and `errander/config/inventory.py` (`_resolve_single_target`) to read `critical_services: list[str]` from YAML; allow it at both environment-level and host-level with host overriding env. Update `example/inventory.yaml` with an annotated example block.

### G3. New `vm_state` table (lightweight, mutable per-VM facts)

This is for facts the agent computes per-run that are not append-only audit events — specifically `needs_reboot`. Add to the same SQLite DB as audit. **Design schema for PostgreSQL portability** (no SQLite-specific types).

```sql
CREATE TABLE IF NOT EXISTS vm_state (
  vm_id TEXT PRIMARY KEY,
  needs_reboot INTEGER NOT NULL DEFAULT 0,
  needs_reboot_reason TEXT,
  needs_reboot_pkgs TEXT,            -- newline-joined list (kept simple for v1)
  needs_reboot_detected_at TIMESTAMP,
  last_uptime_seconds REAL,
  updated_at TIMESTAMP NOT NULL
);
```

New module `errander/safety/vm_state.py` with `VMStateStore` class exposing:
- `async set_needs_reboot(vm_id, reason, pkgs)`
- `async clear_needs_reboot(vm_id)`
- `async get(vm_id) -> VMState | None`
- `async list_needs_reboot() -> list[VMState]`

### G4. Per-kind drift baseline table

This is the **single most important piece of plumbing** in the plan. Four Phase 2 features (sudoers, authorized_keys, listening_ports, cron timers) all share this table. Build it once; do not let each feature roll its own storage.

```sql
CREATE TABLE IF NOT EXISTS vm_baselines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vm_id TEXT NOT NULL,
  baseline_kind TEXT NOT NULL,        -- 'sudoers' | 'authorized_keys' | 'listening_ports' | 'cron'
  scope_key TEXT NOT NULL DEFAULT '', -- e.g., username for authorized_keys; '' otherwise
  captured_at TIMESTAMP NOT NULL,
  content_hash TEXT NOT NULL,         -- sha256 of canonicalized content
  content_blob TEXT NOT NULL,         -- canonicalized content (for diff rendering)
  metadata TEXT,                       -- JSON for extra structured info
  UNIQUE (vm_id, baseline_kind, scope_key, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_vm_baselines_lookup
  ON vm_baselines (vm_id, baseline_kind, scope_key, captured_at DESC);
```

New module `errander/safety/baselines.py`:

```python
@dataclass(frozen=True)
class BaselineCapture:
    kind: str
    scope_key: str
    content: str          # canonicalized
    metadata: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
class BaselineComparison:
    is_first_run: bool
    changed: bool
    previous: BaselineCapture | None
    current: BaselineCapture
    unified_diff: str     # rendered via difflib; '' if no change

class BaselineStore:
    async def latest(vm_id, kind, scope_key='') -> BaselineCapture | None: ...
    async def save(vm_id, capture: BaselineCapture) -> None: ...
    async def compare_and_save(vm_id, capture) -> BaselineComparison: ...
```

Use `difflib.unified_diff` for rendering. Retain last 30 captures per `(vm_id, kind, scope_key)`; truncate older rows in `BaselineStore.save`.

`DriftCheck` protocol — every Phase 2 drift feature implements this:

```python
class DriftCheck(Protocol):
    kind: str
    async def capture(self, ssh, vm) -> list[BaselineCapture]: ...
    # returns one BaselineCapture per scope_key (one per user for authorized_keys; one for sudoers; etc.)
```

### G5. `vm_disk_history` table

For Phase 1 disk growth trend:

```sql
CREATE TABLE IF NOT EXISTS vm_disk_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vm_id TEXT NOT NULL,
  captured_at TIMESTAMP NOT NULL,
  mountpoint TEXT NOT NULL,
  used_bytes INTEGER NOT NULL,
  total_bytes INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vm_disk_history_lookup
  ON vm_disk_history (vm_id, mountpoint, captured_at DESC);
```

90-day retention; cleanup job runs at end of each batch (cheap delete by `captured_at < now - 90d`).

### G6. Report model extension

`errander/models/events.py` (or wherever the report Pydantic model lives — locate it; the SRE doc and CLAUDE.md mention `observability/reporting.py`). Add structured fields the final report can render:

- `preflight_blocks: list[PreflightBlock]`
- `reboot_required: list[VMRebootStatus]`
- `service_health_regressions: list[ServiceRegression]`
- `disk_growth_alerts: list[DiskGrowth]`
- `drift_changes: list[DriftChange]`
- `failed_logins: list[FailedLoginSummary]`

Every field must default to empty so the report renders cleanly when features are disabled.

---

## Phase 1 — Operational Trust

Build order: **G1–G6 → 1.1 → 1.2 → 1.3 → 1.4.** Each feature ships its own PR.

### 1.1 Package manager lock detection (pre-flight)

**Why first:** stops the noisiest class of patch failure (apt/dpkg lock contention) and demonstrates the `ActionStatus.BLOCKED` flow end-to-end.

**Files:**
- `errander/execution/commands.py` — add to `PackageManager` ABC:
  ```python
  @abstractmethod
  def detect_lock(self) -> str: ...
  ```
  Implementations:
  - `AptManager.detect_lock()` — emit a shell snippet that prints holder process info for `/var/lib/dpkg/lock-frontend`, `/var/lib/apt/lists/lock`, `/var/lib/dpkg/lock` using `fuser -v` (preferred) or falling back to `lsof`. Return empty stdout = no lock.
  - `DnfManager.detect_lock()` — check `/var/run/dnf.pid` and `/var/run/yum.pid`; if present and PID is alive (`kill -0 $PID`), print `pid=<pid> cmd=<comm>`.
  Be defensive: lock files may or may not exist on a fresh box; `fuser` may not be installed everywhere. Wrap in `2>/dev/null || true` and parse what you get.
- `errander/safety/validators.py` — add `async def validate_no_pkg_lock(ssh, vm, pm) -> ValidationResult`. On lock present, return blocking result with structured holder info (pid, cmd if available).
- `errander/agent/subgraphs/patching.py` — call the validator in the existing pre-flight node. On block: emit `PREFLIGHT_LOCK_DETECTED` audit event, set `ActionResult.status = ActionStatus.BLOCKED`, populate `detail` with holder info, do NOT proceed to refresh/upgrade.

**Tests:**
- Unit: parse-fuser output → structured holder info; cover "no lock", "lock with named process", "lock with unknown process", "fuser unavailable".
- Subgraph integration: stub SSH to return locked output; assert subgraph reaches BLOCKED without invoking upgrade.

**Acceptance:** Run `--run-now --dry-run` against a sandbox VM with `apt-get update` held by a sleeping background process; agent reports BLOCKED with holder PID/command, no upgrade attempted, audit event present.

### 1.2 Reboot-required detection (post-patching)

**Files:**
- New module `errander/execution/reboot_check.py`:
  ```python
  @dataclass(frozen=True)
  class RebootStatus:
      needs_reboot: bool
      reason: str | None
      pkgs_requiring: tuple[str, ...]

  async def detect_reboot_required(ssh, vm) -> RebootStatus: ...
  ```
  - Debian/Ubuntu: `test -f /var/run/reboot-required && cat /var/run/reboot-required.pkgs 2>/dev/null || true`.
  - RHEL: `command -v needs-restarting >/dev/null && needs-restarting -r; echo "EXIT=$?"`. Exit 1 = needs reboot; 0 = no; treat absent binary as "unknown — skip silently" (don't break runs on minimal images).
- Integrate in `errander/agent/subgraphs/patching.py`: call after a successful upgrade (NOT after BLOCKED or FAILED). Persist via `VMStateStore.set_needs_reboot`. Emit `REBOOT_REQUIRED_DETECTED` audit event. **No auto-reboot.**
- `errander/observability/reporting.py`: surface `needs_reboot=True` VMs as a distinct section ("VMs awaiting reboot: …").

**Tests:**
- Unit: parser for each OS variant; absent-binary path on RHEL must not raise.
- Subgraph: after a stubbed successful upgrade, `VMStateStore.get(vm_id).needs_reboot == True` only when probe says so.

**Acceptance:** Two-VM sandbox run (one Debian needing reboot via touched flag file; one RHEL clean). Report lists Debian under reboot-required; RHEL absent from that list; no reboot is performed.

### 1.3 Critical service health checks

**Files:**
- New module `errander/safety/service_health.py`:
  ```python
  @dataclass(frozen=True)
  class ServiceStatus:
      name: str
      state: Literal['active', 'inactive', 'failed', 'unknown', 'not_present']

  async def check_services(ssh, services: tuple[str, ...]) -> list[ServiceStatus]: ...
  ```
  Implementation: single SSH round-trip — `for s in services; do echo "$s:$(systemctl is-active $s 2>/dev/null || echo unknown)"; done`. Map `unit not found` to `not_present` (operator config error — warn, do not fail).
- Wire into `errander/agent/vm_graph.py`:
  - Run `check_services` **before** any maintenance action (snapshot baseline state).
  - Run `check_services` **after** maintenance completes.
  - If a service was `active` before and is `inactive`/`failed` after: emit `SERVICE_HEALTH_REGRESSION` audit event, set VM result to `unhealthy_post_maintenance` (new field on the per-VM result aggregate — not a status on individual ActionResults).
  - If a service is `not_present`: surface as a non-failing warning in the report (operator mis-config, not a regression).

**Tests:**
- Unit: parse mixed `is-active` outputs (active, inactive, failed, "unit not found" stderr, missing binary). Don't shell out in unit tests; feed canned strings to the parser.
- VM graph integration: pre=active, post=failed → regression recorded; pre=active, post=active → no regression; pre=inactive, post=inactive → no regression (was already down — not our problem).

**Acceptance:** Sandbox VM with `nginx` declared in inventory `critical_services`. Stop nginx mid-run via the test harness; agent reports regression, batch result for that VM is flagged.

### 1.4 Disk growth trend

**Files:**
- Capture point: extend the existing discovery / pre-action probe in `errander/agent/vm_graph.py` to also run `df -P -B1 -x tmpfs -x devtmpfs` (POSIX format, bytes, skip transient FS). Parse to `[(mountpoint, used_bytes, total_bytes), …]`.
- Store via new `VMDiskHistoryStore` in `errander/safety/disk_history.py`. One row per `(vm_id, mountpoint, captured_at)` per run. Emit `DISK_USAGE_CAPTURED` audit event with the count of mountpoints (not the full payload — that lives in the table).
- Reporting: compute deltas vs the oldest capture in the trailing 7-day window. Surface only mountpoints where `(used_pct_now - used_pct_then) >= threshold` (config knob, default 10 percentage points). Show as `mountpoint: 62% → 78% over 6d22h`.
- Add retention: at end of each batch, prune `vm_disk_history` rows older than 90 days. Do this in a tiny dedicated helper, not inline in the subgraph.

**Tests:**
- Unit: parse `df -P -B1` output across both GNU and BusyBox variants (mock the BusyBox variant if you don't have one — RHEL-minimal images can surprise you).
- Reporting: store fake history rows, assert the trend renderer picks the correct delta and ignores rows outside the window.

**Acceptance:** Run 8 sequential dry-runs against a sandbox VM (or fake the `captured_at` timestamps); the eighth report shows a "Disk growth: /var grew from X% to Y% over Nd" line when delta exceeds threshold, no line otherwise.

---

## Phase 2 — Security drift signals

Build order: **G4 must exist → 2.0 (shared check runner) → 2.1 → 2.2 → 2.3 → 2.4 → 2.5.**

### 2.0 Drift orchestration node

Before any individual drift check, add the orchestration that runs them. This is the part that pays back the G4 investment.

**Files:**
- `errander/agent/vm_graph.py` — new node `run_drift_checks` that:
  1. Loads the configured list of enabled drift checks from settings (default = all four security ones, off by config flag per VM via tags or env).
  2. Invokes each `DriftCheck.capture(ssh, vm)` in sequence (single SSH connection reused).
  3. For each `BaselineCapture` returned: calls `BaselineStore.compare_and_save`.
  4. For first-run captures: emit `DRIFT_KIND_BASELINE_SAVED`, no report entry.
  5. For changed captures: emit `DRIFT_KIND_CHANGED` with the unified diff truncated to N lines (config; default 50), and append to `report.drift_changes`.
- Position this node so drift checks run regardless of whether any maintenance action ran. This makes the agent useful even on "passive scan" runs.

**Tests:**
- One fake `DriftCheck` implementation; assert orchestration handles first-run vs change-detected vs no-change correctly across multiple scopes.

### 2.1 SSH authorized_keys drift

**Files:** `errander/safety/drift_checks/authorized_keys.py`.

- Inventory addition: per-VM `monitor_users: list[str]` (default `[ssh_user]`). Validate users exist on target before reading.
- Capture per user: `cat ~user/.ssh/authorized_keys 2>/dev/null || true`. Canonicalize: strip comments and blank lines, sort lines, strip trailing whitespace. `scope_key = username`. Metadata: `{"user": username, "lines": str(line_count)}`.
- Hash AFTER canonicalization.

**Tests:** canonicalization is deterministic (shuffle lines → same hash); ed25519/rsa/ecdsa keys all parse identically; missing file → empty content, not error.

### 2.2 Sudoers drift

**Files:** `errander/safety/drift_checks/sudoers.py`.

- Capture: read `/etc/sudoers` and every regular file in `/etc/sudoers.d/`. Use `sudo -n cat` to avoid permission issues (agent's SSH user is assumed to have passwordless sudo for read; that's already a CLAUDE.md baseline).
- Canonicalize per file: strip comments (`#` to end-of-line), strip blank lines, then concatenate files in lexicographic filename order with `=== FILE: <name> ===` separators. `scope_key = ''`. Metadata: `{"files": "sudoers,sudoers.d/admin,sudoers.d/deploy"}` (comma-joined for grep-ability).
- Important nuance: do NOT include `includedir` directives in the hash if they reference the same dir you're already including — they're a noise source. Filter them in canonicalization.

**Tests:** comment-only changes don't trigger drift; adding a new sudoers.d file does; reordering directives within a file does (intentional — sudoers ordering matters); removing a file does.

### 2.3 Listening ports drift

**Files:** `errander/safety/drift_checks/listening_ports.py`.

- Capture: `ss -tulnH 2>/dev/null` (lowercase n = no DNS, no header). Fall back to `netstat -tuln` on stripped minimal RHEL.
- **Critical canonicalization rule:** strip the PID/process-name column. The hash must be over `(proto, local_address, local_port, state)` tuples only. PIDs change on every restart and would produce constant false drift. Keep process name in `metadata` for the report but not the hash.
- Sort tuples before hashing.

**Tests:** restart simulation (same listener, different PID) → no drift; new port appearing → drift; port disappearing → drift.

### 2.4 Cron / systemd timers drift

**Files:** `errander/safety/drift_checks/scheduled_jobs.py`.

- Capture three sources, concatenated with separators:
  1. `find /etc/cron.d /etc/cron.daily /etc/cron.hourly /etc/cron.weekly /etc/cron.monthly -maxdepth 1 -type f 2>/dev/null -exec sha256sum {} +` — file-level hashes only (avoid storing full contents for these; some are large).
  2. `for u in <monitor_users>; do crontab -u $u -l 2>/dev/null; echo "==="; done`.
  3. `systemctl list-timers --all --no-legend --no-pager 2>/dev/null | awk '{print $NF}'` (last column = unit name; ignore the human-formatted "next" / "left" columns since they drift every second).
- `scope_key = ''`. The hash covers the joined output after sorting per-section.

**Tests:** new file in `/etc/cron.d` → drift; running a timer that changes its `next` field → no drift (we ignored those columns); new crontab line for a monitored user → drift.

### 2.5 Failed SSH logins (snapshot, not drift)

**Why separate:** this is a snapshot for the report, not a drift comparison. It does not use `BaselineStore`.

**Files:** new `errander/execution/auth_log.py`.

- Probe order:
  1. `journalctl -u sshd -u ssh --since '24 hours ago' --no-pager 2>/dev/null | grep -E 'Failed|Invalid user'`
  2. Debian fallback: `awk` over `/var/log/auth.log` with a 24h timestamp filter.
  3. RHEL fallback: same over `/var/log/secure`.
- Parse to `(timestamp, user, source_ip)`. Aggregate: total count, top 5 users (with counts), top 5 source IPs (with counts).
- Privacy: add per-VM tag `disable_failed_login_check: true` honored by the runner. Some environments prohibit reading auth logs. Default is enabled.
- Emit `FAILED_SSH_LOGINS_OBSERVED` audit event with the aggregate (not the raw tuples — raw tuples can contain PII).
- Report integration: a small block per VM, "Failed SSH logins (24h): N total, top users: …".

**Tests:** journalctl-style parser; auth.log parser; secure parser; honors disable flag; redacts gracefully when log files are unreadable.

---

## Reporting integration (do this at the end of each phase, not at the very end)

`errander/observability/reporting.py` is small (47 lines). Each phase should add its section to the report builder as it ships, not in a big-bang final pass. The order in the rendered report should be:

1. Per-VM action results (existing).
2. **Blocked actions** (pre-flight gates — Phase 1.1).
3. **Service health regressions** (Phase 1.3) — this is the most operator-relevant signal; surface it high.
4. **VMs requiring reboot** (Phase 1.2).
5. **Drift changes** (Phase 2.0–2.4) — grouped by kind.
6. **Disk growth alerts** (Phase 1.4).
7. **Failed login summary** (Phase 2.5).
8. Existing footer.

Template + LLM-powered narrative both must absorb the new fields. The LLM path in `errander/observability/reporting.py` should get an updated prompt that names the new sections; the template path is the fallback per CLAUDE.md (LLM unavailability must never block).

---

## Configuration surface (settings.yaml)

Add a new top-level block. Keep every feature opt-out-able so operators can roll it out incrementally:

```yaml
sre_signals:
  preflight_lock_check: true
  reboot_required_check: true
  service_health_check: true
  disk_growth_trend:
    enabled: true
    threshold_pct: 10
    window_days: 7
    retention_days: 90
  drift:
    sudoers: true
    authorized_keys: true
    listening_ports: true
    scheduled_jobs: true
    diff_max_lines: 50
    retention_captures: 30
  failed_ssh_logins:
    enabled: true
    window_hours: 24
```

Wire these through `errander/config/settings.py` and `errander/config/schema.py`. All defaults must mirror the YAML above so existing deployments with no `sre_signals` block get the full feature set.

---

## Migrations

SQLite v1 has no migration framework. Add one now — Phase 2 makes it unavoidable:

- New module `errander/safety/migrations.py` with a numbered list of idempotent `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` statements. Track applied migrations in a `schema_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMP)` table.
- Run on `AuditStore` initialization.
- This must be **PostgreSQL-portable** — no SQLite pragmas in migration SQL beyond what PG accepts.

Migrations to add in this plan:
- `0001_vm_state`
- `0002_vm_baselines`
- `0003_vm_disk_history`

---

## Sequencing and PR plan

Suggested PR breakdown — each is independently shippable:

1. **PR-G** (groundwork): G1–G6 + migrations framework + report model fields. No behavior change yet; tests cover schema + serialization only.
2. **PR-1.1**: pkg lock detection.
3. **PR-1.2**: reboot-required detection + report section.
4. **PR-1.3**: critical service health checks + report section.
5. **PR-1.4**: disk growth trend + retention.
6. **PR-2.0+2.1**: drift orchestration + authorized_keys (the cheapest drift check, validates the framework).
7. **PR-2.2**: sudoers drift.
8. **PR-2.3**: listening ports drift.
9. **PR-2.4**: cron/timers drift.
10. **PR-2.5**: failed SSH logins snapshot.

After each PR: update `STATUS.md`, `tasks/todo.md`, `docs/command-log.md`, and create the matching `docs/learning/XX-feature.md` file. This is mandatory per CLAUDE.md's doc-sync rule and is non-negotiable.

---

## Definition of done for the whole plan

- All eight features ship behind config flags defaulting to ON.
- Every feature has unit tests for parsers + integration tests for graph-level behavior.
- `uv run pytest`, `uv run ruff check .`, `uv run mypy .` all green.
- Sandbox dry-run on a 2-VM inventory (one Ubuntu, one RHEL) produces a report that exercises every new section.
- `--unsafe-legacy-live` is NOT required for any new feature — all of these are read-only or run inside existing approval-gated flows.
- No new public network egress paths. No new inbound endpoints. SSH-and-Slack-only remains the rule.

---

## What this plan deliberately does NOT do

- No numeric VM health score.
- No automatic reboot, restart, or remediation of detected issues. Detection only; humans decide.
- No CVE database lookups (that would push us into SIEM territory — explicitly rejected in `ai_sre_recommendations.md`'s product-boundary section).
- No filesystem-wide scans. World-writable checks, "top disk consumers," and security-update-count breakdowns are deferred to a follow-up plan once the eight features above prove their value.
- No PostgreSQL migration. Schema is portable; the actual swap is v2 work per CLAUDE.md.
