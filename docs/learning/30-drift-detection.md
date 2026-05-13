# 30 — Configuration Drift Detection + Failed SSH Logins (PR-1.5)

## What was built and why

PR-1.5 implements the security signal layer of SRE monitoring: detecting when critical configuration files change between maintenance runs. If someone adds an unauthorized SSH key, modifies sudoers, opens a new port, or installs a cron job, Errander captures it on the next run and emits a diff.

Additionally, failed SSH login counts are collected and surfaced per VM — brute-force probing is an early indicator of targeted compromise.

Components:
- `errander/safety/drift_checks/` — four capture functions (one module each)
- `errander/execution/failed_logins.py` — failed login probe + parser
- `errander/agent/vm_graph.py` — two new nodes wired optionally in `build_vm_graph`

## Key concepts

### BaselineStore + DriftCheck protocol (PR-G groundwork)

`BaselineStore` (built in PR-G) stores per-`(vm_id, kind, scope_key)` snapshots. `compare_and_save` does a read-compare-write in one call:

```python
comparison = await baseline_store.compare_and_save(vm_id, capture)
if comparison.is_first_run:
    # First ever snapshot — baseline established, no alert
elif comparison.changed:
    # Content differs from last snapshot — emit diff
```

### Per-check scope_key design

| Kind              | scope_key   | Why                                         |
|-------------------|-------------|---------------------------------------------|
| `authorized_keys` | username    | Key added for alice ≠ change to bob's keys  |
| `sudoers`         | `""`        | Single global file set                      |
| `listening_ports` | `""`        | One network snapshot                        |
| `scheduled_jobs`  | `""`        | One cron snapshot                           |

`authorized_keys` is the only multi-scope check. The command enumerates non-system users (UID 1000–65533) and dumps their keys in a single SSH round-trip using a shell loop with `USER:username` section delimiters.

### Single-round-trip commands

All four drift checks use a single SSH call to capture their data:

```bash
# authorized_keys — one call, sections delimited by "USER:<name>"
getent passwd | awk -F: '$3>=1000 && $3<65534{print $1":"$6}' \
  | while IFS=: read u h; do echo "USER:$u"; cat "$h/.ssh/authorized_keys" 2>/dev/null; done || true

# sudoers — one call concatenates /etc/sudoers + sudoers.d/*
{ cat /etc/sudoers; for f in /etc/sudoers.d/*; do [ -f "$f" ] && cat "$f"; done; } 2>/dev/null || true

# listening_ports — ss preferred, netstat fallback
ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || true

# scheduled_jobs — user crontab + system crontab + cron.d/
{ crontab -l; cat /etc/crontab; for f in /etc/cron.d/*; do [ -f "$f" ] && cat "$f"; done; } 2>/dev/null || true
```

### Canonicalization before hashing

Raw capture output is "canonicalized" before comparing:
- Strip comment lines (starting with `#`) and blank lines
- Sort remaining lines

Sorting means: adding a new cron entry that gets placed between existing entries doesn't produce a misleading "line removed, line added" diff — it just shows as a new line.

### Failed login detection

```python
_FAIL_RE = re.compile(
    r"(?:Failed password for (?:invalid user )?|Invalid user )(\S+) from (\S+)"
)
```

Matches both:
- `Failed password for root from 1.2.3.4`
- `Failed password for invalid user hacker from 5.5.5.5` (username extracted without "invalid user" prefix)
- `Invalid user admin from 6.6.6.6`

Uses `Counter.most_common(5)` for top-5 aggregation. Both journald and auth.log fallback are tried in one shell compound command.

### Generalized graph wiring

PR-1.4 added `disk_snapshot` as an optional node. PR-1.5 generalizes this to a chain of any enabled SRE snapshot nodes:

```python
sre_snapshot_nodes: list[str] = []
if disk_history_store is not None:
    sre_snapshot_nodes.append("disk_snapshot")
if baseline_store is not None:
    sre_snapshot_nodes.append("drift_baseline")
if sre_failed_logins_settings is not None:
    sre_snapshot_nodes.append("failed_logins")

if sre_snapshot_nodes:
    first = sre_snapshot_nodes[0]
    # route discover → first → ... → last → drift_check
    for prev, nxt in zip(sre_snapshot_nodes, sre_snapshot_nodes[1:]):
        builder.add_edge(prev, nxt)
    builder.add_edge(sre_snapshot_nodes[-1], "drift_check")
```

The default argument trick `def _route_after_discover(state, *, _first: str = _first_sre)` captures the `_first_sre` value at closure creation time, avoiding Python's late-binding issue.

## Gotchas

- **Deferred import patching in tests**: `drift_baseline_node` uses a deferred import `from errander.safety.drift_checks import capture_sudoers`. To mock this in tests, patch `"errander.safety.drift_checks.capture_sudoers"` (the package `__init__.py` namespace), NOT `"errander.safety.drift_checks.sudoers.capture_sudoers"` (the submodule). The deferred `from ... import` reads from `sys.modules["errander.safety.drift_checks"].__dict__`, which is where the patch lands.

- **`try/except Exception` swallows SSH errors**: `drift_baseline_node` wraps each capture call in `try/except Exception`. If the patch doesn't work, the SSH error is caught and swallowed silently, returning an empty list rather than a test failure. The correct signal that your patch isn't working is: `drift_changes == []` when you expected 1.

- **`ss -tlnp` combined flags**: Checking for `-l` in `ss -tlnp` fails because `-l` is not a substring of `-tlnp`. Check for `-tlnp` as a single token.

- **False positives from missing post-probe service**: In `service_check.py` (PR-1.3), missing services from the post-snapshot are treated as unchanged (not a regression). The same philosophy applies here — SSH failure during a drift check skips that check entirely rather than generating a false "no baseline found" comparison.

## Quiz

1. Why does `authorized_keys` use `scope_key=username` while `sudoers` uses `scope_key=""`?
2. If `detect_failed_logins` returns `None`, what does `failed_logins_node` put in state?
3. If none of `disk_history_store`, `baseline_store`, `sre_failed_logins_settings` are provided, how does `build_vm_graph` wire `discover → drift_check`?
4. Why is `compare_and_save` an atomic read-compare-write rather than separate `latest()` and `save()` calls?
5. Why do we sort the canonicalized lines? What class of false positives does this prevent?
