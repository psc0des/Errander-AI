"""Tests for SecretsRedactingFilter — log message redaction."""

from __future__ import annotations

import logging

from errander.observability.redaction import SecretsRedactingFilter, _redact


class TestRedactFunction:
    def test_redacts_openai_key(self) -> None:
        text = "Using key sk-proj-abc123defgh456ijklmnopqrstuvwxyz in request"
        assert "<redacted>" in _redact(text)
        assert "sk-proj-abc123" not in _redact(text)

    def test_redacts_slack_token(self) -> None:
        text = "Posting with xoxb-1234567890-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        result = _redact(text)
        assert "<redacted>" in result
        assert "xoxb-" not in result

    def test_redacts_enc_blob(self) -> None:
        text = "Stored value: enc:v1:gAAAAAbcdefghijklmnopqrstuvwxyz0123456789ABCD"
        result = _redact(text)
        assert "<redacted>" in result
        assert "enc:v1:" not in result

    def test_normal_messages_pass_through(self) -> None:
        text = "Starting maintenance batch for dev/web-01"
        assert _redact(text) == text

    def test_empty_string_passes_through(self) -> None:
        assert _redact("") == ""


class TestSecretsRedactingFilter:
    def _make_record(self, msg: str, args: object = None) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=args,
            exc_info=None,
        )
        return record

    def test_filter_redacts_msg(self) -> None:
        f = SecretsRedactingFilter()
        record = self._make_record("token: sk-abcdefghijklmnopqrstuvwxyz12345")
        f.filter(record)
        assert "sk-" not in record.msg
        assert "<redacted>" in record.msg

    def test_filter_redacts_tuple_args(self) -> None:
        f = SecretsRedactingFilter()
        record = self._make_record("auth: %s", args=("sk-secretkeyabcdefghijklmnopqrst",))
        f.filter(record)
        assert isinstance(record.args, tuple)
        assert "sk-" not in record.args[0]

    def test_filter_redacts_dict_args(self) -> None:
        f = SecretsRedactingFilter()
        record = self._make_record("token: %(tok)s")
        # Set dict args after construction to avoid Python logging's args[0] quirk
        record.args = {"tok": "xoxb-1234567890-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abc"}
        f.filter(record)
        assert isinstance(record.args, dict)
        assert "xoxb-" not in record.args["tok"]

    def test_filter_passes_normal_messages(self) -> None:
        f = SecretsRedactingFilter()
        record = self._make_record("Batch complete for production", args=("dev",))
        result = f.filter(record)
        assert result is True
        assert record.msg == "Batch complete for production"
