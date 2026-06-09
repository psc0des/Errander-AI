"""Shared interactive prompt helpers for inventory_wizard and add_target.

Every constrained input validates at the prompt and re-prompts on bad input —
the user never reaches schema validation with garbage data.
"""

from __future__ import annotations

import re
import zoneinfo

from errander.execution.command_builder import CommandBuildError, safe_systemd_unit_name

_MW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")

_DAY_ALIASES: dict[str, str] = {
    "mon": "monday", "tue": "tuesday", "wed": "wednesday",
    "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday",
    "monday": "monday", "tuesday": "tuesday", "wednesday": "wednesday",
    "thursday": "thursday", "friday": "friday", "saturday": "saturday", "sunday": "sunday",
}


def prompt_val(label: str, default: str = "", indent: int = 4) -> str:
    """Prompt for a required value; re-prompts if empty and no default is given."""
    pad = " " * indent
    while True:
        if default:
            raw = input(f"{pad}{label} [{default}]: ").strip()
            return raw if raw else default
        raw = input(f"{pad}{label}: ").strip()
        if raw:
            return raw
        print(f"{pad}(required — cannot be empty)")


def prompt_val_optional(label: str, hint: str = "", indent: int = 4) -> str:
    """Prompt for an optional value; returns empty string if skipped."""
    pad = " " * indent
    suffix = f"  e.g. {hint}" if hint else ""
    return input(f"{pad}{label} (optional{suffix}): ").strip()


def prompt_yn(question: str, default: bool = True, indent: int = 4) -> bool:
    """Prompt for a yes/no answer; only accepts y/yes/n/no (case-insensitive) or Enter."""
    pad = " " * indent
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            raw = input(f"{pad}{question} {hint} ").strip().lower()
        except EOFError:
            return default
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(f"{pad}Please enter y or n.")


def prompt_policy(indent: int = 4) -> str:
    """Numbered menu for approval policy; re-prompts on invalid choice."""
    pad = " " * indent
    print()
    print(f"{pad}Approval policy:")
    print(f"{pad}  1) strict   — all actions require explicit human approval (recommended)")
    print(f"{pad}  2) moderate — patching + Docker need approval; cleanup is auto-approved")
    print(f"{pad}  3) relaxed  — most non-destructive actions auto-approved")
    print()
    while True:
        raw = input(f"{pad}Choice [1/3, Enter=1]: ").strip()
        if raw in ("", "1"):
            return "strict"
        if raw == "2":
            return "moderate"
        if raw == "3":
            return "relaxed"
        print(f"{pad}✗  Please enter 1, 2, or 3.")


def prompt_maintenance_window(default: str, indent: int = 4) -> str:
    """Prompt for a HH:MM-HH:MM window string; re-prompts if format is invalid."""
    pad = " " * indent
    while True:
        raw = input(f"{pad}Maintenance window (HH:MM-HH:MM) [{default}]: ").strip()
        val = raw if raw else default
        if _MW_RE.match(val):
            return val
        print(f"{pad}✗  {val!r} — expected HH:MM-HH:MM (e.g. 08:00-20:00)")


def prompt_maintenance_days(default: str, indent: int = 4) -> list[str]:
    """Prompt for comma-separated day names; rejects unknown values and re-prompts."""
    pad = " " * indent
    while True:
        raw = input(f"{pad}Maintenance days (comma-separated) [{default}]: ").strip()
        source = raw if raw else default
        parsed = [t.strip().lower() for t in source.split(",") if t.strip()]
        invalid = [d for d in parsed if d not in _DAY_ALIASES]
        if invalid:
            print(f"{pad}✗  Unknown day(s): {', '.join(invalid)}")
            print(f"{pad}   Valid: monday tuesday wednesday thursday friday saturday sunday")
            continue
        return [_DAY_ALIASES[d] for d in parsed]


def prompt_timezone(default: str = "UTC", indent: int = 4) -> str:
    """Prompt for an IANA timezone name; re-prompts if not recognised.

    Falls back to accepting any value if the tzdata set is unavailable (e.g. bare
    Windows without tzdata package installed — not a production scenario).
    """
    pad = " " * indent
    try:
        valid = zoneinfo.available_timezones()
    except Exception:
        valid = set()
    while True:
        raw = input(f"{pad}Maintenance timezone [{default}]: ").strip()
        val = raw if raw else default
        if not valid or val in valid:
            return val
        print(f"{pad}✗  {val!r} is not a recognised IANA timezone.")
        print(f"{pad}   Examples: UTC, America/New_York, Europe/London, Asia/Kolkata")


def prompt_os_family(indent: int = 4) -> str:
    """Numbered menu for OS family; re-prompts on invalid choice."""
    pad = " " * indent
    print()
    print(f"{pad}OS family:")
    print(f"{pad}  1) ubuntu  (Ubuntu / Ubuntu-derived)  (default)")
    print(f"{pad}  2) debian  (Debian)")
    print(f"{pad}  3) rhel    (RHEL / CentOS / Rocky / AlmaLinux)")
    while True:
        raw = input(f"{pad}Choice [1/3, Enter=1]: ").strip()
        if raw in ("", "1"):
            return "ubuntu"
        if raw == "2":
            return "debian"
        if raw == "3":
            return "rhel"
        print(f"{pad}✗  Please enter 1, 2, or 3.")


def prompt_name(label: str, default: str = "", indent: int = 4) -> str:
    """Prompt for a YAML-key-safe identifier (letters, digits, hyphens, underscores)."""
    pad = " " * indent
    while True:
        val = prompt_val(label, default=default, indent=indent)
        if _NAME_RE.match(val):
            return val
        print(f"{pad}✗  {val!r} — use letters, digits, hyphens, or underscores (no spaces)")


def prompt_systemd_units(indent: int = 6) -> list[str]:
    """Prompt for one or more systemd unit names; validates each via safe_systemd_unit_name."""
    pad = " " * indent
    print(f"{pad}Enter unit names (space or comma separated, e.g. nginx.service postgresql.service):")
    while True:
        raw = input(f"{pad}Units: ").strip()
        parsed = [u.strip().rstrip(",") for u in raw.replace(",", " ").split() if u.strip()]
        if not parsed:
            print(f"{pad}(at least one unit name required — e.g. nginx.service)")
            continue
        invalid: list[str] = []
        for u in parsed:
            try:
                safe_systemd_unit_name(u)
            except CommandBuildError as exc:
                invalid.append(f"{pad}  ✗  {u!r} — {exc}")
        if invalid:
            print(f"{pad}Invalid unit name(s):")
            for msg in invalid:
                print(msg)
            print(f"{pad}Unit names must include a type suffix, e.g. docker.service, nginx.service")
            continue
        return parsed
