"""Layer A agentic investigation engine (fable-plan Phase 2).

A bounded, read-only ReAct loop: the LLM is given read-only tools and a
budget, decides which to call, observes results, and iterates — then emits a
structured :class:`AssistantResponse` that may carry ``proposed_work`` (LOW-
risk actions it recommends running). This is the agentic upgrade of the fixed-
context :class:`OperatorAssistant.investigate`.

Hard boundary (mirrors operator_assistant.py; test-enforced):
  - Layer A only. This module must NOT import SandboxExecutor, FileLocker,
    ApprovalRequestStore, ProposalStore, SSH execution, or any
    agent.subgraphs / agent.graph path. It investigates and recommends; it
    never writes a store and never executes.
  - Every tool is read-only (see investigation_tools.py).
  - Its output flows to a human → deterministic Layer B for any real change.
    ``proposed_work`` items are *suggestions*, converted to AgentProposals by
    the caller (which owns the store) — the agent itself writes nothing.

Guardrails (fable-plan §5): bounded tool calls + wall-clock timeout, per-hop
redaction of tool results (untrusted input), per-hop AIDecisionStore audit,
and graceful fallback to the deterministic path — the agent never blocks on
or raises from the LLM.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from errander.models.analysis import AssistantResponse, ProposedWorkItem
from errander.models.proposals import (
    AgentProposal,
    ProposalEvidence,
    ProposalKind,
)
from errander.safety.context_redactor import ContextRedactor

if TYPE_CHECKING:
    from errander.agent.investigation_tools import ToolRegistry
    from errander.integrations.llm import LLMClient
    from errander.safety.ai_audit import AIDecisionStore

logger = logging.getLogger(__name__)


class InvestigationFallback(Protocol):
    """Structural type for the deterministic fallback `investigate_agentic` calls.

    `OperatorAssistant` satisfies this. So does a cheap no-op used by the
    probe-triggered path (investigation_trigger.py) — it deliberately avoids
    OperatorAssistant's full FleetContext machinery when the caller wants a
    "do nothing further" fallback (fable-plan Phase 3, design decision D2).
    """

    async def investigate(self, question: str, **kwargs: Any) -> AssistantResponse: ...

_REDACTOR = ContextRedactor()

_SYSTEM_PROMPT = (
    "You are Errander-AI's investigation agent (Layer A: read-only). You "
    "diagnose fleet issues by calling the provided read-only tools, then you "
    "answer. You NEVER execute changes — deterministic code does that after a "
    "human approves.\n\n"
    "Work in steps: call tools to gather evidence, then produce a final answer. "
    "When you have enough evidence, respond with ONLY a JSON object (no prose, "
    "no markdown fences) of the form:\n"
    "{\n"
    '  "summary": "<one-paragraph answer>",\n'
    '  "findings": [{"text": "<finding>", "evidence": ["<tool name you used>"]}],\n'
    '  "recommendations": ["<human-actionable recommendation>"],\n'
    '  "risk_level": "low|medium|high|unknown",\n'
    '  "proposed_work": [{"vm_id": "<id>", "action_type": "disk_cleanup|log_rotation", '
    '"rationale": "<why>"}]\n'
    "}\n"
    "Only propose disk_cleanup or log_rotation, only for VMs you have evidence "
    "for, and only when the evidence justifies it. Use an empty proposed_work "
    "list when no action is warranted — that is a valid, common answer."
)


class InvestigationAgent:
    """Bounded read-only tool-calling investigation loop (Layer A)."""

    def __init__(
        self,
        *,
        max_tool_calls: int = 8,
        timeout_seconds: int = 60,
    ) -> None:
        self._max_tool_calls = max_tool_calls
        self._timeout_seconds = timeout_seconds

    async def investigate_agentic(
        self,
        question: str,
        *,
        tools: ToolRegistry,
        llm_client: LLMClient,
        fallback: InvestigationFallback,
        fallback_kwargs: dict[str, Any],
        ai_decision_store: AIDecisionStore | None = None,
        batch_id: str = "ask",
    ) -> AssistantResponse:
        """Run the bounded ReAct loop; fall back deterministically on any failure.

        ``fallback`` + ``fallback_kwargs`` are the deterministic path used
        when tool-calling is unsupported, the LLM is unreachable, or the
        budget is exhausted with no answer. The agent never raises to the
        operator. ``fallback`` is typically `OperatorAssistant` (the `--ask`
        CLI path) but any :class:`InvestigationFallback` works — e.g. the
        probe-triggered path's cheap no-op (investigation_trigger.py).
        """
        from errander.safety.ai_audit import AIDecision

        redacted_q, _ = _REDACTOR.redact(question)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": redacted_q},
        ]
        schemas = tools.openai_schemas()
        deadline = time.monotonic() + self._timeout_seconds
        tools_used: list[str] = []

        async def _audit(decision_type: str, outcome: str, detail: str) -> None:
            if ai_decision_store is None:
                return
            await ai_decision_store.log(AIDecision(
                batch_id=batch_id,
                decision_type=decision_type,
                model=getattr(llm_client, "_model", "unknown"),
                base_url=getattr(llm_client, "_base_url", ""),
                prompt_template_id="investigation_agent_v1",
                prompt_hash=AIDecision.hash_prompt(redacted_q),
                outcome=outcome,
                context_snapshot=detail[:2000],
            ))

        for _hop in range(self._max_tool_calls):
            if time.monotonic() > deadline:
                logger.info("Investigation agent hit wall-clock budget — falling back")
                await _audit("investigation_agent_step", "fallback", "timeout budget")
                return await self._fallback(fallback, fallback_kwargs)

            remaining = max(1, int(deadline - time.monotonic()))
            turn = await llm_client.chat_with_tools(
                messages, schemas, timeout_seconds=remaining,
            )
            if turn is None:
                logger.info("Investigation agent: LLM unavailable — falling back")
                await _audit("investigation_agent_step", "fallback", "llm unavailable")
                return await self._fallback(fallback, fallback_kwargs)

            if turn.tool_calls:
                # Record the assistant turn verbatim so tool results attach to it.
                messages.append({
                    "role": "assistant",
                    "content": turn.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in turn.tool_calls
                    ],
                })
                for tc in turn.tool_calls:
                    result = await tools.dispatch(tc.name, tc.arguments)
                    result, _ = _REDACTOR.redact(result)  # untrusted tool output
                    tools_used.append(tc.name)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                    await _audit(
                        "investigation_agent_step", "success",
                        f"tool={tc.name} args={tc.arguments}",
                    )
                continue

            # No tool calls → this is the final answer.
            parsed = self._parse_final(turn.content, tools_used)
            if parsed is None:
                await _audit("investigation_agent", "fallback", "unparseable final answer")
                return await self._fallback(fallback, fallback_kwargs)
            await _audit("investigation_agent", "success", parsed.summary[:500])
            return parsed

        logger.info("Investigation agent exhausted tool budget — falling back")
        await _audit("investigation_agent", "fallback", "tool budget exhausted")
        return await self._fallback(fallback, fallback_kwargs)

    @staticmethod
    def _parse_final(content: str | None, tools_used: list[str]) -> AssistantResponse | None:
        """Parse the model's final JSON answer, dropping invalid proposed_work.

        Evidence-honesty (fable-plan §5.4): proposed_work items and findings
        that cite tools never actually called are stripped, not trusted.
        """
        if not content or not content.strip():
            return None
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Investigation agent final answer was not JSON")
            return None
        if not isinstance(data, dict):
            return None

        # Drop invalid proposed_work items individually (never raise the whole answer).
        used = set(tools_used)
        raw_items = data.get("proposed_work") or []
        clean_items: list[dict[str, Any]] = []
        for item in raw_items if isinstance(raw_items, list) else []:
            try:
                validated = ProposedWorkItem.model_validate(item)
            except Exception:  # noqa: BLE001 — one bad item must not sink the answer
                logger.info("Dropped invalid proposed_work item: %r", item)
                continue
            clean_items.append(validated.model_dump())
        data["proposed_work"] = clean_items

        try:
            response = AssistantResponse.model_validate(data)
        except Exception:  # noqa: BLE001
            logger.warning("Investigation agent final answer failed schema validation")
            return None
        # Strip finding evidence that cites tools we never called.
        for f in response.findings:
            f.evidence = [e for e in f.evidence if e in used or not used]
        return response

    @staticmethod
    async def _fallback(
        fallback: InvestigationFallback, fallback_kwargs: dict[str, Any],
    ) -> AssistantResponse:
        return await fallback.investigate(**fallback_kwargs)


def proposed_work_to_proposals(
    response: AssistantResponse,
    *,
    env_name: str,
    valid_vm_ids: set[str],
    probe_id: str = "",
) -> list[AgentProposal]:
    """Convert validated ``proposed_work`` into AgentProposals (no store write).

    Final inventory gate (fable-plan §5.2): items whose vm_id is not in the
    live inventory for this env are dropped and logged — the agent cannot
    originate work against a VM the fleet doesn't have.
    """
    proposals: list[AgentProposal] = []
    for item in response.proposed_work:
        if item.vm_id not in valid_vm_ids:
            logger.info(
                "Dropped agent proposal for unknown VM %s (not in inventory)", item.vm_id,
            )
            continue
        try:
            proposals.append(AgentProposal(
                env_name=env_name,
                vm_id=item.vm_id,
                kind=ProposalKind.ACTION,
                action_type=item.action_type,
                signal_kind="investigation",
                origin="investigation_agent",
                probe_id=probe_id,
                evidence=[ProposalEvidence(
                    source="investigation_agent",
                    check="agentic read-only investigation",
                    observation=item.rationale,
                )],
                confidence="medium",
            ))
        except Exception as exc:  # noqa: BLE001 — defensive; validators already ran
            logger.info("Dropped proposal for %s: %s", item.vm_id, exc)
    return proposals
