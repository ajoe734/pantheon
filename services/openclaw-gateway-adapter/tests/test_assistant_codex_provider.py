from __future__ import annotations

import json
import subprocess
import sys
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


def _provider(tmp_path: Path, run_func, *, env=None, mount=None) -> AssistantCodexProvider:
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
        which_func=lambda _: "/usr/bin/codex",
        audit_log=AssistantProviderAuditLog(tmp_path / "provider-audit.jsonl", clock=_clock),
        clock=_clock,
    )


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_task_worktree(tmp_path: Path, task_id: str) -> tuple[Path, Path]:
    remote = tmp_path / "origin.git"
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / f"task-{task_id}"
    _git(tmp_path, "init", "--bare", remote.as_posix())
    worktree.mkdir(parents=True)
    _git(worktree, "init")
    _git(worktree, "config", "user.email", "assistant@example.invalid")
    _git(worktree, "config", "user.name", "Assistant Test")
    (worktree / "README.md").write_text("# test\n", encoding="utf-8")
    _git(worktree, "add", "README.md")
    _git(worktree, "commit", "-m", "initial")
    _git(worktree, "branch", "-M", f"task/{task_id}")
    _git(worktree, "remote", "add", "origin", remote.as_posix())
    return worktree_root, worktree


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
    assert result["auth_status"] == "ready"
    assert result["binary_path"] == "/usr/bin/codex"
    assert result["version"] == "codex 1.2.3"
    assert result["credential_mount"]["container_target"] == "codex_home"
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
        "hello",
    ]
    assert kwargs["cwd"] == str(tmp_path / "read-only")
    assert kwargs["timeout"] == 7
    assert kwargs["env"]["CODEX_HOME"] == "/home/pantheon-assistant/.codex"


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


def test_repair_mode_requires_task_worktree_metadata(tmp_path: Path) -> None:
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

    assert exc_info.value.code == "CODEX_REPAIR_METADATA_REQUIRED"
    assert exc_info.value.status_code == 400


def test_repair_mode_uses_workspace_write_for_task_worktree(tmp_path: Path) -> None:
    calls = []
    task_id = "ASST-OCGW-003"
    root, worktree = _init_task_worktree(tmp_path, task_id)

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    provider = _provider(
        tmp_path,
        fake_run,
        env={"PANTHEON_ASSISTANT_REPAIR_WORKTREE_ROOT": str(root)},
    )
    result = provider.invoke(
        {
            "mode": "kernel_repair",
            "prompt": "fix it",
            "metadata": {
                "operator_id": "op-1",
                "task_id": task_id,
                "task_worktree": str(worktree),
                "declared_scope": ["services/openclaw-gateway-adapter"],
            },
        }
    )
    cmd = calls[0][0]
    assert cmd[cmd.index("-C") + 1] == str(worktree)
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("-s") + 1] == "workspace-write"
    assert result["repair_workflow"]["branch"] == f"task/{task_id}"
    assert result["repair_workflow"]["merge_target"] == "dev"


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
