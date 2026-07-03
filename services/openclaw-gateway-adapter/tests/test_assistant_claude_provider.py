"""Tests for assistant_claude_provider — Claude Code CLI provider."""

import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_claude_provider import (
    AssistantClaudeProvider,
    ClaudeProviderError,
    ClaudeProviderResult,
    _extract_auth_fields,
    _normalize_output,
    invoke_claude,
)
from assistant_credential_mounts import CredentialMountValidation


@pytest.fixture(autouse=True)
def _clear_claude_binary_env(monkeypatch):
    monkeypatch.delenv("PANTHEON_ASSISTANT_CLAUDE_BIN", raising=False)


# ---------------------------------------------------------------------------
# AssistantClaudeProvider.readiness tests
# ---------------------------------------------------------------------------


def test_readiness_ready_no_probe():
    mounts = _mock_mounts_ready()
    provider = AssistantClaudeProvider(mounts=mounts)

    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch("subprocess.check_output", return_value=b"claude 2.0.0"),
    ):
        result = provider.readiness(auth_probe=False)

    assert result["ready"] is True
    assert result["status"] == "ready"
    assert result["auth"] == "account_session"
    assert result["auth_status"] == "not_checked"
    assert result["version"] == "claude 2.0.0"
    assert result["mount_mode"] == "rw"
    assert result["credential_mount"]["container_target"] == "claude_config"
    assert "/srv/pantheon-assistant" not in repr(result)
    assert "/home/pantheon-assistant" not in repr(result)


def test_readiness_ready_with_auth_probe():
    mounts = _mock_mounts_ready()
    provider = AssistantClaudeProvider(mounts=mounts)
    mock_result = ClaudeProviderResult(status="ok", text="ok")

    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch("subprocess.check_output", return_value=b"claude 2.0.0"),
        patch.object(AssistantClaudeProvider, "invoke", return_value=mock_result) as invoke_mock,
    ):
        result = provider.readiness(auth_probe=True)

    assert result["ready"] is True
    assert result["status"] == "ready"
    assert result["auth"] == "account_session"
    assert result["auth_status"] == "ready"
    invoke_mock.assert_called_once_with("Reply with: ok", timeout=30)


def test_readiness_degraded_binary_missing():
    provider = AssistantClaudeProvider(mounts=_mock_mounts_ready())

    with patch("assistant_claude_provider._resolve_binary", return_value=None):
        result = provider.readiness()

    assert result["ready"] is False
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "claude_binary_not_found"


def test_readiness_degraded_mount_missing():
    provider = AssistantClaudeProvider(mounts=_mock_mounts_missing())

    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch("subprocess.check_output", return_value=b"claude 2.0.0"),
    ):
        result = provider.readiness()

    assert result["ready"] is False
    assert result["status"] == "degraded"
    assert result["degraded_reason"] == "claude_mount_not_configured"


def test_readiness_degraded_auth_failed():
    mounts = _mock_mounts_ready()
    provider = AssistantClaudeProvider(mounts=mounts)
    mock_result = ClaudeProviderResult(
        status="degraded",
        text="",
        degraded_reason="auth_failure",
        exit_code=1,
        config_dir="/home/pantheon-assistant/.claude",
        diagnostic_reason="stderr_auth_failure",
    )

    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch("subprocess.check_output", return_value=b"claude 2.0.0"),
        patch.object(AssistantClaudeProvider, "invoke", return_value=mock_result),
    ):
        result = provider.readiness(auth_probe=True)

    assert result["ready"] is False
    assert result["status"] == "degraded"
    assert result["auth_status"] == "failed"
    assert result["degraded_reason"] == "claude_auth_failure"
    assert result["auth_probe"] == {
        "status": "degraded",
        "degraded_reason": "auth_failure",
        "config_dir": "claude_config",
        "exit_code": 1,
        "diagnostic_reason": "stderr_auth_failure",
    }


# ---------------------------------------------------------------------------
# _normalize_output unit tests
# ---------------------------------------------------------------------------


def test_normalize_output_plain_text():
    text, events = _normalize_output("Hello from Claude")
    assert text == "Hello from Claude"
    assert events == []


def test_normalize_output_stream_json_result_event():
    line = json.dumps({"type": "result", "result": "The answer is 42"})
    text, events = _normalize_output(line)
    assert text == "The answer is 42"
    assert len(events) == 1
    assert events[0]["type"] == "result"


def test_normalize_output_stream_json_assistant_event():
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Some text from assistant"}]
            },
        }
    )
    text, events = _normalize_output(line)
    assert "Some text from assistant" in text
    assert len(events) == 1


def test_normalize_output_mixed_lines():
    lines = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "result", "result": "Final answer"}),
        ]
    )
    text, events = _normalize_output(lines)
    assert text == "Final answer"
    assert len(events) == 2


def test_normalize_output_empty():
    text, events = _normalize_output("")
    assert text == ""
    assert events == []


def test_normalize_output_malformed_json_falls_back_to_text():
    raw = "not json at all"
    text, events = _normalize_output(raw)
    assert text == "not json at all"
    assert events == []


# ---------------------------------------------------------------------------
# invoke_claude — degraded paths
# ---------------------------------------------------------------------------


def _mock_mounts_missing():
    mounts = MagicMock()
    mounts.validate_mounts.return_value = {}
    mounts._contracts.return_value = []
    return mounts


def _validation(
    *,
    ready: bool = True,
    status: str = "ready",
    mount_mode: str = "rw",
) -> CredentialMountValidation:
    return CredentialMountValidation(
        provider="claude",
        ready=ready,
        status=status,
        configured=True,
        host_source="dedicated_service_user",
        container_target="claude_config",
        mount_mode=mount_mode,
        owner_check="matched" if ready else "not_checked",
    )


def _mock_mounts_ready(config_dir="/home/pantheon-assistant/.claude"):
    validation = _validation()
    mounts = MagicMock()
    mounts.validate_mounts.return_value = {"claude": validation}

    contract = MagicMock()
    contract.provider = "claude"
    contract.container_path = config_dir
    mounts._contracts.return_value = [contract]
    return mounts


def _mock_mounts_auth_failed(status="missing_host_mount"):
    validation = _validation(ready=False, status=status)
    mounts = MagicMock()
    mounts.validate_mounts.return_value = {"claude": validation}
    mounts._contracts.return_value = []
    return mounts


class _FakeLoginProcess:
    def __init__(self):
        self.stdout = io.StringIO("Open https://console.anthropic.com/login\nCode: WXYZ-1234\n")
        self.stderr = io.StringIO("")
        self.stdin = io.StringIO()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0 if self.returncode is None else self.returncode


def test_start_device_reauth_runs_claude_auth_login_and_captures_url():
    fake_process = _FakeLoginProcess()
    popen_calls = []

    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return fake_process

    provider = AssistantClaudeProvider(
        mounts=_mock_mounts_ready(),
        popen_func=fake_popen,
    )
    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch.object(provider, "readiness", return_value={"ready": True, "auth_status": "ready"}),
    ):
        result = provider.start_device_reauth(
            operator_id="op-1",
            reason="expired",
            capture_timeout_seconds=2,
            poll_interval_seconds=1,
            max_wait_seconds=30,
        )

    assert result["provider"] == "claude"
    assert result["status"] in {"pending", "completed"}
    assert result["verification_uri"] == "https://console.anthropic.com/login"
    assert result["user_code"] == "WXYZ-1234"
    assert popen_calls[0][0][0] == ["/usr/bin/claude", "auth", "login"]
    assert popen_calls[0][1]["stdin"] == subprocess.PIPE
    assert popen_calls[0][1]["env"]["CLAUDE_CONFIG_DIR"] == "/home/pantheon-assistant/.claude"
    assert popen_calls[0][1]["env"]["HOME"] == "/home/pantheon-assistant"


def test_claude_auth_url_code_true_is_not_treated_as_user_code():
    fields = _extract_auth_fields(
        "Open https://console.anthropic.com/oauth/authorize?client_id=abc&code=true to continue"
    )

    assert fields["verification_uri_complete"].startswith("https://console.anthropic.com/oauth/authorize")
    assert "user_code" not in fields


def test_submit_reauth_code_writes_to_live_claude_auth_process():
    fake_process = _FakeLoginProcess()
    fake_process.stdout = io.StringIO(
        "Open https://console.anthropic.com/oauth/authorize?client_id=abc&code=true\n"
    )

    provider = AssistantClaudeProvider(
        mounts=_mock_mounts_ready(),
        popen_func=lambda *args, **kwargs: fake_process,
    )
    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch.object(provider, "readiness", return_value={"ready": False, "auth_status": "failed"}),
    ):
        started = provider.start_device_reauth(
            operator_id="op-1",
            capture_timeout_seconds=2,
            poll_interval_seconds=30,
            max_wait_seconds=30,
        )
        result = provider.submit_reauth_code(
            started["reauth_session_id"],
            code="claude-auth-code-123",
            operator_id="op-1",
        )

    assert fake_process.stdin.getvalue() == "claude-auth-code-123\n"
    assert result["status"] == "code_submitted"
    assert result["code_submitted_at"]
    rendered = repr(result)
    assert "claude-auth-code-123" not in rendered
    assert result.get("user_code") is None


def test_submitted_reauth_code_exit_zero_fails_when_probe_degraded():
    fake_process = _FakeLoginProcess()
    fake_process.stdout = io.StringIO(
        "Open https://console.anthropic.com/oauth/authorize?client_id=abc&code=true\n"
    )

    provider = AssistantClaudeProvider(
        mounts=_mock_mounts_ready(),
        popen_func=lambda *args, **kwargs: fake_process,
    )
    degraded_readiness = {
        "ready": False,
        "auth_status": "failed",
        "degraded_reason": "claude_auth_probe_non_zero_exit",
    }
    with (
        patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"),
        patch.object(provider, "readiness", return_value=degraded_readiness),
        patch.object(provider, "_readiness_after_process_exit", return_value=degraded_readiness),
    ):
        started = provider.start_device_reauth(
            operator_id="op-1",
            capture_timeout_seconds=2,
            poll_interval_seconds=30,
            max_wait_seconds=30,
        )
        provider.submit_reauth_code(
            started["reauth_session_id"],
            code="claude-auth-code-123",
            operator_id="op-1",
        )
        fake_process.returncode = 0
        provider._monitor_reauth_session(started["reauth_session_id"], fake_process, 30, 30)  # noqa: SLF001

    result = provider.reauth_status(started["reauth_session_id"])
    assert result["status"] == "failed"
    assert result["code_submitted_at"]
    assert "completed_at" not in result
    assert "completedAt" not in result
    assert result["returncode"] == 0
    assert result["error_code"] == "CLAUDE_REAUTH_READY_PROBE_DEGRADED"
    assert "warning_code" not in result
    assert "warningCode" not in result
    assert (
        result["message"]
        == "Claude auth login accepted the authorization code, but readiness probe is still degraded."
    )
    assert result["readiness"]["ready"] is False
    assert "claude-auth-code-123" not in repr(result)


def test_start_device_reauth_requires_writable_claude_mount():
    provider = AssistantClaudeProvider(mounts=_mock_mounts_ready())
    provider._mounts.validate_mounts.return_value = {  # noqa: SLF001
        "claude": _validation(ready=True, mount_mode="ro")
    }
    with patch("assistant_claude_provider._resolve_binary", return_value="/usr/bin/claude"):
        with pytest.raises(ClaudeProviderError) as exc:
            provider.start_device_reauth(operator_id="op-1")
    assert exc.value.code == "CLAUDE_REAUTH_MOUNT_READ_ONLY"


def test_invoke_claude_binary_not_found():
    with patch("shutil.which", return_value=None):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "degraded"
    assert result.degraded_reason == "binary_not_found"
    assert result.text == ""


def test_invoke_claude_missing_auth_mount():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        result = invoke_claude("hello", mounts=_mock_mounts_missing())
    assert result.status == "degraded"
    assert result.degraded_reason == "auth_mount_missing"


def test_invoke_claude_auth_mount_not_ready():
    with patch("shutil.which", return_value="/usr/bin/claude"):
        result = invoke_claude("hello", mounts=_mock_mounts_auth_failed("missing_host_mount"))
    assert result.status == "degraded"
    assert "auth_mount" in result.degraded_reason


def test_invoke_claude_timeout():
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=1),
        ),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready(), timeout=1)
    assert result.status == "degraded"
    assert result.degraded_reason == "timeout"


def test_invoke_claude_malformed_output():
    completed = MagicMock()
    completed.stdout = b"\x80\x81"  # invalid UTF-8 bytes that produce replacement chars
    completed.stderr = b""
    completed.returncode = 0

    # Force _normalize_output to raise by patching it
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
        patch(
            "assistant_claude_provider._normalize_output",
            side_effect=ValueError("bad"),
        ),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "degraded"
    assert result.degraded_reason == "malformed_output"


def test_invoke_claude_non_zero_exit_no_text():
    completed = MagicMock()
    completed.stdout = b""
    completed.stderr = b"some error"
    completed.returncode = 1

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "degraded"
    assert result.degraded_reason == "non_zero_exit"
    assert result.diagnostic_reason == "stderr_unclassified"
    assert result.exit_code == 1


def test_invoke_claude_non_zero_exit_empty_stderr_has_safe_diagnostic():
    completed = MagicMock()
    completed.stdout = b""
    completed.stderr = b""
    completed.returncode = 1

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "degraded"
    assert result.degraded_reason == "non_zero_exit_no_stderr"
    assert result.diagnostic_reason == "stderr_empty"


def test_invoke_claude_permission_denied_stderr_has_safe_diagnostic():
    completed = MagicMock()
    completed.stdout = b""
    completed.stderr = b"EACCES: permission denied, open '/home/pantheon-assistant/.claude.json'"
    completed.returncode = 1

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "degraded"
    assert result.degraded_reason == "config_permission_denied"
    assert result.diagnostic_reason == "stderr_permission_denied"


def test_invoke_claude_auth_failure_in_stderr():
    completed = MagicMock()
    completed.stdout = b""
    completed.stderr = b"Error: not authenticated, please login first"
    completed.returncode = 1

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "degraded"
    assert result.degraded_reason == "auth_failure"
    assert result.diagnostic_reason == "stderr_auth_failure"


# ---------------------------------------------------------------------------
# invoke_claude — happy path
# ---------------------------------------------------------------------------


def test_invoke_claude_ready_plain_text():
    completed = MagicMock()
    completed.stdout = b"Hello from Claude"
    completed.stderr = b""
    completed.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "ok"
    assert result.text == "Hello from Claude"
    assert result.exit_code == 0


def test_invoke_claude_ready_stream_json():
    event = json.dumps({"type": "result", "result": "Structured answer"})
    completed = MagicMock()
    completed.stdout = event.encode()
    completed.stderr = b""
    completed.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed),
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())
    assert result.status == "ok"
    assert result.text == "Structured answer"
    assert len(result.raw_events) == 1


def test_invoke_claude_uses_plan_permission_mode():
    completed = MagicMock()
    completed.stdout = b"Hello from Claude"
    completed.stderr = b""
    completed.returncode = 0

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())

    assert result.status == "ok"
    argv = run_mock.call_args.args[0]
    env = run_mock.call_args.kwargs["env"]
    assert argv == [
        "/usr/bin/claude",
        "-p",
        "hello",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "plan",
    ]
    assert env["CLAUDE_CONFIG_DIR"] == "/home/pantheon-assistant/.claude"
    assert env["HOME"] == "/home/pantheon-assistant"


def test_invoke_claude_uses_configured_binary_path(monkeypatch):
    monkeypatch.setenv("PANTHEON_ASSISTANT_CLAUDE_BIN", "/opt/pantheon/bin/claude")
    completed = MagicMock()
    completed.stdout = b"Hello from configured Claude"
    completed.stderr = b""
    completed.returncode = 0

    with (
        patch("assistant_claude_provider.os.path.isfile", return_value=True),
        patch("assistant_claude_provider.os.access", return_value=True),
        patch("shutil.which") as which_mock,
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())

    assert result.status == "ok"
    assert run_mock.call_args.args[0][0] == "/opt/pantheon/bin/claude"
    which_mock.assert_not_called()


def test_invoke_claude_configured_binary_missing_degrades(monkeypatch):
    monkeypatch.setenv("PANTHEON_ASSISTANT_CLAUDE_BIN", "/opt/pantheon/bin/missing-claude")

    with patch("assistant_claude_provider.os.path.isfile", return_value=False):
        result = invoke_claude("hello", mounts=_mock_mounts_ready())

    assert result.status == "degraded"
    assert result.degraded_reason == "binary_not_found"


# ---------------------------------------------------------------------------
# ClaudeProviderResult.to_dict
# ---------------------------------------------------------------------------


def test_provider_result_to_dict_ok():
    r = ClaudeProviderResult(status="ok", text="hi", exit_code=0, config_dir="/home/pa/.claude")
    d = r.to_dict()
    assert d["provider"] == "claude"
    assert d["status"] == "ok"
    assert d["text"] == "hi"
    assert d["config_dir"] == "claude_config"
    assert "/home/pa/.claude" not in repr(d)
    assert "degraded_reason" not in d


def test_provider_result_to_dict_degraded():
    r = ClaudeProviderResult(
        status="degraded",
        text="",
        degraded_reason="timeout",
        config_dir="",
    )
    d = r.to_dict()
    assert d["status"] == "degraded"
    assert d["degraded_reason"] == "timeout"
    assert d["text"] == ""
