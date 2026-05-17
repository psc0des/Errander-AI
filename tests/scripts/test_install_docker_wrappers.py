"""Drift test: verify install-docker-wrappers.sh assess output matches parse_assess_output().

This test extracts the errander-docker-assess wrapper body from the install script and
verifies that its output format is parseable by parse_assess_output(). If the wrapper
output format changes without updating the parser, this test will break.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "install-docker-wrappers.sh"


def _extract_wrapper_body(script_text: str, wrapper_name: str) -> str:
    """Extract the body of a named wrapper from the install script's heredoc."""
    pattern = rf"errander-{wrapper_name}[^\n]*<<\s*'WRAPPER_EOF'\n(.*?)^WRAPPER_EOF"
    match = re.search(pattern, script_text, re.DOTALL | re.MULTILINE)
    if match is None:
        raise ValueError(f"Could not find wrapper body for errander-{wrapper_name}")
    return match.group(1)


class TestInstallScriptExists:
    def test_script_file_exists(self) -> None:
        assert SCRIPT_PATH.exists(), f"install-docker-wrappers.sh not found at {SCRIPT_PATH}"

    def test_script_is_executable_on_posix(self) -> None:
        # Just verify it's a valid bash script (header check)
        text = SCRIPT_PATH.read_text()
        assert text.startswith("#!/bin/bash")

    def test_script_contains_set_euo_pipefail(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "set -euo pipefail" in text

    def test_script_requires_root(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "EUID" in text

    def test_script_validates_sudoers_with_visudo(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "visudo -c" in text

    def test_script_is_idempotent_design(self) -> None:
        """Idempotency: install script uses cat > (overwrite) not tee -a (append)."""
        text = SCRIPT_PATH.read_text()
        assert "cat > /usr/local/sbin/errander-docker-assess" in text
        assert "cat > /usr/local/sbin/errander-docker-prune-safe" in text
        assert "cat > /usr/local/sbin/errander-docker-prune-aggressive" in text

    def test_script_sets_chmod_755(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "chmod 755" in text

    def test_script_sets_chmod_440_for_sudoers(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "chmod 440 /etc/sudoers.d/errander-docker" in text

    def test_script_final_message_mentions_check_targets(self) -> None:
        text = SCRIPT_PATH.read_text()
        assert "--check-targets" in text


class TestAssessWrapperBody:
    def test_assess_wrapper_body_extractable(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-assess")
        assert "#!/bin/bash" in body

    def test_assess_wrapper_check_flag_outputs_ok(self) -> None:
        """--check mode must print 'ok' and exit 0 — probed by run_check_targets."""
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-assess")
        assert '[ "${1:-}" = "--check" ]' in body
        assert 'echo "ok"' in body
        assert "exit 0" in body

    def test_assess_wrapper_output_parseable_by_parse_assess_output(self) -> None:
        """Core drift test: simulated assess output must parse without error."""
        from errander.agent.subgraphs.docker_prune import parse_assess_output

        simulated_output = (
            "reachable=yes\n"
            "dangling_images=3\n"
            "stopped_containers=1\n"
            "error=\n"
            "system_df_begin\n"
            "TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE\n"
            "Images          5         2         1.2GB     800MB (66%)\n"
            "system_df_end\n"
        )
        result = parse_assess_output(simulated_output)
        assert result["reachable"] is True
        assert result["dangling_images"] == 3
        assert result["stopped_containers"] == 1
        assert result["error"] is None
        assert "Images" in result["system_df"]

    def test_assess_wrapper_unreachable_output_parseable(self) -> None:
        """When docker daemon is not running, assess still produces parseable output."""
        from errander.agent.subgraphs.docker_prune import parse_assess_output

        simulated_output = (
            "reachable=no\n"
            "error=docker daemon not reachable\n"
        )
        result = parse_assess_output(simulated_output)
        assert result["reachable"] is False
        assert result["error"] == "docker daemon not reachable"

    def test_assess_wrapper_contains_required_output_fields(self) -> None:
        """All fields expected by parse_assess_output are emitted by the wrapper."""
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-assess")
        assert "reachable=yes" in body
        assert "dangling_images=" in body
        assert "stopped_containers=" in body
        assert "error=" in body
        assert "system_df_begin" in body
        assert "system_df_end" in body


class TestPruneWrapperBodies:
    def test_prune_safe_invokes_image_prune_and_container_prune(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-prune-safe")
        assert "docker image prune -f" in body
        assert "docker container prune -f" in body
        assert "docker system prune" not in body

    def test_prune_aggressive_invokes_system_prune_af(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-prune-aggressive")
        assert "docker system prune -af" in body

    def test_prune_safe_has_check_flag(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-prune-safe")
        assert '[ "${1:-}" = "--check" ]' in body

    def test_prune_aggressive_has_check_flag(self) -> None:
        text = SCRIPT_PATH.read_text()
        body = _extract_wrapper_body(text, "docker-prune-aggressive")
        assert '[ "${1:-}" = "--check" ]' in body
