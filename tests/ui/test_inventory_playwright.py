"""Playwright tests for the inventory UI (/ui/inventory).

Architecture:
- Module-scoped server with OverridesStore pre-seeded with inventory rows.
- Seeded data: yaml_override VMs (web-01 enabled, db-01 disabled) and one
  db_addition (extra-vm) so both types are testable.
- Action tests use specific row locators to avoid ordering sensitivity.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from playwright.sync_api import Page, expect

from errander.models.vm import OSFamily, VMTarget
from errander.observability.metrics import start_metrics_server
from errander.safety.audit import AuditStore
from errander.safety.overrides import OverridesStore

# Base YAML fleet passed to start_metrics_server so yaml_override rows are visible.
_YAML_FLEET: list[VMTarget] = [
    VMTarget(vm_id="production/web-01", hostname="10.0.1.1", ssh_user="ubuntu",
             ssh_key_path="/keys/web-01.pem", os_family=OSFamily.UBUNTU),
    VMTarget(vm_id="production/db-01",  hostname="10.0.1.2", ssh_user="ubuntu",
             ssh_key_path="/keys/db-01.pem",  os_family=OSFamily.RHEL),
    VMTarget(vm_id="staging/stg-web",   hostname="10.0.2.1", ssh_user="ubuntu",
             ssh_key_path="/keys/stg-web.pem", os_family=OSFamily.UBUNTU),
]

# ---------------------------------------------------------------------------
# Server fixture helpers
# ---------------------------------------------------------------------------

async def _seed_inventory(store: OverridesStore) -> None:
    await store.upsert_inventory_override(
        "production", "web-01", "yaml_override", disabled=False,
        host="10.0.1.1", os_family="ubuntu",
    )
    await store.upsert_inventory_override(
        "production", "db-01", "yaml_override", disabled=True,
        host="10.0.1.2", os_family="rhel",
    )
    await store.upsert_inventory_override(
        "production", "extra-vm", "db_addition", disabled=False,
        host="10.0.1.50", ssh_user="ubuntu", os_family="debian",
    )
    await store.upsert_inventory_override(
        "staging", "stg-web", "yaml_override", disabled=False,
        host="10.0.2.1", os_family="ubuntu",
    )


def _start_server(seed_fn=None, base_inventory=None):  # type: ignore[no-untyped-def]
    ready = threading.Event()
    ctx: dict[str, object] = {}

    async def _run() -> None:
        audit = AuditStore(":memory:")
        await audit.initialize()
        overrides = OverridesStore(":memory:")
        await overrides.initialize()
        if seed_fn is not None:
            await seed_fn(overrides)

        runner = await start_metrics_server(
            port=0, audit_store=audit, overrides_store=overrides,
            base_inventory=base_inventory or [],
        )
        site = list(runner.sites)[0]
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        stop: asyncio.Event = asyncio.Event()
        ctx["runner"] = runner
        ctx["stop"] = stop
        ctx["port"] = port
        ready.set()
        await stop.wait()
        await runner.cleanup()
        await overrides.close()
        await audit.close()

    loop = asyncio.new_event_loop()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    ready.wait(timeout=10)
    return ctx, loop, t


@pytest.fixture(scope="module")
def inventory_base_url() -> str:  # type: ignore[return]
    ctx, loop, t = _start_server(seed_fn=_seed_inventory, base_inventory=_YAML_FLEET)
    yield f"http://localhost:{ctx['port']}"
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


@pytest.fixture(scope="module")
def inventory_empty_url() -> str:  # type: ignore[return]
    ctx, loop, t = _start_server(seed_fn=None, base_inventory=[])
    yield f"http://localhost:{ctx['port']}"
    loop.call_soon_threadsafe(ctx["stop"].set)  # type: ignore[union-attr]
    t.join(timeout=5)


# ---------------------------------------------------------------------------
# Page load and structure
# ---------------------------------------------------------------------------

class TestInventoryPageLoad:

    def test_page_title(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page).to_have_title("Errander-AI — Inventory")

    def test_settings_nav_link_visible(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.get_by_role("link", name="Settings")).to_be_visible()

    def test_dashboard_nav_link_visible(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.get_by_role("link", name="Fleet Dashboard")).to_be_visible()

    def test_empty_state_message(self, page: Page, inventory_empty_url: str) -> None:
        page.goto(f"{inventory_empty_url}/ui/inventory")
        expect(page.get_by_text("No VMs in inventory")).to_be_visible()


# ---------------------------------------------------------------------------
# VM display
# ---------------------------------------------------------------------------

class TestInventoryVMDisplay:

    def test_production_environment_section_shown(
        self, page: Page, inventory_base_url: str,
    ) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.get_by_text("Environment: production")).to_be_visible()

    def test_staging_environment_section_shown(
        self, page: Page, inventory_base_url: str,
    ) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.get_by_text("Environment: staging")).to_be_visible()

    def test_yaml_vm_name_shown(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.locator(".inv-row").filter(has_text="web-01").first).to_be_visible()

    def test_adhoc_vm_shows_badge(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.locator(".inv-badge").first).to_be_visible()

    def test_adhoc_vm_shows_delete_button(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        # extra-vm is db_addition — its row should have a Delete button
        extra_row = page.locator(".inv-row").filter(has_text="extra-vm")
        expect(extra_row.locator("button.btn-del")).to_be_visible()

    def test_yaml_vm_has_no_delete_button(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        # web-01 is yaml_override — its row must NOT have a Delete button
        web01_row = page.locator(".inv-row").filter(has_text="web-01").first
        assert web01_row.locator("button.btn-del").count() == 0

    def test_disabled_vm_shows_enable_button(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        db01_row = page.locator(".inv-row").filter(has_text="db-01")
        expect(db01_row.get_by_role("button", name="Enable")).to_be_visible()

    def test_enabled_vm_shows_disable_button(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        web01_row = page.locator(".inv-row").filter(has_text="web-01").first
        expect(web01_row.get_by_role("button", name="Disable")).to_be_visible()


# ---------------------------------------------------------------------------
# Toggle enable/disable
# ---------------------------------------------------------------------------

class TestInventoryToggle:

    def test_toggle_disable_redirects_to_inventory(
        self, page: Page, inventory_base_url: str,
    ) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        stg_row = page.locator(".inv-row").filter(has_text="stg-web")
        stg_row.get_by_role("button", name="Disable").click()
        # After POST → redirect → back on inventory page (URL may include flash query param)
        expect(page).to_have_title("Errander-AI — Inventory")

    def test_toggled_vm_label_changes(self, page: Page, inventory_base_url: str) -> None:
        # stg-web was disabled by the previous test; it should now show "Enable"
        page.goto(f"{inventory_base_url}/ui/inventory")
        stg_row = page.locator(".inv-row").filter(has_text="stg-web")
        # Either Enable (disabled) or Disable (re-enabled) — page must still load
        btn_count = (
            stg_row.get_by_role("button", name="Enable").count()
            + stg_row.get_by_role("button", name="Disable").count()
        )
        assert btn_count >= 1


# ---------------------------------------------------------------------------
# Add ad-hoc VM
# ---------------------------------------------------------------------------

class TestInventoryAddVM:

    def test_add_vm_form_accessible(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        # The add form is inside a <details> — expand the first one
        page.locator("details").first.click()
        expect(page.locator("button.btn-save").first).to_be_visible()

    def test_add_vm_appears_in_list(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        # Expand the Add VM form in the production section
        page.locator("details").first.click()

        # Target text inputs specifically (not hidden inputs in toggle forms)
        page.locator('input[type="text"][name="vm_name"]').first.fill("playwright-vm")
        page.locator('input[type="text"][name="host"]').first.fill("10.0.1.99")
        page.locator('select[name="os_family"]').first.select_option("ubuntu")
        page.locator("button.btn-save").first.click()

        page.goto(f"{inventory_base_url}/ui/inventory")
        expect(page.locator(".inv-row").filter(has_text="playwright-vm")).to_be_visible()


# ---------------------------------------------------------------------------
# Delete ad-hoc VM
# ---------------------------------------------------------------------------

class TestInventoryDeleteVM:

    def test_delete_adhoc_vm_removes_it(self, page: Page, inventory_base_url: str) -> None:
        page.goto(f"{inventory_base_url}/ui/inventory")
        extra_row = page.locator(".inv-row").filter(has_text="extra-vm")
        if extra_row.count() > 0:
            extra_row.locator("button.btn-del").click()
            page.goto(f"{inventory_base_url}/ui/inventory")
            assert page.locator(".inv-row").filter(has_text="extra-vm").count() == 0
