from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import (
    CommandAdapterService,
    _reject_body_idempotency_key,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.core import http_security
from services.control_plane.bff.governance.service import GovernanceService, utc_now_rfc3339
from services.control_plane.bff.models import CommandType, ErrorCode
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

_DATA_PATH = Path(__file__).resolve().parent / "data" / "read_surfaces.json"
with open(_DATA_PATH, "r", encoding="utf-8") as _f:
    _RAW_DATA = json.load(_f)


def _create_test_read_store(extra_evolution_decisions=None, extra_approval_decisions=None):
    evo_decisions = dict(_RAW_DATA.get("evolution_decisions", {}))
    if extra_evolution_decisions:
        evo_decisions.update(extra_evolution_decisions)

    appr_decisions = dict(_RAW_DATA.get("approval_decisions", {}))
    appr_decisions.setdefault(
        "approval-final-001",
        {
            "id": "approval-final-001",
            "decision_id": "approval-final-001",
            "outcome": "approved",
            "state": "approved",
            "command": "ApproveDecision",
            "target_type": "ApprovalDecision",
            "target_id": "appr-final-001",
            "reviewer": "governance",
            "risk_level": "medium",
        },
    )
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


# ---------------------------------------------------------------------------
# Test-scoped validator mocks (CB04 migration_approach: explicit validator
# mocks in place of importing main._VALIDATORS). Each mock reproduces only
# the required-field and role-gate checks main.py's per-command-type
# _validate_* function enforces for the scenarios this suite exercises; the
# mutation/committee family delegates to the real, already-importable
# GovernanceService projections (production code, not reimplemented) for
# the allowedActions gate that main.py's own validators call through to.
# ---------------------------------------------------------------------------


def _require_fields(params: Dict[str, Any], required: set, command: str) -> None:
    missing = required - params.keys()
    if missing:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"Missing required params for {command}",
            f"Missing fields: {sorted(missing)}",
        )


def _require_role(identity: Any, roles: set, command: str) -> None:
    if not roles.intersection(getattr(identity, "roles", []) or []):
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            f"{command} requires one of {sorted(roles)} role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
        )


def _validate_approve_decision(params: Dict[str, Any], identity: Any) -> None:
    _require_fields(params, {"decision_id"}, "ApproveDecision")
    _require_role(identity, {"approver", "admin"}, "ApproveDecision")


def _validate_approve_deployment(params: Dict[str, Any], identity: Any) -> None:
    _require_fields(params, {"deployment_plan_id", "approval_decision"}, "ApproveDeployment")
    if params["approval_decision"] not in {"approve", "reject"}:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid approval_decision value",
            "Must be one of {'approve', 'reject'}",
        )
    _require_role(identity, {"approver", "admin"}, "ApproveDeployment")


def _validate_pause_runtime(params: Dict[str, Any], identity: Any) -> None:
    _require_fields(params, {"runtime_binding_id", "pause_action"}, "PauseRuntime")
    if params["pause_action"] not in {"pause", "resume"}:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid pause_action value",
            "Must be one of {'pause', 'resume'}",
        )
    _require_role(identity, {"operator", "admin"}, "PauseRuntime")


def _validate_pause_execution(params: Dict[str, Any], identity: Any) -> None:
    _require_fields(params, {"pause_new_entries", "cancel_open_orders"}, "PauseExecution")
    for field in ("pause_new_entries", "cancel_open_orders"):
        if not isinstance(params.get(field), bool):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {field} value",
                f"{field} must be a boolean",
            )
    _require_role(identity, {"operator", "admin"}, "PauseExecution")


def _validate_escalate_diff(params: Dict[str, Any], identity: Any) -> None:
    _require_fields(params, {"plan_id", "escalation_reason"}, "EscalateDiff")
    if not str(params.get("escalation_reason") or "").strip():
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "EscalateDiff requires a non-empty escalation_reason",
            "escalation_reason must be a non-empty string",
        )
    _require_role(identity, {"operator", "reviewer", "approver", "admin"}, "EscalateDiff")


def _validate_activate_kill_switch(params: Dict[str, Any], identity: Any) -> None:
    _require_fields(params, {"scope", "activate"}, "ActivateKillSwitch")
    if params["scope"] not in {"persona", "pool", "all"}:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid scope for ActivateKillSwitch",
            "Must be one of {'persona', 'pool', 'all'}",
        )
    severity = params.get("severity")
    if severity is not None and severity not in {"critical", "high", "medium"}:
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "Invalid severity for ActivateKillSwitch",
            "Must be one of {'critical', 'high', 'medium'}",
        )
    if "admin" not in (identity.roles or []):
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "ActivateKillSwitch requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
        )
    if not identity.mfa_verified:
        raise bff_error(
            403,
            ErrorCode.AUTH_REQUIRED,
            "ActivateKillSwitch requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
        )


def _build_validators(get_read_store):
    def _record_sponsor_decision(params: Dict[str, Any], identity: Any) -> None:
        _require_fields(params, {"committee_id", "sponsor_decision", "rationale_ref"}, "RecordSponsorDecision")
        sponsor_decision = str(params.get("sponsor_decision") or "").strip().lower()
        if sponsor_decision not in {"approved", "rejected", "conditional"}:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid sponsor_decision value",
                "sponsor_decision must be one of ['approved', 'conditional', 'rejected']",
            )
        if not str(params.get("rationale_ref") or "").strip():
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "RecordSponsorDecision requires a non-empty rationale_ref",
                "rationale_ref must be a non-empty string",
            )
        committee_id = str(params.get("committee_id") or "").strip()
        service = GovernanceService(get_read_store(), utc_now=utc_now_rfc3339)
        projection = service.committee_projection(committee_id, identity=identity, snapshot_at=utc_now_rfc3339())
        if projection is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Committee board not found",
                f"Committee {committee_id} does not exist",
            )
        if projection["meta"]["surfaces"]["committee_board"] == "unavailable":
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "RecordSponsorDecision is blocked while the committee board is unavailable",
                "Committee evidence cannot be composed reliably",
                precondition_failed="committee_board_surface",
            )
        if not projection["allowedActions"]["canRecordSponsorDecision"]:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "RecordSponsorDecision is not allowed for this operator and committee state",
                "allowedActions.canRecordSponsorDecision is false for the current read projection",
                precondition_failed="allowedActions.canRecordSponsorDecision",
            )

    def _mutation_family(params, identity, *, command, required, allowed_action_key):
        _require_fields(params, required, command)
        decision_id = str(params.get("decision_id") or "").strip()
        service = GovernanceService(get_read_store(), utc_now=utc_now_rfc3339)
        projection = service.mutation_review_projection(decision_id, identity=identity, snapshot_at=utc_now_rfc3339())
        if projection is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Mutation review decision not found",
                f"Evolution decision {decision_id} does not exist",
            )
        if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
            raise bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                f"{command} is blocked while the mutation-review surface is unavailable",
                "Mutation-review evidence cannot be composed reliably",
                precondition_failed="mutation_review_surface",
            )
        if not projection["allowedActions"][allowed_action_key]:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                f"{command} is not allowed for this operator and decision state",
                f"allowedActions.{allowed_action_key} is false for the current read projection",
                precondition_failed=f"allowedActions.{allowed_action_key}",
            )

    return {
        CommandType.APPROVE_DECISION: _validate_approve_decision,
        CommandType.APPROVE_DEPLOYMENT: _validate_approve_deployment,
        CommandType.PAUSE_RUNTIME: _validate_pause_runtime,
        CommandType.PAUSE_EXECUTION: _validate_pause_execution,
        CommandType.ESCALATE_DIFF: _validate_escalate_diff,
        CommandType.ACTIVATE_KILL_SWITCH: _validate_activate_kill_switch,
        CommandType.RECORD_SPONSOR_DECISION: _record_sponsor_decision,
        CommandType.APPROVE_MUTATION: lambda p, i: _mutation_family(
            p, i, command="ApproveMutation", required={"decision_id"}, allowed_action_key="canApproveMutation"
        ),
        CommandType.REJECT_MUTATION: lambda p, i: _mutation_family(
            p, i, command="RejectMutation", required={"decision_id"}, allowed_action_key="canRejectMutation"
        ),
        CommandType.REVIEW_MUTATION: lambda p, i: _mutation_family(
            p,
            i,
            command="ReviewMutation",
            required={"decision_id", "approval_decision_id"},
            allowed_action_key="canReviewMutation",
        ),
        CommandType.EXECUTE_MUTATION: lambda p, i: _mutation_family(
            p, i, command="ExecuteMutation", required={"decision_id"}, allowed_action_key="canExecuteMutation"
        ),
    }


async def _noop_process_command(_command_id: str) -> None:
    return None


def _extract_identity(authorization: str | None, mfa_token: str | None = None) -> Any:
    return extract_identity_stub(authorization)


class _Harness:
    """Standalone composition mounting the real command-adapters and
    control-loops router factories against a single shared
    ``CommandAdapterService`` -- the same production service class
    ``main.py``'s composition root binds for ``/bff/v1/commands``,
    ``/bff/confirm-tokens``, and the two-man-sign intervention route.
    """

    def __init__(self, tmp_dir: str, **read_store_kwargs: Any) -> None:
        self.read_store = _create_test_read_store(**read_store_kwargs)
        self.command_store = CommandStore(os.path.join(tmp_dir, "commands.jsonl"))
        self.validators = _build_validators(lambda: self.read_store)
        self.command_adapter_service = CommandAdapterService(
            command_store=lambda: self.command_store,
            read_surface=lambda: self.read_store,
            extract_identity=_extract_identity,
            require_operator_role=require_operator_role,
            require_read_role=require_read_role,
            bff_error=bff_error,
            validators=self.validators,
            process_command_task=_noop_process_command,
        )
        self.app = FastAPI()
        self.app.include_router(create_command_adapters_router(service=self.command_adapter_service))
        self.app.include_router(
            create_control_loops_router(
                read_surface=lambda: self.read_store,
                submit_sem_command=self.command_adapter_service.sem_command_response,
                submit_final_command_admission=self.command_adapter_service.submit_command_admission,
                reject_body_idempotency_key=_reject_body_idempotency_key,
                extract_identity=_extract_identity,
                require_read_role=require_read_role,
                require_operator_role=require_operator_role,
                bff_error=bff_error,
                utc_now_fn=utc_now_rfc3339,
            )
        )
        self.client = TestClient(self.app, raise_server_exceptions=False)


APPROVER_TOKEN = "Bearer op-6:approver"
OPERATOR_TOKEN = "Bearer op-2:operator"
ADMIN_MFA_TOKEN = "Bearer op-admin:admin:mfa"


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


def test_submit_command_accepts_approval_queue_command_types() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_submit_command_rejects_missing_idempotency_key_with_foundation_audit() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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
        assert h.command_store._get_all_commands() == []


def test_submit_command_records_foundation_context_and_replays_idempotency() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
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

        first = h.client.post("/bff/v1/commands", headers=headers, json=body)
        second = h.client.post("/bff/v1/commands", headers=headers, json=body)

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert second.json()["data"]["receipt_id"] == first.json()["data"]["receipt_id"]

        records = h.command_store._get_all_commands()
        assert len(records) == 1
        foundation = records[0]["foundation"]
        assert foundation["trace_context"]["trace_id"] == "trace-bff-001"
        assert foundation["trace_context"]["correlation_id"] == "corr-bff-001"
        assert foundation["idempotency_record"]["idempotency_key"] == "idmp-bff-001"
        assert foundation["idempotency_record"]["status"] == "succeeded"
        assert foundation["policy_decision"]["decision"] == "allow"
        assert foundation["audit_action"]["trace_id"] == "trace-bff-001"
        assert records[0]["audit"]["foundation"]["command_envelope"]["command_id"] == records[0]["command_id"]


def test_submit_command_policy_denial_returns_foundation_error_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_submit_command_validation_error_returns_foundation_error_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_submit_command_accepts_escalate_diff() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_runtime_deployment_approval_incident_commands_record_foundation_controls() -> None:
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
        h = _Harness(td, extra_approval_decisions=approval_decisions)

        confirm = h.client.post(
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

        confirm = h.client.post(
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
            signed = h.client.post(
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
            response = h.client.post(
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
            record for record in h.command_store._get_all_commands() if record.get("type") in case_command_types
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


def test_submit_command_rejects_live_runtime_scope_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_LIVE_BROKER_ENABLED", "false")
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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
        h = _Harness(td)

        # ApproveMutation requires approval evidence on /bff/v1/commands (the
        # retired legacy route skipped this precondition); seed a matching
        # approval decision and reference it via approvalId to keep this
        # test's "submission succeeds" intent true. Fall back to the
        # original lookup (used by the mutation-review-surface validator via
        # the evolution decision's own approval_decision_id) for other ids.
        original_get_approval_decision = h.read_store.get_approval_decision

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

        h.read_store.get_approval_decision = get_approval_decision

        response = h.client.post(
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


def test_submit_command_accepts_reject_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_submit_command_accepts_review_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(
            td,
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

        response = h.client.post(
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


def test_submit_command_accepts_execute_mutation_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(
            td,
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

        response = h.client.post(
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


def test_submit_command_accepts_record_sponsor_decision_published_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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
        h = _Harness(td)
        response = h.client.post(
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

        records = h.command_store._get_all_commands()
        assert len(records) == 1
        trace = records[0]["foundation"]["trace_context"]
        assert trace["correlation_id"] == "corr-final-key-001"
        assert trace["request_id"] == "req-final-key-001"


def test_bff_v1_commands_accepts_x_idempotency_key_as_alias() -> None:
    """X-Idempotency-Key is accepted as a compatibility alias on the final route."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_bff_v1_commands_canonical_key_takes_precedence_over_alias() -> None:
    """When both headers are present, Idempotency-Key takes precedence."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
            "/bff/v1/commands",
            headers={
                "Authorization": APPROVER_TOKEN,
                "Idempotency-Key": "canonical-key-001",
                "X-Idempotency-Key": "alias-key-should-be-ignored",
            },
            json=_FINAL_BODY,
        )
        assert response.status_code == 202, response.text

        records = h.command_store._get_all_commands()
        assert len(records) == 1
        foundation = records[0]["foundation"]
        assert foundation["idempotency_record"]["idempotency_key"] == "canonical-key-001"


def test_bff_v1_commands_rejects_missing_idempotency_key() -> None:
    """Both Idempotency-Key and X-Idempotency-Key absent → 400 INVALID_PARAMS."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
            "/bff/v1/commands",
            headers={"Authorization": APPROVER_TOKEN},
            json=_FINAL_BODY,
        )
        assert response.status_code == 400, response.text
        detail = _error_detail(response)
        assert detail["error"]["code"] == "VALIDATION_FAILED"
        assert detail["error"]["details"]["precondition_failed"] == "idempotency_key"
        assert h.command_store._get_all_commands() == []


def test_bff_v1_commands_rejects_body_idempotency_key() -> None:
    """idempotencyKey in the request body → 400 INVALID_REQUEST on the final route."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        body_with_key = {**_FINAL_BODY, "idempotencyKey": "should-be-in-header"}

        response = h.client.post(
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
        assert h.command_store._get_all_commands() == []


def test_bff_v1_commands_replay_returns_command_response() -> None:
    """Same Idempotency-Key + same body → identical CommandResponse replay."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
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

        first = h.client.post("/bff/v1/commands", headers=headers, json=stable_body)
        second = h.client.post("/bff/v1/commands", headers=headers, json=stable_body)

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

        records = h.command_store._get_all_commands()
        assert len(records) == 1


def test_bff_v1_commands_conflict_returns_idempotency_conflict() -> None:
    """Same Idempotency-Key + different body → 409 IDEMPOTENCY_CONFLICT."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        headers = {
            "Authorization": APPROVER_TOKEN,
            "Idempotency-Key": "final-conflict-key-001",
        }
        # Use fixed timestamps so only the reason differs, guaranteeing different hashes.
        body_a = {**_FINAL_BODY, "audit_context": {"reason": "First payload", "timestamp": "2026-05-07T12:00:00Z"}}
        body_b = {**_FINAL_BODY, "audit_context": {"reason": "Conflicting payload", "timestamp": "2026-05-07T12:00:00Z"}}

        first = h.client.post("/bff/v1/commands", headers=headers, json=body_a)
        assert first.status_code == 202, first.text

        second = h.client.post("/bff/v1/commands", headers=headers, json=body_b)
        assert second.status_code == 409, second.text
        detail = _error_detail(second)
        assert detail["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_bff_v1_commands_returns_command_response_envelope() -> None:
    """Response shape is CommandResponse with status and data fields."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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


def test_legacy_api_v1_commands_post_is_retired() -> None:
    """POST /api/v1/operator/commands has been fully retired (no compat shim);
    only the canonical POST /bff/v1/commands write route and the surviving
    GET /api/v1/operator/commands/{command_id} status readback remain."""
    with tempfile.TemporaryDirectory() as td:
        h = _Harness(td)
        response = h.client.post(
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
        assert h.command_store._get_all_commands() == []
