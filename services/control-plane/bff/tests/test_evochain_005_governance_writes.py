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
        post_calls.append((url, payload))
        if url.endswith("/rollbacks/execute"):
            return {
                "rollback_id": "rollback-test-id",
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

    assert result["rollback_id"] == "rollback-test-id"
    
    # Verify both calls: the internal action and the governance write
    assert len(post_calls) == 2
    
    internal_call = post_calls[0]
    assert internal_call[0] == f"{INTERNAL_URL}/api/internal/v1/rollbacks/execute"
    
    gov_call = post_calls[1]
    assert gov_call[0] == f"{GOVERNANCE_URL}/api/governance/rollbacks"
    
    gov_payload = gov_call[1]
    assert gov_payload["rollback_id"] == "rollback-test-id"
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
