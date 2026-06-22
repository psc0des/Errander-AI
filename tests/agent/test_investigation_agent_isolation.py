"""Layer-A isolation for the agentic investigation module.

investigation_agent.py must never import Layer B (execution, the LangGraph
subgraphs/graph/vm_graph) — directly or transitively. This is a new test,
not a copy of an existing one: there is no pre-existing isolation test
scoped to operator_assistant.py or any other errander.agent module — only
tests/web/test_import_isolation.py exists, and it polices a different
boundary (the web process package, not a single agent module). This test
adapts the same *technique* (snapshot sys.modules, import target, diff,
assert no blocked-prefix modules loaded) to a single module.
"""

from __future__ import annotations

import importlib
import sys

#: Modules investigation_agent.py must never import — directly or transitively.
_BLOCKED_PREFIXES = (
    "errander.execution",
    "errander.agent.subgraphs",
    "errander.agent.graph",
    "errander.agent.vm_graph",
)


def test_investigation_agent_has_no_layer_b_imports() -> None:
    pre_modules = set(sys.modules.keys())

    importlib.import_module("errander.agent.investigation_agent")

    new_modules = set(sys.modules.keys()) - pre_modules
    for name in new_modules:
        for prefix in _BLOCKED_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"errander.agent.investigation_agent imported blocked module "
                f"{name!r} (matches blocked prefix {prefix!r})"
            )
