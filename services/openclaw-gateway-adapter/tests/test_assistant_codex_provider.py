from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


_ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

from assistant_codex_provider import (  # noqa: E402
    AssistantCodexProvider,
    AssistantProviderAuditLog,
    CodexProviderError,
)
from assistant_credential_mounts import CredentialMountValidation  # noqa: E402


def _clock() -> datetime:
    return datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)


def _ready_mount() -> CredentialMountValidation:
    return CredentialMountValidation(
        provider="codex",
        ready=True,
        status="ready",
        configured=True,
        host_source="dedicated_service_user",
        container_target="codex_home",
        mount_mode="rw",
        owner_check="matched",
    )


def _degraded_mount(status: str = "missing_host_mount") -> CredentialMountValidation:
    return CredentialMountValidation(
        provider="codex",
        ready=False,
        status=status,
        configured=True,
        host_source="dedicated_service_user",
        container_target="codex_home",
        mount_mode="rw",
        owner_check="not_checked",
    )


class FakeMounts:
    def __init__(self, validation: CredentialMountValidation) -> None:
        self.validation = validation

    def validate_mounts(self):
        return {"codex": self.validation}


def _provider(tmp_path: Path, run_func, *, env=None, mount=None, popen_func=None) -> AssistantCodexProvider:
    (tmp_path / "read-only").mkdir(parents=True, exist_ok=True)
    environ = {
        "PANTHEON_ASSISTANT_CODEX_BIN": "codex",
        "PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME": "/home/pantheon-assistant/.codex",
        "PANTHEON_ASSISTANT_CODEX_WORKSPACE": str(tmp_path / "read-only"),
        "PANTHEON_ASSISTANT_COMMAND_TIMEOUT_SECONDS": "7",
        **(env or {}),
    }
    return AssistantCodexProvider(
        environ=environ,
        mounts=FakeMounts(mount or _ready_mount()),
        run_func=run_func,
        popen_func=popen_func,
        which_func=lambda _: "/usr/bin/codex",
        audit_log=AssistantProviderAuditLog(tmp_path / "provider-audit.jsonl", clock=_clock),
        clock=_clock,
    )


class FakeDeviceAuthProcess:
    def __init__(self, stdout: str, stderr: str = "") -> None:
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_readiness_ready_with_auth_probe(tmp_path: Path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["/usr/bin/codex", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="codex 1.2.3\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"type":"message","content":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)
    result = provider.readiness(auth_probe=True)

    assert result["ready"] is True
    assert result["status"] == "ready"
    assert result["capabilities"]["read"] is True
    assert result["capabilities"]["repairWrite"] is False
    assert result["auth_status"] == "ready"
    assert result["binary_path"] == "/usr/bin/codex"
    assert result["version"] == "codex 1.2.3"
    assert result["credential_mount"]["container_target"] == "codex_home"
    assert result["usage"]["status"] == "unknown"
    auth_cmd = calls[1][0]
    assert auth_cmd[:7] == [
        "/usr/bin/codex",
        "exec",
        "-C",
        str(tmp_path / "read-only"),
        "--skip-git-repo-check",
        "-s",
        "read-only",
    ]
    assert "--dangerously-bypass-approvals-and-sandbox" not in auth_cmd
    assert auth_cmd[-1] == "-"
    assert calls[1][1]["input"] == "Reply with exactly: ok"
    assert calls[1][1]["env"]["CODEX_HOME"] == "/home/pantheon-assistant/.codex"


def test_readiness_degraded_when_mount_fails(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="codex 1.2.3\n", stderr="")

    provider = _provider(tmp_path, fake_run, mount=_degraded_mount())
    result = provider.readiness()

    assert result["ready"] is False
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "codex_mount_missing_host_mount"
    assert result["auth_status"] == "mount_unavailable"


def test_device_reauth_captures_code_and_reprobes_readiness(tmp_path: Path) -> None:
    popen_calls = []
    process = FakeDeviceAuthProcess(
        "Open https://auth.openai.com/device in your browser\n"
        "Enter code: ABCD-EFGH\n"
    )

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return process

    def fake_run(cmd, **kwargs):
        if cmd == ["/usr/bin/codex", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="codex 1.2.3\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run, popen_func=fake_popen)
    result = provider.start_device_reauth(
        operator_id="op-1",
        trace_id="trace-reauth-1",
        capture_timeout_seconds=3,
        poll_interval_seconds=1,
        max_wait_seconds=30,
    )

    assert result["provider"] == "codex_cli"
    assert result["verification_uri"] == "https://auth.openai.com/device"
    assert result["user_code"] == "ABCD-EFGH"
    assert result["credential_exchange"]["bff_handles_credentials"] is False
    assert result["credential_exchange"]["frontend_handles_credentials"] is False
    assert result["credential_exchange"]["provider_cli_writes_mount"] is True

    cmd, kwargs = popen_calls[0]
    assert cmd == ["/usr/bin/codex", "login", "--device-auth"]
    assert kwargs["env"]["CODEX_HOME"] == "/home/pantheon-assistant/.codex"

    status = result
    for _ in range(20):
        status = provider.reauth_status(result["reauth_session_id"])
        if status["status"] == "completed":
            break
        time.sleep(0.05)

    assert status["status"] == "completed"
    assert status["readiness"]["ready"] is True
    assert process.terminated is True


def test_device_reauth_requires_writable_mount(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="codex 1.2.3\n", stderr="")

    read_only_mount = CredentialMountValidation(
        provider="codex",
        ready=True,
        status="ready",
        configured=True,
        host_source="dedicated_service_user",
        container_target="codex_home",
        mount_mode="ro",
        owner_check="matched",
    )
    provider = _provider(tmp_path, fake_run, mount=read_only_mount)

    with pytest.raises(CodexProviderError) as exc_info:
        provider.start_device_reauth(operator_id="op-1")

    assert exc_info.value.code == "CODEX_REAUTH_MOUNT_READ_ONLY"
    assert exc_info.value.status_code == 409


def test_invoke_uses_read_only_workspace_by_default(tmp_path: Path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)
    result = provider.invoke({"mode": "user", "prompt": "hello", "metadata": {"operator_id": "op-1"}})

    cmd, kwargs = calls[0]
    assert result["status"] == "completed"
    assert result["sandbox"] == "read-only"
    assert result["workspace_class"] == "read_only"
    assert cmd == [
        "/usr/bin/codex",
        "exec",
        "-C",
        str(tmp_path / "read-only"),
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-c",
        'ask_for_approval="never"',
        "--json",
        "-",
    ]
    assert kwargs["input"] == "hello"
    assert kwargs["cwd"] == str(tmp_path / "read-only")
    assert kwargs["timeout"] == 7
    assert kwargs["env"]["CODEX_HOME"] == "/home/pantheon-assistant/.codex"


def test_invoke_sends_large_prompt_on_stdin_not_argv(tmp_path: Path) -> None:
    calls = []
    prompt = "persona context\n" * 10000

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)
    result = provider.invoke({"mode": "user", "prompt": prompt, "metadata": {"operator_id": "op-1"}})

    cmd, kwargs = calls[0]
    assert result["status"] == "completed"
    assert prompt not in cmd
    assert cmd[-1] == "-"
    assert kwargs["input"] == prompt


def test_process_start_failure_returns_provider_error(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        raise OSError(7, "Argument list too long", "/usr/bin/codex")

    provider = _provider(tmp_path, fake_run)

    with pytest.raises(CodexProviderError) as exc_info:
        provider.invoke({"mode": "user", "prompt": "hello", "metadata": {"operator_id": "op-1"}})

    assert exc_info.value.code == "CODEX_PROCESS_START_FAILED"
    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is True
    assert exc_info.value.details["errno"] == 7
    audit_text = (tmp_path / "provider-audit.jsonl").read_text(encoding="utf-8")
    assert "assistant.provider.start_failed" in audit_text
    assert "Argument list too long" not in audit_text


def test_invoke_audit_records_trace_context_and_output_summary(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                '{"type":"thread.started","thread_id":"thread-test"}\n'
                '{"type":"turn.started"}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
                '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":1}}\n'
            ),
            stderr="",
        )

    provider = _provider(tmp_path, fake_run)
    provider.invoke(
        {
            "mode": "user",
            "prompt": "hello",
            "metadata": {
                "operator_id": "op-1",
                "trace_id": "trace-1",
                "provider_run_id": "trace-1",
                "session_id": "session-1",
                "message_id": "message-1",
                "tenant_id": "tenant-alpha",
                "route": "POST /bff/management/nl/ask",
            },
        }
    )

    audit_lines = [
        json.loads(line)
        for line in (tmp_path / "provider-audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    started = audit_lines[0]
    completed = audit_lines[-1]
    assert started["trace_id"] == "trace-1"
    assert started["session_id"] == "[REDACTED_SESSION]"
    assert started["message_id"] == "message-1"
    assert started["route"] == "POST /bff/management/nl/ask"
    assert completed["output_summary"]["json_event_types"] == [
        "thread.started",
        "turn.started",
        "item.completed",
        "turn.completed",
    ]
    assert completed["output_summary"]["usage"] == {"input_tokens": 3, "output_tokens": 1}
    assert "hello" not in json.dumps(audit_lines)


def test_invoke_requires_operator_id_metadata_before_exec(tmp_path: Path) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)

    with pytest.raises(CodexProviderError) as exc_info:
        provider.invoke({"mode": "user", "prompt": "hello"})

    assert exc_info.value.code == "OPERATOR_REQUIRED"
    assert exc_info.value.status_code == 401
    assert calls == []


def test_repair_mode_is_not_available_in_the_product_adapter(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    provider = _provider(tmp_path, fake_run)

    with pytest.raises(CodexProviderError) as exc_info:
        provider.invoke(
            {
                "mode": "kernel_repair",
                "prompt": "fix it",
                "metadata": {"operator_id": "op-1", "task_id": "ASST-OCGW-003"},
            }
        )

    assert exc_info.value.code == "CODEX_MODE_UNSUPPORTED"
    assert exc_info.value.status_code == 400


def test_successful_returncode_with_bwrap_namespace_error_fails_closed(tmp_path: Path) -> None:
    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"turn.started"}',
            (
                '{"type":"item.completed","item":{"type":"command_execution",'
                '"aggregated_output":"bwrap: No permissions to create a new namespace\\n",'
                '"exit_code":1,"status":"failed"}}'
            ),
            '{"type":"turn.completed"}',
        ]
    )

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    provider = _provider(tmp_path, fake_run)

    with pytest.raises(CodexProviderError) as exc_info:
        provider.invoke(
            {
                "mode": "kernel_debug",
                "prompt": "fix it",
                "metadata": {"operator_id": "op-1"},
            }
        )

    assert exc_info.value.code == "CODEX_SANDBOX_UNAVAILABLE"
    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is False
    audit_text = (tmp_path / "provider-audit.jsonl").read_text(encoding="utf-8")
    assert "assistant.provider.failed" in audit_text
    assert "CODEX_SANDBOX_UNAVAILABLE" in audit_text
    assert "assistant.provider.completed" not in audit_text


def test_timeout_records_redacted_audit_fallback(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output="ACCESS_TOKEN=abc123", stderr="oauth expired")

    provider = _provider(tmp_path, fake_run)

    with pytest.raises(CodexProviderError) as exc_info:
        provider.invoke({"mode": "kernel_observe", "prompt": "hello", "metadata": {"operator_id": "op-1"}})

    assert exc_info.value.code == "CODEX_TIMEOUT"
    audit_text = (tmp_path / "provider-audit.jsonl").read_text(encoding="utf-8")
    assert "assistant.provider.timeout" in audit_text
    assert "abc123" not in audit_text
    assert "[REDACTED_ENV_VALUE]" in audit_text


def test_auth_failure_is_classified_for_invoke_and_readiness(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        if cmd == ["/usr/bin/codex", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="codex 1.2.3\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in; run codex login")

    provider = _provider(tmp_path, fake_run)

    readiness = provider.readiness(auth_probe=True)
    assert readiness["ready"] is False
    assert readiness["degraded_reason"] == "codex_auth_unavailable"

    with pytest.raises(CodexProviderError) as exc_info:
        provider.invoke({"mode": "user", "prompt": "hello", "metadata": {"operator_id": "op-1"}})

    assert exc_info.value.code == "CODEX_AUTH_UNAVAILABLE"
    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is False


def test_audit_redaction_failure_suppresses_payload(tmp_path: Path) -> None:
    audit = AssistantProviderAuditLog(tmp_path / "audit.jsonl", clock=_clock)

    def broken_redactor(*args, **kwargs):
        raise RuntimeError("boom")

    # Monkeypatch the module global so the audit fallback path is exercised
    # without leaking the raw event.
    import assistant_codex_provider

    original = assistant_codex_provider.redact_assistant_payload
    assistant_codex_provider.redact_assistant_payload = broken_redactor
    try:
        audit.record({"event_type": "assistant.provider.started", "prompt": "secret"})
    finally:
        assistant_codex_provider.redact_assistant_payload = original

    payload = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert payload["event_type"] == "assistant.redaction.failed"
    assert payload["payload"] == "[REDACTION_FAILED_PAYLOAD_SUPPRESSED]"
    assert "secret" not in json.dumps(payload)


def test_invoke_attaches_images_with_dash_i_flag(tmp_path: Path) -> None:
    import base64 as _b64

    raw = b"\x89PNG\r\n\x1a\nFAKE-IMAGE-BYTES"
    data_url = "data:image/png;base64," + _b64.b64encode(raw).decode("ascii")
    seen = {}

    def fake_run(cmd, **kwargs):
        imgs = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-i"]
        seen["cmd"] = cmd
        seen["images"] = imgs
        seen["bytes"] = [Path(p).read_bytes() for p in imgs]
        seen["exists_during_run"] = all(Path(p).exists() for p in imgs)
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)
    result = provider.invoke(
        {
            "mode": "user",
            "prompt": "what colour is this?",
            "metadata": {"operator_id": "op-1"},
            "attachments": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                    "mimeType": "image/png",
                    "filename": "x.png",
                }
            ],
        }
    )

    assert result["status"] == "completed"
    assert len(seen["images"]) == 1
    assert seen["images"][0].endswith(".png")
    assert seen["bytes"][0] == raw
    assert seen["exists_during_run"] is True
    # prompt still travels on stdin; command still ends with the "-" sentinel
    assert seen["cmd"][-1] == "-"
    assert seen["input"] == "what colour is this?"
    # temp dir is cleaned up after the run completes
    assert not Path(seen["images"][0]).exists()


def test_invoke_reads_images_from_messages_content(tmp_path: Path) -> None:
    import base64 as _b64

    raw = b"JPEGFAKE"
    data_url = "data:image/jpeg;base64," + _b64.b64encode(raw).decode("ascii")
    seen = {}

    def fake_run(cmd, **kwargs):
        imgs = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-i"]
        seen["images"] = imgs
        seen["bytes"] = [Path(p).read_bytes() for p in imgs]
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)
    result = provider.invoke(
        {
            "mode": "user",
            "prompt": "describe",
            "metadata": {"operator_id": "op-1"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
    )

    assert result["status"] == "completed"
    assert len(seen["images"]) == 1
    assert seen["images"][0].endswith(".jpg")
    assert seen["bytes"][0] == raw


def test_invoke_degrades_to_text_only_on_bad_image_url(tmp_path: Path) -> None:
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='{"final":"ok"}\n', stderr="")

    provider = _provider(tmp_path, fake_run)
    result = provider.invoke(
        {
            "mode": "user",
            "prompt": "hello",
            "metadata": {"operator_id": "op-1"},
            "attachments": [
                {"type": "image_url", "image_url": {"url": "not-a-data-url"}, "mimeType": "image/png"}
            ],
        }
    )

    assert result["status"] == "completed"
    assert "-i" not in seen["cmd"]
    assert seen["cmd"][-1] == "-"
