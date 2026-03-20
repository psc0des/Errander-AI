"""Tests for secrets interface."""

from __future__ import annotations

import os

import pytest

from automaint.integrations.secrets import get_secret


class TestSecrets:
    """Tests for secret retrieval."""

    def test_get_secret_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_SECRET", "value123")
        assert get_secret("TEST_SECRET") == "value123"

    def test_get_secret_default(self) -> None:
        assert get_secret("NONEXISTENT_SECRET", default="fallback") == "fallback"

    def test_get_secret_missing_raises(self) -> None:
        with pytest.raises(ValueError, match="Required secret not set"):
            get_secret("DEFINITELY_NOT_SET_12345")
