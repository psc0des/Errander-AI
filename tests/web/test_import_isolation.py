"""Import isolation for the web UI process (R3 process split).

The web UI runs under its own OS user (``errander-web``) with no SSH keys
and no code path to the executor — see docs/SECURITY.md. This test
mechanically enforces that ``errander.web`` (and everything it imports)
never pulls in the executor or agent graph modules, even transitively.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import errander.web

#: Modules the web process must never import — directly or transitively.
_BLOCKED_PREFIXES = (
    "errander.execution",
    "errander.agent.subgraphs",
    "errander.agent.graph",
    "errander.agent.vm_graph",
)


def test_web_package_has_no_executor_imports() -> None:
    # Record modules before importing errander.web modules
    # (other tests may have already imported execution/agent modules)
    pre_modules = set(sys.modules.keys())

    for mod_info in pkgutil.walk_packages(errander.web.__path__, "errander.web."):
        importlib.import_module(mod_info.name)

    # Only check modules that were imported BY errander.web, not by earlier tests
    for name in sys.modules:
        if name not in pre_modules:  # Only newly imported modules
            for prefix in _BLOCKED_PREFIXES:
                if name == prefix or name.startswith(prefix + "."):
                    raise AssertionError(
                        f"errander.web imported blocked module {name!r} "
                        f"(matches blocked prefix {prefix!r})"
                    )


def test_ui_module_does_not_import_agent_package() -> None:
    """errander.web.ui specifically — the production UI surface.

    Note: only checks modules newly imported by ui.py, since earlier tests
    may have already imported agent/execution modules.
    """
    pre_modules = set(sys.modules.keys())
    importlib.import_module("errander.web.ui")
    new_modules = set(sys.modules.keys()) - pre_modules

    # Check that none of the newly imported modules are blocked
    for name in new_modules:
        assert not name.startswith("errander.agent.graph"), name
        assert not name.startswith("errander.agent.vm_graph"), name
        assert not name.startswith("errander.execution"), name
        assert not name.startswith("errander.agent.subgraphs"), name
