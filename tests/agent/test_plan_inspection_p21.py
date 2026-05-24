"""P2-1 tests — full plan artifact inspectable before approval.

Tests cover:
- Slack message includes signed URL when web_base_url is set and packages > 10
- Slack message includes CLI hint when web_base_url is not set and packages > 10
- Signed URL in token is tamper-resistant (wrong signature → rejected)
- plan_show renders all packages from the stored snapshot
"""

from __future__ import annotations

import json

import pytest

from errander.agent.graph import _format_plan_for_approval


def _make_patching_plan(n_packages: int = 15) -> dict:  # type: ignore[type-arg]
    packages = [
        {"name": f"pkg-{i}", "current": "1.0.0", "target": "2.0.0"}
        for i in range(n_packages)
    ]
    return {
        "vm_id": "web-01",
        "planned_actions": [
            {
                "action_type": "patching",
                "risk_tier": "medium",
                "params": {},
                "preview": {"packages": packages, "package_count": n_packages},
            }
        ],
    }


class TestSlackMessageFullPlanLink:
    def test_no_link_when_packages_le_10(self) -> None:
        """No extra link when all packages fit in the message."""
        plan = _make_patching_plan(n_packages=8)
        msg = _format_plan_for_approval(
            [plan], "b1", "plan-abc", "a" * 64,
            web_base_url="http://10.0.0.5:9090",
        )
        assert "plan-show" not in msg
        assert "/plans/" not in msg

    def test_cli_hint_when_no_web_base_url_and_packages_gt_10(self) -> None:
        """CLI hint shown when web_base_url is absent and packages overflow."""
        plan = _make_patching_plan(n_packages=15)
        msg = _format_plan_for_approval(
            [plan], "b1", "plan-abc", "a" * 64,
            web_base_url=None,
        )
        assert "plan-show" in msg
        assert "plan-abc" in msg

    def test_cli_hint_when_web_base_url_empty_string(self) -> None:
        """Empty string web_base_url is treated as absent."""
        plan = _make_patching_plan(n_packages=15)
        msg = _format_plan_for_approval(
            [plan], "b1", "plan-xyz", "a" * 64,
            web_base_url="",
        )
        assert "plan-show" in msg
        assert "plan-xyz" in msg

    def test_signed_url_when_web_base_url_set_and_signing_secret_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When web_base_url is set and ERRANDER_SIGNING_SECRET is present, a signed URL appears."""
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", "test-secret-for-p21-tests-minimum-length")
        plan = _make_patching_plan(n_packages=15)
        msg = _format_plan_for_approval(
            [plan], "b1", "plan-signed", "a" * 64,
            web_base_url="http://10.0.0.5:9090",
        )
        assert "/plans/plan-signed" in msg
        assert "token=" in msg
        assert "10.0.0.5:9090" in msg

    def test_cli_hint_fallback_when_signing_secret_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When signing secret is absent, falls back to CLI hint even if web_base_url is set."""
        monkeypatch.delenv("ERRANDER_SIGNING_SECRET", raising=False)
        plan = _make_patching_plan(n_packages=15)
        msg = _format_plan_for_approval(
            [plan], "b1", "plan-nosec", "a" * 64,
            web_base_url="http://10.0.0.5:9090",
        )
        assert "plan-show" in msg
        assert "plan-nosec" in msg


class TestSignedUrlTamperResistance:
    def test_tampered_token_rejected_by_verifier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A token with a replaced signature must not verify."""
        from errander.integrations.signed_url import (
            InvalidSignedTokenError,
            make_signed_token,
            verify_signed_token,
        )
        secret = b"test-secret-for-tamper-test"
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", secret.decode())

        token = make_signed_token({"plan_id": "plan-123"}, ttl_seconds=60, secret=secret)
        body_b64, _, _ = token.partition(".")

        # Replace the signature with the all-zeros HMAC (will never match a real one)
        import base64
        fake_sig = base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode()
        tampered_token = f"{body_b64}.{fake_sig}"

        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token(tampered_token, secret=secret)

    def test_expired_token_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An expired token must not verify."""
        import time

        from errander.integrations.signed_url import (
            InvalidSignedTokenError,
            make_signed_token,
            verify_signed_token,
        )
        secret = b"test-secret-for-expiry-test"
        monkeypatch.setenv("ERRANDER_SIGNING_SECRET", secret.decode())

        past_now = time.time() - 7200  # 2 hours ago
        token = make_signed_token(
            {"plan_id": "plan-expired"},
            ttl_seconds=60,
            secret=secret,
            now=past_now,
        )

        with pytest.raises(InvalidSignedTokenError):
            verify_signed_token(token, secret=secret)


class TestPlanShowCLI:
    @pytest.mark.asyncio
    async def test_plan_show_renders_all_packages(self, tmp_path: object) -> None:
        """run_plan_show must print every package from a stored snapshot."""
        from pathlib import Path
        assert isinstance(tmp_path, Path)

        from errander.main import run_plan_show
        from errander.safety.audit import AuditStore

        db_path = str(tmp_path / "test.sqlite")
        packages = [
            {"name": f"pkg-{i}", "current": "1.0", "target": "2.0"}
            for i in range(15)
        ]
        plan_json = json.dumps({
            "plan_id": "plan-test",
            "batch_id": "batch-test",
            "vm_plans": [
                {
                    "vm_id": "web-01",
                    "planned_actions": [
                        {
                            "action_type": "patching",
                            "risk_tier": "medium",
                            "params": {},
                            "preview": {"packages": packages, "package_count": 15},
                        }
                    ],
                }
            ],
        })

        async with AuditStore(db_path, strict_mode=False) as store:
            await store.save_plan_snapshot(
                plan_id="plan-test",
                batch_id="batch-test",
                env_name="production",
                plan_hash="a" * 64,
                plan_json=plan_json,
            )

        from unittest.mock import patch
        captured: list[str] = []
        with patch("builtins.print", side_effect=lambda *a: captured.append(" ".join(str(x) for x in a))):
            result = await run_plan_show("plan-test", db_path)

        assert result == 0
        output = "\n".join(captured)
        for i in range(15):
            assert f"pkg-{i}" in output, f"pkg-{i} missing from plan-show output"

    @pytest.mark.asyncio
    async def test_plan_show_not_found_returns_1(self, tmp_path: object) -> None:
        """run_plan_show must return 1 when plan_id does not exist."""
        from pathlib import Path
        assert isinstance(tmp_path, Path)

        from errander.main import run_plan_show

        db_path = str(tmp_path / "empty.sqlite")

        result = await run_plan_show("plan-does-not-exist", db_path)
        assert result == 1
