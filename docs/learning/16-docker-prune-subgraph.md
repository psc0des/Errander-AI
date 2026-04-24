# 16 — Docker Prune Sub-Graph (Phase 2)

## What Was Built

The Docker prune sub-graph — checks whether Docker is installed and running, counts dangling images and stopped containers, prunes unused resources, and verifies disk was reclaimed. Same 4-node lifecycle as the other sub-graphs.

## Why It's Interesting

Docker prune has a unique validate step: it checks a discovery-time flag (`docker_available`) rather than validating paths or patterns. This is the first sub-graph where the validate node gates on a system capability rather than user input.

## Key Concepts

### 1. Two-Layer Docker Availability Check

Layer 1 — the validate node checks `docker_available` from VM discovery:

```python
def validate_node(state: DockerPruneGraphState) -> dict[str, Any]:
    docker_available = state.get("docker_available", True)
    if not docker_available:
        return {
            "status": ActionStatus.SKIPPED.value,
            "error": "Docker not installed or not running",
        }
    return {"status": ActionStatus.PENDING.value}
```

Layer 2 — the assess node verifies Docker daemon is actually responding:

```python
docker_check = await executor.execute(
    ..., command="docker info >/dev/null 2>&1 && echo ok",
)
if not docker_check.success or "ok" not in docker_check.stdout:
    return {
        "status": ActionStatus.SKIPPED.value,
        "error": "Docker daemon not responding",
        "nothing_to_do": True,
    }
```

Why two layers? The discovery flag might be stale (set minutes ago). The daemon might have crashed between discovery and dispatch. The assess node is the authoritative "is Docker actually available right now?" check.

### 2. Counting Resources Before Deciding

The assess node runs three SSH commands to build a picture:

```python
# 1. Overall disk usage
"docker system df 2>/dev/null"

# 2. Dangling images (images without tags, not used by containers)
"docker images -f dangling=true -q 2>/dev/null | wc -l"

# 3. Stopped containers (exited, not running)
"docker ps -a -f status=exited -q 2>/dev/null | wc -l"
```

The idempotency check is simple:

```python
if dangling == 0 and stopped == 0:
    return {"nothing_to_do": True, "status": ActionStatus.SKIPPED.value, ...}
```

### 3. Prune Without `--volumes`

The execute command is `docker system prune -af` — notably **without** `--volumes`:

```python
result = await executor.execute(
    ...,
    command="docker system prune -af 2>&1",
    simulate_command="docker system df 2>/dev/null",
)
```

Why no `--volumes`? Volumes may contain persistent data (databases, uploads). Pruning volumes is a destructive operation that should require explicit human approval. The `-a` flag removes all unused images (not just dangling), and `-f` skips the confirmation prompt.

### 4. Before/After Comparison

The verify node captures `docker system df` output after pruning and stores it as `disk_after`. The assess node already stored `disk_before`. This gives the audit trail a clear before/after picture, even though the sub-graph doesn't calculate the delta itself — that's for the report generator.

### 5. Integer Parsing Helper

A small but important pattern — parsing shell command output to integers safely:

```python
def _parse_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0
```

Shell commands can return unexpected output (error messages, empty strings, None). This helper ensures the sub-graph never crashes on bad input — it defaults to 0, which means "nothing to prune."

## Graph Flow

```
START → validate → assess → execute → verify → END
           │                    │
           │ (no docker)        │ (dry-run)
           └──→ END             └──→ END
                    │
                    │ (0 dangling + 0 stopped)
           assess ──→ END
```

## Dispatch Wiring

Docker prune is the only sub-graph where the dispatch helper passes discovery data into sub-state:

```python
async def _run_docker_prune(state: VMGraphState, compiled: Any) -> dict[str, object]:
    vm_info = state.get("vm_info", {})
    sub_state: DockerPruneGraphState = {
        ...,
        "docker_available": bool(vm_info.get("docker_available", True)),
    }
```

Other sub-graphs only need `vm_id`, `os_family`, `dry_run`, and SSH params. Docker prune needs the discovery flag to avoid SSHing into a VM without Docker.

## Gotchas

1. **`docker system prune -af` can hang**: If the Docker daemon is stuck, the prune command blocks indefinitely. The SSH executor has a timeout, but it's worth knowing that prune can be slow on VMs with many stopped containers.

2. **`dangling=true` vs all unused**: `docker images -f dangling=true` returns images without tags. `docker system prune -a` removes ALL unused images (including tagged ones not used by any container). The assess counts dangling only, but execute prunes more aggressively.

3. **Routing uses both FAILED and SKIPPED**: The validate routing checks for both statuses — `if state.get("status") in (ActionStatus.FAILED.value, ActionStatus.SKIPPED.value)`. This is slightly different from other sub-graphs where validate only checks FAILED.

## Quiz Yourself

1. Why does Docker prune have two availability checks (validate + assess) instead of one?
2. What would happen if `--volumes` were added to the prune command? What data could be lost?
3. Why does `_parse_int` catch `AttributeError` in addition to `ValueError`?
4. How does the Docker prune dispatch helper differ from the disk cleanup dispatch helper?
5. What's the difference between `docker images -f dangling=true` and what `docker system prune -a` removes?
