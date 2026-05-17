"""Drift test: verify install-systemctl-restart-wrapper.sh wrapper body matches parse_restart_output().

Extracts the errander-systemctl-restart wrapper body from the install script and verifies
that its output format is parseable by parse_restart_output(). If the wrapper output format
changes without updating the parser, this test will break.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent / "scripts" / "install-systemctl-restart-wrapper.sh"
)


def _extract_wrapper_body(script_text: str) -> str:
    """Extract the wrapper body from the install script's heredoc."""
    pattern = r"errander-systemctl-restart[^\n]*<<\s*'WRAPPER_EOF'\n(.*?)^WRAPPER_EOF"
    match = re.search(pattern, script_text, re.DOTALL | re.MULTILINE)
    if match is None:
        raise ValueError("Could not find wrapper body for errander-systemctl-restart")
    return match.group(1)


class TestInstallScriptExists:
    def test_script_file_exists(self) -> None:
        assert SCRIPT_PATH.exists(), f"install-systemctl-restart-wrapper.sh not found at {SCRIPT_PATH}"

    def test_script_starts_with_bash_shebang(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert text.startswith("#!/bin/bash")

    def test_script_contains_set_euo_pipefail(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "set -euo pipefail" in text

    def test_script_requires_root(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "EUID" in text

    def test_script_requires_at_least_one_unit_argument(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert '$#' in text

    def test_script_validates_sudoers_with_visudo(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "visudo -c" in text

    def test_script_is_idempotent_design(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "cat > /usr/local/sbin/errander-systemctl-restart" in text

    def test_script_sets_chmod_755_on_wrapper(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "chmod 755 /usr/local/sbin/errander-systemctl-restart" in text

    def test_script_sets_chmod_440_for_sudoers(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "chmod 440 /etc/sudoers.d/errander-systemctl-restart" in text

    def test_script_creates_allowlist_from_args(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "/etc/errander/restart-allowlist" in text
        assert "printf" in text or '> /etc/errander/restart-allowlist' in text

    def test_script_final_message_mentions_check_targets(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "--check-targets" in text


class TestWrapperBody:
    def test_wrapper_body_extractable(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "#!/bin/bash" in body

    def test_wrapper_check_flag_outputs_ok(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert '[ "${1:-}" = "--check" ]' in body
        assert 'echo "ok"' in body
        assert "exit 0" in body

    def test_wrapper_snapshot_only_flag_exists(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert '"--snapshot-only"' in body

    def test_wrapper_snapshot_only_emits_pre_sections(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "pre_status_begin" in body
        assert "pre_status_end" in body
        assert "pre_journal_begin" in body
        assert "pre_journal_end" in body

    def test_wrapper_full_restart_emits_post_sections(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "post_active_begin" in body
        assert "post_active_end" in body
        assert "post_status_begin" in body
        assert "post_status_end" in body
        assert "post_journal_begin" in body
        assert "post_journal_end" in body

    def test_wrapper_enforces_allowlist_with_exit_code_4(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "grep -qFx" in body
        assert "exit 4" in body

    def test_wrapper_reads_allowlist_file(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "/etc/errander/restart-allowlist" in body

    def test_wrapper_exits_3_when_allowlist_not_readable(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "exit 3" in body

    def test_wrapper_exits_2_when_no_unit_specified(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "exit 2" in body

    def test_wrapper_uses_systemctl_restart(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text)
        assert "/bin/systemctl restart" in body

    def test_wrapper_output_parseable_by_parse_restart_output(self) -> None:
        """Core drift test: simulated full restart output must parse without error."""
        from errander.agent.subgraphs.service_restart import parse_restart_output

        simulated_output = (
            "pre_status_begin\n"
            "● nginx.service - nginx web server\n"
            "   Active: active (running)\n"
            "pre_status_end\n"
            "pre_journal_begin\n"
            "May 17 10:00:00 host nginx[1]: started\n"
            "pre_journal_end\n"
            "post_active_begin\n"
            "active\n"
            "post_active_end\n"
            "post_status_begin\n"
            "● nginx.service - nginx web server\n"
            "   Active: active (running)\n"
            "post_status_end\n"
            "post_journal_begin\n"
            "May 17 10:00:05 host nginx[2]: reloaded\n"
            "post_journal_end\n"
        )
        ctx = parse_restart_output(simulated_output)
        assert "nginx.service" in ctx.pre_status
        assert "started" in ctx.pre_journal
        assert "active" in ctx.post_active
        assert "nginx.service" in ctx.post_status
        assert "reloaded" in ctx.post_journal

    def test_snapshot_output_parseable_by_parse_restart_output(self) -> None:
        """Snapshot mode output (pre sections only) must parse correctly."""
        from errander.agent.subgraphs.service_restart import parse_restart_output

        simulated_snapshot = (
            "pre_status_begin\n"
            "● nginx.service - nginx\n"
            "   Active: active (running)\n"
            "pre_status_end\n"
            "pre_journal_begin\n"
            "May 17 09:00:00 host nginx[1]: started\n"
            "pre_journal_end\n"
        )
        ctx = parse_restart_output(simulated_snapshot)
        assert "nginx.service" in ctx.pre_status
        assert "started" in ctx.pre_journal
        assert ctx.post_active == ""
        assert ctx.post_status == ""
        assert ctx.post_journal == ""
