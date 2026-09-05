"""EVOCHAIN-005 BFF governance write integration tests."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

import pytest


from services.control_plane.bff import command_executor as bff_executor
from services.control_plane.bff.models import CommandType


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


def test_execute_rollback_derives_runtime_id_when_omitted(configure_urls, monkeypatch) -> None:
    """EVOCHAIN-005 round 2: callers routinely only supply target_id (see
    _ROLLBACK_REQUIRED in bff/main.py, which does not list runtime_id), but
    the canonical governance POST requires runtime_id and 400s without it.
    _execute_rollback must derive a non-empty runtime_id rather than sending
    a bare POST that fails the write."""
    post_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        post_calls.append((url, dict(payload)))
        if "/api/governance/rollbacks" in url and not payload.get("runtime_id"):
            # Mirrors the real canonical store's required-field enforcement.
            import urllib.error
            raise urllib.error.HTTPError(url=url, code=400, msg="Missing required audit field: runtime_id", hdrs=None, fp=None)
        if url.endswith("/rollbacks/execute"):
            return {"rollback_id": payload.get("rollback_id"), "status": "executed"}
        return payload

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)

    result = bff_executor._execute_rollback(
        command_id="cmd-rollback-no-runtime-id",
        params={
            "rollback_target_type": "runtime",
            "target_id": "binding-xyz",
            "rollback_to_version": "previous",
        },
        auth_token="op-user:admin",
    )

    assert result["runtime_id"] == "binding-xyz"
    gov_calls = [call for call in post_calls if "/api/governance/rollbacks" in call[0]]
    assert len(gov_calls) == 2
    for _, payload in gov_calls:
        assert payload["runtime_id"] == "binding-xyz"


def test_execute_rollback_retry_does_not_repeat_internal_execute(configure_urls, monkeypatch) -> None:
    """EVOCHAIN-005 round 2: the real internal rollbacks/execute API reports
    its terminal state as "executed" (services/control-plane/internal/
    internal_api.py::execute_rollback), not "completed". Before the fix, the
    replay short-circuit only recognized "completed", so a same-command retry
    (e.g. a client timeout-and-retry) would re-dispatch the rollback action
    against the runtime a second time."""
    governance_records: Dict[str, Dict[str, Any]] = {}
    internal_execute_calls = []

    def mock_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        if url.endswith("/rollbacks/execute"):
            internal_execute_calls.append(dict(payload))
            return {"rollback_id": payload.get("rollback_id"), "status": "executed"}
        if "/api/governance/rollbacks" in url:
            rollback_id = payload.get("rollback_id")
            governance_records[rollback_id] = dict(payload)
            return dict(payload)
        return {}

    def mock_get_json(url: str, auth_token=None, mfa_token=None) -> Dict[str, Any]:
        rollback_id = url.rsplit("/", 1)[-1]
        record = governance_records.get(rollback_id)
        if record is None:
            raise urllib_error_not_found(url)
        return record

    monkeypatch.setattr(bff_executor, "_post_json", mock_post_json)
    monkeypatch.setattr(bff_executor, "_get_json", mock_get_json)

    params = {
        "runtime_id": "runtime-retry",
        "runtime_binding_id": "binding-retry",
        "rollback_action_type": "replace",
        "target_artifact_id": "art-retry",
    }

    first = bff_executor._execute_rollback(command_id="cmd-retry-1", params=params, auth_token="op-user:admin")
    assert first["status"] == "completed"
    assert len(internal_execute_calls) == 1

    second = bff_executor._execute_rollback(command_id="cmd-retry-1", params=params, auth_token="op-user:admin")
    assert second["status"] == "completed"
    # The replay short-circuit must recognize the normalized "completed"
    # status from the first call — no second dispatch to the runtime.
    assert len(internal_execute_calls) == 1


def urllib_error_not_found(url: str):
    import urllib.error
    return urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs=None, fp=None)


def test_bff_command_to_governance_to_journal_composition(configure_urls, monkeypatch) -> None:
    """EVOCHAIN-005 round 2: end-to-end BFF command -> governance canonical
    write (through the real, now-authenticated FastAPI routes) -> governance
    read -> Evolution Journal item composition. Proves actor, identity,
    timestamps, and source_command_id survive the full round trip, not just
    a mocked _post_json call."""
    from fastapi.testclient import TestClient
    from services.governance import main as gov_main
    from services.governance.record_store import JsonGovernanceRecordStore
    from services.control_plane.bff import main as bff_main
    import tempfile
    from pathlib import Path

    tmpdir = tempfile.TemporaryDirectory()
    tmp_path = Path(tmpdir.name)
    rollback_store = JsonGovernanceRecordStore(tmp_path / "rollbacks.json", id_fields=("rollback_id", "id"))
    monkeypatch.setattr(gov_main, "rollback_store", rollback_store)
    gov_client = TestClient(gov_main.app)

    def routed_post_json(url: str, payload: Dict[str, Any], auth_token=None, mfa_token=None) -> Dict[str, Any]:
        if url.endswith("/rollbacks/execute"):
            return {"rollback_id": payload.get("rollback_id"), "status": "executed"}
        if "/api/governance/rollbacks" in url:
            headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            resp = gov_client.post("/api/governance/rollbacks", json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
        return {}

    def routed_get_json(url: str, auth_token=None, mfa_token=None) -> Any:
        rollback_id = url.rsplit("/", 1)[-1]
        resp = gov_client.get(f"/api/governance/rollbacks/{rollback_id}")
        if resp.status_code == 404:
            raise urllib_error_not_found(url)
        resp.raise_for_status()
        return resp.json()

    monkeypatch.setattr(bff_executor, "_post_json", routed_post_json)
    monkeypatch.setattr(bff_executor, "_get_json", routed_get_json)

    result = bff_executor._execute_rollback(
        command_id="cmd-composition-1",
        params={
            "runtime_id": "runtime-composition",
            "runtime_binding_id": "binding-composition",
            "rollback_action_type": "replace",
            "target_artifact_id": "art-composition",
        },
        auth_token="op-composition:admin",
    )
    assert result["status"] == "completed"

    read_resp = gov_client.get(f"/api/governance/rollbacks/{result['rollback_id']}")
    assert read_resp.status_code == 200
    canonical_record = read_resp.json()
    assert canonical_record["actor"] == "admin"
    assert canonical_record["identity"] == "op-composition"
    assert canonical_record["source_command_id"] == "cmd-composition-1"
    assert canonical_record["runtime_id"] == "runtime-composition"

    journal_item = bff_main._evolution_journal_rollback_item(canonical_record)
    assert journal_item is not None
    assert journal_item["record"]["actor"] == "admin"
    assert journal_item["record"]["identity"] == "op-composition"
    assert journal_item["record"]["source_command_id"] == "cmd-composition-1"
    assert journal_item["record"]["transition_actor"] == "admin"
    assert journal_item["record"]["transition_identity"] == "op-composition"
    assert journal_item["record"]["transition_source_command_id"] == "cmd-composition-1"

    tmpdir.cleanup()


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
        "source_command_id": "cmd-init-123",
        "created_at": "2026-07-14T01:00:00Z",
    }
    resp1 = client.post(
        "/api/governance/rollbacks",
        json=init_payload,
        headers={"Authorization": "Bearer op-user-1:operator"},
    )
    assert resp1.status_code == 201

    # Step 2: Transition (Approve). Real roles only — the old
    # "approver-role"/"rejecter-role" test-only aliases were removed from the
    # governance service's authority check, so this now authenticates as the
    # real "approver" role via the bearer token.
    approve_payload = {
        "rollback_id": "rb-lifecycle-test",
        "status": "approved",
        "actor": "approver",
        "source_command_id": "cmd-approve-456",
        "approved_at": "2026-07-14T01:05:00Z",
    }
    resp2 = client.post(
        "/api/governance/rollbacks",
        json=approve_payload,
        headers={"Authorization": "Bearer op-user-2:approver"},
    )
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
    # Contained transition audit details, derived from the authenticated
    # bearer token rather than the self-declared body fields.
    assert record["transition_actor"] == "approver"
    assert record["transition_identity"] == "op-user-2"
    assert record["transition_source_command_id"] == "cmd-approve-456"

    # Step 3: Transition (Reject)
    reject_payload = {
        "rollback_id": "rb-lifecycle-test",
        "status": "rejected",
        "actor": "governance_reviewer",
        "source_command_id": "cmd-reject-789",
        "rejected_at": "2026-07-14T01:10:00Z",
    }
    resp3 = client.post(
        "/api/governance/rollbacks",
        json=reject_payload,
        headers={"Authorization": "Bearer op-user-3:governance_reviewer"},
    )
    assert resp3.status_code == 200

    # Readback and verify
    record_rejected = rollback_store.get("rb-lifecycle-test")
    assert record_rejected["status"] == "rejected"
    assert record_rejected["actor"] == "operator"
    assert record_rejected["identity"] == "op-user-1"
    assert record_rejected["transition_actor"] == "governance_reviewer"
    assert record_rejected["transition_identity"] == "op-user-3"

    tmpdir.cleanup()


def test_rollback_transition_lifecycle_rejects_unauthenticated_and_spoofed_writes(
    configure_urls, monkeypatch
) -> None:
    """EVOCHAIN-005 round 2: canonical writes must be authenticated and the
    declared actor role must be one the caller's token actually carries."""
    from fastapi.testclient import TestClient
    from services.governance import main as gov_main
    from services.governance.record_store import JsonGovernanceRecordStore

    import tempfile
    from pathlib import Path
    tmpdir = tempfile.TemporaryDirectory()
    tmp_path = Path(tmpdir.name)

    rollback_store = JsonGovernanceRecordStore(tmp_path / "rollbacks.json", id_fields=("rollback_id", "id"))
    monkeypatch.setattr(gov_main, "rollback_store", rollback_store)

    client = TestClient(gov_main.app)

    # No Authorization header at all.
    unauth_resp = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rb-unauth",
            "runtime_id": "runtime-test",
            "action_type": "replace",
            "status": "initiated",
            "actor": "operator",
            "source_command_id": "cmd-unauth",
        },
    )
    assert unauth_resp.status_code == 401
    assert rollback_store.get("rb-unauth") is None

    # Create the record as a legitimate operator.
    init_resp = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rb-spoof-test",
            "runtime_id": "runtime-test",
            "action_type": "replace",
            "status": "initiated",
            "actor": "operator",
            "source_command_id": "cmd-init",
        },
        headers={"Authorization": "Bearer op-user-1:operator"},
    )
    assert init_resp.status_code == 201

    # Authenticated as a plain operator, but declaring "approver" — a role the
    # token does not carry — to approve the rollback. Must be rejected.
    spoof_resp = client.post(
        "/api/governance/rollbacks",
        json={
            "rollback_id": "rb-spoof-test",
            "status": "approved",
            "actor": "approver",
            "source_command_id": "cmd-spoof",
        },
        headers={"Authorization": "Bearer op-user-4:operator"},
    )
    assert spoof_resp.status_code == 403
    assert rollback_store.get("rb-spoof-test")["status"] == "initiated"

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
