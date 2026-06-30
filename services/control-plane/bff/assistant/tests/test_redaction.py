from __future__ import annotations

import os
import sys

import pytest


BFF_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BFF_DIR not in sys.path:
    sys.path.insert(0, BFF_DIR)

from assistant.redaction import (  # noqa: E402
    RedactionError,
    redact_assistant_payload,
    redact_payload,
    redact_text,
)


def test_redact_text_covers_representative_secret_patterns() -> None:
    raw = """
Authorization: Bearer sk-live-token-123456789
Cookie: session_id=sess_abc123; theme=dark
OPENAI_API_KEY="sk-test-abcdef"
DATABASE_URL=postgres://pantheon:super-secret@db.internal/pantheon
CODEX_HOME=/srv/pantheon-assistant/.codex/auth.json
BROKER_SECRET=broker-secret-value
broker_account=9876543210
-----BEGIN PRIVATE KEY-----
secret material
-----END PRIVATE KEY-----
"""

    redacted = redact_text(raw)

    assert "Bearer [REDACTED_TOKEN]" in redacted
    assert "session_id=[REDACTED_COOKIE]" in redacted
    assert 'OPENAI_API_KEY="[REDACTED_API_KEY]"' in redacted
    assert "postgres://[REDACTED_CREDENTIALS]@db.internal/pantheon" in redacted
    assert "[REDACTED_PROVIDER_SESSION_PATH]" in redacted
    assert "BROKER_SECRET=[REDACTED_BROKER_CREDENTIAL]" in redacted
    assert "broker_account=[REDACTED_ACCOUNT]" in redacted
    assert "[REDACTED_PRIVATE_KEY]" in redacted
    assert "sk-live-token-123456789" not in redacted
    assert "super-secret" not in redacted
    assert "/srv/pantheon-assistant/.codex" not in redacted


def test_redact_payload_preserves_structured_shape_and_counts_fields() -> None:
    payload = {
        "headers": {
            "Authorization": "Bearer provider-token-123456",
            "Cookie": "assistant_session=sess-123; locale=en-US",
        },
        "env": {
            "DATABASE_URL": "postgres://user:pass@localhost/app",
            "SAFE_FLAG": "enabled",
        },
        "provider": {
            "credential_mount_path": "/home/pantheon-assistant/.claude",
        },
        "events": [
            {"message": "refresh_token=refresh-secret-value"},
            {"message": "plain diagnostic"},
        ],
    }

    result = redact_payload(payload)

    assert result.value["headers"]["Authorization"] == "Bearer [REDACTED_TOKEN]"
    assert result.value["headers"]["Cookie"] == "assistant_session=[REDACTED_COOKIE]; locale=[REDACTED_COOKIE]"
    assert result.value["env"]["DATABASE_URL"] == "postgres://[REDACTED_CREDENTIALS]@localhost/app"
    assert result.value["env"]["SAFE_FLAG"] == "enabled"
    assert result.value["provider"]["credential_mount_path"] == "[REDACTED_PROVIDER_SESSION_PATH]"
    assert result.value["events"][0]["message"] == "refresh_token=[REDACTED_ENV_VALUE]"
    assert result.value["events"][1]["message"] == "plain diagnostic"
    assert result.summary.redacted_fields >= 4
    assert result.summary.categories["auth_header"] == 1
    assert result.summary.categories["database_credentials"] >= 1


def test_redact_payload_keeps_reauth_tracking_id_but_not_general_session_id() -> None:
    payload = {
        "reauth_session_id": "claude_reauth_123",
        "reauthSessionId": "claude_reauth_123",
        "session_id": "browser-session-should-redact",
        "access_token": "provider-token-should-redact",
    }

    result = redact_payload(payload)

    assert result.value["reauth_session_id"] == "claude_reauth_123"
    assert result.value["reauthSessionId"] == "claude_reauth_123"
    assert result.value["session_id"] == "[REDACTED_SESSION]"
    assert result.value["access_token"] == "[REDACTED_TOKEN]"


class BrokenMapping(dict):
    def items(self):  # type: ignore[override]
        raise RuntimeError("cannot iterate")


def test_user_mode_fails_closed_on_redaction_failure() -> None:
    with pytest.raises(RedactionError, match="failed closed"):
        redact_assistant_payload(BrokenMapping({"token": "secret"}), mode="user", stage="provider_invocation")


def test_kernel_mode_requires_explicit_redaction_failure_override() -> None:
    with pytest.raises(RedactionError, match="failed closed"):
        redact_assistant_payload(BrokenMapping({"token": "secret"}), mode="kernel_debug", stage="provider_invocation")

    result = redact_assistant_payload(
        BrokenMapping({"token": "secret"}),
        mode="kernel_debug",
        stage="provider_invocation",
        allow_kernel_override=True,
    )

    assert result.value["redaction_failed"] is True
    assert result.value["payload"] == "[REDACTION_FAILED_PAYLOAD_SUPPRESSED]"
    assert result.summary.failed is True
