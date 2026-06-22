"""Tests for LLM client and fallback behavior."""

from __future__ import annotations

import json
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from errander.integrations.llm import LLMClient, _parse_response

# --- Test response model ---

class _Echo(BaseModel):
    message: str
    count: int


# --- Shared client fixture (created once per module to avoid httpx setup overhead) ---

@pytest.fixture(scope="module")
def llm_client() -> LLMClient:
    return LLMClient(
        base_url="http://10.0.1.5:8000/v1",
        model="test-model",
        api_key="not-needed",
        timeout_seconds=60,
        max_retries=2,
    )


@pytest.fixture(scope="module")
def llm_client_no_retry() -> LLMClient:
    return LLMClient(
        base_url="http://10.0.1.5:8000/v1",
        model="test-model",
        timeout_seconds=60,
        max_retries=0,
    )


# --- _parse_response unit tests ---

class TestParseResponse:
    def test_parses_raw_json(self) -> None:
        result = _parse_response('{"message": "hello", "count": 3}', _Echo)
        assert result is not None
        assert result.message == "hello"
        assert result.count == 3

    def test_parses_markdown_fenced_json(self) -> None:
        content = '```json\n{"message": "fenced", "count": 1}\n```'
        result = _parse_response(content, _Echo)
        assert result is not None
        assert result.message == "fenced"

    def test_parses_plain_fenced_json(self) -> None:
        content = '```\n{"message": "plain", "count": 2}\n```'
        result = _parse_response(content, _Echo)
        assert result is not None
        assert result.count == 2

    def test_returns_none_on_invalid_json(self) -> None:
        result = _parse_response("not json at all", _Echo)
        assert result is None

    def test_returns_none_on_schema_mismatch(self) -> None:
        result = _parse_response('{"wrong_field": "x"}', _Echo)
        assert result is None

    def test_strips_whitespace(self) -> None:
        result = _parse_response('  \n{"message": "trimmed", "count": 0}\n  ', _Echo)
        assert result is not None
        assert result.message == "trimmed"


# --- LLMClient tests ---

def _make_chat_response(content: str) -> MagicMock:
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


class TestLLMClientComplete:
    @pytest.mark.asyncio
    async def test_returns_parsed_model_on_success(self, llm_client: LLMClient) -> None:
        payload = json.dumps({"message": "ok", "count": 7})
        mock_response = _make_chat_response(payload)
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            result = await llm_client.complete("test prompt", _Echo)
        assert result is not None
        assert result.message == "ok"
        assert result.count == 7

    @pytest.mark.asyncio
    async def test_sends_prompt_verbatim_without_modification(self, llm_client: LLMClient) -> None:
        """complete() must send the prompt as-is — no prefix injection."""
        payload = json.dumps({"message": "ok", "count": 1})
        mock_response = _make_chat_response(payload)
        captured: list[list[dict]] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured.append(kwargs.get("messages", []))  # type: ignore[arg-type]
            return mock_response

        with patch.object(llm_client._client.chat.completions, "create", _capture):
            await llm_client.complete("my exact prompt", _Echo)

        user_msg = captured[0][1]["content"]
        assert user_msg == "my exact prompt"

    @pytest.mark.asyncio
    async def test_uses_configured_temperature(self, llm_client: LLMClient) -> None:
        """complete() must use self._temperature, not a hardcoded literal."""
        client = LLMClient(
            base_url="http://10.0.1.5:8000/v1",
            model="test-model",
            temperature=0.7,
        )
        payload = json.dumps({"message": "ok", "count": 1})
        mock_response = _make_chat_response(payload)
        captured_kwargs: list[dict] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured_kwargs.append(dict(kwargs))
            return mock_response

        with patch.object(client._client.chat.completions, "create", _capture):
            await client.complete("prompt", _Echo)

        assert captured_kwargs[0]["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_uses_configured_model(self, llm_client: LLMClient) -> None:
        """complete() must use self._model, not a hardcoded constant."""
        payload = json.dumps({"message": "ok", "count": 1})
        mock_response = _make_chat_response(payload)
        captured_kwargs: list[dict] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured_kwargs.append(dict(kwargs))
            return mock_response

        with patch.object(llm_client._client.chat.completions, "create", _capture):
            await llm_client.complete("prompt", _Echo)

        assert captured_kwargs[0]["model"] == "test-model"

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, llm_client_no_retry: LLMClient) -> None:
        from openai import APITimeoutError
        with patch.object(
            llm_client_no_retry._client.chat.completions, "create",
            AsyncMock(side_effect=APITimeoutError(request=MagicMock())),
        ):
            result = await llm_client_no_retry.complete("test", _Echo)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self, llm_client_no_retry: LLMClient) -> None:
        from openai import APIConnectionError
        with patch.object(
            llm_client_no_retry._client.chat.completions, "create",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ):
            result = await llm_client_no_retry.complete("test", _Echo)
        assert result is None

    @pytest.mark.asyncio
    async def test_retries_on_transient_errors(self, llm_client: LLMClient) -> None:
        from openai import APIConnectionError
        payload = json.dumps({"message": "ok", "count": 1})
        mock_response = _make_chat_response(payload)
        call_count = 0

        async def _flaky(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise APIConnectionError(request=MagicMock())
            return mock_response

        with patch.object(llm_client._client.chat.completions, "create", _flaky):
            result = await llm_client.complete("test", _Echo)

        assert result is not None
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_returns_none_after_exhausting_retries(self, llm_client: LLMClient) -> None:
        from openai import APIConnectionError
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ):
            result = await llm_client.complete("test", _Echo)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_bad_json_response(self, llm_client: LLMClient) -> None:
        mock_response = _make_chat_response("This is not JSON")
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            result = await llm_client.complete("test", _Echo)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_status_error(self, llm_client: LLMClient) -> None:
        from openai import APIStatusError
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(
                side_effect=APIStatusError(
                    message="Internal Server Error",
                    response=MagicMock(status_code=500),
                    body=None,
                )
            ),
        ):
            result = await llm_client.complete("test", _Echo)
        assert result is None


class TestLLMClientHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_when_models_available(self, llm_client: LLMClient) -> None:
        mock_models = MagicMock()
        mock_models.data = [MagicMock()]
        with patch.object(
            llm_client._client.models, "list",
            AsyncMock(return_value=mock_models),
        ):
            assert await llm_client.health_check() is True

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self, llm_client: LLMClient) -> None:
        from openai import APIConnectionError
        with patch.object(
            llm_client._client.models, "list",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ):
            assert await llm_client.health_check() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_models(self, llm_client: LLMClient) -> None:
        mock_models = MagicMock()
        mock_models.data = []
        with patch.object(
            llm_client._client.models, "list",
            AsyncMock(return_value=mock_models),
        ):
            assert await llm_client.health_check() is False


# --- Integration: decisions.py with LLM client ---

class TestDecisionsWithLLM:
    """Verify decisions.py uses LLM when available and falls back when not."""

    @pytest.mark.asyncio
    async def test_generate_planning_note_uses_llm_when_available(
        self, llm_client: LLMClient
    ) -> None:
        from errander.agent.decisions import generate_planning_note, prioritize_actions
        from errander.models.vm import OSFamily, VMInfo

        vm_info = VMInfo(
            os_family=OSFamily.UBUNTU,
            os_version="Ubuntu 22.04",
            disk_usage={"/": 60.0},
            docker_available=True,
            pending_packages=5,
            uptime_seconds=86400.0,
        )
        plan = await prioritize_actions(vm_info)
        llm_payload = json.dumps({"note": "Pending patches include a security fix."})
        mock_response = _make_chat_response(llm_payload)
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            note = await generate_planning_note(vm_info, plan, llm_client=llm_client)

        assert note == "Pending patches include a security fix."

    @pytest.mark.asyncio
    async def test_generate_planning_note_returns_none_when_llm_unavailable(
        self, llm_client: LLMClient
    ) -> None:
        from openai import APIConnectionError

        from errander.agent.decisions import generate_planning_note, prioritize_actions
        from errander.models.vm import OSFamily, VMInfo

        vm_info = VMInfo(
            os_family=OSFamily.UBUNTU,
            os_version="Ubuntu 22.04",
            disk_usage={"/": 50.0},
            docker_available=True,
            pending_packages=5,
            uptime_seconds=86400.0,
        )
        plan = await prioritize_actions(vm_info)
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ):
            note = await generate_planning_note(vm_info, plan, llm_client=llm_client)

        assert note is None

    @pytest.mark.asyncio
    async def test_generate_planning_note_returns_none_on_bad_llm_response(
        self, llm_client: LLMClient
    ) -> None:
        from errander.agent.decisions import generate_planning_note, prioritize_actions
        from errander.models.vm import OSFamily, VMInfo

        vm_info = VMInfo(
            os_family=OSFamily.UBUNTU,
            os_version="Ubuntu 22.04",
            disk_usage={"/": 50.0},
            docker_available=True,
            pending_packages=5,
            uptime_seconds=86400.0,
        )
        plan = await prioritize_actions(vm_info)
        mock_response = _make_chat_response("not valid json")
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            note = await generate_planning_note(vm_info, plan, llm_client=llm_client)

        assert note is None

    @pytest.mark.asyncio
    async def test_generate_report_uses_llm(self, llm_client: LLMClient) -> None:
        from datetime import datetime

        from errander.agent.decisions import generate_report
        from errander.models.actions import ActionResult, ActionStatus, ActionType

        now = datetime.now(tz=UTC)
        results = [
            ActionResult(
                action_type=ActionType.DISK_CLEANUP,
                status=ActionStatus.SUCCESS,
                vm_id="dev/web-01",
                started_at=now,
                completed_at=now,
            )
        ]
        llm_report = "Errander-AI ran disk cleanup on dev/web-01. All clear."
        mock_response = _make_chat_response(json.dumps({"report": llm_report}))
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            report = await generate_report(results, batch_id="b-001", llm_client=llm_client)

        assert report == llm_report

    @pytest.mark.asyncio
    async def test_generate_report_falls_back_to_template(self, llm_client: LLMClient) -> None:
        from datetime import datetime

        from openai import APIConnectionError

        from errander.agent.decisions import generate_report
        from errander.models.actions import ActionResult, ActionStatus, ActionType

        now = datetime.now(tz=UTC)
        results = [
            ActionResult(
                action_type=ActionType.DISK_CLEANUP,
                status=ActionStatus.DRY_RUN_OK,
                vm_id="dev/web-01",
                started_at=now,
                completed_at=now,
            )
        ]
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ):
            report = await generate_report(results, batch_id="b-002", llm_client=llm_client)

        assert "b-002" in report
        assert "dev/web-01" in report
        assert "[DRY]" in report


# ---------------------------------------------------------------------------
# Phase 6a: Provider prefix/input caching
# ---------------------------------------------------------------------------


class TestPrefixCaching:
    """Verify that cache_control is attached for Anthropic endpoints and absent otherwise."""

    def _anthropic_client(self) -> LLMClient:
        return LLMClient(
            base_url="https://api.anthropic.com/v1",
            model="claude-sonnet-4-6",
        )

    def _vllm_client(self) -> LLMClient:
        return LLMClient(
            base_url="http://10.0.1.5:8000/v1",
            model="Qwen/Qwen3-8B-AWQ",
        )

    def _openai_client(self) -> LLMClient:
        return LLMClient(
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )

    def test_prefix_cache_enabled_for_anthropic_url(self) -> None:
        assert self._anthropic_client()._prefix_cache is True

    def test_prefix_cache_disabled_for_vllm_url(self) -> None:
        assert self._vllm_client()._prefix_cache is False

    def test_prefix_cache_disabled_for_openai_url(self) -> None:
        assert self._openai_client()._prefix_cache is False

    def test_prefix_cache_disabled_by_default(self) -> None:
        client = LLMClient(base_url="http://localhost:8000/v1", model="m")
        assert client._prefix_cache is False

    def test_build_messages_anthropic_has_cache_control(self) -> None:
        client = self._anthropic_client()
        messages = client._build_messages("hello")
        system_msg = messages[0]
        assert system_msg["role"] == "system"
        content = system_msg["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        block = content[0]
        assert isinstance(block, dict)
        assert block.get("cache_control") == {"type": "ephemeral"}
        assert block.get("type") == "text"
        assert block.get("text") == client._build_messages("x")[0]["content"][0]["text"]  # type: ignore[index]

    def test_build_messages_vllm_has_no_cache_control(self) -> None:
        client = self._vllm_client()
        messages = client._build_messages("hello")
        system_content = messages[0]["content"]
        assert isinstance(system_content, str)

    def test_build_messages_openai_has_no_cache_control(self) -> None:
        client = self._openai_client()
        messages = client._build_messages("hello")
        system_content = messages[0]["content"]
        assert isinstance(system_content, str)

    def test_build_messages_user_content_is_prompt(self) -> None:
        client = self._vllm_client()
        messages = client._build_messages("my exact prompt")
        user_msg = messages[1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "my exact prompt"

    def test_build_messages_anthropic_user_content_is_prompt(self) -> None:
        client = self._anthropic_client()
        messages = client._build_messages("my exact prompt")
        user_msg = messages[1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "my exact prompt"

    def test_system_prompt_is_byte_stable(self) -> None:
        """_SYSTEM_PROMPT must be a static string — no runtime format placeholders."""
        import re

        from errander.integrations.llm import _SYSTEM_PROMPT
        assert isinstance(_SYSTEM_PROMPT, str)
        # No Python format placeholders that could introduce volatile values
        assert not re.search(r"\{[^}]*\}", _SYSTEM_PROMPT), (
            "_SYSTEM_PROMPT must not contain format placeholders — "
            "volatile values would break prefix caching"
        )

    @pytest.mark.asyncio
    async def test_complete_uses_build_messages_for_anthropic(self) -> None:
        """complete() must call _build_messages and send its output to the API."""
        client = self._anthropic_client()
        payload = json.dumps({"message": "ok", "count": 1})
        mock_response = _make_chat_response(payload)
        captured: list[list[dict]] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured.append(kwargs.get("messages", []))  # type: ignore[arg-type]
            return mock_response

        with patch.object(client._client.chat.completions, "create", _capture):
            await client.complete("test", _Echo)

        assert captured, "completions.create was not called"
        system_content = captured[0][0]["content"]
        assert isinstance(system_content, list), "Anthropic path must use content-block list"
        assert system_content[0].get("cache_control") == {"type": "ephemeral"}  # type: ignore[index]


# --- complete_with_tools() — investigation-agent tool-calling path ---


def _make_tool_call(call_id: str, name: str, arguments: str, *, kind: str = "function") -> MagicMock:
    func = MagicMock()
    func.name = name
    func.arguments = arguments
    tc = MagicMock()
    tc.id = call_id
    tc.type = kind
    tc.function = func
    return tc


def _make_tool_response(
    *, content: str | None = None, tool_calls: list[MagicMock] | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


_DUMMY_TOOLS = [{
    "type": "function",
    "function": {"name": "noop", "description": "no-op", "parameters": {"type": "object", "properties": {}}},
}]


class TestCompleteWithTools:
    @pytest.mark.asyncio
    async def test_returns_tool_calls_when_model_requests_them(self, llm_client: LLMClient) -> None:
        tc = _make_tool_call("call_1", "get_audit_events", '{"limit": 5}')
        mock_response = _make_tool_response(tool_calls=[tc])
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            result = await llm_client.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert result is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_1"
        assert result.tool_calls[0].name == "get_audit_events"
        assert result.tool_calls[0].arguments_json == '{"limit": 5}'

    @pytest.mark.asyncio
    async def test_returns_final_content_when_no_tool_calls(self, llm_client: LLMClient) -> None:
        mock_response = _make_tool_response(content='{"summary": "done"}', tool_calls=None)
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            result = await llm_client.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert result is not None
        assert result.tool_calls == []
        assert result.content == '{"summary": "done"}'

    @pytest.mark.asyncio
    async def test_filters_non_function_tool_calls(self, llm_client: LLMClient) -> None:
        """Only function-type tool calls are surfaced — we only register
        function tools, so a custom-type call (if a provider ever sent one)
        must not crash the union-attr access on .function."""
        good = _make_tool_call("call_1", "get_audit_events", "{}")
        custom = MagicMock(id="call_2", type="custom")
        del custom.function  # custom tool calls have no .function attr
        mock_response = _make_tool_response(tool_calls=[good, custom])
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(return_value=mock_response),
        ):
            result = await llm_client.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert result is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_1"

    @pytest.mark.asyncio
    async def test_empty_tools_list_omits_tools_param_entirely(self, llm_client: LLMClient) -> None:
        """An empty tools=[] forces a final, non-tool-calling answer — but
        most OpenAI-compatible endpoints reject a literal empty tools array
        with a 400. tools=/tool_choice= must be omitted from the request
        entirely, not sent as empty, when the caller passes []."""
        mock_response = _make_tool_response(content='{"summary": "done"}')
        captured: list[dict] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured.append(dict(kwargs))
            return mock_response

        with patch.object(llm_client._client.chat.completions, "create", _capture):
            result = await llm_client.complete_with_tools(
                [{"role": "user", "content": "hi"}], [],
            )
        assert result is not None
        assert "tools" not in captured[0]
        assert "tool_choice" not in captured[0]

    @pytest.mark.asyncio
    async def test_sends_tools_and_tool_choice_auto(self, llm_client: LLMClient) -> None:
        mock_response = _make_tool_response(content="{}")
        captured: list[dict] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured.append(dict(kwargs))
            return mock_response

        with patch.object(llm_client._client.chat.completions, "create", _capture):
            await llm_client.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert captured[0]["tools"] == _DUMMY_TOOLS
        assert captured[0]["tool_choice"] == "auto"

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, llm_client_no_retry: LLMClient) -> None:
        from openai import APITimeoutError
        with patch.object(
            llm_client_no_retry._client.chat.completions, "create",
            AsyncMock(side_effect=APITimeoutError(request=MagicMock())),
        ):
            result = await llm_client_no_retry.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_connection_error(self, llm_client_no_retry: LLMClient) -> None:
        from openai import APIConnectionError
        with patch.object(
            llm_client_no_retry._client.chat.completions, "create",
            AsyncMock(side_effect=APIConnectionError(request=MagicMock())),
        ):
            result = await llm_client_no_retry.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_status_error(self, llm_client: LLMClient) -> None:
        """A 400 here is the caller's capability-detection signal — the
        endpoint likely doesn't support tools=."""
        from openai import APIStatusError
        with patch.object(
            llm_client._client.chat.completions, "create",
            AsyncMock(
                side_effect=APIStatusError(
                    message="Bad Request", response=MagicMock(status_code=400), body=None,
                )
            ),
        ):
            result = await llm_client.complete_with_tools(
                [{"role": "user", "content": "hi"}], _DUMMY_TOOLS,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_does_not_redact_internally(self, llm_client: LLMClient) -> None:
        """Unlike complete(), this method has no single prompt string to
        redact — the caller (investigation_agent.py) owns redaction of the
        question and every tool result. A secret-shaped message must pass
        through unmodified here."""
        mock_response = _make_tool_response(content="{}")
        captured: list[dict] = []

        async def _capture(**kwargs: object) -> MagicMock:
            captured.append(dict(kwargs))
            return mock_response

        secret_message = [{"role": "user", "content": "password: hunter2"}]
        with patch.object(llm_client._client.chat.completions, "create", _capture):
            await llm_client.complete_with_tools(secret_message, _DUMMY_TOOLS)
        assert captured[0]["messages"][0]["content"] == "password: hunter2"

    @pytest.mark.asyncio
    async def test_concurrent_calls_are_serialized(self, llm_client: LLMClient) -> None:
        """Sequential LLM calls preferred on self-hosted endpoints (low VRAM
        concurrency on a single GPU) — concurrent complete_with_tools()
        calls must never run create() at the same time."""
        import asyncio

        concurrent_count = 0
        max_concurrent = 0

        async def _slow_create(**kwargs: object) -> MagicMock:
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1
            return _make_tool_response(content="{}")

        with patch.object(llm_client._client.chat.completions, "create", _slow_create):
            await asyncio.gather(
                llm_client.complete_with_tools([{"role": "user", "content": "a"}], _DUMMY_TOOLS),
                llm_client.complete_with_tools([{"role": "user", "content": "b"}], _DUMMY_TOOLS),
                llm_client.complete_with_tools([{"role": "user", "content": "c"}], _DUMMY_TOOLS),
            )
        assert max_concurrent == 1
