---
title: "28 — Service Health Checks (PR-1.3)"
---

# Service Health Regression Detection

## What was built and why

After a non-kernel patching run, package restarts or shared library updates can silently knock a service offline. An `apt-get upgrade` on a server running nginx might restart nginx via a `%postinst` script — or might not, leaving a stale process running against new libraries. PR-1.3 adds pre/post service health snapshots to the patching subgraph that detect this class of failure before it becomes a support incident.

No auto-restart is performed. Detection only — the operator is informed via `SERVICE_HEALTH_REGRESSION` audit event.

## Key concepts

### `service_status_command(services)` — always exits 0

```bash
for svc in nginx postgresql sshd; do
  state=$(systemctl is-active "$svc" 2>/dev/null);
  [ -z "$state" ] && state=unknown;
  echo "$svc=$state";
done
```

Two design decisions:
1. **`$(...)` captures stdout, ignores exit code**: `systemctl is-active` exits non-zero for non-active services. The assignment silently ignores that, so `$state` gets the actual state string ("inactive", "failed", etc.) rather than triggering the `|| echo unknown` branch — which would mask the real state.
2. **`[ -z "$state" ] && state=unknown`**: If `systemctl` is absent (Docker-based image, minimal VM), the subshell outputs nothing → `$state` is empty → falls back to "unknown". The probe never blocks on missing tools.

### `parse_service_statuses()` — fills missing services as unknown

Services not present in the command output are filled in as "unknown". This handles transient probe issues without generating false regressions — an absent line means "no data", not "service stopped".

### `find_regressions()` — only flags pre-active services

```python
return [
    name
    for name, pre_status in pre.items()
    if pre_status.active and not post.get(name, pre_status).active
]
```

`post.get(name, pre_status)` — when a service is missing from `post` (SSH failure during post probe), it returns `pre_status`, so `not pre_status.active = False` → no false regression. This is intentional: a failed post probe should not generate operator alerts when the upgrade itself may have succeeded.

Pre-existing non-active services are not regressions — we only flag services that the maintenance action knocked offline.

### `service_health_pre_node` and `service_health_post_node`

Two async nodes are added to the patching subgraph:

- **`service_health_pre_node`**: runs after `snapshot`, before `execute`. Probes `critical_services` from state. Stores results as `service_pre_snapshot: dict[str, str]` (name → state string). No-op when `critical_services` is empty — zero SSH calls.

- **`service_health_post_node`**: runs after the last SRE node (after `reboot_check` if enabled, otherwise after `verify`). Probes services again, compares with pre-snapshot via `find_regressions()`. Emits `SERVICE_HEALTH_REGRESSION` with `metadata={"regressed_services": [...], "pre_states": {...}, "post_states": {...}}` when regressions found.

### Graph wiring with `sre_service_check`

```
# All SRE checks enabled (default):
preflight_lock → validate → assess → snapshot → service_pre → execute → verify → reboot_check → service_post → END
                                                                        ↓ (fail)
                                                             rollback → END

# sre_reboot_check=False, sre_service_check=True:
... → verify → service_post → END

# sre_service_check=False (any combination):
... → snapshot → execute (unchanged)
... → verify → reboot_check → END  (or → END without reboot_check)
```

The routing after `verify` is a local closure inside `build_patching_subgraph` that captures all three flags and produces the correct next node:
- FAILED/error → "rollback" always
- success → "reboot_check" if enabled, else "service_post" if enabled, else END

### `critical_services` flows from VMTarget

`PatchingGraphState` has a `critical_services: list[str]` field. This is populated from `VMTarget.critical_services` (set in the inventory YAML with env-level defaults and host-level overrides). When no services are configured, both nodes are effective no-ops.

## Gotchas encountered

- **`find_regressions` missing-service behaviour**: Initial implementation and test assumed a missing post-service entry = regression. Corrected: SSH failure during post-probe should not generate false regressions, so `post.get(name, pre_status)` (defaulting to pre state) is the right fallback.
- **Dry-run path**: DRY_RUN_OK exits from `route_after_execute` before `verify`, so `service_pre` collects a snapshot that is never used. This is acceptable — the snapshot call is cheap and the pre-snapshot is simply absent from the final state.

## Quiz yourself

1. What happens when `systemctl is-active nginx` exits non-zero (service inactive)? Does `state` get "inactive" or "unknown"?
2. Why does a missing service in the post snapshot not trigger a regression?
3. If an operator has `critical_services: []` in their inventory, how many extra SSH calls does PR-1.3 add?
4. How does the graph routing change when `sre_reboot_check=True` and `sre_service_check=True` vs. when only `sre_service_check=True`?
5. Why is `find_regressions` a pure function (no SSH, no state mutation)?
