"""Playwright test prerequisites.

Skips all UI tests gracefully when the Chromium browser binary is not
installed, rather than ERRORing with a missing-executable message.

To install: uv run playwright install chromium
"""

from __future__ import annotations

import glob
import os

import pytest


def _chromium_installed() -> bool:
    pattern = os.path.expanduser(
        "~/.cache/ms-playwright/chromium*/chrome-headless-shell-linux64/chrome-headless-shell"
    )
    return bool(glob.glob(pattern))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if _chromium_installed():
        return
    skip = pytest.mark.skip(
        reason="Chromium not installed — run: uv run playwright install chromium"
    )
    for item in items:
        item_path = str(item.fspath)
        if "tests/ui" in item_path or "tests\\ui" in item_path:
            item.add_marker(skip)
