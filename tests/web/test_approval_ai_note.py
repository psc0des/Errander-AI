"""Tests for the AI advisory planning-note rendering in the approval UI (R1)."""

from __future__ import annotations

from errander.web.ui import _render_approval_plan


def test_ai_note_rendered_when_present() -> None:
    plan: dict[str, object] = {
        "vm_id": "vm-01",
        "planned_actions": [],
        "ai_note": "Disk trending up.",
    }
    html = _render_approval_plan([plan], "b1")
    assert "AI analysis — informational only" in html
    assert "Disk trending up." in html


def test_ai_note_absent_when_not_set() -> None:
    plan: dict[str, object] = {"vm_id": "vm-01", "planned_actions": []}
    html = _render_approval_plan([plan], "b1")
    assert "apv-ai-note" not in html


def test_ai_note_html_escaped() -> None:
    plan: dict[str, object] = {
        "vm_id": "vm-01",
        "planned_actions": [],
        "ai_note": "<script>alert(1)</script>",
    }
    html = _render_approval_plan([plan], "b1")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
