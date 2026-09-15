from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pathlib import Path

_BFF_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BFF_DIR.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json

import main as bff_main
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core import http_security
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

_DATA_PATH = Path(__file__).resolve().parent / "data" / "read_surfaces.json"
with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW_DATA = json.load(_f)


def _create_test_read_store(extra_evolution_decisions=None, extra_approval_decisions=None):
    evo_decisions = dict(_RAW_DATA.get("evolution_decisions", {}))
    if extra_evolution_decisions:
        evo_decisions.update(extra_evolution_decisions)

    appr_decisions = dict(_RAW_DATA.get("approval_decisions", {}))
    if extra_approval_decisions:
        appr_decisions.update(extra_approval_decisions)

    store = create_in_memory_read_surface_ports(
        lifecycle_telemetry_governance_kwargs={
            "evolution_decisions": evo_decisions,
            "freeze_orders": _RAW_DATA.get("freeze_orders", {}),
            "all_rollbacks": _RAW_DATA.get("all_rollbacks", []),
        },
        ooda_management_kwargs={
            "approval_decisions": list(appr_decisions.values()),
            "evolution_decisions": list(evo_decisions.values()),
        },
    )
    committee_data = {
        "committee_id": "committee-regime-risk-20260419-081",
        "committee_ref": "committee-regime-risk-20260419-081",
        "consensus_state": "sponsor_required",
        "sponsor_decision": None,
        "sponsor_assignment": {"participant_id": "session-sponsor-001"},
        "participant_roster": [{"participant_id": "session-sponsor-001", "role": "sponsor"}],
    }
    store.get_evolution_decision = store.lifecycle_telemetry_governance.get_evolution_decision_by_id
    store.get_approval_decision = lambda aid: appr_decisions.get(aid)
    store.get_committee = lambda cid: committee_data if cid == "committee-regime-risk-20260419-081" else None
    store.dataset_source = lambda d: "typed_store"
    return store


APPROVER_TOKEN = "Bearer op-6:approver"
OPERATOR_TOKEN = "Bearer op-2:operator"
ADMIN_MFA_TOKEN = "Bearer op-admin:admin:mfa"


async def _noop_process_command(_command_id: str) -> None:
    return None


def _command_headers(token: str, key: str, trace: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": token,
        "X-Idempotency-Key": key,
    }
    if trace:
        headers["X-Trace-Id"] = trace
    return headers


def _error_detail(response) -> dict:
    body = response.json()
    return body.get("detail") or body


@pytest.fixture(autouse=True)
def _final_contract_approval_decision(monkeypatch):
    original_get = bff_main.read_store.get_approval_decision

    def get_approval_decision(decision_id: str | None):
        if decision_id == "approval-final-001":
            return {
                "id": "approval-final-001",
                "decision_id": "approval-final-001",
                "outcome": "approved",
                "state": "approved",
                "command": "ApproveDecision",
                "target_type": "ApprovalDecision",
                "target_id": "appr-final-001",
                "reviewer": "governance",
                "risk_level": "medium",
            }
        return original_get(decision_id)

    monkeypatch.setattr(bff_main.read_store, "get_approval_decision", get_approval_decision)


def test_submit_command_accepts_approval_queue_command_types() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(APPROVER_TOKEN, "idmp-approve-decision-001"),
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-final-001"},
                    "action": "approve",
                    "params": {
                        "decision_id": "appr-final-001",
                        "approval_notes": "Proceed to approval",
                    },
                    "approvalId": "approval-final-001",
                    "audit_context": {"reason": "Policy checks passed"},
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "ApproveDecision"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_rejects_missing_idempotency_key_with_foundation_audit() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={"Authorization": APPROVER_TOKEN, "X-Trace-Id": "trace-missing-idmp"},
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-001"},
                    "action": "approve",
                    "params": {"decision_id": "appr-001"},
                    "audit_context": {"reason": "Policy checks passed"},
                },
            )

            assert response.status_code == 400, response.text
            detail = _error_detail(response)
            assert detail["error"]["details"]["precondition_failed"] == "idempotency_key"
            assert detail["foundation_error"]["error_kind"] == "validation"
            assert detail["foundation_error"]["trace"]["trace_id"] == "trace-missing-idmp"
            assert detail["audit_action"]["trace_id"] == "trace-missing-idmp"
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_records_foundation_context_and_replays_idempotency() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        headers = {
            "Authorization": APPROVER_TOKEN,
            "X-Trace-Id": "trace-bff-001",
            "X-Correlation-Id": "corr-bff-001",
            "X-Idempotency-Key": "idmp-bff-001",
        }
        body = {
            "command": "ApproveDecision",
            "target": {"type": "ApprovalDecision", "id": "appr-final-001"},
            "action": "approve",
            "params": {
                "decision_id": "appr-final-001",
                "approval_notes": "Proceed to approval",
            },
            "approvalId": "approval-final-001",
            "audit_context": {"reason": "Policy checks passed"},
        }

        try:
            first = client.post("/bff/v1/commands", headers=headers, json=body)
            second = client.post("/bff/v1/commands", headers=headers, json=body)

            assert first.status_code == 202, first.text
            assert second.status_code == 202, second.text
            assert second.json()["data"]["receipt_id"] == first.json()["data"]["receipt_id"]

            records = bff_main.command_store._get_all_commands()
            assert len(records) == 1
            foundation = records[0]["foundation"]
            assert foundation["trace_context"]["trace_id"] == "trace-bff-001"
            assert foundation["trace_context"]["correlation_id"] == "corr-bff-001"
            assert foundation["idempotency_record"]["idempotency_key"] == "idmp-bff-001"
            assert foundation["idempotency_record"]["status"] == "succeeded"
            assert foundation["policy_decision"]["decision"] == "allow"
            assert foundation["audit_action"]["trace_id"] == "trace-bff-001"
            assert records[0]["audit"]["foundation"]["command_envelope"]["command_id"] == records[0]["command_id"]
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_policy_denial_returns_foundation_error_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": OPERATOR_TOKEN,
                    "X-Trace-Id": "trace-bff-deny",
                    "X-Idempotency-Key": "idmp-bff-deny",
                },
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-001"},
                    "action": "approve",
                    "params": {"decision_id": "appr-001"},
                    "audit_context": {"reason": "Attempt approval without approver role"},
                },
            )

            assert response.status_code == 403, response.text
            detail = _error_detail(response)
            assert detail["foundation_error"]["error_kind"] == "policy_denial"
            assert detail["foundation_error"]["trace"]["trace_id"] == "trace-bff-deny"
            assert detail["policy_decision"]["decision"] == "deny"
            assert detail["audit_action"]["policy_decision_ref"] == detail["policy_decision"]["decision_id"]
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_validation_error_returns_foundation_error_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "X-Trace-Id": "trace-bff-validation",
                    "X-Idempotency-Key": "idmp-bff-validation",
                },
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-001"},
                    "action": "approve",
                    "params": {},
                    "audit_context": {"reason": "Missing decision id"},
                },
            )

            assert response.status_code == 422, response.text
            detail = _error_detail(response)
            assert detail["foundation_error"]["error_kind"] == "validation"
            assert detail["foundation_error"]["trace"]["trace_id"] == "trace-bff-validation"
            assert detail["audit_action"]["trace_id"] == "trace-bff-validation"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_escalate_diff() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(OPERATOR_TOKEN, "idmp-escalate-diff-001"),
                json={
                    "command": "EscalateDiff",
                    "target": {"type": "DeploymentPlan", "id": "plan-dp-001"},
                    "action": "escalate_diff",
                    "params": {
                        "plan_id": "plan-dp-001",
                        "escalation_reason": "Binding move requires manual committee review",
                    },
                    "audit_context": {"reason": "Committee review needed"},
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "EscalateDiff"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_runtime_deployment_approval_incident_commands_record_foundation_controls(monkeypatch) -> None:
    # /bff/v1/commands enforces the full action-catalog precondition set for
    # every command type (unlike the retired legacy route, which skipped
    # require_final_command_preconditions for all but a couple of command
    # types). ApproveDeployment/ApproveDecision require approval evidence and
    # PauseRuntime/ActivateKillSwitch require a confirm token (plus, for
    # ActivateKillSwitch, a two-man signature); supply that evidence here so
    # each case still exercises "submission succeeds" as originally intended.
    approval_decisions = {
        "appr-deploy-001": {
            "id": "appr-deploy-001",
            "outcome": "approved",
            "state": "approved",
            "command": "ApproveDeployment",
            "target_type": "DeploymentPlan",
            "target_id": "dp-001",
        },
        "appr-review-001": {
            "id": "appr-review-001",
            "outcome": "approved",
            "state": "approved",
            "command": "ApproveDecision",
            "target_type": "ApprovalDecision",
            "target_id": "appr-001",
        },
        "appr-incident-001": {
            "id": "appr-incident-001",
            "outcome": "approved",
            "state": "approved",
            "command": "ActivateKillSwitch",
            "target_type": "KillSwitchOrder",
            "target_id": "ks-pool-001",
        },
    }
    original_get_approval_decision = bff_main.read_store.get_approval_decision

    def get_approval_decision(decision_id):
        return approval_decisions.get(decision_id) or original_get_approval_decision(decision_id)

    monkeypatch.setattr(bff_main.read_store, "get_approval_decision", get_approval_decision)

    cases = [
        {
            "key": "deployment",
            "token": APPROVER_TOKEN,
            "actor_id": "op-6",
            "body": {
                "command": "ApproveDeployment",
                "target": {"type": "DeploymentPlan", "id": "dp-001"},
                "action": "approve_deployment",
                "params": {
                    "deployment_plan_id": "dp-001",
                    "approval_decision": "approve",
                },
                "approvalId": "appr-deploy-001",
                "audit_context": {"reason": "Deployment review passed"},
            },
        },
        {
            "key": "approval",
            "token": APPROVER_TOKEN,
            "actor_id": "op-6",
            "body": {
                "command": "ApproveDecision",
                "target": {"type": "ApprovalDecision", "id": "appr-001"},
                "action": "approve",
                "params": {"decision_id": "appr-001"},
                "approvalId": "appr-review-001",
                "audit_context": {"reason": "Approval evidence is complete"},
            },
        },
        {
            "key": "runtime",
            "token": OPERATOR_TOKEN,
            "actor_id": "op-2",
            "body": {
                "command": "PauseRuntime",
                "target": {"type": "RuntimeBinding", "id": "rb-001"},
                "action": "pause_runtime",
                "params": {
                    "runtime_binding_id": "rb-001",
                    "pause_action": "pause",
                },
                "confirmToken": "ct-p0-bff-cmd-runtime",
                "audit_context": {"reason": "Operator requested runtime pause"},
            },
        },
        {
            "key": "incident",
            "token": ADMIN_MFA_TOKEN,
            "actor_id": "op-admin",
            "body": {
                "command": "ActivateKillSwitch",
                "target": {"type": "KillSwitchOrder", "id": "ks-pool-001"},
                "action": "activate_kill_switch",
                "params": {
                    "scope": "pool",
                    "activate": True,
                    "severity": "critical",
                },
                "confirmToken": "ct-p0-bff-cmd-incident",
                "approvalId": "appr-incident-001",
                "twoManSignatureId": "tms-p0-bff-cmd-incident",
                "audit_context": {"reason": "Incident commander activated emergency stop"},
            },
        },
    ]

    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            confirm = client.post(
                "/bff/confirm-tokens",
                json={
                    "tokenId": "ct-p0-bff-cmd-runtime",
                    "command": "PauseRuntime",
                    "target": {"type": "RuntimeBinding", "id": "rb-001"},
                    "operator_id": "op-2",
                    "reason": "confirm operator runtime pause",
                },
                headers={"Authorization": OPERATOR_TOKEN, "Idempotency-Key": "confirm-p0-bff-cmd-runtime"},
            )
            assert confirm.status_code == 201, confirm.text

            confirm = client.post(
                "/bff/confirm-tokens",
                json={
                    "tokenId": "ct-p0-bff-cmd-incident",
                    "command": "ActivateKillSwitch",
                    "target": {"type": "KillSwitchOrder", "id": "ks-pool-001"},
                    "operator_id": "op-admin",
                    "reason": "confirm incident kill switch activation",
                },
                headers={"Authorization": ADMIN_MFA_TOKEN, "Idempotency-Key": "confirm-p0-bff-cmd-incident"},
            )
            assert confirm.status_code == 201, confirm.text

            for operator_id, authorization in (
                ("op-2", "Bearer op-2:operator"),
                ("op-3", "Bearer op-3:operator"),
            ):
                signed = client.post(
                    "/bff/v5/interventions/tms-p0-bff-cmd-incident/two-man-sign",
                    json={
                        "twoManSignatureId": "tms-p0-bff-cmd-incident",
                        "command": "ActivateKillSwitch",
                        "target": {"type": "KillSwitchOrder", "id": "ks-pool-001"},
                        "reason": "authenticated operator approved kill switch activation",
                    },
                    headers={
                        "Authorization": authorization,
                        "Idempotency-Key": f"sign-p0-bff-cmd-incident-{operator_id}",
                    },
                )
                assert signed.status_code == 202, signed.text

            for case in cases:
                response = client.post(
                    "/bff/v1/commands",
                    headers=_command_headers(
                        case["token"],
                        f"idmp-p0-bff-cmd-{case['key']}",
                        f"trace-p0-bff-cmd-{case['key']}",
                    ),
                    json=case["body"],
                )
                assert response.status_code == 202, response.text

            # The confirm-token/two-man-sign setup above also wrote command
            # records; filter down to the four commands under test (each a
            # distinct, non-overlapping command type) before asserting on
            # foundation metadata.
            case_command_types = {case["body"]["command"] for case in cases}
            records = [
                record
                for record in bff_main.command_store._get_all_commands()
                if record.get("type") in case_command_types
            ]
            assert len(records) == len(cases)

            for record, case in zip(records, cases):
                foundation = record["foundation"]
                envelope = foundation["command_envelope"]
                policy_decision = foundation["policy_decision"]
                audit_action = foundation["audit_action"]
                trace_id = f"trace-p0-bff-cmd-{case['key']}"
                idempotency_key = f"idmp-p0-bff-cmd-{case['key']}"

                assert envelope["actor_ref"]["actor_id"] == case["actor_id"]
                assert envelope["trace"]["trace_id"] == trace_id
                assert envelope["trace"]["actor_ref"]["actor_id"] == case["actor_id"]
                assert envelope["idempotency_key"] == idempotency_key
                assert foundation["idempotency_record"]["idempotency_key"] == idempotency_key
                assert foundation["idempotency_record"]["trace_id"] == trace_id
                assert policy_decision["decision"] == "allow"
                assert policy_decision["actor_ref"]["actor_id"] == case["actor_id"]
                assert policy_decision["trace_id"] == trace_id
                assert audit_action["actor_ref"]["actor_id"] == case["actor_id"]
                assert audit_action["trace_id"] == trace_id
                assert audit_action["policy_decision_ref"] == policy_decision["decision_id"]
                assert record["audit"]["foundation"]["command_envelope"]["command_id"] == record["command_id"]
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_submit_command_rejects_live_runtime_scope_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_LIVE_BROKER_ENABLED", "false")
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(OPERATOR_TOKEN, "idmp-live-scope-deny-001"),
                json={
                    "command": "PauseExecution",
                    "target": {"type": "Runtime", "id": "runtime-live-001"},
                    "action": "pause_execution",
                    "params": {
                        "pause_new_entries": True,
                        "cancel_open_orders": True,
                        "broker_mode": "live",
                    },
                    "audit_context": {"reason": "EP5-002 live broker rehearsal"},
                },
            )
            assert response.status_code == 403, response.text
            error = _error_detail(response)["error"]
            assert error["code"] == "PRECONDITION_FAILED"
            assert error["details"]["precondition_failed"] == "live_broker_scope"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_cors_origin_env_parser_trims_and_normalizes(monkeypatch) -> None:
    monkeypatch.setattr(http_security, "_is_production_strict_mode", lambda: True)
    monkeypatch.setenv(
        "PANTHEON_BFF_CORS_ORIGINS",
        " https://dev.lovable.app/, https://staging.lovable.app ",
    )
    assert http_security._cors_origins_from_env() == [
        "https://dev.lovable.app",
        "https://staging.lovable.app",
    ]


def test_submit_command_accepts_approve_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = _create_test_read_store()
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        # ApproveMutation requires approval evidence on /bff/v1/commands (the
        # retired legacy route skipped this precondition); seed a matching
        # approval decision and reference it via approvalId to keep this
        # test's "submission succeeds" intent true. Fall back to the
        # original lookup (used by the mutation-review-surface validator via
        # the evolution decision's own approval_decision_id) for other ids.
        original_get_approval_decision = bff_main.read_store.get_approval_decision

        def get_approval_decision(aid):
            if aid == "appr-approve-mutation-001":
                return {
                    "id": aid,
                    "outcome": "approved",
                    "state": "approved",
                    "command": "ApproveMutation",
                    "target_type": "EvolutionDecision",
                    "target_id": "evo-dec-88f3a2c1",
                }
            return original_get_approval_decision(aid)

        bff_main.read_store.get_approval_decision = get_approval_decision

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(APPROVER_TOKEN, "idmp-approve-mutation-001"),
                json={
                    "command_type": "ApproveMutation",
                    "decision_id": "evo-dec-88f3a2c1",
                    "note": "Risk review complete",
                    "approvalId": "appr-approve-mutation-001",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "ApproveMutation"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_reject_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = _create_test_read_store()
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(OPERATOR_TOKEN, "idmp-reject-mutation-001"),
                json={
                    "command_type": "RejectMutation",
                    "decision_id": "evo-dec-88f3a2c1",
                    "note": "Evidence is still incomplete",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "RejectMutation"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_review_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = _create_test_read_store(
            extra_evolution_decisions={
                "evo-dec-review-001": {
                    "id": "evo-dec-review-001",
                    "decision_id": "evo-dec-review-001",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-review-001",
                    "target_version": "v1.0.0",
                    "action_type": "freeze_canary",
                    "risk_level": "medium",
                    "status": "proposed",
                    "decision_state": "proposed",
                    "created_at": "2026-07-01T00:00:00Z",
                    "rationale": "Initial threshold breach triage.",
                }
            },
            # ReviewMutation requires approval evidence on /bff/v1/commands
            # (the retired legacy route skipped this precondition); seed a
            # matching, approved decision bound to this command/target.
            extra_approval_decisions={
                "appr-automated-gate-001": {
                    "id": "appr-automated-gate-001",
                    "outcome": "approved",
                    "state": "approved",
                    "command": "ReviewMutation",
                    "target_type": "EvolutionDecision",
                    "target_id": "evo-dec-review-001",
                }
            },
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(APPROVER_TOKEN, "idmp-review-mutation-001"),
                json={
                    "command_type": "ReviewMutation",
                    "decision_id": "evo-dec-review-001",
                    "approval_decision_id": "appr-automated-gate-001",
                    "note": "Automated gate triage complete",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "ReviewMutation"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_execute_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = _create_test_read_store(
            extra_approval_decisions={
                # ExecuteMutation requires approval evidence on
                # /bff/v1/commands (the retired legacy route skipped this
                # precondition); bind this decision to the command/target so
                # it satisfies the new precondition.
                "appr-dec-exec-001": {
                    "id": "appr-dec-exec-001",
                    "decision_id": "appr-dec-exec-001",
                    "outcome": "approved",
                    "state": "approved",
                    "command": "ExecuteMutation",
                    "target_type": "EvolutionDecision",
                    "target_id": "evo-dec-exec-001",
                }
            },
            extra_evolution_decisions={
                "evo-dec-exec-001": {
                    "id": "evo-dec-exec-001",
                    "decision_id": "evo-dec-exec-001",
                    "target_type": "candidate_artifact",
                    "target_id": "artifact-exec-001",
                    "target_version": "v1.0.0",
                    "action_type": "freeze_canary",
                    "risk_level": "medium",
                    "status": "approved",
                    "decision_state": "approved",
                    "approval_decision_id": "appr-dec-exec-001",
                    "created_at": "2026-07-01T00:00:00Z",
                    "rationale": "Ready for execution.",
                }
            },
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(OPERATOR_TOKEN, "idmp-execute-mutation-001"),
                json={
                    "command_type": "ExecuteMutation",
                    "decision_id": "evo-dec-exec-001",
                    "note": "Executing approved freeze",
                    "approvalId": "appr-dec-exec-001",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "ExecuteMutation"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


def test_submit_command_accepts_record_sponsor_decision_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = _create_test_read_store()
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers=_command_headers(OPERATOR_TOKEN, "idmp-record-sponsor-decision-001"),
                json={
                    "command_type": "RecordSponsorDecision",
                    "committee_id": "committee-regime-risk-20260419-081",
                    "sponsor_decision": "approved",
                    "rationale_ref": "workspace://committee-rationales/committee-regime-risk-20260419-081/final",
                    "note": "Sponsor resolved the split decision",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["data"]["command"] == "RecordSponsorDecision"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker


# --------------------------------------------------------------------------- #
# BFF-FINAL-002: /bff/v1/commands — idempotency and command envelope tests
# --------------------------------------------------------------------------- #

_FINAL_BODY = {
    "command": "ApproveDecision",
    "target": {"type": "ApprovalDecision", "id": "appr-final-001"},
    "approvalId": "approval-final-001",
    "params": {"decision_id": "appr-final-001"},
    "audit_context": {"reason": "Final contract test"},
}


def test_bff_v1_commands_accepts_idempotency_key_header() -> None:
    """Idempotency-Key header is accepted on the final contract route."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "Idempotency-Key": "final-key-001",
                    "X-Correlation-Id": "corr-final-key-001",
                    "X-Request-Id": "req-final-key-001",
                },
                json=_FINAL_BODY,
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["status"] in ("accepted", "queued", "completed")
            assert "data" in payload
            assert payload["meta"]["idempotency"]["idempotencyKey"] == "final-key-001"
            assert payload["meta"]["idempotency"]["replayed"] is False

            records = bff_main.command_store._get_all_commands()
            assert len(records) == 1
            trace = records[0]["foundation"]["trace_context"]
            assert trace["correlation_id"] == "corr-final-key-001"
            assert trace["request_id"] == "req-final-key-001"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_accepts_x_idempotency_key_as_alias() -> None:
    """X-Idempotency-Key is accepted as a compatibility alias on the final route."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "X-Idempotency-Key": "final-alias-key-001",
                },
                json=_FINAL_BODY,
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert "data" in payload
            assert payload["meta"]["idempotency"]["idempotencyKey"] == "final-alias-key-001"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_canonical_key_takes_precedence_over_alias() -> None:
    """When both headers are present, Idempotency-Key takes precedence."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "Idempotency-Key": "canonical-key-001",
                    "X-Idempotency-Key": "alias-key-should-be-ignored",
                },
                json=_FINAL_BODY,
            )
            assert response.status_code == 202, response.text

            records = bff_main.command_store._get_all_commands()
            assert len(records) == 1
            foundation = records[0]["foundation"]
            assert foundation["idempotency_record"]["idempotency_key"] == "canonical-key-001"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_rejects_missing_idempotency_key() -> None:
    """Both Idempotency-Key and X-Idempotency-Key absent → 400 INVALID_PARAMS."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={"Authorization": APPROVER_TOKEN},
                json=_FINAL_BODY,
            )
            assert response.status_code == 400, response.text
            detail = _error_detail(response)
            assert detail["error"]["code"] == "VALIDATION_FAILED"
            assert detail["error"]["details"]["precondition_failed"] == "idempotency_key"
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_rejects_body_idempotency_key() -> None:
    """idempotencyKey in the request body → 400 INVALID_REQUEST on the final route."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        body_with_key = {**_FINAL_BODY, "idempotencyKey": "should-be-in-header"}

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "Idempotency-Key": "final-key-body-reject",
                },
                json=body_with_key,
            )
            assert response.status_code == 400, response.text
            detail = _error_detail(response)
            assert detail["error"]["code"] == "VALIDATION_FAILED"
            assert detail["error"]["details"]["precondition_failed"] == "body_idempotency_key"
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_replay_returns_command_response() -> None:
    """Same Idempotency-Key + same body → identical CommandResponse replay."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        headers = {
            "Authorization": APPROVER_TOKEN,
            "Idempotency-Key": "final-replay-key-001",
        }
        # Provide a fixed timestamp so both requests compute identical request hashes
        # regardless of when they execute (audit_context.timestamp is auto-generated otherwise).
        stable_body = {
            **_FINAL_BODY,
            "audit_context": {"reason": "Final contract test", "timestamp": "2026-05-07T12:00:00Z"},
        }

        try:
            first = client.post("/bff/v1/commands", headers=headers, json=stable_body)
            second = client.post("/bff/v1/commands", headers=headers, json=stable_body)

            assert first.status_code == 202, first.text
            assert second.status_code == 202, second.text

            first_data = first.json()
            second_data = second.json()
            assert "data" in first_data
            assert "data" in second_data
            assert first_data["data"]["receipt_id"] == second_data["data"]["receipt_id"]
            assert first_data["meta"]["idempotency"]["replayed"] is False
            assert second_data["meta"]["idempotency"]["idempotencyKey"] == "final-replay-key-001"
            assert second_data["meta"]["idempotency"]["replayed"] is True

            records = bff_main.command_store._get_all_commands()
            assert len(records) == 1
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_conflict_returns_idempotency_conflict() -> None:
    """Same Idempotency-Key + different body → 409 IDEMPOTENCY_CONFLICT."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        headers = {
            "Authorization": APPROVER_TOKEN,
            "Idempotency-Key": "final-conflict-key-001",
        }
        # Use fixed timestamps so only the reason differs, guaranteeing different hashes.
        body_a = {**_FINAL_BODY, "audit_context": {"reason": "First payload", "timestamp": "2026-05-07T12:00:00Z"}}
        body_b = {**_FINAL_BODY, "audit_context": {"reason": "Conflicting payload", "timestamp": "2026-05-07T12:00:00Z"}}

        try:
            first = client.post("/bff/v1/commands", headers=headers, json=body_a)
            assert first.status_code == 202, first.text

            second = client.post("/bff/v1/commands", headers=headers, json=body_b)
            assert second.status_code == 409, second.text
            detail = _error_detail(second)
            assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_bff_v1_commands_returns_command_response_envelope() -> None:
    """Response shape is CommandResponse with status and data fields."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/bff/v1/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "Idempotency-Key": "final-envelope-key-001",
                },
                json=_FINAL_BODY,
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert "status" in payload
            assert payload["status"] in ("accepted", "queued", "completed")
            assert "data" in payload
            assert payload["data"] is not None
            assert "receipt_id" in payload["data"]
            command_id = payload["data"]["receipt_id"]
            tracking_url = f"/api/v1/operator/commands/{command_id}"
            assert payload["data"]["command_id"] == command_id
            assert payload["data"]["commandId"] == command_id
            assert payload["data"]["tracking_url"] == tracking_url
            assert payload["data"]["trackingUrl"] == tracking_url
            assert payload["data"]["receipt"]["command_id"] == command_id
            assert payload["data"]["receipt"]["trackingUrl"] == tracking_url
            assert payload["meta"]["idempotency"]["idempotencyKey"] == "final-envelope-key-001"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_legacy_api_v1_commands_post_is_retired() -> None:
    """POST /api/v1/operator/commands has been fully retired (no compat shim);
    only the canonical POST /bff/v1/commands write route and the surviving
    GET /api/v1/operator/commands/{command_id} status readback remain."""
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers={
                    "Authorization": APPROVER_TOKEN,
                    "X-Idempotency-Key": "legacy-key-001",
                },
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-legacy-001"},
                    "params": {"decision_id": "appr-legacy-001"},
                    "audit_context": {"reason": "Legacy path test"},
                },
            )
            assert response.status_code in (404, 405), response.text
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker
