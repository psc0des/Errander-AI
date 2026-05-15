"""Tests for the P0-1 updated _format_plan_for_approval() message."""

from __future__ import annotations

from errander.agent.graph import _format_plan_for_approval


def _make_vm_plan(
    vm_id: str = "vm-01",
    action_type: str = "patching",
    preview: dict | None = None,
    params: dict | None = None,
) -> dict:
    return {
        "vm_id": vm_id,
        "planned_actions": [
            {
                "action_type": action_type,
                "risk_tier": "medium",
                "params": params or {},
                "preview": preview or {},
            }
        ],
    }


def _patching_preview(packages: list[dict] | None = None, error: str | None = None) -> dict:
    if error:
        return {"error": error}
    pkgs = packages or [
        {"name": "nginx", "current": "1.18.0-0ubuntu1", "target": "1.24.0-1ubuntu1"},
        {"name": "openssl", "current": "1.1.1f-1ubuntu2.20", "target": "1.1.1f-1ubuntu2.21"},
    ]
    return {"packages": pkgs, "package_count": len(pkgs)}


# ---------------------------------------------------------------------------
# Exact packages shown
# ---------------------------------------------------------------------------


def test_approval_message_shows_package_names() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "nginx" in msg
    assert "openssl" in msg


def test_approval_message_shows_current_to_target_arrow() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "1.18.0-0ubuntu1" in msg
    assert "1.24.0-1ubuntu1" in msg
    assert "->" in msg


def test_approval_message_shows_package_count() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "2 package(s)" in msg


# ---------------------------------------------------------------------------
# No disclaimer
# ---------------------------------------------------------------------------


def test_approval_message_no_categories_disclaimer() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "You are approving action categories" not in msg
    assert "not exact pinned" not in msg


def test_approval_message_shows_hash_commitment_line() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "commits to the exact packages" in msg


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_approval_message_preview_unavailable_shown() -> None:
    plan = _make_vm_plan(preview=_patching_preview(error="SSH timeout"))
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "preview unavailable" in msg
    assert "SSH timeout" in msg


def test_approval_message_patching_no_preview_shows_action_type() -> None:
    """Empty preview dict → show "patching" without crashing."""
    plan = _make_vm_plan(preview={})
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "patching" in msg


def test_approval_message_caps_packages_at_10() -> None:
    packages = [
        {"name": f"pkg-{i}", "current": "1.0", "target": "2.0"}
        for i in range(15)
    ]
    plan = _make_vm_plan(preview={"packages": packages, "package_count": 15})
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "and 5 more" in msg
    # Only 10 package lines rendered
    rendered_pkg_lines = [l for l in msg.splitlines() if "`pkg-" in l]
    assert len(rendered_pkg_lines) == 10


# ---------------------------------------------------------------------------
# Disk cleanup preview
# ---------------------------------------------------------------------------


def test_approval_message_disk_cleanup_with_preview() -> None:
    plan = _make_vm_plan(
        action_type="disk_cleanup",
        preview={"disk_pct": 78, "apt_cache_mb": 450},
    )
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "disk_cleanup" in msg
    assert "78%" in msg
    assert "450MB" in msg


def test_approval_message_disk_cleanup_no_preview() -> None:
    plan = _make_vm_plan(action_type="disk_cleanup", preview={})
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "disk_cleanup" in msg


# ---------------------------------------------------------------------------
# Other action types still shown
# ---------------------------------------------------------------------------


def test_approval_message_log_rotation_shows_action() -> None:
    plan = _make_vm_plan(action_type="log_rotation", preview={})
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64)
    assert "log_rotation" in msg


def test_approval_message_deferred_reapproval_header() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", "a" * 64, is_deferred_reapproval=True)
    assert "Deferred Re-Approval" in msg


# ---------------------------------------------------------------------------
# Hash uses 16 chars
# ---------------------------------------------------------------------------


def test_approval_message_hash_truncated_to_16_chars() -> None:
    plan = _make_vm_plan(preview=_patching_preview())
    full_hash = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    msg = _format_plan_for_approval([plan], "b1", "plan-abc", full_hash)
    assert "abcdef1234567890" in msg
    # Should not show the full 64-char hash
    assert full_hash not in msg
