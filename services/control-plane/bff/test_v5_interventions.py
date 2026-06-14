"""Tests for BFF-FINAL-009: /bff/v5/interventions HIQ Sentinel contract.

Acceptance criteria:
- canonical v5 route present (GET /bff/v5/interventions returns 200)
- remediation guarded (two-man check enforced)
- two-man semantics tested (POST without signature returns 409)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from models import (
    InterventionKind,
    InterventionRecord,
    InterventionStatus,
    ObjectType,
)
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-v5:operator"
APPROVER_TOKEN = "Bearer op-v5-approver:approver"
ADMIN_MFA_TOKEN = "Bearer op-v5-admin:admin:mfa"


async def _noop_process_command(_command_id: str) -> None:
    return None


@contextmanager
def _isolated_client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        original_interventions = list(bff_main._V5_INTERVENTIONS_STORE)
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        bff_main._V5_INTERVENTIONS_STORE.clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker
            bff_main._V5_INTERVENTIONS_STORE.clear()
            bff_main._V5_INTERVENTIONS_STORE.extend(original_interventions)


# --------------------------------------------------------------------------- #
# 1. Canonical v5 route present
# --------------------------------------------------------------------------- #

def test_get_v5_interventions_returns_200_empty_list() -> None:
    """GET /bff/v5/interventions must be present and return an empty list."""
    with _isolated_client() as client:
        response = client.get(
            "/bff/v5/interventions",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "items" in body
        assert "count" in body
        assert "generated_at" in body
        assert body["items"] == []
        assert body["count"] == 0


def test_get_v5_interventions_returns_seeded_records() -> None:
    """GET /bff/v5/interventions returns records seeded into the in-memory store."""
    with _isolated_client() as client:
        bff_main._V5_INTERVENTIONS_STORE.append({
            "intervention_id": "intv-v5-001",
            "kind": "hiq_sentinel",
            "status": "pending",
            "target_type": "Runtime",
            "target_id": "rt-v5-001",
            "triggered_at": "2026-05-08T00:00:00Z",
            "triggered_by": "sentinel",
            "description": "HIQ Sentinel detected anomalous loop behavior",
        })
        response = client.get(
            "/bff/v5/interventions",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 1
        assert body["items"][0]["intervention_id"] == "intv-v5-001"
        assert body["items"][0]["kind"] == "hiq_sentinel"
        assert body["items"][0]["status"] == "pending"


def test_get_v5_interventions_filters_by_status() -> None:
    with _isolated_client() as client:
        bff_main._V5_INTERVENTIONS_STORE.extend([
            {
                "intervention_id": "intv-v5-002",
                "kind": "hiq_sentinel",
                "status": "pending",
                "target_type": "Runtime",
                "target_id": "rt-v5-002",
                "triggered_at": "2026-05-08T00:01:00Z",
            },
            {
                "intervention_id": "intv-v5-003",
                "kind": "risk_breach",
                "status": "remediated",
                "target_type": "Runtime",
                "target_id": "rt-v5-003",
                "triggered_at": "2026-05-08T00:02:00Z",
                "remediated_at": "2026-05-08T00:05:00Z",
            },
        ])
        response = client.get(
            "/bff/v5/interventions?status=pending",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 1
        assert body["items"][0]["intervention_id"] == "intv-v5-002"


def test_get_v5_interventions_reads_service_backed_store() -> None:
    with tempfile.TemporaryDirectory() as td:
        intervention_path = os.path.join(td, "v5_interventions.json")
        with open(intervention_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "intv-store-001": {
                        "intervention_id": "intv-store-001",
                        "kind": "hiq_sentinel",
                        "status": "pending",
                        "target_type": "Runtime",
                        "target_id": "rt-store-001",
                        "triggered_at": "2026-06-03T08:00:00Z",
                    }
                },
                handle,
            )
        original_env = os.environ.get("PANTHEON_BFF_V5_INTERVENTION_STORE")
        original_read_store = bff_main.read_store
        os.environ["PANTHEON_BFF_V5_INTERVENTION_STORE"] = intervention_path
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        try:
            with _isolated_client() as client:
                response = client.get(
                    "/bff/v5/interventions",
                    headers={"Authorization": OPERATOR_TOKEN},
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["count"] == 1
                assert body["items"][0]["intervention_id"] == "intv-store-001"
        finally:
            bff_main.read_store = original_read_store
            if original_env is None:
                os.environ.pop("PANTHEON_BFF_V5_INTERVENTION_STORE", None)
            else:
                os.environ["PANTHEON_BFF_V5_INTERVENTION_STORE"] = original_env


def test_get_v5_interventions_requires_auth() -> None:
    with _isolated_client() as client:
        response = client.get("/bff/v5/interventions")
        assert response.status_code in {401, 403}, response.text


# --------------------------------------------------------------------------- #
# 2. Remediation guarded — two-man semantics via /bff/v5/interventions/{id}/remediate
# --------------------------------------------------------------------------- #

def test_remediate_v5_intervention_missing_two_man_returns_409() -> None:
    """Remediation without two-man signature must return 409 TWO_MAN_REQUIRED."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-guard-001/remediate",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": "remediate-guard-no-two-man-001",
                "X-Confirm-Token": "confirm-guard-001",
                "X-Correlation-Id": "corr-guard-no-two-man-001",
            },
            json={
                "reason": "Sentinel remediation test",
                "remediation_action": "resolve",
                "approvalId": "approval-guard-001",
            },
        )
        assert response.status_code == 409, response.text
        detail = response.json()
        error = detail["error"]
        assert error["code"] == "TWO_MAN_REQUIRED"
        assert error["details"]["kind"] == "two_man"
        assert "corr-guard-no-two-man-001" in str(detail)


def test_remediate_v5_intervention_missing_approval_returns_409() -> None:
    """Remediation without approval evidence must return 409 APPROVAL_REQUIRED."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-guard-002/remediate",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": "remediate-guard-no-approval-001",
                "X-Confirm-Token": "confirm-guard-002",
                "X-Correlation-Id": "corr-guard-no-approval-001",
            },
            json={
                "reason": "Sentinel remediation test",
                "remediation_action": "resolve",
                "twoManSignatureId": "tms-guard-002",
            },
        )
        assert response.status_code == 409, response.text
        detail = response.json()
        error = detail["error"]
        assert error["code"] == "APPROVAL_REQUIRED"
        assert error["details"]["kind"] == "approval"


def test_remediate_v5_intervention_missing_confirm_token_returns_428() -> None:
    """Remediation without confirm token must return 428 CONFIRM_TOKEN_REQUIRED."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-guard-003/remediate",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": "remediate-guard-no-confirm-001",
                "X-Correlation-Id": "corr-guard-no-confirm-001",
            },
            json={
                "reason": "Sentinel remediation test",
                "remediation_action": "resolve",
                "approvalId": "approval-guard-003",
                "twoManSignatureId": "tms-guard-003",
            },
        )
        assert response.status_code == 428, response.text
        detail = response.json()
        error = detail["error"]
        assert error["code"] == "CONFIRMATION_REQUIRED"
        assert error["details"]["kind"] == "confirm_token"


def test_remediate_v5_intervention_all_preconditions_returns_202() -> None:
    """Remediation with all required preconditions must be accepted (202)."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-accept-001/remediate",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": "remediate-accept-001",
                "X-Confirm-Token": "confirm-accept-001",
                "X-Correlation-Id": "corr-accept-001",
            },
            json={
                "reason": "Two-man authorized sentinel remediation",
                "remediation_action": "resolve",
                "approvalId": "approval-accept-001",
                "twoManSignatureId": "tms-accept-001",
            },
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] in {"accepted", "queued"}
        assert "data" in body
        receipt = body["data"]
        assert receipt.get("receipt_id") or receipt.get("command_id")


# --------------------------------------------------------------------------- #
# 3. Two-man semantics also enforced via /bff/v1/commands
# --------------------------------------------------------------------------- #

def test_bff_v1_commands_remediate_sentinel_missing_two_man_returns_409() -> None:
    """RemediateSentinelIntervention via /bff/v1/commands enforces two-man gate."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": "remediate-v1-no-two-man-001",
                "X-Confirm-Token": "confirm-v1-001",
                "X-Correlation-Id": "corr-v1-no-two-man-001",
            },
            json={
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "intv-v1-001"},
                "approvalId": "approval-v1-001",
                "params": {
                    "intervention_id": "intv-v1-001",
                    "remediation_action": "resolve",
                },
                "audit_context": {"reason": "v1 remediation test"},
            },
        )
        assert response.status_code == 409, response.text
        detail = response.json()
        error = detail["error"]
        assert error["code"] == "TWO_MAN_REQUIRED"
        assert "TWO_MAN_SIGNATURE_MISSING" in str(detail)


def test_decide_v5_intervention_records_dedicated_command_and_trace() -> None:
    """POST /bff/v5/interventions/{id}/decide routes through final command admission."""
    idem_key = "v5-decide-final-admission-001"
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-decide-001/decide",
            headers={
                "Authorization": OPERATOR_TOKEN,
                "Idempotency-Key": idem_key,
                "X-Trace-Id": "trace-v5-decide-001",
                "X-Correlation-Id": "corr-v5-decide-001",
                "X-Request-Id": "req-v5-decide-001",
            },
            json={"decision": "defer", "memo": "Need another observation window"},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["data"]["command"] == "DecideV5Intervention"
        assert body["data"]["commandId"]
        assert body["meta"]["durable"] is True
        assert body["meta"]["liveCapitalSideEffects"] is False
        assert body["meta"]["idempotency"]["key"] == idem_key

        stored = bff_main.command_store.get_command_by_idempotency_key(idem_key)
        assert stored is not None
        assert stored["type"] == "DecideV5Intervention"
        assert stored["target"]["type"] == ObjectType.SENTINEL_INTERVENTION.value
        assert stored["target"]["id"] == "intv-v5-decide-001"
        params = stored["params"]
        assert params["intervention_id"] == "intv-v5-decide-001"
        assert params["interventionId"] == "intv-v5-decide-001"
        assert params["decision"] == "defer"
        assert params["audit_event"] == "intervention.defer"

        audit = stored["audit"]
        assert audit["action_id"] == "decide"
        assert audit["audit_event"] == "intervention.defer"
        foundation = stored["foundation"]
        assert foundation["admission_route"] == "POST /bff/v5/interventions/{id}/decide"
        trace = foundation["trace_context"]
        assert trace["trace_id"] == "trace-v5-decide-001"
        assert trace["correlation_id"] == "corr-v5-decide-001"
        assert trace["request_id"] == "req-v5-decide-001"
        assert trace["idempotency_key"] == idem_key


def test_decide_v5_intervention_replays_same_idempotency_key() -> None:
    idem_key = "v5-decide-replay-001"
    with _isolated_client() as client:
        first = client.post(
            "/bff/v5/interventions/intv-v5-decide-replay/decide",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": idem_key},
            json={"decision": "dismiss", "reason": "Resolved by observation"},
        )
        second = client.post(
            "/bff/v5/interventions/intv-v5-decide-replay/decide",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": idem_key},
            json={"decision": "dismiss", "reason": "Resolved by observation"},
        )
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert second.json()["data"]["commandId"] == first.json()["data"]["commandId"]
        assert second.json()["meta"]["idempotency"]["replayed"] is True
        assert len(bff_main.command_store._get_all_commands()) == 1


def test_decide_v5_intervention_invalid_decision_returns_422_without_store() -> None:
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-decide-invalid/decide",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "v5-decide-invalid-001"},
            json={"decision": "not-a-decision"},
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["precondition_failed"] == "decision"
        assert bff_main.command_store._get_all_commands() == []


def test_decide_v5_intervention_body_idempotency_key_rejected_before_store() -> None:
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-v5-decide-body-idem/decide",
            headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "v5-decide-body-idem-001"},
            json={"decision": "defer", "idempotencyKey": "body-key-not-allowed"},
        )
        assert response.status_code in {400, 422}, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert bff_main.command_store._get_all_commands() == []


# --------------------------------------------------------------------------- #
# 4. v5 interventions route in SSE resync metadata
# --------------------------------------------------------------------------- #

def test_v5_interventions_listed_in_approval_sse_resync_routes() -> None:
    """The approval SSE resync routes must reference /bff/v5/interventions."""
    resync_routes = bff_main._SSE_RESYNC_ROUTES.get("approval", ())
    assert "/bff/v5/interventions" in resync_routes, (
        f"Expected /bff/v5/interventions in approval resync routes, got: {resync_routes}"
    )
    assert "/bff/approvals" in resync_routes, (
        f"Expected /bff/approvals in approval resync routes, got: {resync_routes}"
    )


# --------------------------------------------------------------------------- #
# 5. RemediateSentinelIntervention in action catalog
# --------------------------------------------------------------------------- #

def test_remediate_sentinel_intervention_in_action_catalog() -> None:
    """RemediateSentinelIntervention must be in the action catalog with two-man gate."""
    from action_catalog import get_catalog_entry
    entry = get_catalog_entry("RemediateSentinelIntervention")
    assert entry is not None, "RemediateSentinelIntervention missing from action catalog"
    assert entry.requires_two_man is True, "RemediateSentinelIntervention must require two-man"
    assert entry.requires_approval is True, "RemediateSentinelIntervention must require approval"
    assert entry.requires_confirm_token is True, "RemediateSentinelIntervention must require confirm token"
    assert entry.risk_level.value == "critical"


def test_decide_v5_intervention_in_action_catalog() -> None:
    from action_catalog import get_catalog_entry
    entry = get_catalog_entry("DecideV5Intervention")
    assert entry is not None, "DecideV5Intervention missing from action catalog"
    assert entry.endpoint == "/bff/v5/interventions/{intervention_id}/decide"
    assert entry.required_roles == ["operator", "approver"]
    assert entry.requires_confirm_token is False
    assert entry.idempotency_required is True


# --------------------------------------------------------------------------- #
# 6. InterventionRecord and InterventionListResponse model validation
# --------------------------------------------------------------------------- #

def test_intervention_record_model_fields() -> None:
    """InterventionRecord must be constructable with expected fields."""
    record = InterventionRecord(
        intervention_id="intv-model-001",
        kind=InterventionKind.HIQ_SENTINEL,
        status=InterventionStatus.PENDING,
        target_type="Runtime",
        target_id="rt-model-001",
        triggered_at="2026-05-08T00:00:00Z",
    )
    assert record.intervention_id == "intv-model-001"
    assert record.kind == InterventionKind.HIQ_SENTINEL
    assert record.status == InterventionStatus.PENDING
    assert record.two_man_signature_id is None


def test_sentinel_intervention_object_type_present() -> None:
    """ObjectType must include SentinelIntervention."""
    assert ObjectType.SENTINEL_INTERVENTION.value == "SentinelIntervention"


def test_remediate_sentinel_intervention_command_type_present() -> None:
    """CommandType must include RemediateSentinelIntervention."""
    from models import CommandType
    assert CommandType.REMEDIATE_SENTINEL_INTERVENTION.value == "RemediateSentinelIntervention"


def test_decide_v5_intervention_command_type_present() -> None:
    from models import CommandType
    assert CommandType.DECIDE_V5_INTERVENTION.value == "DecideV5Intervention"


# --------------------------------------------------------------------------- #
# 7. Role enforcement — approver gate
# --------------------------------------------------------------------------- #

VIEWER_TOKEN = "Bearer op-viewer:viewer"


def test_remediate_v5_intervention_insufficient_role_returns_403() -> None:
    """A caller without approver/admin role must receive 403 INSUFFICIENT_ROLE on the remediate endpoint."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-role-check-001/remediate",
            headers={
                "Authorization": OPERATOR_TOKEN,  # operator role, not approver
                "Idempotency-Key": "remediate-role-check-op-001",
                "X-Confirm-Token": "confirm-role-001",
                "X-Correlation-Id": "corr-role-check-001",
            },
            json={
                "reason": "Sentinel remediation role check",
                "remediation_action": "resolve",
                "approvalId": "approval-role-001",
                "twoManSignatureId": "tms-role-001",
            },
        )
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "FORBIDDEN"
        assert error["details"]["precondition_failed"] == "role_check"


def test_remediate_v5_intervention_viewer_role_returns_403() -> None:
    """A viewer-only caller must receive 403 INSUFFICIENT_ROLE on the remediate endpoint."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-role-check-002/remediate",
            headers={
                "Authorization": VIEWER_TOKEN,
                "Idempotency-Key": "remediate-role-check-viewer-001",
                "X-Confirm-Token": "confirm-role-002",
                "X-Correlation-Id": "corr-role-check-002",
            },
            json={
                "reason": "Sentinel remediation viewer check",
                "remediation_action": "resolve",
                "approvalId": "approval-role-002",
                "twoManSignatureId": "tms-role-002",
            },
        )
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "FORBIDDEN"


def test_bff_v1_commands_remediate_sentinel_insufficient_role_returns_403() -> None:
    """RemediateSentinelIntervention via /bff/v1/commands enforces approver role gate."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": OPERATOR_TOKEN,  # operator, not approver
                "Idempotency-Key": "remediate-v1-role-check-001",
                "X-Confirm-Token": "confirm-v1-role-001",
                "X-Correlation-Id": "corr-v1-role-check-001",
            },
            json={
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "intv-v1-role-001"},
                "approvalId": "approval-v1-role-001",
                "twoManSignatureId": "tms-v1-role-001",
                "params": {
                    "intervention_id": "intv-v1-role-001",
                    "remediation_action": "resolve",
                },
                "audit_context": {"reason": "v1 role check test"},
            },
        )
        assert response.status_code == 403, response.text
        error = response.json()["error"]
        assert error["code"] == "FORBIDDEN"
        assert error["details"]["precondition_failed"] == "role_check"


# --------------------------------------------------------------------------- #
# 8. Shape validation — invalid remediation_action
# --------------------------------------------------------------------------- #

def test_remediate_v5_intervention_invalid_action_returns_422() -> None:
    """An invalid remediation_action value must return 422 INVALID_PARAMS."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v5/interventions/intv-shape-001/remediate",
            headers={
                "Authorization": APPROVER_TOKEN,
                "Idempotency-Key": "remediate-shape-001",
                "X-Confirm-Token": "confirm-shape-001",
                "X-Correlation-Id": "corr-shape-001",
            },
            json={
                "reason": "Shape validation check",
                "remediation_action": "invalid_action",
                "approvalId": "approval-shape-001",
                "twoManSignatureId": "tms-shape-001",
            },
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"


def test_bff_v1_commands_remediate_sentinel_invalid_action_returns_422() -> None:
    """RemediateSentinelIntervention via /bff/v1/commands rejects invalid remediation_action."""
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": APPROVER_TOKEN,
                "Idempotency-Key": "remediate-v1-shape-001",
                "X-Confirm-Token": "confirm-v1-shape-001",
                "X-Correlation-Id": "corr-v1-shape-001",
            },
            json={
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "intv-v1-shape-001"},
                "approvalId": "approval-v1-shape-001",
                "twoManSignatureId": "tms-v1-shape-001",
                "params": {
                    "intervention_id": "intv-v1-shape-001",
                    "remediation_action": "invalid_action",
                },
                "audit_context": {"reason": "v1 shape check test"},
            },
        )
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------- #
# 9. Regression: v1/commands two-man top-level alias propagates to stored params
# --------------------------------------------------------------------------- #

def test_bff_v1_commands_remediate_sentinel_top_level_two_man_propagates_to_stored_params() -> None:
    """
    Regression: twoManSignatureId supplied at the top level of a v1/commands
    RemediateSentinelIntervention payload must be normalized into the stored
    command params so the executor always receives a non-empty two_man_signature_id.

    Without the fix the precondition gate accepted the command but stored_params
    contained only cmd.params (which had no two-man field), leaving the executor
    with an empty two_man_signature_id downstream.
    """
    idem_key = "remediate-v1-two-man-propagation-001"
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": idem_key,
                "X-Confirm-Token": "confirm-propagation-001",
                "X-Correlation-Id": "corr-propagation-001",
            },
            json={
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "intv-v1-propagation-001"},
                # twoManSignatureId is at the top level, NOT inside params
                "twoManSignatureId": "tms-propagation-sig-001",
                "approvalId": "approval-propagation-001",
                "params": {
                    "intervention_id": "intv-v1-propagation-001",
                    "remediation_action": "resolve",
                },
                "audit_context": {"reason": "two-man propagation regression test"},
            },
        )
        assert response.status_code == 202, response.text

        # Retrieve the stored command and verify the two-man evidence was propagated
        stored = bff_main.command_store.get_command_by_idempotency_key(idem_key)
        assert stored is not None, "Command should be stored after 202 acceptance"
        stored_params = stored.get("params") or {}
        assert stored_params.get("two_man_signature_id") == "tms-propagation-sig-001", (
            f"Expected two_man_signature_id='tms-propagation-sig-001' in stored params, "
            f"got: {stored_params}"
        )


def test_bff_v1_commands_remediate_sentinel_second_operator_alias_propagates() -> None:
    """Regression: secondOperatorId alias at the top level also propagates to stored params."""
    idem_key = "remediate-v1-second-operator-propagation-001"
    with _isolated_client() as client:
        response = client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": ADMIN_MFA_TOKEN,
                "Idempotency-Key": idem_key,
                "X-Confirm-Token": "confirm-second-op-001",
                "X-Correlation-Id": "corr-second-op-001",
            },
            json={
                "command": "RemediateSentinelIntervention",
                "target": {"type": "SentinelIntervention", "id": "intv-v1-second-op-001"},
                # Using the secondOperatorId alias instead of twoManSignatureId
                "secondOperatorId": "op-second-sig-001",
                "approvalId": "approval-second-op-001",
                "params": {
                    "intervention_id": "intv-v1-second-op-001",
                    "remediation_action": "resolve",
                },
                "audit_context": {"reason": "secondOperatorId propagation regression test"},
            },
        )
        assert response.status_code == 202, response.text

        stored = bff_main.command_store.get_command_by_idempotency_key(idem_key)
        assert stored is not None
        stored_params = stored.get("params") or {}
        assert stored_params.get("two_man_signature_id") == "op-second-sig-001", (
            f"Expected two_man_signature_id='op-second-sig-001' in stored params, "
            f"got: {stored_params}"
        )
