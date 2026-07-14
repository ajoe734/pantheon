"""EVOCHAIN-005 BFF governance write integration tests."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import command_executor as bff_executor
from models import CommandType


GOVERNANCE_URL = "http://governance-approval-test:8082"
INTERNAL_URL = "http://internal-api-test:8081"


@pytest.fixture
def configure_urls(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", GOVERNANCE_URL)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_SERVICE_URL", "")
    monkeypatch.setenv("PANTHEON_INTERNAL_API_URL", INTERNAL_URL)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_API_URL", "http://evolution:8093")
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution:8093")


def test_execute_rollback_writes_to_governance(configure_urls, monkeypatch) -> None:
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, dict(payload)))
        if url.endswith("/rollbacks/execute"):
            return {
                "rollback_id": payload.get("rollback_id") or "rollback-test-id",
                "status": "completed",
                "tracking_url": "http://tracking/1",
            }
        if "/api/governance/rollbacks" in url:
            return payload
        return {}

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    params = {
        "runtime_id": "runtime-abc",
        "runtime_binding_id": "binding-abc",
        "rollback_action_type": "pause_then_replace",
        "target_artifact_id": "art-123",
        "rollback_to_version": "art-123",
    }
    
    result = bff_executor._execute_rollback(
        command_id="cmd-rollback-123",
        params=params,
        auth_token="op-user:admin",
    )

    assert result["rollback_id"].startswith("rb-unknown-")

    # Verify three calls: governance write (initiated), the internal action, and governance update (completed)
    assert len(post_calls) == 3

    gov_init_call = post_calls[0]
    assert gov_init_call[0] == f"{GOVERNANCE_URL}/api/governance/rollbacks"
    assert gov_init_call[1]["status"] == "initiated"
    
    internal_call = post_calls[1]
    assert internal_call[0] == f"{INTERNAL_URL}/api/internal/v1/rollbacks/execute"
    
    gov_call = post_calls[2]
    assert gov_call[0] == f"{GOVERNANCE_URL}/api/governance/rollbacks"
    
    gov_payload = gov_call[1]
    assert gov_payload["rollback_id"] == result["rollback_id"]
    assert gov_payload["runtime_id"] == "runtime-abc"
    assert gov_payload["action_type"] == "pause_then_replace"
    assert gov_payload["status"] == "completed"
    assert gov_payload["actor"] == "admin"
    assert gov_payload["identity"] == "op-user"
    assert gov_payload["source_command_id"] == "cmd-rollback-123"


def test_approve_reject_rollback_writes_to_governance(configure_urls, monkeypatch) -> None:
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, payload))
        if "/approve" in url:
            return {"rollback_id": "rollback-test-id", "status": "approved", "decision": "approved", "approved_at": "2026-07-13T10:00:00Z"}
        if "/reject" in url:
            return {"rollback_id": "rollback-test-id", "status": "rejected", "decision": "rejected", "rejected_at": "2026-07-13T10:05:00Z"}
        if "/api/governance/rollbacks" in url:
            return payload
        return {}

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    # Approve
    result_approve = bff_executor._execute_approve_rollback(
        command_id="cmd-approve-123",
        params={"rollback_id": "rollback-test-id", "approval_notes": "All looks good"},
        auth_token="op-user:approver",
    )
    assert result_approve["status"] == "approved"
    assert len(post_calls) == 2
    assert post_calls[1][0] == f"{GOVERNANCE_URL}/api/governance/rollbacks"
    assert post_calls[1][1]["status"] == "approved"
    assert post_calls[1][1]["actor"] == "approver"
    assert post_calls[1][1]["identity"] == "op-user"

    # Reject
    post_calls.clear()
    result_reject = bff_executor._execute_reject_rollback(
        command_id="cmd-reject-123",
        params={"rollback_id": "rollback-test-id", "rejection_reason": "Not approved yet"},
        auth_token="op-user:approver",
    )
    assert result_reject["status"] == "rejected"
    assert len(post_calls) == 2
    assert post_calls[1][0] == f"{GOVERNANCE_URL}/api/governance/rollbacks"
    assert post_calls[1][1]["status"] == "rejected"
    assert post_calls[1][1]["actor"] == "approver"
    assert post_calls[1][1]["identity"] == "op-user"


def test_activate_kill_switch_writes_to_governance(configure_urls, monkeypatch) -> None:
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, payload))
        if "/kill-switch" in url:
            return {"kill_switch_order_id": "ks-test-123", "status": "active", "action": "activate"}
        if "/api/governance/freeze-orders" in url:
            return payload
        return {}

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    params = {
        "scope": "persona",
        "scope_id": "persona-gamma",
        "severity": "critical",
        "reason": "Drawdown limit reached",
    }
    
    result = bff_executor._execute_activate_kill_switch(
        command_id="cmd-ks-123",
        params=params,
        auth_token="op-user:admin",
    )

    assert result["kill_switch_order_id"] == "ks-test-123"
    assert len(post_calls) == 2
    assert post_calls[0][0] == f"{INTERNAL_URL}/api/internal/v1/kill-switch"
    assert post_calls[1][0] == f"{GOVERNANCE_URL}/api/governance/freeze-orders"
    
    freeze_payload = post_calls[1][1]
    assert freeze_payload["freeze_order_id"] == "freeze-ks-test-123"
    assert freeze_payload["scope"] == "persona"
    assert freeze_payload["target_id"] == "persona-gamma"
    assert freeze_payload["status"] == "active"
    assert freeze_payload["actor"] == "admin"
    assert freeze_payload["identity"] == "op-user"


def test_execute_mutation_writes_to_governance_when_frozen(configure_urls, monkeypatch) -> None:
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, payload))
        if "/execute" in url:
            return {
                "decision_id": "evo-sweep-1",
                "decision_state": "executed",
                "target_id": "persona-delta",
                "execution_result": {"executed_at": "2026-07-13T11:00:00Z"},
            }
        if "/api/governance/freeze-orders" in url:
            return payload
        return {}

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    params = {
        "decision_id": "evo-sweep-1",
        "freeze_mode": "persona",
        "persona_id": "persona-delta",
        "note": "Sweep freeze mutation",
    }
    
    result = bff_executor._execute_execute_mutation(
        command_id="cmd-mutation-123",
        params=params,
        auth_token="op-user:admin",
    )

    assert result["decision_state"] == "executed"
    assert len(post_calls) == 2
    assert post_calls[0][0] == "http://evolution:8093/api/evolution/proposals/evo-sweep-1/execute"
    assert post_calls[1][0] == f"{GOVERNANCE_URL}/api/governance/freeze-orders"
    
    freeze_payload = post_calls[1][1]
    assert freeze_payload["freeze_order_id"] == "freeze-evo-sweep-1"
    assert freeze_payload["status"] == "active"
    assert freeze_payload["scope"] == "persona"
    assert freeze_payload["target_id"] == "persona-delta"
    assert freeze_payload["actor"] == "admin"


def test_execute_mutation_non_freeze_does_not_emit_freeze_order(configure_urls, monkeypatch) -> None:
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, payload))
        if "/execute" in url:
            return {
                "decision_id": "evo-sweep-2",
                "decision_state": "executed",
                "target_id": "persona-delta",
                "execution_result": {"executed_at": "2026-07-13T11:00:00Z"},
            }
        return {}

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    params = {
        "decision_id": "evo-sweep-2",
        "freeze_mode": "governance_only",
        "persona_id": "persona-delta",
        "note": "Non-freeze mutation",
    }
    
    result = bff_executor._execute_execute_mutation(
        command_id="cmd-mutation-124",
        params=params,
        auth_token="op-user:admin",
    )

    assert result["decision_state"] == "executed"
    # Verify ONLY evolution api is called, no governance freeze order emitted
    assert len(post_calls) == 1
    assert post_calls[0][0] == "http://evolution:8093/api/evolution/proposals/evo-sweep-2/execute"


def test_governance_write_failure_propagates(configure_urls, monkeypatch) -> None:
    import urllib.error

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        if "/api/governance/" in url:
            # Simulate a 500 Internal Server Error from governance write api
            raise urllib.error.HTTPError(
                url=url,
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=None
            )
        return {
            "rollback_id": "rollback-test-id",
            "status": "completed",
        }

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    # Calling execute_command_with_status should catch the propagated HTTPError and return CommandStatus.FAILED
    status, result, error = bff_executor.execute_command_with_status(
        command_id="cmd-rollback-failure",
        command_type=CommandType.EXECUTE_ROLLBACK,
        params={
            "runtime_id": "runtime-abc",
            "runtime_binding_id": "binding-abc",
            "rollback_action_type": "replace",
            "target_artifact_id": "art-123",
        },
        auth_token="op-user:admin",
    )

    assert status == bff_executor.CommandStatus.FAILED
    assert error is not None
    assert error["code"] == "DOWNSTREAM_ERROR"
    assert "Command backend returned 500" in error["message"]


def test_rollback_transition_lifecycle_preserves_origin(configure_urls, monkeypatch) -> None:
    # A local test to verify our real merge logic in record_rollback/record_freeze_order.
    # We will simulate the governance API lifecycle using an actual fastapi test client.
    from fastapi.testclient import TestClient
    from services.governance import main as gov_main
    from services.governance.record_store import JsonGovernanceRecordStore

    # Set up isolated json stores
    import tempfile
    from pathlib import Path
    tmpdir = tempfile.TemporaryDirectory()
    tmp_path = Path(tmpdir.name)

    freeze_store = JsonGovernanceRecordStore(tmp_path / "freezes.json", id_fields=("freeze_order_id", "id"))
    rollback_store = JsonGovernanceRecordStore(tmp_path / "rollbacks.json", id_fields=("rollback_id", "id"))

    monkeypatch.setattr(gov_main, "freeze_order_store", freeze_store)
    monkeypatch.setattr(gov_main, "rollback_store", rollback_store)

    client = TestClient(gov_main.app)

    # Step 1: Initial Rollback Write
    init_payload = {
        "rollback_id": "rb-lifecycle-test",
        "runtime_id": "runtime-test",
        "action_type": "pause_then_replace",
        "status": "initiated",
        "actor": "operator",
        "identity": "op-user-1",
        "source_command_id": "cmd-init-123",
        "created_at": "2026-07-14T01:00:00Z",
    }
    resp1 = client.post("/api/governance/rollbacks", json=init_payload)
    assert resp1.status_code == 201

    # Step 2: Transition (Approve)
    approve_payload = {
        "rollback_id": "rb-lifecycle-test",
        "status": "approved",
        "actor": "approver-role",
        "identity": "op-user-2",
        "source_command_id": "cmd-approve-456",
        "approved_at": "2026-07-14T01:05:00Z",
    }
    resp2 = client.post("/api/governance/rollbacks", json=approve_payload)
    assert resp2.status_code == 200
    
    # Readback and verify
    record = rollback_store.get("rb-lifecycle-test")
    assert record["status"] == "approved"
    # Preserved original metadata
    assert record["runtime_id"] == "runtime-test"
    assert record["action_type"] == "pause_then_replace"
    assert record["created_at"] == "2026-07-14T01:00:00Z"
    # Preserved original audit origin
    assert record["actor"] == "operator"
    assert record["identity"] == "op-user-1"
    assert record["source_command_id"] == "cmd-init-123"
    # Contained transition audit details
    assert record["transition_actor"] == "approver-role"
    assert record["transition_identity"] == "op-user-2"
    assert record["transition_source_command_id"] == "cmd-approve-456"

    # Step 3: Transition (Reject)
    reject_payload = {
        "rollback_id": "rb-lifecycle-test",
        "status": "rejected",
        "actor": "rejecter-role",
        "identity": "op-user-3",
        "source_command_id": "cmd-reject-789",
        "rejected_at": "2026-07-14T01:10:00Z",
    }
    resp3 = client.post("/api/governance/rollbacks", json=reject_payload)
    assert resp3.status_code == 200
    
    # Readback and verify
    record_rejected = rollback_store.get("rb-lifecycle-test")
    assert record_rejected["status"] == "rejected"
    assert record_rejected["actor"] == "operator"
    assert record_rejected["identity"] == "op-user-1"
    assert record_rejected["transition_actor"] == "rejecter-role"
    assert record_rejected["transition_identity"] == "op-user-3"

    tmpdir.cleanup()


def test_mfa_token_propagation(configure_urls, monkeypatch) -> None:
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, dict(payload), auth_token, mfa_token))
        if "/rollbacks/execute" in url:
            return {"rollback_id": "rb-mfa-test", "status": "completed"}
        if "/api/governance" in url:
            return payload
        return {}

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    status, result, error = bff_executor.execute_command_with_status(
        command_id="cmd-mfa-test-1",
        command_type=CommandType.EXECUTE_ROLLBACK,
        params={
            "runtime_id": "runtime-abc",
            "runtime_binding_id": "binding-abc",
            "rollback_action_type": "replace",
        },
        auth_token="op-user:admin",
        mfa_token="mfa-secret-token-value",
    )

    assert status == bff_executor.CommandStatus.EXECUTED
    # We should have three POST calls: governance (initiated), internal execute, governance (completed)
    assert len(post_calls) == 3
    # Verify that the mfa_token was propagated to all calls!
    assert post_calls[0][3] == "mfa-secret-token-value"
    assert post_calls[1][3] == "mfa-secret-token-value"
    assert post_calls[2][3] == "mfa-secret-token-value"

