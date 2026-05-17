"""Tests for parse_restart_output() — ensures wrapper output contract stays in sync."""

from __future__ import annotations

from errander.agent.subgraphs.service_restart import parse_restart_output
from errander.models.service_restart import RestartContext

_FULL_OUTPUT = (
    "pre_status_begin\n"
    "● nginx.service - A high performance web server\n"
    "   Loaded: loaded (/lib/systemd/system/nginx.service; enabled)\n"
    "   Active: active (running) since Mon 2026-05-17\n"
    "pre_status_end\n"
    "pre_journal_begin\n"
    "May 17 10:00:00 host nginx[1234]: Starting nginx\n"
    "pre_journal_end\n"
    "post_active_begin\n"
    "active\n"
    "post_active_end\n"
    "post_status_begin\n"
    "● nginx.service - A high performance web server\n"
    "   Active: active (running) since Mon 2026-05-17 10:00:05\n"
    "post_status_end\n"
    "post_journal_begin\n"
    "May 17 10:00:05 host nginx[5678]: Reloading nginx\n"
    "post_journal_end\n"
)

_SNAPSHOT_OUTPUT = (
    "pre_status_begin\n"
    "● nginx.service\n"
    "   Active: inactive (dead)\n"
    "pre_status_end\n"
    "pre_journal_begin\n"
    "May 17 09:00:00 host nginx[111]: Stopped nginx\n"
    "pre_journal_end\n"
)


class TestParseFullOutput:
    def test_returns_restart_context(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        assert isinstance(ctx, RestartContext)

    def test_pre_status_extracted(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        assert "nginx.service" in ctx.pre_status
        assert "active (running)" in ctx.pre_status

    def test_pre_journal_extracted(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        assert "Starting nginx" in ctx.pre_journal

    def test_post_active_extracted(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        assert "active" in ctx.post_active

    def test_post_status_extracted(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        assert "nginx.service" in ctx.post_status

    def test_post_journal_extracted(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        assert "Reloading nginx" in ctx.post_journal

    def test_no_markers_in_extracted_text(self) -> None:
        ctx = parse_restart_output(_FULL_OUTPUT)
        for field in (ctx.pre_status, ctx.pre_journal, ctx.post_active, ctx.post_status, ctx.post_journal):
            assert "begin" not in field
            assert "end" not in field


class TestParseSnapshotOutput:
    def test_pre_sections_populated(self) -> None:
        ctx = parse_restart_output(_SNAPSHOT_OUTPUT)
        assert "nginx.service" in ctx.pre_status
        assert "Stopped nginx" in ctx.pre_journal

    def test_post_sections_empty(self) -> None:
        ctx = parse_restart_output(_SNAPSHOT_OUTPUT)
        assert ctx.post_active == ""
        assert ctx.post_status == ""
        assert ctx.post_journal == ""


class TestParseMalformedOutput:
    def test_empty_string_returns_empty_context(self) -> None:
        ctx = parse_restart_output("")
        assert ctx.pre_status == ""
        assert ctx.pre_journal == ""
        assert ctx.post_active == ""
        assert ctx.post_status == ""
        assert ctx.post_journal == ""

    def test_no_markers_returns_empty_context(self) -> None:
        ctx = parse_restart_output("some garbage\nno markers here\n")
        assert ctx.pre_status == ""

    def test_unclosed_section_captures_until_eof(self) -> None:
        # begin without matching end — lines should still be captured
        ctx = parse_restart_output("pre_status_begin\nline1\nline2\n")
        assert "line1" in ctx.pre_status
        assert "line2" in ctx.pre_status

    def test_inactive_in_post_active_is_preserved(self) -> None:
        output = "post_active_begin\ninactive\npost_active_end\n"
        ctx = parse_restart_output(output)
        assert ctx.post_active == "inactive"
