"""Action sub-graphs (Level 3 of Option C architecture).

Each sub-graph handles the full lifecycle of one action type:
validate → snapshot → execute (or dry-run) → verify → rollback (on failure).

All sub-graphs follow the same structural pattern but contain
action-specific logic for validation, execution, and rollback.

BUILTIN_ACTIONS is the canonical allowlist of supported maintenance
actions. New actions require a new sub-graph + manifest entry here —
there is no dynamic discovery.
"""

from errander.agent.subgraphs.backup_verify import MANIFEST as _BACKUP_VERIFY
from errander.agent.subgraphs.disk_cleanup import MANIFEST as _DISK_CLEANUP
from errander.agent.subgraphs.docker_prune import MANIFEST as _DOCKER_PRUNE
from errander.agent.subgraphs.log_rotation import MANIFEST as _LOG_ROTATION
from errander.agent.subgraphs.patching import MANIFEST as _PATCHING
from errander.agent.subgraphs.service_restart import MANIFEST as _SERVICE_RESTART
from errander.models.manifest import ActionManifest

BUILTIN_ACTIONS: dict[str, ActionManifest] = {
    "patching": _PATCHING,
    "disk_cleanup": _DISK_CLEANUP,
    "log_rotation": _LOG_ROTATION,
    "docker_prune": _DOCKER_PRUNE,
    "backup_verify": _BACKUP_VERIFY,
    "service_restart": _SERVICE_RESTART,
}
