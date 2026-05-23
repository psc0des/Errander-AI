# 44 — Context Budget & Redaction Policy (Phase 3 of AI Trust Layer)

## What was built and why

Before a prompt reaches the LLM, two safety controls now run:

1. **`ContextBudgeter`** — prevents oversized prompts by capping the number of VMs included, the number of log entries per VM, and the length of text fields. A fleet with 100 VMs doesn't mean the LLM should see all 100; it should see the first 20 (configurable) and know some were dropped.

2. **`ContextRedactor`** — strips known secret patterns from the final rendered prompt string. Even if a secret somehow ended up in a context field (e.g. a password in a log error message), it is stripped before the string is sent to the LLM endpoint.

Both run in `OperatorAssistant.investigate()`, wired in between context assembly and the LLM call. Neither blocks the deterministic fallback — if the LLM is unavailable, the fallback path also uses the budget-capped context.

## Key concepts

### Defence-in-depth at the prompt layer

The two controls are complementary:
- The **budget** operates at the `FleetContext` data level — it caps the *structured* context before it's rendered to a string.
- The **redactor** operates at the *string* level — it's a final safety net after rendering.

Doing both means a secret that entered a list field AND survived the budget cap will still be stripped from the final string.

### `ContextBudgeter` — no mutation

`ContextBudgeter.apply(context)` returns a *copy* of `FleetContext` with caps applied. It uses `dataclasses.replace()` for the dataclass types (`FleetContext`, `VMSignalSummary`) and `model.model_copy(update={...})` for Pydantic models (`ActionOutcomeFact`). The original `context` object is never touched.

```python
# Pydantic v2 — create a field-modified copy
capped_fact = fact.model_copy(update={"last_failure_reason": truncated})

# dataclass — create a field-modified copy
capped_vm = replace(vm, elk_errors=vm.elk_errors[:5])
```

### `ContextRedactor` — compiled patterns, applied in order

All patterns are compiled once at module load time (stored in `_SECRET_RULES`). Each is applied as `pattern.subn(replacement, text)` — `subn` returns `(new_text, count_of_substitutions)`, so counting substitutions is free.

The five pattern families:
- `sk-[A-Za-z0-9_-]{20,}` — OpenAI/Anthropic-style API keys. The 20-char minimum suffix prevents false positives on short sk- prefixed identifiers.
- `AKIA[0-9A-Z]{16}` — AWS IAM access key IDs (always exactly 20 chars total).
- `(?i)password\s*[:=]\s*\S+` — `password=`, `password:` assignments.
- `(?i)authorization\s*:\s*bearer\s+\S+` — `Authorization: Bearer <token>` headers.
- PEM private key blocks — `-----BEGIN <type> PRIVATE KEY-----...-----END <type> PRIVATE KEY-----`, multiline (`re.DOTALL`).

IP addresses (`\b(?:\d{1,3}\.){3}\d{1,3}\b`) are **not** redacted by default because private IPs are legitimate operational context. Enable with `ContextRedactor(redact_ips=True)`.

### `from __future__ import annotations` and TYPE_CHECKING

`VMSignalSummary` is used only as a type annotation in `context_budget.py` (`capped_vms: list[VMSignalSummary] = []`). With `from __future__ import annotations`, all annotations are lazy strings at runtime — the class doesn't need to be importable. So it belongs in `if TYPE_CHECKING:`, not in a local runtime import inside the method. ruff's `TC001` rule catches this correctly.

## Code walkthrough

**`context_budget.py`**

`ContextBudgeter.__init__()` accepts `max_vms`, `max_chars_per_field`, and `max_log_entries_per_vm` with sensible defaults (20, 500, 5).

`apply()` does three passes:
1. Slice `vm_summaries` to `max_vms`
2. For each retained VM, truncate each of the 7 list fields (`disk_alerts`, `drift_kinds`, `last_action_types`, `prometheus_metrics`, `elk_errors`, `journal_errors`, `failed_services`) to `max_log_entries_per_vm`
3. For each `ActionOutcomeFact`, truncate `last_failure_reason` to `max_chars_per_field`

Returns the capped context and a `BudgetStats` dataclass with `vms_dropped`, `entries_truncated`, `fields_truncated`.

**`context_redactor.py`**

`redact(text)` runs each of the 5 `_SECRET_RULES` patterns sequentially. Returns `(redacted_text, count)`.

`redact_prompt(prompt)` wraps `redact()` and returns `(clean_prompt, RedactionStats)`.

**`operator_assistant.py` wiring**

Two lines added to `investigate()` after `_build_context()`:

```python
context, budget_stats = _BUDGETER.apply(context)
if budget_stats.vms_dropped:
    logger.info("Context budget: dropped %d VM(s)", budget_stats.vms_dropped)
```

And before `llm_client.complete()`:

```python
prompt = _format_prompt(question, context)
prompt, redaction_stats = _REDACTOR.redact_prompt(prompt)
if redaction_stats.total_redactions:
    logger.warning("Redacted %d secret pattern(s) from LLM prompt", ...)
```

The `_BUDGETER` and `_REDACTOR` are module-level singletons with default configs — no state between calls.

## Gotchas

- `dataclasses.replace()` with `**overrides` where `overrides: dict[str, list[str]]` may need a `# type: ignore[arg-type]` because mypy can't verify the dict values match field types. This is safe — the keys are checked against `_VM_LIST_FIELDS` and the values are `list[str]` which is correct for all those fields.
- `re.DOTALL` is needed for the PEM pattern since PEM blocks span multiple lines (the body contains `\n`). Without `DOTALL`, `.` won't match newlines.
- Keep `redact_ips=False` as the default. Private IPs like `10.0.0.1` are the normal way target VMs are identified in this system — redacting them would strip useful context from the prompt.

## Quiz yourself

1. Why does `ContextBudgeter.apply()` use `dataclasses.replace()` rather than mutating the VM directly?
2. Why is `re.DOTALL` required for the PEM pattern but not for the password or bearer token patterns?
3. What is the minimum suffix length required for an `sk-` string to be redacted, and why?
4. What happens if the LLM is unavailable — does the budget still apply? Does the redactor run?
