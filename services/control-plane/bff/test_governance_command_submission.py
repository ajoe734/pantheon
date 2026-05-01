from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


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


def test_submit_command_accepts_approval_queue_command_types() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers=_command_headers(APPROVER_TOKEN, "idmp-approve-decision-001"),
                json={
                    "command": "ApproveDecision",
                    "target": {"type": "ApprovalDecision", "id": "appr-001"},
                    "action": "approve",
                    "params": {
                        "decision_id": "appr-001",
                        "approval_notes": "Proceed to approval",
                    },
                    "audit_context": {"reason": "Policy checks passed"},
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "ApproveDecision"
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
                "/api/v1/operator/commands",
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
            detail = response.json()["detail"]
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
            "target": {"type": "ApprovalDecision", "id": "appr-001"},
            "action": "approve",
            "params": {
                "decision_id": "appr-001",
                "approval_notes": "Proceed to approval",
            },
            "audit_context": {"reason": "Policy checks passed"},
        }

        try:
            first = client.post("/api/v1/operator/commands", headers=headers, json=body)
            second = client.post("/api/v1/operator/commands", headers=headers, json=body)

            assert first.status_code == 202, first.text
            assert second.status_code == 202, second.text
            assert second.json()["receipt_id"] == first.json()["receipt_id"]

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
                "/api/v1/operator/commands",
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
            detail = response.json()["detail"]
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
                "/api/v1/operator/commands",
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
            detail = response.json()["detail"]
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
                "/api/v1/operator/commands",
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
            assert payload["command"] == "EscalateDiff"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_runtime_deployment_approval_incident_commands_record_foundation_controls() -> None:
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
            for case in cases:
                response = client.post(
                    "/api/v1/operator/commands",
                    headers=_command_headers(
                        case["token"],
                        f"idmp-p0-bff-cmd-{case['key']}",
                        f"trace-p0-bff-cmd-{case['key']}",
                    ),
                    json=case["body"],
                )
                assert response.status_code == 202, response.text

            records = bff_main.command_store._get_all_commands()
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
                "/api/v1/operator/commands",
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
            error = response.json()["detail"]["error"]
            assert error["code"] == "PRECONDITION_NOT_MET"
            assert error["details"]["precondition_failed"] == "live_broker_scope"
        finally:
            bff_main.command_store = original_store
            bff_main._process_command_stub = original_worker


def test_cors_origin_env_parser_trims_and_normalizes(monkeypatch) -> None:
    monkeypatch.setenv(
        "PANTHEON_BFF_CORS_ORIGINS",
        " https://dev.lovable.app/, https://staging.lovable.app ",
    )
    assert bff_main._cors_origins_from_env() == [
        "https://dev.lovable.app",
        "https://staging.lovable.app",
    ]


def test_submit_command_accepts_approve_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.command_store
        original_read_store = bff_main.read_store
        original_worker = bff_main._process_command_stub
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers=_command_headers(APPROVER_TOKEN, "idmp-approve-mutation-001"),
                json={
                    "command_type": "ApproveMutation",
                    "decision_id": "evo-dec-88f3a2c1",
                    "note": "Risk review complete",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "ApproveMutation"
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
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
                headers=_command_headers(OPERATOR_TOKEN, "idmp-reject-mutation-001"),
                json={
                    "command_type": "RejectMutation",
                    "decision_id": "evo-dec-88f3a2c1",
                    "note": "Evidence is still incomplete",
                },
            )
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["command"] == "RejectMutation"
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
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main._process_command_stub = _noop_process_command
        client = TestClient(bff_main.app)

        try:
            response = client.post(
                "/api/v1/operator/commands",
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
            assert payload["command"] == "RecordSponsorDecision"
            assert payload["status"] == "accepted"
        finally:
            bff_main.command_store = original_store
            bff_main.read_store = original_read_store
            bff_main._process_command_stub = original_worker
