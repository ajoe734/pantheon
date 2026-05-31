from __future__ import annotations

import sys
from pathlib import Path

import pytest


ADAPTER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ADAPTER_DIR.parents[1]
BFF_DIR = REPO_ROOT / "services" / "control-plane" / "bff"
for path in (ADAPTER_DIR, BFF_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from assistant.redaction import RedactionError, RedactionResult, RedactionSummary  # noqa: E402
from assistant_provider_runtime import (  # noqa: E402
    AssistantProviderRuntime,
    AssistantProviderRuntimeError,
    ProviderInvocationRequest,
)


def test_provider_invocation_and_transcript_are_redacted_before_use() -> None:
    runner_payloads: list[dict] = []
    transcripts: list[dict] = []

    def runner(payload):
        runner_payloads.append(payload)
        return "provider output Authorization: Bearer output-token-123456"

    runtime = AssistantProviderRuntime(runner, transcript_sink=transcripts.append)
    result = runtime.invoke(
        ProviderInvocationRequest(
            provider="codex_cli",
            mode="user",
            prompt="Authorization: Bearer input-token-123456",
            context_pack={
                "env": {"DATABASE_URL": "postgres://user:pass@db/pantheon"},
                "provider_session": "/srv/pantheon-assistant/.codex/auth.json",
            },
            metadata={"broker_secret": "broker-secret-value"},
        )
    )

    assert runner_payloads == [result.invocation_payload]
    invocation_text = repr(runner_payloads[0])
    assert "input-token-123456" not in invocation_text
    assert "postgres://user:pass" not in invocation_text
    assert "/srv/pantheon-assistant/.codex" not in invocation_text
    assert "broker-secret-value" not in invocation_text
    assert "Bearer [REDACTED_TOKEN]" in invocation_text
    assert "[REDACTED_CREDENTIALS]" in invocation_text
    assert "[REDACTED_PROVIDER_SESSION_PATH]" in invocation_text
    assert "[REDACTED_BROKER_CREDENTIAL]" in invocation_text

    assert transcripts == [result.transcript_payload]
    transcript_text = repr(transcripts[0])
    assert "output-token-123456" not in transcript_text
    assert result.output == "provider output Authorization: Bearer [REDACTED_TOKEN]"
    assert result.redaction["provider_invocation"]["enabled"] is True
    assert result.redaction["transcript_persistence"]["enabled"] is True


def test_user_mode_redaction_failure_fails_closed_before_provider_call() -> None:
    calls: list[dict] = []
    transcripts: list[dict] = []

    def failing_redactor(*args, **kwargs):
        raise RedactionError("boom")

    runtime = AssistantProviderRuntime(
        lambda payload: calls.append(payload),
        transcript_sink=transcripts.append,
        redactor=failing_redactor,
    )

    with pytest.raises(AssistantProviderRuntimeError) as exc_info:
        runtime.invoke(ProviderInvocationRequest(provider="codex_cli", mode="user", prompt="hello"))

    assert exc_info.value.code == "REDACTION_FAILED"
    assert exc_info.value.stage == "provider_invocation"
    assert calls == []
    assert transcripts == []


class BrokenMapping(dict):
    def items(self):  # type: ignore[override]
        raise RuntimeError("cannot iterate")


def test_kernel_override_suppresses_unredactable_payload_before_provider_call() -> None:
    runner_payloads: list[dict] = []

    def runner(payload):
        runner_payloads.append(payload)
        return "diagnostic complete"

    runtime = AssistantProviderRuntime(runner, kernel_redaction_override=True)
    result = runtime.invoke(
        ProviderInvocationRequest(
            provider="codex_cli",
            mode="kernel_debug",
            prompt="debug",
            context_pack=BrokenMapping({"token": "raw-secret"}),
        )
    )

    assert runner_payloads[0]["redaction_failed"] is True
    assert runner_payloads[0]["payload"] == "[REDACTION_FAILED_PAYLOAD_SUPPRESSED]"
    assert "raw-secret" not in repr(runner_payloads[0])
    assert result.output == "diagnostic complete"
    assert result.redaction["provider_invocation"]["failed"] is True


def test_transcript_redaction_failure_does_not_persist() -> None:
    transcripts: list[dict] = []

    def redactor(payload, *, stage, **kwargs):
        if stage == "transcript_persistence":
            raise RedactionError("transcript boom")
        return RedactionResult(
            value=payload,
            summary=RedactionSummary(enabled=True),
        )

    runtime = AssistantProviderRuntime(
        lambda payload: "unpersisted output",
        transcript_sink=transcripts.append,
        redactor=redactor,
    )

    with pytest.raises(AssistantProviderRuntimeError) as exc_info:
        runtime.invoke(ProviderInvocationRequest(provider="codex_cli", mode="user", prompt="hello"))

    assert exc_info.value.stage == "transcript_persistence"
    assert transcripts == []
