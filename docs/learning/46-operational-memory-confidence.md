# 46 — AI Trust Layer Phase 4: Operational Memory Confidence

## What was built and why

Phase 4 adds a `confidence` field to the three operational-memory fact models: `ActionOutcomeFact`, `VMRebootPatternFact`, and `ActionRejectionFact`. The LLM now sees not just the raw statistic, but how much evidence backs it up.

Without confidence labels, the LLM prompt treats "100% success rate (2 samples)" the same as "100% success rate (15 samples)". The first is anecdotal; the second is a reliable track record. A confidence tier lets the model — and the operator reading the findings — calibrate accordingly.

## Design: `@computed_field` instead of a stored field

The confidence is always derivable from fields already on the model (`sample_size` or `rejections_last_90d`). There's no reason to accept it as an input and no reason to store it separately. Pydantic v2's `@computed_field` + `@property` is the right primitive:

```python
from pydantic import BaseModel, computed_field

class ActionOutcomeFact(BaseModel):
    sample_size: int
    ...

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> str:
        return _sample_confidence(self.sample_size)
```

**Properties of this approach:**
- `confidence` is always consistent with `sample_size` — impossible to pass the wrong value
- Existing instantiation sites (`ActionOutcomeFact(vm_id=..., sample_size=5, ...)`) continue to work unchanged
- `model_dump()` and `model_dump_json()` include `confidence` automatically
- mypy strict mode requires `# type: ignore[prop-decorator]` because it doesn't recognize the `@computed_field` + `@property` combo as a valid property descriptor

## Confidence thresholds

Two helper functions in `vm_facts.py`:

```python
def _sample_confidence(sample_size: int) -> str:
    if sample_size >= 10: return "high"
    if sample_size >= 5:  return "medium"
    return "low"

def _rejection_confidence(rejections: int) -> str:
    if rejections >= 5: return "high"
    if rejections >= 2: return "medium"
    return "low"
```

| Tier | ActionOutcomeFact / VMRebootPatternFact | ActionRejectionFact |
|---|---|---|
| `high` | sample_size ≥ 10 | rejections ≥ 5 |
| `medium` | sample_size ≥ 5 | rejections ≥ 2 |
| `low` | sample_size < 5 | rejections < 2 |

## Prompt integration

`_format_prompt()` in `operator_assistant.py` now embeds confidence inline:

```
Action outcomes (last 20 attempts per VM/action):
  prod/vm1 patching: 75% success (12 samples, confidence: high) — last failure: dpkg lock
  prod/vm2 disk_cleanup: 100% success (3 samples, confidence: low)

Reboot patterns after patching:
  prod/vm1: 3 reboots required (8 patching runs, confidence: medium)

Frequently rejected actions (last 90 days):
  patching: 6 rejection(s) [confidence: high] — maintenance freeze; risk too high
```

The LLM can now weight its findings accordingly: a low-confidence fact with a bad success rate might not warrant a "high" risk finding, whereas the same rate at high confidence should.

## Tests

**`TestConfidenceLabels`** in `tests/safety/test_vm_facts.py` (10 tests):
- Low/medium/high for `ActionOutcomeFact` based on sample_size
- Low/high for `VMRebootPatternFact` based on sample_size
- Low/medium/high for `ActionRejectionFact` based on rejections
- Boundary conditions: exactly 5 → medium, exactly 10 → high

**3 new tests in `TestFormatPromptWithFacts`** in `test_operator_assistant_facts.py`:
- `test_prompt_includes_confidence_label_for_outcomes` — `"confidence: high"` appears for sample_size=12
- `test_prompt_includes_confidence_label_for_reboot_patterns` — `"confidence: low"` for sample_size=3
- `test_prompt_includes_confidence_label_for_rejections` — `"confidence: high"` for 6 rejections

## Gotcha: `type: ignore[prop-decorator]`

mypy strict mode emits `error: Decorated property not supported` on `@computed_field` + `@property`. The correct suppression is `# type: ignore[prop-decorator]`. Using `[misc]` triggers a follow-up `unused-ignore` error in newer mypy. Always use the specific error code.
