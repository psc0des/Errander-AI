"""Tests for errander.config._prompts — shared interactive prompt helpers.

All tests mock builtins.input so no actual terminal interaction is needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from errander.config._prompts import (
    _DAY_ALIASES,
    _MW_RE,
    _NAME_RE,
    prompt_maintenance_days,
    prompt_maintenance_window,
    prompt_name,
    prompt_os_family,
    prompt_policy,
    prompt_systemd_units,
    prompt_timezone,
    prompt_val,
    prompt_val_optional,
    prompt_yn,
)

# ── prompt_val ─────────────────────────────────────────────────────────────────


class TestPromptVal:
    def test_returns_input_when_given(self) -> None:
        with patch("builtins.input", return_value="myvalue"):
            assert prompt_val("Label") == "myvalue"

    def test_uses_default_on_empty_input(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_val("Label", default="thedefault") == "thedefault"

    def test_returns_explicit_input_over_default(self) -> None:
        with patch("builtins.input", return_value="override"):
            assert prompt_val("Label", default="thedefault") == "override"

    def test_re_prompts_until_non_empty_when_no_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["", "  ", "actual"]):
            result = prompt_val("Label")
        assert result == "actual"

    def test_strips_whitespace(self) -> None:
        with patch("builtins.input", return_value="  padded  "):
            assert prompt_val("Label") == "padded"

    def test_indent_parameter_affects_prompt_string(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", return_value="x") as mock_input:
            prompt_val("Label", indent=6)
        assert mock_input.call_args[0][0].startswith("      ")  # 6 spaces


# ── prompt_val_optional ────────────────────────────────────────────────────────


class TestPromptValOptional:
    def test_returns_empty_string_on_empty_input(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_val_optional("Label") == ""

    def test_returns_value_when_given(self) -> None:
        with patch("builtins.input", return_value="something"):
            assert prompt_val_optional("Label") == "something"

    def test_hint_appears_in_prompt(self) -> None:
        with patch("builtins.input", return_value="") as mock_input:
            prompt_val_optional("Label", hint="e.g. web,prod")
        assert "e.g. web,prod" in mock_input.call_args[0][0]


# ── prompt_yn ─────────────────────────────────────────────────────────────────


class TestPromptYn:
    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", "Yes"])
    def test_accepts_yes_variants(self, answer: str) -> None:
        with patch("builtins.input", return_value=answer):
            assert prompt_yn("Question?") is True

    @pytest.mark.parametrize("answer", ["n", "N", "no", "NO", "No"])
    def test_accepts_no_variants(self, answer: str) -> None:
        with patch("builtins.input", return_value=answer):
            assert prompt_yn("Question?") is False

    def test_empty_returns_default_true(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_yn("Question?", default=True) is True

    def test_empty_returns_default_false(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_yn("Question?", default=False) is False

    def test_eof_returns_default(self) -> None:
        with patch("builtins.input", side_effect=EOFError):
            assert prompt_yn("Question?", default=True) is True

    def test_invalid_input_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["wrong", "maybe", "y"]):
            result = prompt_yn("Question?")
        assert result is True
        out = capsys.readouterr().out
        assert "Please enter y or n" in out

    def test_invalid_input_does_not_default_to_yes(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["wrong", "n"]):
            result = prompt_yn("Question?", default=True)
        assert result is False


# ── prompt_policy ──────────────────────────────────────────────────────────────


class TestPromptPolicy:
    def test_choice_1_returns_strict(self) -> None:
        with patch("builtins.input", return_value="1"):
            assert prompt_policy() == "strict"

    def test_empty_returns_strict(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_policy() == "strict"

    def test_choice_2_returns_moderate(self) -> None:
        with patch("builtins.input", return_value="2"):
            assert prompt_policy() == "moderate"

    def test_choice_3_returns_relaxed(self) -> None:
        with patch("builtins.input", return_value="3"):
            assert prompt_policy() == "relaxed"

    def test_invalid_choice_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["4", "strict", "wrong", "1"]):
            result = prompt_policy()
        assert result == "strict"
        out = capsys.readouterr().out
        assert "Please enter 1, 2, or 3" in out


# ── prompt_maintenance_window ──────────────────────────────────────────────────


class TestPromptMaintenanceWindow:
    @pytest.mark.parametrize("val", ["08:00-20:00", "00:00-23:59", "22:30-06:00", "02:00-06:00"])
    def test_valid_windows_accepted(self, val: str) -> None:
        with patch("builtins.input", return_value=val):
            assert prompt_maintenance_window("08:00-20:00") == val

    def test_empty_input_returns_default(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_maintenance_window("08:00-20:00") == "08:00-20:00"

    @pytest.mark.parametrize("bad", ["wrong", "25:00-20:00", "8:00-20:00", "08:00", "08:00-20:60"])
    def test_invalid_format_re_prompts(self, bad: str, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=[bad, "08:00-20:00"]):
            result = prompt_maintenance_window("08:00-20:00")
        assert result == "08:00-20:00"
        out = capsys.readouterr().out
        assert "expected HH:MM-HH:MM" in out


# ── prompt_maintenance_days ────────────────────────────────────────────────────


class TestPromptMaintenanceDays:
    def test_valid_full_names_accepted(self) -> None:
        with patch("builtins.input", return_value="monday,wednesday,friday"):
            assert prompt_maintenance_days("monday") == ["monday", "wednesday", "friday"]

    def test_short_aliases_resolved(self) -> None:
        with patch("builtins.input", return_value="mon,wed,fri"):
            assert prompt_maintenance_days("monday") == ["monday", "wednesday", "friday"]

    def test_empty_input_uses_default(self) -> None:
        with patch("builtins.input", return_value=""):
            result = prompt_maintenance_days("tuesday,thursday")
        assert result == ["tuesday", "thursday"]

    def test_unknown_day_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["monday,xyz,friday", "monday,friday"]):
            result = prompt_maintenance_days("monday")
        assert result == ["monday", "friday"]
        out = capsys.readouterr().out
        assert "Unknown day" in out
        assert "xyz" in out

    def test_all_seven_days_accepted(self) -> None:
        all_days = "monday,tuesday,wednesday,thursday,friday,saturday,sunday"
        with patch("builtins.input", return_value=all_days):
            result = prompt_maintenance_days("monday")
        assert len(result) == 7

    def test_case_insensitive(self) -> None:
        with patch("builtins.input", return_value="MONDAY,FRIDAY"):
            result = prompt_maintenance_days("monday")
        assert result == ["monday", "friday"]


# ── prompt_timezone ────────────────────────────────────────────────────────────


class TestPromptTimezone:
    def test_utc_accepted(self) -> None:
        with patch("builtins.input", return_value="UTC"):
            assert prompt_timezone() == "UTC"

    def test_empty_returns_default(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_timezone(default="UTC") == "UTC"

    def test_known_timezone_accepted(self) -> None:
        with patch("builtins.input", return_value="America/New_York"):
            assert prompt_timezone() == "America/New_York"

    def test_invalid_timezone_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["Not/ATimezone", "UTC"]):
            result = prompt_timezone()
        assert result == "UTC"
        out = capsys.readouterr().out
        assert "not a recognised IANA timezone" in out

    def test_falls_back_gracefully_when_tzdata_unavailable(self) -> None:
        with (
            patch("zoneinfo.available_timezones", side_effect=Exception("no tzdata")),
            patch("builtins.input", return_value="Anything/Goes"),
        ):
            result = prompt_timezone()
        assert result == "Anything/Goes"


# ── prompt_os_family ───────────────────────────────────────────────────────────


class TestPromptOsFamily:
    def test_choice_1_returns_ubuntu(self) -> None:
        with patch("builtins.input", return_value="1"):
            assert prompt_os_family() == "ubuntu"

    def test_empty_returns_ubuntu(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_os_family() == "ubuntu"

    def test_choice_2_returns_debian(self) -> None:
        with patch("builtins.input", return_value="2"):
            assert prompt_os_family() == "debian"

    def test_choice_3_returns_rhel(self) -> None:
        with patch("builtins.input", return_value="3"):
            assert prompt_os_family() == "rhel"

    def test_invalid_choice_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["ubuntu", "4", "D", "2"]):
            result = prompt_os_family()
        assert result == "debian"
        out = capsys.readouterr().out
        assert "Please enter 1, 2, or 3" in out

    def test_no_silent_default_on_arbitrary_input(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Entering 'ubuntu' must not silently become ubuntu — it must re-prompt."""
        with patch("builtins.input", side_effect=["ubuntu", "1"]):
            result = prompt_os_family()
        assert result == "ubuntu"
        out = capsys.readouterr().out
        assert "Please enter 1, 2, or 3" in out


# ── prompt_name ────────────────────────────────────────────────────────────────


class TestPromptName:
    def test_valid_identifier_accepted(self) -> None:
        with patch("builtins.input", return_value="production"):
            assert prompt_name("Env name") == "production"

    def test_hyphens_and_underscores_accepted(self) -> None:
        with patch("builtins.input", return_value="prod-web_01"):
            assert prompt_name("Name") == "prod-web_01"

    def test_spaces_rejected_and_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["my env", "my-env"]):
            result = prompt_name("Env name")
        assert result == "my-env"
        out = capsys.readouterr().out
        assert "no spaces" in out

    def test_special_chars_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["prod:env", "prod-env"]):
            result = prompt_name("Name")
        assert result == "prod-env"
        out = capsys.readouterr().out
        assert "no spaces" in out

    def test_default_used_on_empty_input(self) -> None:
        with patch("builtins.input", return_value=""):
            assert prompt_name("Name", default="production") == "production"

    def test_digits_only_after_first_char_accepted(self) -> None:
        with patch("builtins.input", return_value="env-01"):
            assert prompt_name("Name") == "env-01"


# ── prompt_systemd_units ───────────────────────────────────────────────────────


class TestPromptSystemdUnits:
    def test_valid_units_accepted(self) -> None:
        with patch("builtins.input", return_value="nginx.service postgresql.service"):
            result = prompt_systemd_units()
        assert result == ["nginx.service", "postgresql.service"]

    def test_comma_separated_also_accepted(self) -> None:
        with patch("builtins.input", return_value="nginx.service,docker.service"):
            result = prompt_systemd_units()
        assert result == ["nginx.service", "docker.service"]

    def test_missing_suffix_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["docker", "docker.service"]):
            result = prompt_systemd_units()
        assert result == ["docker.service"]
        out = capsys.readouterr().out
        assert "type suffix" in out

    def test_empty_input_re_prompts(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("builtins.input", side_effect=["", "nginx.service"]):
            result = prompt_systemd_units()
        assert result == ["nginx.service"]
        out = capsys.readouterr().out
        assert "at least one unit name required" in out

    def test_mixed_valid_invalid_re_prompts_whole_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        """If any unit in the line is invalid, the whole line is rejected."""
        with patch("builtins.input", side_effect=["nginx.service docker", "nginx.service docker.service"]):
            result = prompt_systemd_units()
        assert result == ["nginx.service", "docker.service"]
        out = capsys.readouterr().out
        assert "Invalid unit name" in out

    def test_various_valid_unit_types_accepted(self) -> None:
        with patch("builtins.input", return_value="cron.timer sshd.service cups.socket"):
            result = prompt_systemd_units()
        assert result == ["cron.timer", "sshd.service", "cups.socket"]


# ── Regex constants ────────────────────────────────────────────────────────────


class TestRegexConstants:
    @pytest.mark.parametrize("val", ["08:00-20:00", "00:00-23:59", "22:30-06:00"])
    def test_mw_re_valid(self, val: str) -> None:
        assert _MW_RE.match(val)

    @pytest.mark.parametrize("val", ["8:00-20:00", "25:00-20:00", "08:00-20:60", "wrong", "08:00"])
    def test_mw_re_invalid(self, val: str) -> None:
        assert not _MW_RE.match(val)

    @pytest.mark.parametrize("val", ["production", "prod-web", "env_01", "a", "A1"])
    def test_name_re_valid(self, val: str) -> None:
        assert _NAME_RE.match(val)

    @pytest.mark.parametrize("val", ["my env", "prod:env", "-bad", "", "env name"])
    def test_name_re_invalid(self, val: str) -> None:
        assert not _NAME_RE.match(val)

    def test_day_aliases_cover_full_and_short_names(self) -> None:
        for full in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
            assert _DAY_ALIASES[full] == full
        for short, full in [("mon", "monday"), ("tue", "tuesday"), ("wed", "wednesday"),
                             ("thu", "thursday"), ("fri", "friday"), ("sat", "saturday"), ("sun", "sunday")]:
            assert _DAY_ALIASES[short] == full
