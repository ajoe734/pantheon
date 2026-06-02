from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest


ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from assistant_command_policy import (  # noqa: E402
    ASSISTANT_COMMAND_TOOL_NAME,
    AssistantCommandAuditLog,
    AssistantCommandBroker,
    AssistantCommandDenied,
    AssistantCommandPolicy,
)
from tool_workflow_bridge import BridgeAuditLog, BridgeError, ToolPolicy, ToolWorkflowBridge  # noqa: E402


def _audit(tmp_path: Path) -> AssistantCommandAuditLog:
    return AssistantCommandAuditLog(path=tmp_path / "assistant_command_audit.jsonl")


def test_user_mode_denies_every_command_and_audits(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    broker = AssistantCommandBroker(
        audit_log=audit,
        command_id_factory=lambda: "asst_cmd_fixed",
    )

    with pytest.raises(AssistantCommandDenied) as exc_info:
        broker.request_command(
            session_id="s1",
            operator_id="op1",
            mode="user",
            command_class="repo_status",
            argv=["git", "status", "-sb"],
            trace_id="trace-user",
        )

    assert exc_info.value.decision.policy_class == "mode_denied"
    entries = audit.read(session_id="s1", operator_id="op1")
    assert len(entries) == 1
    assert entries[0]["event_type"] == "assistant.command.denied"
    assert entries[0]["outcome"] == "denied"
    assert entries[0]["policy_class"] == "mode_denied"
    assert entries[0]["argv_hash"]


def test_denied_command_audit_omits_raw_secret_argv(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    broker = AssistantCommandBroker(audit_log=audit)

    with pytest.raises(AssistantCommandDenied):
        broker.request_command(
            session_id="s1",
            operator_id="op1",
            mode="kernel_debug",
            command_class="health_probe",
            argv=["curl", "http://localhost:8000/healthz?token=secret-token-123456"],
        )

    raw_audit = (tmp_path / "assistant_command_audit.jsonl").read_text(encoding="utf-8")
    entry = audit.read(session_id="s1", operator_id="op1")[0]
    assert entry["event_type"] == "assistant.command.denied"
    assert entry["policy_decision"] == "denied"
    assert entry["argv_head"] == "curl"
    assert entry["argv_hash"]
    assert "secret-token-123456" not in raw_audit
    assert "healthz?token=" not in raw_audit


def test_expired_kernel_session_denies_command_and_audits(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    broker = AssistantCommandBroker(audit_log=audit)
    expired_at = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with pytest.raises(AssistantCommandDenied) as exc_info:
        broker.request_command(
            session_id="s-expired",
            operator_id="op1",
            mode="kernel_debug",
            command_class="repo_status",
            argv=["git", "status", "-sb"],
            session_expires_at=expired_at,
        )

    assert exc_info.value.decision.policy_class == "session_expired"
    entries = audit.read(session_id="s-expired", operator_id="op1")
    assert len(entries) == 1
    assert entries[0]["event_type"] == "assistant.command.denied"
    assert entries[0]["policy_class"] == "session_expired"
    assert entries[0]["argv_head"] == "git"


def test_kernel_observe_allows_read_probe_and_git_status(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    broker = AssistantCommandBroker(
        audit_log=audit,
        command_id_factory=lambda: "asst_cmd_observe",
    )

    health = broker.request_command(
        session_id="s1",
        operator_id="op1",
        mode="kernel_observe",
        command_class="health_probe",
        argv=["curl", "-fsS", "http://localhost:8000/healthz"],
    )
    status = broker.request_command(
        session_id="s1",
        operator_id="op1",
        mode="kernel_observe",
        command_class="git_status",
        argv=["git", "status", "-sb"],
    )

    assert health["status"] == "allowed"
    assert health["command_class"] == "health_probe"
    assert status["status"] == "allowed"
    assert status["command_class"] == "repo_status"
    assert [entry["outcome"] for entry in audit.read(session_id="s1")] == ["allowed", "allowed"]


def test_kernel_observe_denies_code_search(tmp_path: Path) -> None:
    broker = AssistantCommandBroker(audit_log=_audit(tmp_path))

    with pytest.raises(AssistantCommandDenied) as exc_info:
        broker.request_command(
            session_id="s1",
            operator_id="op1",
            mode="kernel_observe",
            command_class="code_search",
            argv=["rg", "--max-count", "20", "ToolPolicy", "services/openclaw-gateway-adapter"],
        )

    assert exc_info.value.decision.policy_class == "not_in_allowlist"


def test_kernel_debug_allows_bounded_diagnostics() -> None:
    policy = AssistantCommandPolicy()
    allowed = [
        ("code_search", ["rg", "--max-count", "25", "ToolPolicy", "services/openclaw-gateway-adapter"]),
        ("file_slice", ["sed", "-n", "1,80p", "services/openclaw-gateway-adapter/tool_workflow_bridge.py"]),
        ("test_run", ["pytest", "services/openclaw-gateway-adapter/tests/test_assistant_command_policy.py", "-q"]),
        ("log_read", ["tail", "-n", "50", "/var/log/pantheon/sanitized/openclaw.log"]),
    ]

    for command_class, argv in allowed:
        decision = policy.evaluate(mode="kernel_debug", command_class=command_class, argv=argv)
        assert decision.allowed, decision.reason
        assert decision.policy_class == "allowlist"


@pytest.mark.parametrize(
    "argv",
    [
        ["git", "reset", "--hard"],
        ["sudo", "cat", "/etc/shadow"],
        ["cat", ".env"],
        ["curl", "https://example.com/upload"],
        ["psql", "postgres://user:pass@db/pantheon", "-c", "update t set x=1"],
        ["broker_order", "--live"],
    ],
)
def test_denylist_blocks_destructive_secret_live_and_exfiltration_commands(argv: list[str]) -> None:
    decision = AssistantCommandPolicy().evaluate(mode="kernel_debug", argv=argv)

    assert not decision.allowed
    assert decision.policy_class == "denylist"


def test_unbounded_rg_is_denied() -> None:
    decision = AssistantCommandPolicy().evaluate(
        mode="kernel_debug",
        command_class="code_search",
        argv=["rg", "ToolPolicy", "services/openclaw-gateway-adapter"],
    )

    assert not decision.allowed
    assert decision.policy_class == "not_in_allowlist"
    assert "--max-count" in decision.reason


def test_tool_workflow_bridge_requires_openclaw_tool_allowlist(tmp_path: Path) -> None:
    audit = BridgeAuditLog(path=tmp_path / "bridge_audit.jsonl")
    bridge = ToolWorkflowBridge(
        policy=ToolPolicy(allowed_tools=[]),
        audit_log=audit,
        trace_id_factory=lambda: "trace-bridge",
    )

    with pytest.raises(BridgeError) as exc_info:
        bridge.request_assistant_command(
            session_id="s1",
            operator_id="op1",
            mode="kernel_observe",
            command_class="repo_status",
            argv=["git", "status", "-sb"],
        )

    assert exc_info.value.error_code == "BRIDGE_ASSISTANT_COMMAND_DENIED"
    entries = audit.read(session_id="s1")
    assert len(entries) == 1
    assert entries[0]["request_type"] == "assistant_command"
    assert entries[0]["policy_layer"] == "openclaw_tool_policy"
    assert entries[0]["policy_class"] == "deny_all"


def test_tool_workflow_bridge_allows_when_broker_tool_and_command_are_allowed(tmp_path: Path) -> None:
    audit = BridgeAuditLog(path=tmp_path / "bridge_audit.jsonl")
    bridge = ToolWorkflowBridge(
        policy=ToolPolicy(allowed_tools=[ASSISTANT_COMMAND_TOOL_NAME]),
        audit_log=audit,
        trace_id_factory=lambda: "trace-bridge",
    )

    result = bridge.request_assistant_command(
        session_id="s1",
        operator_id="op1",
        mode="kernel_debug",
        command_class="file_slice",
        argv=["sed", "-n", "1,40p", "services/openclaw-gateway-adapter/tool_workflow_bridge.py"],
        command_id="asst_cmd_bridge",
    )

    assert result["status"] == "allowed"
    assert result["command_class"] == "file_slice"
    entries = audit.read(session_id="s1")
    assert len(entries) == 1
    assert entries[0]["event_type"] == "assistant.command.requested"
    assert entries[0]["outcome"] == "allowed"
    assert entries[0]["tool_policy_class"] == "allowlist"


def test_tool_workflow_bridge_denies_user_mode_even_when_tool_is_allowed(tmp_path: Path) -> None:
    audit = BridgeAuditLog(path=tmp_path / "bridge_audit.jsonl")
    bridge = ToolWorkflowBridge(
        policy=ToolPolicy(allowed_tools=[ASSISTANT_COMMAND_TOOL_NAME]),
        audit_log=audit,
    )

    with pytest.raises(BridgeError) as exc_info:
        bridge.request_assistant_command(
            session_id="s1",
            operator_id="op1",
            mode="user",
            command_class="repo_status",
            argv=["git", "status", "-sb"],
        )

    assert exc_info.value.details["policy_layer"] == "assistant_command_policy"
    assert exc_info.value.details["policy_class"] == "mode_denied"
    entries = audit.read(session_id="s1")
    assert entries[0]["event_type"] == "assistant.command.denied"
    assert entries[0]["policy_class"] == "mode_denied"
