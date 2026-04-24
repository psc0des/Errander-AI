# 07 — LLM Client: OpenAI SDK → Self-Hosted vLLM

## What Was Built and Why

`errander/integrations/llm.py` wraps the OpenAI Python SDK to communicate with a **self-hosted Qwen3-8B-AWQ model running on vLLM**. The agent uses this for three decisions: action prioritization, failure analysis, and report generation.

Key constraint: **the agent must never be blocked by LLM unavailability**. Every call returns `None` on failure and the caller falls back to hardcoded logic. This is non-negotiable — a DevOps agent that stops working because the GPU is busy is useless.

---

## Key Concepts

### 1. OpenAI SDK as a Universal Client

vLLM exposes an OpenAI-compatible REST API (`/v1/chat/completions`, `/v1/models`). The OpenAI Python SDK works against it unchanged — just point `base_url` at the private vLLM endpoint:

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="http://10.0.1.5:8000/v1",
    api_key="not-needed",   # vLLM doesn't require auth by default
    timeout=60,
    max_retries=0,           # we handle retries ourselves
)
```

`max_retries=0` disables the SDK's built-in retry so we control retry behaviour ourselves (see below).

### 2. Thinking Mode vs /no_think

Qwen3 supports chain-of-thought reasoning ("thinking mode"). vLLM is started with `--enable-reasoning --reasoning-parser deepseek_r1` which strips `<think>...</think>` blocks before returning the response. You never see the raw thinking tokens — just the final answer.

Two modes:
- **Thinking mode** (`thinking=True`): Full chain-of-thought reasoning. Better quality for complex decisions (action prioritization, failure analysis). Slower — T4 needs ~30-60s.
- **/no_think** (`thinking=False`): Prepend `/no_think\n\n` to the prompt. Suppresses reasoning. Faster — used for report generation where speed matters more than deliberation.

```python
full_prompt = prompt if thinking else f"/no_think\n\n{prompt}"
```

### 3. Structured JSON via Pydantic

All LLM responses are parsed into Pydantic models. This gives type safety and validation without manual parsing:

```python
class _PrioritizedActions(BaseModel):
    action_types: list[str]

result = await client.complete(prompt, _PrioritizedActions, thinking=True)
if result is not None:
    # result.action_types is guaranteed to be list[str]
```

`_parse_response()` handles three formats:
- Raw JSON: `{"action_types": ["disk_cleanup", ...]}`
- Markdown-fenced: `` ```json\n{...}\n``` ``
- Plain-fenced: `` ```\n{...}\n``` ``

Returns `None` if JSON is invalid or fails Pydantic validation.

### 4. Error Handling and Retry

Three OpenAI exception types:

| Exception | Meaning | Action |
|---|---|---|
| `APITimeoutError` | Request timed out | Retry up to `max_retries` |
| `APIConnectionError` | Can't reach the server | Retry up to `max_retries` |
| `APIStatusError` | 4xx/5xx from server | No retry — log and return `None` |

```python
for attempt in range(self._max_retries + 1):
    try:
        response = await self._client.chat.completions.create(...)
        return _parse_response(response.choices[0].message.content, model)
    except APITimeoutError:
        if attempt < self._max_retries:
            continue
        return None
    except APIConnectionError:
        if attempt < self._max_retries:
            continue
        return None
    except APIStatusError:
        return None  # don't retry 4xx/5xx
```

No `asyncio.sleep()` between retries — the T4 is within the VPN, so connection failures are typically fast-failing, not load-related.

### 5. The Fallback Contract in decisions.py

Every decision function accepts an optional `llm_client` parameter:

```python
async def prioritize_actions(
    vm_info: VMInfo,
    available_actions: list[ActionType] | None = None,
    llm_client: LLMClient | None = None,      # ← optional
) -> list[Action]:
    if llm_client is not None:
        result = await llm_client.complete(prompt, _PrioritizedActions, thinking=True)
        if result is not None:
            ordered = _parse_action_types(result.action_types, available_actions)
            if ordered:
                return [Action(...) for a in ordered]
    # Hardcoded fallback — always works
    return _hardcoded_priority(available_actions, vm_info)
```

Three levels of fallback:
1. `llm_client is None` → skip LLM entirely
2. `result is None` → LLM failed (timeout/connection/parse error)
3. `ordered` is empty → LLM returned invalid action types

The hardcoded path is always reachable.

---

## Code Walkthrough

### complete()

```python
async def complete(self, prompt, response_model, thinking=True, timeout_seconds=None):
    full_prompt = prompt if thinking else f"/no_think\n\n{prompt}"
    for attempt in range(self._max_retries + 1):
        try:
            response = await self._client.chat.completions.create(
                model=_MODEL_ID,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": full_prompt},
                ],
                temperature=0.1,   # deterministic structured output
                timeout=effective_timeout,
            )
            return _parse_response(response.choices[0].message.content, response_model)
        except APITimeoutError: ...
        except APIConnectionError: ...
        except APIStatusError: ...
    return None
```

`temperature=0.1` keeps output deterministic — we want consistent JSON, not creative variation.

### health_check()

```python
async def health_check(self) -> bool:
    try:
        models = await self._client.models.list()
        return len(models.data) > 0
    except (APIConnectionError, APITimeoutError, APIStatusError):
        return False
```

Calls `/v1/models` — lightweight, no GPU work. Used at startup to warn if LLM is unreachable.

---

## Gotchas

### 1. AsyncOpenAI initialisation is expensive

`AsyncOpenAI.__init__` sets up an httpx `AsyncClient` with connection pool configuration. This takes ~1.4s. In tests, creating a new client per test function multiplied to 57s for 23 tests.

**Fix**: Use `pytest.fixture(scope="module")` to create the client once per test module.

### 2. APIStatusError doesn't retry

A 500 from vLLM (e.g., OOM on T4) won't be fixed by retrying immediately. Return `None` and let the caller use the fallback.

### 3. Markdown fence stripping

Qwen3 sometimes wraps JSON in ` ```json ` fences even when told not to. `_parse_response()` handles both cases. The system prompt says "no markdown fences" but models don't always comply.

---

## Quiz Yourself

1. Why is `max_retries=0` set on the `AsyncOpenAI` client if we want retries?
2. What's the difference between `APITimeoutError` and `APIConnectionError`? Which one means "server is down"?
3. Why use `temperature=0.1` instead of `0` for structured JSON output?
4. Why doesn't `APIStatusError` trigger a retry?
5. The fallback in `prioritize_actions` has three levels. What are they and what triggers each?
