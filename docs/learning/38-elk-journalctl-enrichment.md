# 38 — ELK + journalctl Enrichment in probe_vm

## What was built and why

Phase E (commits E2–E4) added three layers of observability to the daily probe:

1. **ELK integration** (E2): `probe_vm` queries ELK for error-level log events in the 24-hour window.
2. **journalctl + systemctl enrichment** (E3): every probe SSH call now also runs `journalctl -p err` and `systemctl --failed` directly on the VM, so teams without ELK still get live error context.
3. **Data source transparency** (E4): `--ask` shows which data sources were consulted (audit, prometheus, elk, live_ssh_probe) so operators know how complete the picture is.

### Why this matters

Before E2–E4, `probe_vm` returned: disk trends, drift diffs, failed SSH login count. A VM with a crashed nginx and 800 journal error lines looked identical to a healthy VM in the digest. Operators had to SSH in themselves to understand "why is this VM behaving oddly?" — exactly the toil Errander-AI is meant to eliminate.

---

## Key concepts

### Two SSH calls per probe (E3)

```python
journal_result = await ssh_manager.execute(
    vm_id, hostname, ssh_user, ssh_key_path,
    "journalctl -n 100 --no-pager -p err 2>/dev/null | tail -50 || true",
)
failed_result = await ssh_manager.execute(
    vm_id, hostname, ssh_user, ssh_key_path,
    "systemctl --failed --no-legend --no-pager 2>/dev/null || true",
)
```

The `|| true` at the end ensures the SSH call succeeds even if journalctl/systemctl isn't available (non-systemd containers, etc.). `2>/dev/null` prevents permission noise.

### _parse_journal_errors — deduplication by pattern

Raw journalctl output contains duplicate lines with varying PIDs and timestamps. A crashed unit can produce 50 identical errors differing only in PID.

```python
key = re.sub(r"\d+", "N", msg)[:80]
```

Replace all digits with `N` before adding to the seen set. `"nginx[1234]: worker died"` and `"nginx[5678]: worker died"` produce the same key `"nginx[N]: worker died"` and are deduplicated. Cap at 5 unique patterns to keep the digest readable.

### _parse_failed_services — stripping the ● marker

`systemctl --failed` in most distros prefixes lines with a unicode bullet `●`. The parser strips it:

```python
stripped = line.strip().lstrip("●").strip()
```

Then takes `parts[0]` as the unit name, validated by checking `"." in unit` (all systemd units have a `.` suffix like `.service`, `.socket`, `.timer`).

### Data source transparency (E4)

`FleetContext.sources_used` is a `list[str]` that accumulates source names as the context is built:

```python
sources_used.append("audit_store")
if self.prometheus_client:
    sources_used.append(f"prometheus({self.prometheus_client.base_url})")
if self.elk_client:
    sources_used.append(f"elk({self.elk_client.base_url})")
```

The `--ask` CLI then prints:

```
Sources consulted: audit_store, prometheus(http://prom:9090), elk(http://elk:9200)
```

If `live_ssh_probe` is absent, the CLI adds a tip: `(add --probe-now for live SSH data)`.

---

## Models touched

**`ProbeVMResult`** gained two new fields:
```python
journal_errors: list[str] = field(default_factory=list)   # journalctl -p err (live only)
failed_services: list[str] = field(default_factory=list)  # systemctl --failed (live only)
```

**`DigestReport`** rendering in `render_digest_report()` now emits:
- ELK errors section (always, when present)
- failed_services inline per-VM
- journal_errors (only when no ELK, to avoid duplication)

---

## Gotchas

### `dry_run=False` is not in `SSHConnectionManager.execute()`

`SandboxExecutor.execute()` accepts `dry_run`, but `SSHConnectionManager.execute()` does not — it always executes. The probe makes real read-only SSH calls (journalctl, df, systemctl) that bypass the sandbox because they're observation-only, never modification.

### MagicMock is not awaitable

After adding the two extra `ssh_manager.execute()` calls in `probe_vm`, all existing tests using `ssh_manager=MagicMock()` broke with `TypeError: object MagicMock is not awaitable`. Fix: create an `AsyncMock` for `.execute`:

```python
def _make_ssh_manager():
    mgr = MagicMock()
    mgr.execute = AsyncMock(return_value=MagicMock(success=True, stdout="", stderr=""))
    return mgr
```

---

## Quiz

1. Why does `_parse_journal_errors` replace digits with `N` before deduplicating?
2. `systemctl --failed` output may start with `●` or not. How does the parser handle both formats?
3. If ELK is configured but the connection fails mid-probe, what happens? (Hint: look at `probe_vm`'s `try/except`)
4. What's the difference between `journal_errors` and `elk_errors` in the digest? When does each appear?
5. Why is `sources_used` a list of strings rather than a set?
