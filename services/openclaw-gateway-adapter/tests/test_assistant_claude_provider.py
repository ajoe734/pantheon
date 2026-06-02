"""Tests for assistant_claude_provider — Claude Code CLI provider."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assistant_claude_provider import (
    ClaudeProviderResult,
    _normalize_output,
    invoke_claude,
)


@pytest.fixture(autouse=True)
def _clear_claude_binary_env(monkeypatch):
    monkeypatch.delenv("PANTHEON_ASSISTANT_CLAUDE_BIN", raising=False)


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


def _mock_mounts_ready(config_dir="/home/pantheon-assistant/.claude"):
    validation = MagicMock()
    validation.ready = True
    validation.status = "ready"
    mounts = MagicMock()
    mounts.validate_mounts.return_value = {"claude": validation}

    contract = MagicMock()
    contract.provider = "claude"
    contract.container_path = config_dir
    mounts._contracts.return_value = [contract]
    return mounts


def _mock_mounts_auth_failed(status="missing_host_mount"):
    validation = MagicMock()
    validation.ready = False
    validation.status = status
    mounts = MagicMock()
    mounts.validate_mounts.return_value = {"claude": validation}
    mounts._contracts.return_value = []
    return mounts


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
    assert result.degraded_reason in {"non_zero_exit", "auth_failure"}


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
    assert argv == [
        "/usr/bin/claude",
        "-p",
        "hello",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "plan",
    ]


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
