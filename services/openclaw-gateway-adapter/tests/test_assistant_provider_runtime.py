import subprocess
from unittest.mock import MagicMock, patch

import pytest

from assistant_provider_runtime import AssistantProviderRuntime


@pytest.fixture(autouse=True)
def _clear_provider_binary_env(monkeypatch):
    monkeypatch.delenv("PANTHEON_ASSISTANT_CLAUDE_BIN", raising=False)
    monkeypatch.delenv("PANTHEON_ASSISTANT_CODEX_BIN", raising=False)


def test_readiness_probe_binary_missing():
    runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
    # Mock shutil.which to return None
    with patch('shutil.which', return_value=None):
        result = runtime.check_readiness("non-existent-binary")
        assert result["ready"] is False
        assert "not found" in result["reason"]

def test_readiness_probe_binary_exists_but_mount_fails():
    with (
        patch('shutil.which', return_value="/usr/bin/codex"),
        patch('subprocess.check_output', return_value=b"1.0.0"),
        patch('assistant_credential_mounts.AssistantCredentialMounts.validate_mounts',
              return_value={"codex": MagicMock(ready=False, status="missing_host_mount", mount_mode="rw")}),
    ):
        runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
        result = runtime.check_readiness("codex")
        assert result["ready"] is False
        assert "missing_host_mount" in result["reason"]

def test_readiness_probe_all_good():
    with (
        patch('shutil.which', return_value="/usr/bin/codex"),
        patch('subprocess.check_output', return_value=b"1.0.0"),
        patch('assistant_credential_mounts.AssistantCredentialMounts.validate_mounts',
              return_value={"codex": MagicMock(ready=True, mount_mode="rw")}),
    ):
        runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
        result = runtime.check_readiness("codex")
        assert result["ready"] is True
        assert result["binary_path"] == "/usr/bin/codex"
        assert result["version"] == "1.0.0"
        assert result["mount_mode"] == "rw"


@pytest.mark.parametrize("provider", ["claude", "claude_cli"])
def test_readiness_probe_uses_configured_claude_binary(provider, monkeypatch):
    configured_binary = "/opt/pantheon/bin/claude"
    monkeypatch.setenv("PANTHEON_ASSISTANT_CLAUDE_BIN", configured_binary)

    with (
        patch("assistant_provider_runtime.os.path.isfile", return_value=True),
        patch("assistant_provider_runtime.os.access", return_value=True),
        patch("subprocess.check_output", return_value=b"claude 2.0.0") as check_output,
        patch(
            "assistant_credential_mounts.AssistantCredentialMounts.validate_mounts",
            return_value={"claude": MagicMock(ready=True, mount_mode="rw")},
        ),
    ):
        runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
        result = runtime.check_readiness(provider)

    assert result["ready"] is True
    assert result["binary_path"] == configured_binary
    assert result["version"] == "claude 2.0.0"
    assert result["mount_mode"] == "rw"
    check_output.assert_called_once_with([configured_binary, "--version"], stderr=subprocess.STDOUT)
