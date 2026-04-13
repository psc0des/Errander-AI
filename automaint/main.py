"""Entry point for the AutoMaint agent.

Long-lived process that:
1. Loads configuration and validates inventory
2. Starts APScheduler for maintenance window scheduling
3. Starts Prometheus /metrics and /health HTTP server
4. Runs LangGraph batch orchestrator on schedule (or on-demand via --run-now)
5. Handles graceful shutdown on SIGTERM/SIGINT

Usage:
    uv run python -m automaint [options]
    uv run python -m automaint --run-now --env production --dry-run
    uv run python -m automaint --run-now --env production --force --force-reason "emergency"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

import structlog

from automaint.config.schema import EnvironmentSchema, validate_inventory, validate_settings
from automaint.models.events import EventType
from automaint.config.settings import Settings, load_settings
from automaint.execution.sandbox import SandboxExecutor
from automaint.execution.ssh import SSHConnectionManager
from automaint.integrations.llm import LLMClient
from automaint.integrations.slack import SlackClient
from automaint.observability.metrics import start_metrics_server
from automaint.safety.audit import AuditStore
from automaint.safety.locking import FileLocker
from automaint.scheduling.scheduler import MaintenanceScheduler
from automaint.scheduling.windows import MaintenanceWindow

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="automaint",
        description="AutoMaint — autonomous VM maintenance agent",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("settings.yaml"),
        help="Path to settings.yaml (default: settings.yaml)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("inventory.yaml"),
        help="Path to inventory.yaml (default: inventory.yaml)",
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run a maintenance batch immediately, then exit",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="Environment name to run (required with --run-now)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry-run mode — simulate commands, no live changes (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live execution (overrides --dry-run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass maintenance window check",
    )
    parser.add_argument(
        "--force-reason",
        default="",
        help="Reason for forcing outside window (required with --force)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    # LLM health check mode
    parser.add_argument(
        "--check-llm",
        action="store_true",
        help="Check vLLM endpoint connectivity, model info, and latency, then exit",
    )

    # Audit query mode
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Query the audit trail and exit",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Filter audit events by batch ID (use with --audit)",
    )
    parser.add_argument(
        "--vm-id",
        default=None,
        help="Filter audit events by VM ID (use with --audit)",
    )
    parser.add_argument(
        "--action-type",
        default=None,
        help="Filter audit events by action type, e.g. disk_cleanup (use with --audit)",
    )
    parser.add_argument(
        "--event-type",
        default=None,
        help="Filter audit events by event type, e.g. action_started (use with --audit)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=50,
        help="Maximum events to return (default: 50, use with --audit)",
    )
    parser.add_argument(
        "--batches",
        action="store_true",
        help="Show recent batch summaries instead of individual events (use with --audit)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Component builders
# ---------------------------------------------------------------------------

def _build_maintenance_window(env: EnvironmentSchema) -> MaintenanceWindow | None:
    """Build a MaintenanceWindow from an EnvironmentSchema, or None if unconfigured."""
    if not env.maintenance_window or not env.maintenance_days:
        return None

    # Parse "HH:MM-HH:MM" → (start_hour, end_hour)
    try:
        start_str, end_str = env.maintenance_window.split("-")
        start_hour = int(start_str.split(":")[0])
        end_hour = int(end_str.split(":")[0])
    except (ValueError, AttributeError) as exc:
        logger.warning(
            "Could not parse maintenance_window",
            window=env.maintenance_window,
            error=str(exc),
        )
        return None

    try:
        return MaintenanceWindow(
            days=[d.lower() for d in env.maintenance_days],
            start_hour=start_hour,
            end_hour=end_hour,
            timezone=env.maintenance_timezone,
        )
    except ValueError as exc:
        logger.warning("Invalid maintenance window config", error=str(exc))
        return None


def _build_components(settings: Settings) -> tuple[
    SSHConnectionManager,
    SandboxExecutor,
    FileLocker,
    SlackClient | None,
    LLMClient | None,
]:
    """Construct shared infrastructure components from settings."""
    ssh_manager = SSHConnectionManager(
        command_timeout=settings.ssh_command_timeout_seconds,
        reconnect_attempts=settings.ssh_reconnect_attempts,
        reconnect_backoff=settings.ssh_reconnect_backoff,
    )

    executor = SandboxExecutor(ssh_manager=ssh_manager, dry_run=settings.dry_run_default)

    locker = FileLocker(lock_dir=Path(".automaint-locks"))

    slack: SlackClient | None = None
    if settings.slack_bot_token and settings.slack_channel_id:
        slack = SlackClient(
            bot_token=settings.slack_bot_token,
            channel_id=settings.slack_channel_id,
        )
    else:
        logger.warning("Slack not configured — approval notifications disabled")

    llm: LLMClient | None = None
    if settings.llm_base_url:
        llm = LLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    else:
        logger.warning("LLM not configured — using hardcoded fallback logic")

    return ssh_manager, executor, locker, slack, llm


# ---------------------------------------------------------------------------
# LLM check
# ---------------------------------------------------------------------------

async def run_llm_check(settings: Settings) -> int:
    """Check the vLLM endpoint and print a human-readable status report."""
    if not settings.llm_base_url:
        print("LLM not configured — set AUTOMAINT_LLM_BASE_URL (e.g. http://10.0.1.5:8000/v1)")
        return 1

    from automaint.integrations.llm import LLMClient

    client = LLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=1,
    )

    print(f"Checking vLLM endpoint: {settings.llm_base_url}")
    print("-" * 50)

    result = await client.check_endpoint()

    if not result["reachable"]:
        print(f"  Status   : UNREACHABLE")
        print(f"  Error    : {result['error']}")
        return 1

    model_ids = result["model_ids"]
    latency = result["latency_ms"]
    test_resp = result["test_response"]
    error = result["error"]

    print(f"  Status   : OK")
    print(f"  Models   : {', '.join(str(m) for m in model_ids) if model_ids else '(none listed)'}")

    if latency is not None:
        print(f"  Latency  : {latency} ms (test completion)")
        print(f"  Response : {test_resp!r}")
    elif error:
        print(f"  Latency  : n/a — {error}")

    print("-" * 50)
    if error and latency is None:
        print("Endpoint reachable but test completion failed.")
        return 1

    print("vLLM endpoint is healthy.")
    return 0


# ---------------------------------------------------------------------------
# Audit CLI
# ---------------------------------------------------------------------------

_COL_WIDTHS = (26, 12, 20, 16, 14, 50)
_HEADERS = ("timestamp", "event_type", "batch_id", "vm_id", "action_type", "detail")


def _truncate(s: str, width: int) -> str:
    return s if len(s) <= width else s[: width - 1] + "…"


def _print_audit_table(rows: list[tuple[str, ...]]) -> None:
    header = "  ".join(h.ljust(w) for h, w in zip(_HEADERS, _COL_WIDTHS))
    separator = "  ".join("-" * w for w in _COL_WIDTHS)
    print(header)
    print(separator)
    for row in rows:
        line = "  ".join(_truncate(v, w).ljust(w) for v, w in zip(row, _COL_WIDTHS))
        print(line)


async def run_audit_query(args: argparse.Namespace, settings: Settings) -> int:
    """Run an audit query and print results to stdout."""
    audit_store = AuditStore(settings.audit_db_url)
    await audit_store.__aenter__()
    try:
        if args.batches:
            batches = await audit_store.get_recent_batches(limit=args.last)
            if not batches:
                print("No batches found.")
                return 0
            print(f"{'batch_id':<36}  {'started_at':<26}  {'events':>6}  vms")
            print("-" * 80)
            for b in batches:
                vms = ", ".join(str(v) for v in b["vm_ids"]) if b["vm_ids"] else "(none)"
                print(
                    f"{str(b['batch_id']):<36}  "
                    f"{str(b['started_at']):<26}  "
                    f"{str(b['event_count']):>6}  "
                    f"{vms}"
                )
            return 0

        event_type: EventType | None = None
        if args.event_type:
            try:
                event_type = EventType(args.event_type.lower())
            except ValueError:
                valid = [e.value for e in EventType]
                print(f"Unknown event type '{args.event_type}'. Valid: {valid}")
                return 1

        events = await audit_store.get_events(
            batch_id=args.batch_id,
            vm_id=args.vm_id,
            event_type=event_type,
            action_type=args.action_type,
            limit=args.last,
        )

        if not events:
            print("No events found matching the given filters.")
            return 0

        rows = [
            (
                e.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                e.event_type.value,
                e.batch_id,
                e.vm_id or "",
                e.action_type or "",
                e.detail,
            )
            for e in events
        ]
        _print_audit_table(rows)
        print(f"\n{len(events)} event(s) shown.")
        return 0
    finally:
        await audit_store.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

async def run_env_batch(
    env_name: str,
    env_schema: EnvironmentSchema,
    settings: Settings,
    executor: SandboxExecutor,
    locker: FileLocker,
    ssh_manager: SSHConnectionManager,
    audit_store: AuditStore,
    dry_run: bool = True,
    force: bool = False,
    force_reason: str = "",
) -> None:
    """Run a full maintenance batch for one environment.

    Builds the batch graph, constructs initial state from inventory,
    and invokes the compiled graph.
    """
    from automaint.agent.graph import build_batch_graph

    window = _build_maintenance_window(env_schema)

    graph = build_batch_graph(
        executor=executor,
        locker=locker,
        audit_store=audit_store,
        ssh_manager=ssh_manager,
        window=window,
    ).compile()

    targets = [
        {
            "vm_id": f"{env_name}/{t.name}",
            "hostname": t.host,
            "ssh_user": t.ssh_user or env_schema.ssh_user,
            "ssh_key_path": t.ssh_key_path or env_schema.ssh_key_path,
            "os_family": t.os_family,
        }
        for t in env_schema.targets
    ]

    initial_state = {
        "targets": targets,
        "dry_run": dry_run,
        "force": force,
        "force_reason": force_reason,
        "vm_results": [],
    }

    logger.info(
        "Starting batch",
        env=env_name,
        targets=len(targets),
        dry_run=dry_run,
        force=force,
    )

    final = await graph.ainvoke(initial_state)

    logger.info(
        "Batch complete",
        env=env_name,
        batch_id=final.get("batch_id"),
        results=len(final.get("vm_results", [])),
    )

    if final.get("report"):
        logger.info("Batch report", report=final["report"])

    if final.get("error"):
        logger.warning("Batch ended with error", error=final["error"])


# ---------------------------------------------------------------------------
# Long-running agent (scheduler mode)
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> int:
    """Main async entry point.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    # --- Configuration ---
    settings = load_settings(
        settings_path=args.config if args.config.exists() else None,
    )

    # --- LLM check mode: verify endpoint and exit ---
    if args.check_llm:
        return await run_llm_check(settings)

    # --- Audit mode: query and exit, no scheduler or metrics needed ---
    if args.audit:
        return await run_audit_query(args, settings)

    inventory_path: Path = args.inventory
    if not inventory_path.exists():
        logger.error("Inventory file not found", path=str(inventory_path))
        return 1

    inventory = validate_inventory(inventory_path)

    # --- Shared components ---
    ssh_manager, executor, locker, slack, llm = _build_components(settings)

    dry_run = not args.live  # --live overrides --dry-run
    force = args.force
    force_reason = args.force_reason

    if force and not force_reason:
        logger.error("--force requires --force-reason")
        return 1

    # --- Early validation for --run-now (before binding ports) ---
    if args.run_now:
        env_name = args.env
        if not env_name:
            logger.error("--run-now requires --env <environment-name>")
            return 1
        if env_name not in inventory.environments:
            logger.error(
                "Environment not found in inventory",
                env=env_name,
                available=list(inventory.environments.keys()),
            )
            return 1
    else:
        env_name = None

    # --- Audit store ---
    audit_store = AuditStore(settings.audit_db_url)
    await audit_store.__aenter__()

    try:
        # --- Metrics server ---
        metrics_runner = await start_metrics_server(
            port=settings.metrics_port,
            audit_store=audit_store,
        )

        # --- --run-now mode: run once and exit ---
        if args.run_now:

            await run_env_batch(
                env_name=env_name,
                env_schema=inventory.environments[env_name],
                settings=settings,
                executor=executor,
                locker=locker,
                ssh_manager=ssh_manager,
                audit_store=audit_store,
                dry_run=dry_run,
                force=force,
                force_reason=force_reason,
            )
            return 0

        # --- Scheduler mode: run continuously ---
        scheduler_settings = validate_settings(args.config) if args.config.exists() else None
        scheduler = MaintenanceScheduler()

        for env_name, env_schema in inventory.environments.items():
            cron: str | None = None
            if scheduler_settings and env_name in scheduler_settings.schedules:
                cron = scheduler_settings.schedules[env_name].maintenance

            if not cron:
                logger.info("No schedule for environment — skipping", env=env_name)
                continue

            async def _run(
                _env=env_name,
                _schema=env_schema,
            ) -> None:
                await run_env_batch(
                    env_name=_env,
                    env_schema=_schema,
                    settings=settings,
                    executor=executor,
                    locker=locker,
                    ssh_manager=ssh_manager,
                    audit_store=audit_store,
                    dry_run=dry_run,
                )

            scheduler.add_maintenance_job(_run, cron, job_id=f"maint-{env_name}")

        await scheduler.start()

        logger.info(
            "AutoMaint agent running",
            jobs=scheduler.list_jobs(),
            metrics_port=settings.metrics_port,
            dry_run=dry_run,
        )

        # --- Graceful shutdown ---
        stop_event = asyncio.Event()

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info("Shutdown signal received", signal=sig.name)
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _handle_signal, sig)
            except (NotImplementedError, OSError):
                # Windows doesn't support add_signal_handler for all signals
                pass

        await stop_event.wait()

        logger.info("Shutting down scheduler...")
        await scheduler.stop()
        await metrics_runner.cleanup()
        return 0

    finally:
        await audit_store.__aexit__(None, None, None)


def main(argv: list[str] | None = None) -> None:
    """Synchronous entry point — parses args and runs the async main."""
    args = _parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    exit_code = asyncio.run(async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
