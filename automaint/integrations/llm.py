"""LLM client — OpenAI SDK pointed at self-hosted vLLM endpoint.

Uses OpenAI Python SDK with configurable base_url to communicate with
a self-hosted Qwen3-8B-AWQ model running on vLLM.

Key design:
- All responses parsed as structured JSON via Pydantic models
- Two modes: thinking (planning/analysis) and /no_think (reports)
- 60-second timeout (T4 is slower than cloud APIs)
- Sequential calls preferred (low VRAM concurrency)
- MANDATORY fallback: when LLM is unreachable, return None and let
  caller use hardcoded defaults. Agent must NEVER be blocked by LLM.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Async client for self-hosted LLM via OpenAI-compatible API.

    Wraps the OpenAI Python SDK, pointed at the vLLM endpoint.
    All calls return structured Pydantic models or None on failure.
    """

    def __init__(self, base_url: str, api_key: str = "not-needed") -> None:
        """Initialize LLM client.

        Args:
            base_url: vLLM endpoint URL (e.g., http://10.0.1.5:8000/v1).
            api_key: API key if vLLM requires auth.
        """
        self._base_url = base_url
        self._api_key = api_key

    async def complete(
        self,
        prompt: str,
        response_model: type[T],
        thinking: bool = True,
        timeout_seconds: int = 60,
    ) -> T | None:
        """Send a completion request and parse structured response.

        Args:
            prompt: The user prompt.
            response_model: Pydantic model to parse response into.
            thinking: If True, use thinking mode. If False, prepend /no_think.
            timeout_seconds: Request timeout.

        Returns:
            Parsed response model, or None if LLM is unreachable/fails.
        """
        raise NotImplementedError("LLM completion not yet implemented")

    async def health_check(self) -> bool:
        """Check if the LLM endpoint is reachable.

        Returns:
            True if endpoint responds, False otherwise.
        """
        raise NotImplementedError("LLM health check not yet implemented")
