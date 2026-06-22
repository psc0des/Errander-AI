"""Entry point for the Errander-AI web UI.

Long-lived process that serves the operator-facing web UI (RBAC login,
approvals, settings, inventory, monitoring) plus this process's own
/metrics + /health. Runs under its own OS user (``errander-web``), with
no SSH keys and no code path to the executor — see docs/SECURITY.md.

Usage:
    uv run python -m errander.web [options]
    uv run python -m errander.web --port 9091 --bind 0.0.0.0 --public-mode
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from errander.config.inventory import load_inventory
from errander.config.settings import load_settings
from errander.db.core import AsyncDatabase
from errander.models.events import AuditEvent, EventType
from errander.safety.ai_audit import AIDecisionStore
from errander.safety.approval_store import ApprovalRequestStore
from errander.safety.audit import AuditStore
from errander.safety.hygiene_store import HygieneApprovalStore
from errander.safety.overrides import OverridesStore
from errander.safety.user_store import SessionStore, UserStore
from errander.web.ui import start_web_server

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="errander-web",
        description="Errander-AI web UI — operator approvals, settings, monitoring",
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
        help="Path to inventory.yaml — shown read-only on /ui/inventory (default: inventory.yaml)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: $ERRANDER_WEB_PORT or 9091)",
    )
    parser.add_argument(
        "--bind",
        default=None,
        help="Bind address (default: $ERRANDER_UI_BIND or 127.0.0.1)",
    )
    parser.add_argument(
        "--public-mode",
        action="store_true",
        help="Enable public-mode hardening (mandatory TOTP for the admin group)",
    )
    return parser.parse_args(argv)


async def _seed_admin_from_env(
    user_store: UserStore, audit_store: AuditStore, *, username: str, password: str
) -> None:
    """One-time migration: seed the legacy shared credential as the initial
    admin account when no users exist yet (audited)."""
    await user_store.create_user(username, password, groups=["admin"], actor="migration:env")
    await audit_store.log_event(AuditEvent(
        event_type=EventType.USER_CREATED,
        batch_id="user-management",
        detail=(
            f"user={username} groups=admin by=migration:env "
            "(seeded from ERRANDER_UI_USER/ERRANDER_UI_PASSWORD)"
        ),
    ))
    logger.info("Seeded initial admin user from ERRANDER_UI_USER: %s", username)


async def main(argv: list[str] | None = None) -> int:
    """Run the web UI process until SIGTERM/SIGINT."""
    args = _parse_args(argv)
    settings = load_settings(settings_path=args.config if args.config.exists() else None)

    port = args.port if args.port is not None else settings.web_port
    bind_address = args.bind if args.bind is not None else settings.ui_bind_address
    public_mode = args.public_mode or settings.public_mode

    base_inventory = []
    if args.inventory.exists():
        base_inventory = load_inventory(args.inventory)
    else:
        logger.warning("Inventory file not found: %s — /ui/inventory will be empty", args.inventory)

    _db = AsyncDatabase(settings.audit_db_url)

    audit_store = AuditStore(_db, strict_mode=(settings.audit_mode == "strict"))
    await audit_store.__aenter__()  # runs migrations

    approval_store = ApprovalRequestStore(_db)
    await approval_store.initialize()

    hygiene_store = HygieneApprovalStore(_db)
    await hygiene_store.initialize()

    overrides_store = OverridesStore(_db)
    await overrides_store.initialize()

    user_store = UserStore(_db)
    await user_store.initialize()

    session_store = SessionStore(_db, user_store)
    await session_store.purge_expired()

    ai_decision_store = AIDecisionStore(_db)
    await ai_decision_store.initialize()

    if (
        settings.ui_user and settings.ui_password
        and await user_store.count_users() == 0
    ):
        await _seed_admin_from_env(
            user_store, audit_store, username=settings.ui_user, password=settings.ui_password,
        )

    signing_secret = os.environ.get("ERRANDER_SIGNING_SECRET")

    try:
        runner = await start_web_server(
            port=port,
            bind_address=bind_address,
            audit_store=audit_store,
            approval_store=approval_store,
            hygiene_store=hygiene_store,
            overrides_store=overrides_store,
            base_inventory=base_inventory,
            user_store=user_store,
            session_store=session_store,
            ai_decision_store=ai_decision_store,
            signing_secret=signing_secret,
            public_mode=public_mode,
        )
    except RuntimeError as exc:
        logger.error("Cannot start web UI: %s", exc)
        await audit_store.__aexit__(None, None, None)
        return 1

    logger.info(
        "Errander-AI web UI running on %s:%d (public_mode=%s)",
        bind_address, port, public_mode,
    )

    stop_event = asyncio.Event()

    def _handle_signal(sig: signal.Signals) -> None:
        logger.info("Shutdown signal received: %s", sig.name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows doesn't support add_signal_handler for all signals
        with contextlib.suppress(NotImplementedError, OSError):
            loop.add_signal_handler(sig, _handle_signal, sig)

    await stop_event.wait()

    logger.info("Shutting down web UI...")
    await runner.cleanup()
    await audit_store.__aexit__(None, None, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
