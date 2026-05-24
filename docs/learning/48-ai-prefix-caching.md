# 48 — AI Trust Layer Phase 6a: Provider Prefix/Input Caching

## What was built and why

Phase 6a reduces LLM cost and latency by making the stable system-prompt prefix eligible for provider-side caching. The system prompt (`_SYSTEM_PROMPT`) is sent with every request and never changes — it's wasteful to process it from scratch each time.

Two mechanisms exist, depending on provider:

- **Anthropic endpoints**: requires explicit `cache_control: {"type": "ephemeral"}` breakpoints on the content blocks you want cached. Without this, caching does not activate.
- **OpenAI / vLLM endpoints**: prefix caching is automatic when the prompt bytes are identical on consecutive calls. No extra fields needed — just ensure the system prompt is byte-stable (no volatile interpolation).

## Design: auto-detection from `base_url`

The provider is detected once at construction from the `base_url`, not on every call:

```python
self._prefix_cache = "anthropic.com" in base_url
```

This is simple, testable, and doesn't require a new config knob. A vLLM instance behind `api.anthropic.com` (rare but valid) would get the Anthropic treatment, which is correct.

## `_build_messages()` isolates the message format

All message construction now goes through one method:

```python
def _build_messages(self, prompt: str) -> list[dict[str, object]]:
    if self._prefix_cache:
        system_content: str | list[dict[str, object]] = [
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        system_content = _SYSTEM_PROMPT
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ]
```

`complete()` now calls `self._build_messages(prompt)` instead of building messages inline.

## Why `cache_control` lives in a content block list (Anthropic)

Anthropic's caching API operates at the content-block level, not the message level. The content field must be a list of typed blocks, each of which can carry a `cache_control` annotation:

```json
{
  "role": "system",
  "content": [
    {
      "type": "text",
      "text": "...",
      "cache_control": {"type": "ephemeral"}
    }
  ]
}
```

For all other providers, the OpenAI SDK expects `"content": "string"` for the system role. Sending a list works on some providers but is non-standard — the non-Anthropic path keeps string content.

## Byte stability of `_SYSTEM_PROMPT`

For OpenAI/vLLM automatic prefix caching to work, the system prompt bytes must be identical on every call. The test `test_system_prompt_is_byte_stable` asserts that `_SYSTEM_PROMPT` contains no Python format placeholders (`{...}`), which would allow volatile values to be interpolated at call time and break caching.

This is a regression guard: if someone later adds `f"You are {agent_name}..."` to the system prompt, the test fails immediately.

## Tests (11 new in `TestPrefixCaching`)

- `test_prefix_cache_enabled_for_anthropic_url` — `_prefix_cache is True` for `api.anthropic.com`
- `test_prefix_cache_disabled_for_vllm_url` — False for vLLM private IP
- `test_prefix_cache_disabled_for_openai_url` — False for `api.openai.com`
- `test_prefix_cache_disabled_by_default` — False for `localhost`
- `test_build_messages_anthropic_has_cache_control` — system content is a list with `cache_control`
- `test_build_messages_vllm_has_no_cache_control` — system content is a plain string
- `test_build_messages_openai_has_no_cache_control` — same
- `test_build_messages_user_content_is_prompt` — user message carries the prompt verbatim
- `test_build_messages_anthropic_user_content_is_prompt` — same for Anthropic path
- `test_system_prompt_is_byte_stable` — no format placeholders in `_SYSTEM_PROMPT`
- `test_complete_uses_build_messages_for_anthropic` — end-to-end: `complete()` sends content-block list with `cache_control` to the API when using Anthropic
