# AI SRE Recommendations

## Product Boundary

Keep the product focused:

> AI-assisted autonomous maintenance for heterogeneous Linux VM fleets.

Do not turn it into a full incident platform, SIEM, Kubernetes platform, CMDB, database recovery tool, or ServiceNow clone.

Every new feature should pass this test:

1. Does it reduce repetitive VM maintenance toil?
2. Can it be safely checked over SSH?
3. Can it run in dry-run mode first?
4. Can the output be audited?
5. Can live execution be approved or safely blocked?

If the answer is no, it belongs in a future product or integration, not the core agent.

## Small Additions With Real SRE Value

### 1. Reboot-Required Detection

Detect when a VM needs a reboot after patching.

Examples:

- Debian/Ubuntu: check `/var/run/reboot-required`
- RHEL-like systems: use `needs-restarting -r`

Recommended behavior:

- Report reboot requirement.
- Do not auto-reboot in v1.
- Mark VM as `needs_reboot`.
- Include reason in final report.

Value:

- Very common SRE task.
- Low risk.
- Fits naturally after patching.

### 2. Critical Service Health Checks

Allow inventory to define critical services per VM:

```yaml
services:
  - nginx
  - docker
  - postgresql
```

After maintenance, check:

```text
systemctl is-active <service>
```

Recommended behavior:

- Read-only check.
- Fail the VM result if a critical service is down after maintenance.
- Include service status in the report.

Value:

- Converts "command succeeded" into "system is actually healthy."
- Important for real operator trust.

### 3. Disk Growth Trend

Store disk usage after each run and show growth over time.

Example:

```text
/ grew from 62% to 78% in 7 days
```

Recommended behavior:

- Store historical disk usage in audit DB.
- Show trend in report/UI.
- Flag abnormal growth.

Value:

- Helps SREs catch disk problems before outages.
- Small feature, high operational value.

### 4. Package Manager Lock Detection

Before patching, detect package manager locks.

Examples:

- `apt` / `dpkg` lock
- `dnf` / `yum` lock

Recommended behavior:

- If lock exists, skip patching cleanly.
- Mark action as `blocked`.
- Report which process holds the lock if possible.

Value:

- Prevents noisy patch failures.
- Common real-world issue.

### 5. Uptime / Stale Reboot Warning

Flag machines with very high uptime.

Example thresholds:

- 90 days: warning
- 180 days: high warning

Recommended behavior:

- Read uptime.
- Add warning to report.
- Do not reboot automatically.

Value:

- Helps operators find neglected servers.
- Very easy to implement.

### 6. Top Disk Consumers In Safe Paths

Before disk cleanup, show top consumers under safe paths.

Examples:

- `/tmp`
- package cache
- journal logs

Recommended behavior:

- Read-only pre-check.
- Show top 5 largest files/directories.
- Only inspect approved safe paths.

Value:

- Makes cleanup explainable.
- Helps operators understand what the agent is doing.

### 7. Service Restart Recommendation

After patching, detect services that may need restart.

Recommended behavior:

- Report recommendation only.
- Do not restart automatically in v1.
- Allow future approval-gated restart workflow.

Value:

- Real SRE usefulness without unsafe automation.

### 8. Pre-Flight Dependency Checks

Before running actions, verify basic target readiness:

- SSH reachable
- expected OS
- `sudo` available
- package manager available
- enough free disk space
- system clock sane

Recommended behavior:

- Fail early with clear reason.
- Do not attempt risky maintenance if prerequisites are missing.

Value:

- Reduces failed runs.
- Makes reports clearer.

## Security-Focused Additions

Security can add real value, but keep it lightweight. Do not build a SIEM or vulnerability scanner inside this product.

### 1. Failed SSH Login Check

Read recent authentication failures.

Examples:

- Debian/Ubuntu: `/var/log/auth.log`
- RHEL-like systems: `/var/log/secure`
- systemd systems: `journalctl -u ssh -u sshd`

Recommended behavior:

- Count failed login attempts in last 24 hours.
- Report top usernames and source IPs if available.
- Never block maintenance based on this alone.

Value:

- Simple security signal.
- Useful to sysadmins.

### 2. Open Listening Ports Snapshot

Capture listening ports:

```text
ss -tulpn
```

Recommended behavior:

- Store snapshot.
- Compare against previous baseline.
- Report newly opened ports.

Value:

- Excellent lightweight drift/security signal.
- Helps detect unexpected exposed services.

### 3. Sudoers Change Detection

Track changes to privileged access files:

- `/etc/sudoers`
- `/etc/sudoers.d/*`

Recommended behavior:

- Hash file contents.
- Compare against previous baseline.
- Report drift only.

Value:

- High-value security check.
- Read-only and safe.

### 4. SSH Authorized Keys Drift

Track changes to authorized SSH keys.

Recommended behavior:

- Hash `~/.ssh/authorized_keys` for configured users.
- Report new or removed keys.
- Do not modify keys automatically.

Value:

- Very useful for access hygiene.
- Small and directly relevant to VM ops.

### 5. World-Writable Sensitive Path Check

Check for unsafe permissions in common sensitive paths:

- `/etc`
- `/usr/local/bin`
- `/opt`
- application config directories if configured

Recommended behavior:

- Read-only scan.
- Report suspicious world-writable files.
- Avoid scanning the whole filesystem in v1.

Value:

- Practical hardening check.
- Low blast radius if scoped.

### 6. Security Update Count

Separate normal package updates from security updates where the OS supports it.

Recommended behavior:

- Report number of security updates pending.
- Prioritize security updates in the plan.
- Still exclude kernel packages unless explicitly approved in a future workflow.

Value:

- Adds security relevance without becoming a CVE platform.

### 7. Suspicious Cron/Systemd Timer Check

List recently changed scheduled jobs:

- `/etc/cron*`
- user crontabs if accessible
- systemd timers

Recommended behavior:

- Compare against previous baseline.
- Report new or changed jobs.
- Do not delete or disable automatically.

Value:

- Small but useful compromise/drift signal.

## Best Security Additions To Start With

Start with these three:

1. Failed SSH login check.
2. Open listening ports snapshot and drift.
3. SSH authorized keys drift.

Why these three:

- They are read-only.
- They are easy to explain.
- They fit VM operations.
- They provide real security value.
- They do not expand the product into a full security platform.

## Recommended Next Roadmap

### Phase 1: Operational Trust

1. Reboot-required detection.
2. Critical service health checks.
3. Package manager lock detection.
4. Disk growth trend.

### Phase 2: Lightweight Security Signals

1. Failed SSH login check.
2. Open listening ports drift.
3. SSH authorized keys drift.
4. Sudoers drift.

### Phase 3: Operator Experience

1. Clear VM health score.
2. Better final report with "safe", "needs attention", and "blocked" categories.
3. UI trend view for disk and security drift.

## Final Recommendation

Do not add huge modules.

Add small checks that make the current maintenance agent safer, smarter, and more trusted.

The best positioning remains:

> An AI-assisted SRE maintenance agent that safely handles routine Linux VM toil and surfaces early operational/security risks.
