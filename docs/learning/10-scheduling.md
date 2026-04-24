# 10 — Maintenance Windows + APScheduler

## What Was Built and Why

`errander/scheduling/windows.py` implements the maintenance window gate — the agent refuses to perform live maintenance outside the configured time windows. `errander/scheduling/scheduler.py` wraps APScheduler to trigger batch runs on a cron schedule.

Key design: **the scheduler triggers runs, but the graph enforces windows**. The `validate_window` node in `graph.py` calls `is_within_window()`. This separation means the scheduler can fire at any time and the graph decides whether to proceed — no need for the scheduler itself to know about window logic.

---

## Key Concepts

### 1. Maintenance Window as Day + Hour Range

A maintenance window is defined by three dimensions:
1. **Days of week** — which days are allowed (e.g., "tuesday and thursday")
2. **Hour range** — which hours within those days (e.g., 02:00–06:00)
3. **Timezone** — what timezone the hours are interpreted in

```python
@dataclass
class MaintenanceWindow:
    days: list[str]       # ["tuesday", "thursday"]
    start_hour: int       # 2
    end_hour: int         # 6
    timezone: str         # "UTC"
```

The check: convert `now` to the target timezone, verify the day name is in `days`, verify the hour is in `[start_hour, end_hour)`.

### 2. Overnight Windows

Not all maintenance windows are within a single calendar day. `start_hour=23, end_hour=3` spans midnight. The logic:

```python
if start_hour < end_hour:
    # Normal: [02:00, 06:00) → hour in range
    in_hours = start_hour <= hour < end_hour
elif start_hour > end_hour:
    # Overnight: [23:00, 03:00) → hour >= 23 OR hour < 3
    in_hours = hour >= start_hour or hour < end_hour
else:
    # start == end: zero-length window, never in it
    in_hours = False
```

For overnight windows, the `days` list must include both the day the window starts AND the day it ends (for the early-morning portion):

```yaml
maintenance_days: [monday, tuesday]  # Monday night into Tuesday morning
maintenance_window: "23:00-03:00"
```

### 3. Timezone-Aware Checks with zoneinfo

Python 3.9+ includes `zoneinfo` in the standard library — no third-party `pytz` needed:

```python
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

tz = ZoneInfo("Australia/Sydney")
local_now = now.astimezone(tz)
hour = local_now.hour
```

`ZoneInfoNotFoundError` is raised for unknown timezone strings. This is validated at construction time in `MaintenanceWindow.__post_init__` so misconfigured inventories fail fast.

**DST gotcha**: `Europe/Paris` is CET (UTC+1) in winter but CEST (UTC+2) in April–October. Always verify the UTC offset for the specific date you're testing against — don't assume a fixed offset.

### 4. APScheduler: AsyncIOScheduler

APScheduler 3.x provides `AsyncIOScheduler` — an async-compatible scheduler that runs as a background thread but integrates with the asyncio event loop for job execution:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()
trigger = CronTrigger.from_crontab("0 2 * * tue,thu")
scheduler.add_job(run_batch, trigger, id="prod-nightly", misfire_grace_time=600)
scheduler.start()
```

`CronTrigger.from_crontab()` parses a standard 5-field cron expression. Day-of-week supports ranges (`mon-fri`) and lists (`tue,thu`).

### 5. Misfire Grace Time

If the agent is down at the scheduled trigger time, APScheduler records the missed trigger. When the agent comes back up, if the missed time is within `misfire_grace_time` seconds, the job still runs:

```python
misfire_grace_time=600  # Run if we missed by up to 10 minutes
```

`coalesce=True` means multiple missed triggers are collapsed into one run — the agent doesn't try to "catch up" on all missed batches.

### 6. Scheduler vs Window Separation

The scheduler fires at `cron="0 2 * * tue"` — it doesn't check if `02:00` is within the maintenance window. The graph's `validate_window` node calls `is_within_window()` and stops the batch if the window has shifted (e.g., DST change pushed the cron trigger outside the window).

This is intentional: the cron schedule is a "roughly when to run" hint. The window check is the authoritative safety gate.

---

## Code Walkthrough

### MaintenanceScheduler

```python
class MaintenanceScheduler:
    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._started = False

    def add_maintenance_job(self, func, cron_expr, job_id, kwargs=None):
        trigger = CronTrigger.from_crontab(cron_expr)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
        )

    async def start(self):
        if self._started:
            return  # idempotent
        self._scheduler.start()
        self._started = True

    async def stop(self):
        if not self._started:
            return  # safe no-op
        self._scheduler.shutdown(wait=True)
        self._started = False
```

`wait=True` on `shutdown()` means the scheduler waits for running jobs to finish before stopping. This prevents cut-off maintenance operations on agent restart.

### list_jobs safe attribute access

```python
def list_jobs(self):
    for job in self._scheduler.get_jobs():
        next_run_time = getattr(job, "next_run_time", None)  # see Gotchas
        yield {"id": job.id, "next_run": str(next_run_time) or "pending", ...}
```

---

## Example Configuration

```yaml
# inventory.yaml — production environment
environments:
  production:
    maintenance_window: "02:00-06:00"
    maintenance_days: [tuesday, thursday]
    ssh_user: errander
    ssh_key_path: ~/.ssh/errander_prod
    approval_policy: strict
    targets:
      - host: 10.0.1.10
        name: prod-web-01
        os_family: ubuntu

# settings.yaml — cron schedule
schedules:
  production:
    maintenance: "0 2 * * tue,thu"  # 02:00 UTC on Tue and Thu
```

---

## Gotchas

### 1. APScheduler 3.x: next_run_time on Unstarted Scheduler

`Job` uses `__slots__`. Before the scheduler starts, jobs are pending and `next_run_time` is never set — accessing it raises `AttributeError`. Use `getattr(job, "next_run_time", None)`.

### 2. replace_existing Only Works After Scheduler Starts

`replace_existing=True` deduplicates against the jobstore, not the in-memory pending list. Two jobs with the same ID added before `start()` both sit in the pending list. After `start()`, APScheduler processes pending jobs into the jobstore and deduplication applies.

### 3. DST Shifts Can Break Window Alignment

If the cron is `"0 2 * * tue"` UTC and the window is `"02:00-06:00 UTC"`, they're aligned. But if the window is defined in `"Europe/London"`, a DST transition can shift the effective window by an hour, making the 02:00 UTC trigger fall outside `[02:00, 06:00) BST` (which is `[01:00, 05:00) UTC`). Define crons and windows in the same timezone to avoid this.

### 4. Overnight Windows Need Both Days in `days`

```yaml
# WRONG — "saturday" only matches the pre-midnight portion
maintenance_days: [saturday]
maintenance_window: "23:00-03:00"

# CORRECT — "sunday" matches the post-midnight portion
maintenance_days: [saturday, sunday]
maintenance_window: "23:00-03:00"
```

---

## Quiz Yourself

1. What is the difference between `start_hour < end_hour` and `start_hour > end_hour` in window logic?
2. Why does the scheduler not enforce maintenance windows itself?
3. What does `coalesce=True` do in APScheduler?
4. Why use `getattr(job, "next_run_time", None)` instead of `job.next_run_time`?
5. You configure a window of `02:00-06:00 Europe/Paris` and a cron of `0 2 * * *` UTC. In March (CET, UTC+1), is the trigger inside the window? What about in June (CEST, UTC+2)?
