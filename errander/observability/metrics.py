"""Prometheus metrics definitions + minimal /metrics + /health HTTP server.

All metrics are module-level singletons registered in a shared CollectorRegistry.
This module is imported by both the agent process (to record metrics during
batch execution) and the web UI process (to read counters for the monitoring
page) — it has no dependency on either side and stays tiny on purpose.

Metrics exposed:
- errander_actions_total (counter): Actions executed, labeled by type/status/vm
- errander_action_duration_seconds (histogram): Action execution wall time
- errander_batch_duration_seconds (histogram): Full batch run time
- errander_ssh_errors_total (counter): SSH connection failures by vm/reason
- errander_llm_requests_total (counter): LLM calls labeled by outcome
- errander_approval_wait_seconds (histogram): Time waiting for human approval
- errander_vm_lock_held_seconds (histogram): How long VM locks are held
- errander_wave_health_checks_total (counter): Wave health check outcomes
- errander_agent_starts_total (counter): Agent process startups
- errander_batches_interrupted_total (counter): Batches interrupted by restart

The agent process serves this module's /metrics + /health via
:func:`start_metrics_server`. The web UI process serves its own copy of
the same two routes (see :mod:`errander.web.ui`).
"""

from __future__ import annotations

import logging

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

#: Shared registry — all metrics in one place, easy to pass to tests.
REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

ACTIONS_TOTAL = Counter(
    "errander_actions_total",
    "Total maintenance actions executed",
    ["action_type", "status", "vm_id"],
    registry=REGISTRY,
)

ACTION_DURATION = Histogram(
    "errander_action_duration_seconds",
    "Time spent executing a single maintenance action",
    ["action_type"],
    buckets=(5, 15, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)

BATCH_DURATION = Histogram(
    "errander_batch_duration_seconds",
    "Time for a full batch maintenance run",
    buckets=(30, 60, 120, 300, 600, 1200, 1800),
    registry=REGISTRY,
)

SSH_ERRORS_TOTAL = Counter(
    "errander_ssh_errors_total",
    "SSH connection or command failures",
    ["vm_id", "reason"],
    registry=REGISTRY,
)

LLM_REQUESTS_TOTAL = Counter(
    "errander_llm_requests_total",
    "LLM completion calls",
    ["outcome"],  # "success" | "fallback" | "timeout" | "error"
    registry=REGISTRY,
)

APPROVAL_WAIT = Histogram(
    "errander_approval_wait_seconds",
    "Seconds waiting for the operator's web UI approval decision",
    buckets=(30, 60, 120, 300, 600, 900, 1800),
    registry=REGISTRY,
)

VM_LOCK_HELD = Histogram(
    "errander_vm_lock_held_seconds",
    "Duration VM lock was held",
    ["vm_id"],
    buckets=(10, 30, 60, 120, 300, 600, 1200),
    registry=REGISTRY,
)

WAVE_HEALTH_CHECKS = Counter(
    "errander_wave_health_checks_total",
    "Wave health check outcomes",
    ["wave", "outcome"],
    registry=REGISTRY,
)

AGENT_STARTS_TOTAL = Counter(
    "errander_agent_starts_total",
    "Agent process startups (proxy for restart frequency)",
    registry=REGISTRY,
)

BATCHES_INTERRUPTED_TOTAL = Counter(
    "errander_batches_interrupted_total",
    "Batches detected on startup with BATCH_STARTED but no terminal event",
    registry=REGISTRY,
)


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

async def _metrics_handler(request: web.Request) -> web.Response:
    """Serve Prometheus /metrics in text exposition format."""
    output = generate_latest(REGISTRY)
    return web.Response(
        body=output,
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


async def _health_handler(request: web.Request) -> web.Response:
    """Serve /health liveness check.

    Returns 200 OK with a JSON body. No dependency checks — if the
    process is alive enough to serve HTTP, it's alive.
    """
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

async def start_metrics_server(
    port: int = 9090,
    bind_address: str = "127.0.0.1",
) -> web.AppRunner:
    """Start the minimal Prometheus metrics + health HTTP server.

    Serves only:
    - GET /metrics — Prometheus text format
    - GET /health  — {"status": "ok"}

    No stores, no UI, no auth — this is the agent process's surface.

    Args:
        port: Port to listen on (default 9090).
        bind_address: Address to bind (default loopback-only).

    Returns:
        Running AppRunner — call runner.cleanup() on shutdown.
    """
    app = web.Application()
    app.router.add_get("/metrics", _metrics_handler)
    app.router.add_get("/health", _health_handler)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=bind_address, port=port)
    await site.start()
    logger.info("Metrics server listening on %s:%d (/metrics, /health)", bind_address, port)
    return runner
