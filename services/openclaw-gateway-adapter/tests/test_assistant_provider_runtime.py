import pytest
from unittest.mock import MagicMock, patch
from assistant_provider_runtime import AssistantProviderRuntime

def test_readiness_probe_binary_missing():
    runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
    # Mock shutil.which to return None
    with patch('shutil.which', return_value=None):
        result = runtime.check_readiness("non-existent-binary")
        assert result["ready"] is False
        assert "not found" in result["reason"]

def test_readiness_probe_binary_exists_but_mount_fails():
    # Mock shutil.which to return a fake path
    with patch('shutil.which', return_value="/usr/bin/codex"), 
         patch('subprocess.check_output', return_value=b"1.0.0"), 
         patch('assistant_credential_mounts.AssistantCredentialMounts.validate_mounts', return_value={"codex": MagicMock(ready=False, status="missing_host_mount", mount_mode="rw")}):
        
        runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
        result = runtime.check_readiness("codex")
        assert result["ready"] is False
        assert "missing_host_mount" in result["reason"]

def test_readiness_probe_all_good():
    # Mock shutil.which to return a fake path
    with patch('shutil.which', return_value="/usr/bin/codex"), 
         patch('subprocess.check_output', return_value=b"1.0.0"), 
         patch('assistant_credential_mounts.AssistantCredentialMounts.validate_mounts', return_value={"codex": MagicMock(ready=True, mount_mode="rw")}):
        
        runtime = AssistantProviderRuntime(lambda p: {"status": "ok"})
        result = runtime.check_readiness("codex")
        assert result["ready"] is True
        assert result["binary_path"] == "/usr/bin/codex"
        assert result["version"] == "1.0.0"
        assert result["mount_mode"] == "rw"
