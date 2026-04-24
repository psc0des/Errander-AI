"""LLM client — OpenAI SDK pointed at any OpenAI-compatible endpoint.

Works with vLLM, OpenAI, Anthropic (OpenAI-compat), Groq, Ollama, and any
other provider that exposes the OpenAI chat completions API.

Key design:
- All responses parsed as structured JSON via Pydantic models
- 60-second default timeout (configurable per provider)
- Sequential calls preferred (low VRAM concurrency on self-hosted models)
- MANDATORY fallback: when LLM is unreachable, return None and let
  caller use hardcoded defaults. Agent must NEVER be blocked by LLM.

See docs/LLM-PROVIDERS.md for provider-specific configuration examples.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from errander.observability.metrics import LLM_REQUESTS_TOTAL

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: System prompt used for all completions
_SYSTEM_PROMPT = (
    "You are Errander-AI, an autonomous DevOps maintenance agent. "
    "Respond with valid JSON only — no explanation, no markdown fences."
)


class LLMClient:
    """Async client for any OpenAI-compatible LLM API.

    Wraps the OpenAI Python SDK, pointed at a configurable endpoint.
    All calls return structured Pydantic models or None on failure.

    Usage:
        client = LLMClient(base_url="http://10.0.1.5:8000/v1", model="Qwen/Qwen3-8B-AWQ")
        result = await client.complete(prompt, MyResponseModel)
        if result is None:
            # LLM unavailable — use hardcoded fallback
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "not-needed",
        temperature: float = 0.1,
        timeout_seconds: int = 60,
        max_retries: int = 2,
    ) -> None:
        """Initialise LLM client.

        Args:
            base_url: API endpoint URL (e.g., http://10.0.1.5:8000/v1 or https://api.openai.com/v1).
            model: Model ID as required by the provider (e.g., "Qwen/Qwen3-8B-AWQ", "gpt-4o-mini").
            api_key: API key. Use "not-needed" for unauthenticated vLLM instances.
            temperature: Sampling temperature. Keep low (0.1) for structured JSON responses.
            timeout_seconds: Per-request timeout.
            max_retries: Retry attempts on transient errors (connection/timeout).
        """
        self._base_url = base_url
        self._model = model
        self._temperature = temperature
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,  # we handle retries ourselves
        )

    async def complete(
        self,
        prompt: str,
        response_model: type[T],
        timeout_seconds: int | None = None,
    ) -> T | None:
        """Send a completion request and parse structured JSON response.

        Args:
            prompt: The user prompt describing the task.
            response_model: Pydantic model to parse the JSON response into.
            timeout_seconds: Override per-request timeout.

        Returns:
            Parsed response_model instance, or None if LLM
            is unreachable, times out, or returns unparseable output.
        """
        effective_timeout = timeout_seconds or self._timeout_seconds

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self._temperature,
                    timeout=effective_timeout,
                )
                content = response.choices[0].message.content or ""
                parsed = _parse_response(content, response_model)
                if parsed is not None:
                    LLM_REQUESTS_TOTAL.labels(outcome="success").inc()
                else:
                    LLM_REQUESTS_TOTAL.labels(outcome="fallback").inc()
                return parsed

            except APITimeoutError:
                logger.warning(
                    "LLM timeout on attempt %d/%d (timeout=%ds)",
                    attempt + 1, self._max_retries + 1, effective_timeout,
                )
                if attempt < self._max_retries:
                    continue
                logger.error("LLM timed out after %d attempts — using fallback", attempt + 1)
                LLM_REQUESTS_TOTAL.labels(outcome="timeout").inc()
                return None

            except APIConnectionError as exc:
                logger.warning(
                    "LLM connection error on attempt %d/%d: %s",
                    attempt + 1, self._max_retries + 1, exc,
                )
                if attempt < self._max_retries:
                    continue
                logger.error("LLM unreachable after %d attempts — using fallback", attempt + 1)
                LLM_REQUESTS_TOTAL.labels(outcome="error").inc()
                return None

            except APIStatusError as exc:
                # 4xx/5xx — don't retry, log and return None
                logger.error(
                    "LLM API error %d: %s — using fallback",
                    exc.status_code, exc.message,
                )
                LLM_REQUESTS_TOTAL.labels(outcome="error").inc()
                return None

        return None  # unreachable, but satisfies type checker

    async def health_check(self) -> bool:
        """Check if the LLM endpoint is reachable.

        Calls the /v1/models endpoint (OpenAI-compatible, lightweight).

        Returns:
            True if endpoint responds with 200, False otherwise.
        """
        try:
            models = await self._client.models.list()
            return len(models.data) > 0
        except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
            logger.debug("LLM health check failed: %s", exc)
            return False

    async def check_endpoint(self) -> dict[str, object]:
        """Detailed endpoint check — connectivity, model info, and round-trip latency.

        Runs two checks:
        1. /v1/models — lists available models
        2. A minimal completion — measures first-token latency

        Returns:
            Dict with keys:
                reachable (bool), model_ids (list[str]),
                latency_ms (float | None), test_response (str | None),
                error (str | None)
        """
        result: dict[str, object] = {
            "reachable": False,
            "model_ids": [],
            "latency_ms": None,
            "test_response": None,
            "error": None,
        }

        # Step 1: model list
        try:
            models = await self._client.models.list()
            result["model_ids"] = [m.id for m in models.data]
            result["reachable"] = True
        except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
            result["error"] = str(exc)
            return result

        # Step 2: test completion with timing
        test_prompt = "Respond with exactly: OK"
        try:
            t0 = time.monotonic()
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": test_prompt}],
                max_tokens=8,
                temperature=0.0,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            result["latency_ms"] = round(latency_ms, 1)
            result["test_response"] = (resp.choices[0].message.content or "").strip()
        except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
            result["error"] = f"Completion failed: {exc}"

        return result


def _parse_response(content: str, response_model: type[T]) -> T | None:
    """Parse raw LLM response text into a Pydantic model.

    Handles:
    - Raw JSON object: `{"key": "value"}`
    - JSON wrapped in markdown fences: ```json\n{...}\n```
    - Whitespace / BOM stripping

    Args:
        content: Raw text from the LLM.
        response_model: Pydantic model to parse into.

    Returns:
        Parsed model or None if parsing fails.
    """
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()

    try:
        data = json.loads(text)
        return response_model.model_validate(data)
    except json.JSONDecodeError as exc:
        logger.warning("LLM response is not valid JSON: %s — content: %.200s", exc, text)
        return None
    except ValidationError as exc:
        logger.warning("LLM response failed schema validation: %s — content: %.200s", exc, text)
        return None
