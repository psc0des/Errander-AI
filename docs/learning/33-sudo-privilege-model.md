# 33 — Sudo Privilege Model, Docker Wrapper Mode, and Pre-flight Target Validation

## What was built and why

Phase A closed the last set of SRE findings from the fifth-pass audit. Three areas of work:

1. **Privilege hygiene** (Commit 1): Removed `/usr/bin/env DEBIAN_FRONTEND=noninteractive` from privileged apt commands; dropped sudo from the `apt --simulate upgrade` dry-run; added a proper `SUDO_PREFLIGHT_FAILED` event type.

2. **Docker wrapper mode** (Commit 2): Introduced `docker_command_mode: wrapper | direct_sudo | disabled` per environment. Production uses root-owned wrapper scripts so the sudoers entry never grants raw `sudo docker`.

3. **`--check-targets` CLI** (Commit 3): Pre-flight readiness validation — operators can probe every target VM before a maintenance window to discover broken sudoers or missing wrapper scripts.

---

## Key concepts

### Why `/usr/bin/env` is dangerous in sudoers

`sudo -n /usr/bin/env VAR=val /usr/bin/apt-get` requires `/usr/bin/env` to be in the sudoers allowlist. `env` is general-purpose: if you allow `sudo env`, an attacker with the errander account can run `sudo env rm -rf /`. The fix is to drop `env` entirely and use `apt-get -o Dpkg::Options::=--force-confdef` for the interactive-prompt suppression.

### Why `apt --simulate` shouldn't need sudo

`apt-get --simulate upgrade` is a read-only operation — it calculates what would be installed without touching any file. There is no reason to escalate privilege for a read. The rule: run with the minimum privilege required. Simulate = read-only = no sudo.

### SUDO_PREFLIGHT_FAILED vs PREFLIGHT_LOCK_DETECTED

`PREFLIGHT_LOCK_DETECTED` means "the package manager is locked by another process." `SUDO_PREFLIGHT_FAILED` means "sudo -n is denied for a required binary." These are different failures with different operator responses. Having a single event type for both was confusing.

### Docker wrapper scripts — the narrow sudoers pattern

Raw `sudo docker` is root-equivalent because `docker run -v /:/host` mounts the filesystem. The wrapper pattern gives the agent only what it needs:

```
sudo /usr/local/sbin/errander-docker-assess      # read-only: query state
sudo /usr/local/sbin/errander-docker-prune-safe  # prune dangling + stopped only
sudo /usr/local/sbin/errander-docker-prune-aggressive  # full system prune
```

The wrappers support `--check` (print "ok" and exit 0) so the sudo preflight can probe them without side effects.

### docker_command_mode plumbing

The mode flows:

```
EnvironmentSchema.docker_command_mode
  → main.py initial_state["docker_command_mode"]
  → BatchGraphState.docker_command_mode
  → VMGraphState.docker_command_mode (both simple and wave dispatchers)
  → DockerPruneGraphState.docker_command_mode
```

The preflight node (`sudo_preflight_node`) also reads `docker_command_mode` to pick the right binary set:
- `wrapper` → checks `/usr/local/sbin/errander-docker-{assess,prune-safe,prune-aggressive}`
- `direct_sudo` → checks `/usr/bin/docker` (and emits a non-blocking warning audit event)
- `disabled` → skips docker preflight entirely

### parse_assess_output — safe key=value parsing

The assess wrapper outputs a fixed key=value format plus a delimited block for `docker system df`. The parser handles missing fields with safe defaults and is tolerant of extra lines. The critical invariant: `reachable=no` (or missing) always results in skipping prune.

### --check-targets exit code semantics

Exit 0: all VMs are ready. Exit 1: at least one VM is blocked. This makes `--check-targets prod` scriptable in CI or pre-window automation.

---

## Code walkthrough

### `errander/execution/privilege.py` — split docker key

Before:
```python
"docker_prune": ["/usr/bin/docker"],
```

After:
```python
"docker_prune_wrapper": [
    "/usr/local/sbin/errander-docker-assess",
    "/usr/local/sbin/errander-docker-prune-safe",
    "/usr/local/sbin/errander-docker-prune-aggressive",
],
"docker_prune_direct": ["/usr/bin/docker"],
```

The `sudo_preflight_node` maps `docker_command_mode` to these keys:
```python
key = "docker_prune_wrapper" if docker_mode == "wrapper" else "docker_prune_direct"
```

Note: the mode value is `"direct_sudo"` (not `"direct"`), so an f-string interpolation would produce the wrong key. The explicit conditional is intentional.

### `errander/agent/subgraphs/docker_prune.py` — mode dispatch

`assess_node` dispatches to `_assess_wrapper` or `_assess_direct`. The wrapper path makes a single SSH call to `errander-docker-assess` and parses the structured output. The direct path makes 4 SSH calls (the old behavior).

`execute_node` dispatches the prune command similarly. In wrapper mode, `errander-docker-prune-safe` vs `errander-docker-prune-aggressive` is chosen based on `docker_prune_aggressive`.

`parse_assess_output` handles the `system_df_begin / system_df_end` block correctly even if the block is empty.

### `errander/execution/target_validation.py` — TargetReadiness

`check_target()` is a pure SSH probe — no state mutation. It returns a `TargetReadiness` dataclass with `verdict: "ready" | "blocked"` and `issues: list[str]`.

`render_readiness_report()` produces a human-readable table. Exit code from `run_check_targets` is driven by whether any VM has `verdict == "blocked"`.

---

## Gotchas

1. **Mode key naming**: `docker_command_mode = "direct_sudo"` but the privilege dict key is `"docker_prune_direct"`. Don't use f-string interpolation for this lookup.

2. **`validate_inventory` vs `load_inventory`**: `load_inventory` returns `list[VMTarget]` (flattened). `validate_inventory` returns `InventoryConfig` with `.environments`. The `--check-targets` handler needs the environment-level config (`docker_command_mode`, target list, ssh_user, etc.), so it uses `validate_inventory`.

3. **Warning event is not blocking**: The `direct_sudo` mode emits a `SUDO_PREFLIGHT_FAILED` event with a WARNING detail, but does not fail the preflight. This is by design — `direct_sudo` is allowed for pre-prod; the audit event is informational.

---

## Quiz

1. Why is `apt-get --simulate upgrade` not run with sudo?
2. What would happen if you added `env` to sudoers?
3. How does the assess wrapper's `--check` flag enable safe preflight probing?
4. What is the difference between `PREFLIGHT_LOCK_DETECTED` and `SUDO_PREFLIGHT_FAILED`?
5. Why does `parse_assess_output` use `safe defaults` for all fields?
6. Why does the `--check-targets` exit with code 1 (not just print a warning) when a VM is blocked?
