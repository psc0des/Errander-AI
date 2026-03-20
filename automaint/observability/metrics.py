"""Prometheus metrics and /metrics HTTP endpoint.

Exposes metrics for Grafana dashboards:
- automaint_actions_total (counter): Actions executed, labeled by type/status/vm
- automaint_action_duration_seconds (histogram): Action execution time
- automaint_batch_duration_seconds (histogram): Full batch run time
- automaint_ssh_errors_total (counter): SSH connection failures
- automaint_llm_requests_total (counter): LLM calls, labeled by success/fallback
- automaint_approval_wait_seconds (histogram): Time waiting for human approval
- automaint_vm_lock_held_seconds (gauge): How long VM locks are held

Also serves a /health endpoint for liveness checks.
"""

from __future__ import annotations


async def start_metrics_server(port: int = 9090) -> None:
    """Start the Prometheus metrics HTTP server.

    Serves /metrics and /health endpoints.

    Args:
        port: Port to listen on (default 9090).
    """
    raise NotImplementedError("Metrics server not yet implemented")
