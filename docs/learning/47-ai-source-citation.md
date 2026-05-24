# 47 — AI Trust Layer Phase 5: Source Citation for AI Answers

## What was built and why

Phase 5 adds traceable evidence to every finding the Operator Assistant produces. Previously `AssistantResponse.findings` was `list[str]` — the LLM could say "VM v1 has issues" but there was no way to know if that claim came from the audit log, a disk trend, or thin air.

Now each finding carries an `evidence` list of source IDs. An operator (or an eval harness) can see exactly which data stores backed the claim.

## Design: typed `Finding` model with backward-compatible coercion

```python
class Finding(BaseModel):
    text: str
    evidence: list[str] = []

    @property
    def is_cited(self) -> bool:
        return bool(self.evidence)


class AssistantResponse(BaseModel):
    findings: list[Finding]   # was list[str]
    ...

    @field_validator("findings", mode="before")
    @classmethod
    def _coerce_findings(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return v  # type: ignore[return-value]
        return [{"text": item} if isinstance(item, str) else item for item in v]
```

The `@field_validator(mode="before")` runs before Pydantic validates the list items, so bare strings are coerced to `{"text": str}` dicts and then validated as `Finding` objects. This means all existing call sites (`findings=["no issues"]`) continue to work without modification.

## Source ID vocabulary

IDs are plain strings with a consistent format:

| Source | ID pattern |
|---|---|
| Audit event log | `audit_store` |
| Disk growth history | `disk_history` |
| Config drift baselines | `drift_baselines` |
| ELK error index | `elk_store` |
| Live SSH probe | `live_ssh_probe` |
| VM-level action outcome facts | `vm_facts:{vm_id}:{action_type}` |
| Fleet-level rejection facts | `vm_facts:fleet:{action_type}` |

The LLM prompt now shows these IDs in the JSON schema and lists the valid IDs for the current context:

```
Valid source IDs for evidence: audit_store, disk_history, vm_facts:prod/vm1:patching
A finding with no traceable source should set evidence to [].
```

## Fallback response citations

`_fallback_response()` now builds `Finding` objects, not strings:

```python
findings.append(Finding(
    text=f"{len(alarm_vms)} VM(s) have recent action failures: ...",
    evidence=["audit_store"],
))
findings.append(Finding(
    text=f"{v.vm_id}: disk growth detected ...",
    evidence=["disk_history"],
))
# uncited fallback:
findings.append(Finding(text="No significant signals detected ...", evidence=[]))
```

A healthy-fleet finding is deliberately uncited — there is no positive evidence to cite, only the absence of signals.

## Tests (10 new)

**`test_operator_assistant.py`** (8 new):
- `test_format_prompt_includes_evidence_field_in_schema` — `'"evidence"'` in prompt
- `test_format_prompt_includes_valid_source_ids_when_sources_used` — source IDs from `context.sources_used` appear in prompt
- `test_format_prompt_source_ids_absent_when_no_sources` — `"none"` when `sources_used=[]`
- `test_fallback_findings_are_finding_objects` — all findings are `Finding` instances
- `test_fallback_failure_finding_is_cited` — `"audit_store"` in evidence
- `test_fallback_disk_finding_is_cited` — `"disk_history"` in evidence
- `test_fallback_healthy_finding_is_uncited` — `is_cited == False`
- `test_assistant_response_coerces_bare_string_findings` — backward-compat validator

**`test_operator_assistant_facts.py`** (2 new):
- `test_low_success_rate_finding_cites_vm_facts` — `"vm_facts:vm1:patching"` in evidence
- `test_rejection_finding_cites_vm_facts_fleet` — `"vm_facts:fleet:patching"` in evidence

## Migration: existing tests that treat findings as strings

Any test that did `f.lower()` or `"word" in f` on findings items broke because `f` is now a `Finding`, not a `str`. Fix: add `.text`:

```python
# OLD:
assert any("failure" in f.lower() for f in result.findings)

# NEW:
assert any("failure" in f.text.lower() for f in result.findings)
```

The `@field_validator` coercion means the model itself is lenient; it's only the test code that treated the output as strings that needed updating.

## Gotcha: `mode="before"` is required for list coercion

`@field_validator("findings", mode="after")` receives `list[Finding]` — the items are already validated, so bare strings would have already failed. `mode="before"` receives the raw Python value and runs before Pydantic tries to validate list items, which is the only window where coercion from `str` → `dict` is possible.
