# 53 — Enterprise Wizard Input Validation

## What was built and why

The interactive inventory wizards (`inventory_wizard.py`, `add_target.py`) collect ~11 data fields each and previously passed them straight to Pydantic schema validation. Bad input only surfaced as an error message *after* the entire wizard completed — forcing the operator to restart from scratch.

Additionally, piecemeal fixes earlier in the session had copy-pasted `_prompt_yn`, `_prompt_maintenance_window`, `_prompt_policy`, and `_MW_RE` into both files independently. Two copies of the same function always drift over time.

The fix: a single shared `errander/config/_prompts.py` module that owns all interactive prompt logic, with inline validation and immediate re-prompting on any invalid input.

## What was validated and how

| Input | Before | After |
|---|---|---|
| y/n questions | any non-"n" treated as yes | only y/yes/n/no/Enter; re-prompts with `Please enter y or n.` |
| Maintenance window | accepted any string | regex `^([01]\d\|2[0-3]):[0-5]\d-([01]\d\|2[0-3]):[0-5]\d$` |
| Maintenance days | unknown names passed through as-is | rejected with `✗ Unknown day(s): xyz`; re-prompts |
| Timezone | free text | checked against `zoneinfo.available_timezones()` |
| OS family | any non-"2"/non-"3" → silent ubuntu | numbered menu 1/2/3 only; loops on invalid |
| Approval policy | already fixed earlier session | same loop, now from shared module |
| Env/target name | any string became YAML key | `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` enforced |
| Systemd units | already fixed earlier session | same `safe_systemd_unit_name()` call, now from shared module |
| Inventory keep/replace | any non-"2" silently kept | explicit loop: 1/Enter keeps, 2 replaces, anything else re-prompts |

## Key concepts

### Shared module pattern

The module is `_prompts.py` (leading underscore = internal to the `config` package, not a public API). Both wizard files do:

```python
from errander.config._prompts import (
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
```

All helpers accept an `indent: int = 4` parameter so each call site can control how deep the prompt appears in the UI hierarchy — environment-level at 4 spaces, target-level at 6 spaces, top-level at 2 spaces.

### Validate at the prompt, not at the schema

The invariant: if the schema rejects it, the prompt must catch it first. The schema is still the safety net, but the prompt is the user-facing gate. Users should never see a Pydantic `ValidationError` for data they entered through a wizard.

### Timezone fallback

`zoneinfo.available_timezones()` requires `tzdata` on Windows. The prompt falls back gracefully:

```python
try:
    valid = zoneinfo.available_timezones()
except Exception:
    valid = set()

if not valid or val in valid:   # empty set → accept anything
    return val
```

This maintains the "fail open" principle for validation that can't be performed — the wizard runs on a Linux controller in production where `tzdata` is always present.

## Gotchas

**The OS family silent default was a real bug.** Entering `"ubuntu"` (the text) instead of `"1"` silently produced `ubuntu` because:
```python
os_map = {"2": "debian", "3": "rhel"}
os_family = os_map.get(os_raw, "ubuntu")  # any non-match → ubuntu
```
An operator typing `"debian"` thinking they'd get Debian actually got Ubuntu. The fix is a proper loop with re-prompt.

**Day aliases must include both short and full forms.** `_DAY_ALIASES` maps both `"mon" → "monday"` and `"monday" → "monday"`. The validation check is `d not in _DAY_ALIASES` — if someone types `"monday"` and the dict only has `"mon"`, it would wrongly reject it.

**`prompt_val` with a default should never loop.** If a default is provided, we return the default on empty input — we don't re-prompt. Only `prompt_val` without a default loops until non-empty. This matches the UX expectation of `[default]` in brackets.

## Tests

87 new tests in `tests/config/test_prompts.py`. Key patterns:

```python
# Mock input() for non-interactive testing
def test_invalid_input_re_prompts(self, capsys):
    with patch("builtins.input", side_effect=["wrong", "maybe", "y"]):
        result = prompt_yn("Question?")
    assert result is True
    out = capsys.readouterr().out
    assert "Please enter y or n" in out
```

The `side_effect` list simulates multiple inputs in sequence. `capsys` captures stdout to verify the error message was printed.

## Quiz

1. Why does `_DAY_ALIASES` map both `"monday": "monday"` and `"mon": "monday"` — what breaks if you only have the short forms?
2. What does `indent=6` produce in `prompt_yn("Is Docker installed?", indent=6)`?
3. Why is the timezone fallback (`if not valid or val in valid`) correct rather than just `if val in valid`?
4. What was the concrete bug with the OS family menu before this change?
5. Why does `prompt_val_optional` never loop, while `prompt_val` (without a default) does?
