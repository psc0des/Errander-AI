"""Layer-A isolation contract for the investigation engine (fable-plan §5.1).

The investigation agent investigates and recommends; it must NEVER import a
Layer B execution path or any store it could write through. Enforced by
statically scanning every import statement (direct imports — the contract is
"this module does not import X") in the two Phase 2 modules, plus a runtime
check that a fresh import doesn't pull Layer B transitively.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import errander.agent.investigation_agent as agent_mod
import errander.agent.investigation_tools as tools_mod

#: Prefixes the investigation engine must never import — directly.
#: Note: proposal_store and approval_store are BOTH forbidden — the agent
#: originates suggestions but never writes them (the caller files proposals).
_FORBIDDEN = (
    "errander.execution",
    "errander.agent.subgraphs",
    "errander.agent.graph",
    "errander.agent.vm_graph",
    "errander.safety.approval_store",
    "errander.safety.proposal_store",
    "errander.safety.locking",
)


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _assert_clean(path: Path) -> None:
    for name in _imported_names(path):
        for bad in _FORBIDDEN:
            assert not (name == bad or name.startswith(bad + ".")), (
                f"{path.name} imports forbidden Layer-B module {name!r}"
            )


def test_investigation_agent_imports_no_layer_b() -> None:
    _assert_clean(Path(agent_mod.__file__))


def test_investigation_tools_imports_no_layer_b() -> None:
    _assert_clean(Path(tools_mod.__file__))


def test_fresh_import_pulls_no_layer_b_transitively() -> None:
    """A subprocess importing the modules must not load Layer B anywhere."""
    code = (
        "import importlib, sys\n"
        "importlib.import_module('errander.agent.investigation_agent')\n"
        "importlib.import_module('errander.agent.investigation_tools')\n"
        "bad = [m for m in sys.modules if m.startswith("
        "('errander.execution','errander.agent.subgraphs','errander.agent.graph',"
        "'errander.agent.vm_graph','errander.safety.approval_store',"
        "'errander.safety.proposal_store'))]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"fresh import pulled Layer B: {result.stdout}{result.stderr}"
    )


def test_modules_actually_importable() -> None:
    # Sanity: the scan above is meaningless if the modules don't import.
    importlib.import_module("errander.agent.investigation_agent")
    importlib.import_module("errander.agent.investigation_tools")
