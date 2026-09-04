"""
Smoke / unit tests for the Pantheon Operator BFF.

Covers:
  - Health endpoint
  - Submit + poll happy path (ApproveDeployment)
  - Command not found
  - Missing Authorization header -> AUTH_REQUIRED
  - Missing required params -> VALIDATION_FAILED
  - Insufficient role -> FORBIDDEN
  - Kill-switch without MFA -> AUTH_REQUIRED
  - Kill-switch with invalid scope -> VALIDATION_FAILED
  - Concurrent modification detection -> RESOURCE_CONFLICT
  - Degraded read surface → staleness_warning in response
  - All eight command types submit successfully with correct roles
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

# Use a temp dir so tests don't share state with each other
os.environ["BFF_DATA_DIR"] = "/tmp/pantheon/bff_test"
os.environ.setdefault("BFF_READ_SURFACE_STATE", "fresh")
os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")
from fastapi import HTTPException
from fastapi.testclient import TestClient
from services.control_plane.bff import main as bff_main
from services.control_plane.bff.agora.identity.scope import AgoraScopeResolutionError
from services.control_plane.bff.agora.router import _raise_scope_error
from services.control_plane.bff.models import (
    CommandReceiptStatus,
    CommandRoutingPath,
    CommandStatus,
    CommandType,
    ErrorCode,
)
from services.control_plane.bff.ports import ReadSurfacePorts


class SmokeTestStore(ReadSurfacePorts):
    def __init__(self, data: Optional[dict[str, Any]] = None) -> None:
        super().__init__()
        self._data = data or {}

    def get_deployment_plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        plans = self._data.get("deployment_plans", {})
        return plans.get(plan_id)

    def get_capital_pool(self, pool_id: str) -> Optional[dict[str, Any]]:
        pools = self._data.get("capital_pools", {})
        return pools.get(pool_id)

    def get_bindings_for_pool(self, pool_id: str) -> list[dict[str, Any]]:
        bindings = self._data.get("bindings", {})
        return [b for b in bindings.values() if b.get("capital_pool_id") == pool_id]

    def get_runtime_binding(self, binding_id: str) -> Optional[dict[str, Any]]:
        rb = self._data.get("runtime_bindings", {})
        return rb.get(binding_id)

    def get_approval_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        dec = self._data.get("approval_decisions", {})
        return dec.get(decision_id)

    def get_rollbacks(self, runtime_id: Optional[str] = None) -> list[dict[str, Any]]:
        rbs = self._data.get("rollbacks", {})
        return list(rbs.values())

    def get_allowed_actions(self, plan_id: str) -> list[str]:
        actions = self._data.get("allowed_actions", {})
        return actions.get(plan_id, ["approve", "reject"])

    def get_latest_run(self, plan_id: str) -> Optional[dict[str, Any]]:
        runs = self._data.get("latest_runs", {})
        return runs.get(plan_id)

    def get_review_summary(self, plan_id: str) -> Optional[dict[str, Any]]:
        reviews = self._data.get("review_summaries", {})
        return reviews.get(plan_id)

    def get_rollback_review(self, rollback_id: str) -> Optional[dict[str, Any]]:
        reviews = self._data.get("rollback_reviews", {})
        return reviews.get(rollback_id)

    def list_governance_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        raw = self._data.get("governance_review_queue_items", [])
        if isinstance(raw, dict):
            return list(raw.values())
        return list(raw)


command_store = bff_main.command_store

# ------------------------------------------------------------------ helpers --

APPROVER_TOKEN = "Bearer op-1:approver"
OPERATOR_TOKEN = "Bearer op-2:operator"
ADMIN_TOKEN = "Bearer op-3:admin"
ADMIN_MFA_TOKEN = "Bearer op-4:admin:mfa"
REVIEWER_TOKEN = "Bearer op-5:reviewer"


def _command_headers(token: str | None, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Idempotency-Key": idempotency_key or f"idmp-smoke-{uuid.uuid4().hex}"}
    if token:
        headers["Authorization"] = token
    return headers


def _submit(client, token=APPROVER_TOKEN, **overrides):
    payload = {
        "command": "ApproveDeployment",
        "target": {"type": "DeploymentPlan", "id": "dp-001"},
        "action": "approve",
        "params": {
            "deployment_plan_id": "dp-001",
            "approval_decision": "approve",
        },
        "audit_context": {"reason": "Test approval"},
    }
    idempotency_key = overrides.pop("idempotency_key", None)
    payload.update(overrides)
    headers = _command_headers(token, idempotency_key)
    return client.post("/api/v1/operator/commands", json=payload, headers=headers)


# ----------------------------------------------------------------- fixtures --

class TestOperatorBFF(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(bff_main.app)
        # Reset env to fresh each test
        os.environ["BFF_READ_SURFACE_STATE"] = "fresh"
        os.makedirs(os.path.dirname(bff_main.command_store.file_path), exist_ok=True)
        with open(bff_main.command_store.file_path, "w", encoding="utf-8") as handle:
            handle.write("")
        data_path = Path(__file__).resolve().parent / "data" / "read_surfaces.json"
        if data_path.exists():
            with open(data_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        else:
            raw_data = {}
        self._seeded_read_store = SmokeTestStore(raw_data)

    def _assert_error_code(self, response, code: str) -> None:
        body = response.json()
        self.assertNotIn("detail", body)
        self.assertEqual(body["error"]["code"], code)
        self.assertIn("correlationId", body["meta"])
        self.assertEqual(response.headers["X-Correlation-Id"], body["meta"]["correlationId"])

    # ---------------------------------------------------------------------- #
    # Health
    # ---------------------------------------------------------------------- #
    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["service"], "operator-bff")

    # ---------------------------------------------------------------------- #
    # Read surfaces (Wave 1)
    # ---------------------------------------------------------------------- #
    def test_deployment_review_composed_view(self):
        original_read_store = bff_main.read_store
        bff_main.read_store = self._seeded_read_store
        try:
            r = self.client.get(
                "/api/v1/operator/deployment-review/plan-F-042",
                headers={"Authorization": OPERATOR_TOKEN},
            )
        finally:
            bff_main.read_store = original_read_store
        self.assertEqual(r.status_code, 200, r.text)
        payload = r.json()
        data = payload.get("data", {})
        for key in [
            "deployment_plan",
            "capital_pool",
            "bindings",
            "runtime_binding",
            "allowedActions",
            "latestRun",
            "review",
        ]:
            self.assertIn(key, data)
        meta = payload.get("meta", {})
        self.assertIn("snapshot_at", meta)
        self.assertIn("surfaces", meta)

    def test_deployment_review_requires_backend_owned_plan_in_honest_mode(self):
        store = SmokeTestStore({})
        original_read_store = bff_main.read_store
        with patch.dict(
            os.environ,
            {
                "PANTHEON_GOVERNANCE_DATA_DIR": "",
                "PANTHEON_RUNTIME_DATA_DIR": "",
            },
            clear=False,
        ):
            bff_main.read_store = store
            try:
                r = self.client.get(
                    "/api/v1/operator/deployment-review/plan-F-042",
                    headers={"Authorization": OPERATOR_TOKEN},
                )
            finally:
                bff_main.read_store = original_read_store

            self.assertEqual(r.status_code, 404, r.text)

    def test_rollback_review_composed_view(self):
        original_read_store = bff_main.read_store
        bff_main.read_store = self._seeded_read_store
        try:
            r = self.client.get(
                "/api/v1/operator/rollback-review/rollback-rb-001",
                headers={"Authorization": OPERATOR_TOKEN},
            )
        finally:
            bff_main.read_store = original_read_store
        self.assertEqual(r.status_code, 200, r.text)
        payload = r.json()
        for key in [
            "rollback_id",
            "target_plan_id",
            "trigger_reason",
            "position_impact",
            "affected_bindings",
            "trigger_evidence",
            "allowedActions",
            "meta",
        ]:
            self.assertIn(key, payload)
        self.assertIn("surfaces", payload["meta"])

    def test_governance_review_queue_composed_view(self):
        original_read_store = bff_main.read_store
        bff_main.read_store = self._seeded_read_store
        try:
            r = self.client.get(
                "/api/v1/operator/governance/review-queue",
                headers={"Authorization": OPERATOR_TOKEN},
            )
        finally:
            bff_main.read_store = original_read_store
        self.assertEqual(r.status_code, 200, r.text)
        payload = r.json()
        self.assertIn("items", payload)
        self.assertIn("page_info", payload)
        self.assertIn("meta", payload)
        self.assertIn("snapshot_at", payload["meta"])
        self.assertIn("surfaces", payload["meta"])
        self.assertIn("review_queue", payload["meta"]["surfaces"])
        self.assertIn("allowedActions", payload["meta"]["surfaces"])
        self.assertGreaterEqual(len(payload["items"]), 1)

    # ---------------------------------------------------------------------- #
    # Happy path — submit + poll
    # ---------------------------------------------------------------------- #
    def test_submit_and_poll_command(self):
        # Submit
        r = _submit(self.client, APPROVER_TOKEN)
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertEqual(body["status"], CommandReceiptStatus.ACCEPTED.value)
        self.assertEqual(body["command"], CommandType.APPROVE_DEPLOYMENT.value)
        self.assertEqual(body["routing_path"], CommandRoutingPath.DIRECT.value)
        self.assertIn("accepted_at", body)
        self.assertIsNotNone(body["expected_completion_at"])
        self.assertIsNone(body["error_message"])
        self.assertIsNone(body.get("staleness_warning"))

        command_id = body["receipt_id"]

        # Poll
        r2 = self.client.get(
            f"/api/v1/operator/commands/{command_id}",
            headers=_command_headers(APPROVER_TOKEN),
        )
        self.assertEqual(r2.status_code, 200)
        status_body = r2.json()
        self.assertEqual(status_body["command_id"], command_id)
        self.assertIn(
            status_body["status"],
            [
                CommandStatus.SUBMITTED.value,
                CommandStatus.PROCESSING.value,
                CommandStatus.EXECUTED.value,
                CommandStatus.FAILED.value,
                CommandStatus.TIMEOUT.value,
            ],
        )
        # Audit record should be present
        self.assertIsNotNone(status_body.get("audit"))
        self.assertIn("operator_id", status_body["audit"])

    # ---------------------------------------------------------------------- #
    # 404 on unknown command ID
    # ---------------------------------------------------------------------- #
    def test_command_not_found(self):
        r = self.client.get(
            "/api/v1/operator/commands/non-existent-id",
            headers=_command_headers(APPROVER_TOKEN),
        )
        self.assertEqual(r.status_code, 404)

    # ---------------------------------------------------------------------- #
    # Authentication
    # ---------------------------------------------------------------------- #
    def test_agora_scope_error_uses_canonical_package_import(self):
        def make_error(status_code, code, message, reason, **details):
            return HTTPException(
                status_code=status_code,
                detail={
                    "code": code,
                    "message": message,
                    "reason": reason,
                    **details,
                },
            )

        scope_error = AgoraScopeResolutionError(
            status_code=401,
            reason="missing_identity",
            message="Authentication required",
        )

        with self.assertRaises(HTTPException) as raised:
            _raise_scope_error(scope_error, make_error)

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail["code"], ErrorCode.AUTH_REQUIRED)

    def test_missing_auth_header_submit(self):
        r = _submit(self.client, token=None)
        self.assertEqual(r.status_code, 401, r.text)
        self._assert_error_code(r, ErrorCode.AUTH_REQUIRED.value)

    def test_missing_auth_header_poll(self):
        r = self.client.get("/api/v1/operator/commands/fake-id")
        self.assertEqual(r.status_code, 401, r.text)
        self._assert_error_code(r, ErrorCode.AUTH_REQUIRED.value)

    # ---------------------------------------------------------------------- #
    # Param validation
    # ---------------------------------------------------------------------- #
    def test_missing_required_param_approve_deployment(self):
        r = _submit(
            self.client, APPROVER_TOKEN,
            params={"deployment_plan_id": "dp-001"},  # missing approval_decision
        )
        self.assertEqual(r.status_code, 422, r.text)
        self._assert_error_code(r, ErrorCode.VALIDATION_FAILED.value)

    def test_invalid_approval_decision_value(self):
        r = _submit(
            self.client, APPROVER_TOKEN,
            params={"deployment_plan_id": "dp-001", "approval_decision": "maybe"},
        )
        self.assertEqual(r.status_code, 422, r.text)
        self._assert_error_code(r, ErrorCode.VALIDATION_FAILED.value)

    # ---------------------------------------------------------------------- #
    # Role checks
    # ---------------------------------------------------------------------- #
    def test_insufficient_role_approve_deployment(self):
        # 'operator' role alone is not enough for ApproveDeployment
        r = _submit(self.client, OPERATOR_TOKEN)
        self.assertEqual(r.status_code, 403, r.text)
        self._assert_error_code(r, ErrorCode.FORBIDDEN.value)

    def test_pause_runtime_insufficient_role(self):
        # 'approver' role alone is not listed for PauseRuntime (needs operator or admin)
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "PauseRuntime",
                "target": {"type": "RuntimeBinding", "id": "rb-1"},
                "action": "pause",
                "params": {"runtime_binding_id": "rb-1", "pause_action": "pause", "reason": "investigation"},
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(APPROVER_TOKEN),
        )
        self.assertEqual(r.status_code, 403, r.text)
        self._assert_error_code(r, ErrorCode.FORBIDDEN.value)

    # ---------------------------------------------------------------------- #
    # Kill-switch: MFA required
    # ---------------------------------------------------------------------- #
    def test_kill_switch_requires_mfa(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ActivateKillSwitch",
                "target": {"type": "KillSwitchOrder", "id": "ks-1"},
                "action": "activate",
                "params": {"scope": "all", "activate": True, "severity": "critical", "rationale": "test"},
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(ADMIN_TOKEN),  # admin but no MFA
        )
        self.assertEqual(r.status_code, 403, r.text)
        self._assert_error_code(r, ErrorCode.AUTH_REQUIRED.value)

    def test_kill_switch_with_mfa_succeeds(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ActivateKillSwitch",
                "target": {"type": "KillSwitchOrder", "id": "ks-2"},
                "action": "activate",
                "params": {"scope": "all", "activate": True, "severity": "critical", "rationale": "test"},
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(ADMIN_MFA_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_kill_switch_invalid_scope(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ActivateKillSwitch",
                "target": {"type": "KillSwitchOrder", "id": "ks-3"},
                "action": "activate",
                "params": {"scope": "galaxy", "activate": True},
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(ADMIN_MFA_TOKEN),
        )
        self.assertEqual(r.status_code, 422, r.text)
        self._assert_error_code(r, ErrorCode.VALIDATION_FAILED.value)

    # ---------------------------------------------------------------------- #
    # Concurrent modification detection
    # ---------------------------------------------------------------------- #
    def test_concurrent_modification_rejected(self):
        target_id = "dp-concurrent-001"
        # Submit first command (should succeed)
        r1 = _submit(
            self.client, APPROVER_TOKEN,
            target={"type": "DeploymentPlan", "id": target_id},
            params={"deployment_plan_id": target_id, "approval_decision": "approve"},
        )
        self.assertEqual(r1.status_code, 202, r1.text)
        command_id = r1.json()["receipt_id"]
        command_store.update_status(command_id, CommandStatus.SUBMITTED)

        # Second command on same target while first is submitted/processing → CONCURRENT_MODIFICATION
        r2 = _submit(
            self.client, APPROVER_TOKEN,
            target={"type": "DeploymentPlan", "id": target_id},
            params={"deployment_plan_id": target_id, "approval_decision": "reject"},
        )
        self.assertEqual(r2.status_code, 409, r2.text)
        self._assert_error_code(r2, ErrorCode.RESOURCE_CONFLICT.value)

    # ---------------------------------------------------------------------- #
    # Degraded read surface → staleness_warning
    # ---------------------------------------------------------------------- #
    def test_degraded_surface_returns_staleness_warning(self):
        os.environ["BFF_READ_SURFACE_STATE"] = "degraded"
        r = _submit(self.client, APPROVER_TOKEN,
                    target={"type": "DeploymentPlan", "id": "dp-stale-001"},
                    params={"deployment_plan_id": "dp-stale-001", "approval_decision": "approve"})
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertIsNotNone(body.get("staleness_warning"))
        self.assertEqual(body["staleness_warning"]["read_surface_state"], "degraded")

    # ---------------------------------------------------------------------- #
    # All eight command types submit successfully with appropriate roles
    # ---------------------------------------------------------------------- #
    def test_pause_runtime_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "PauseRuntime",
                "target": {"type": "RuntimeBinding", "id": "rb-happy"},
                "action": "pause",
                "params": {"runtime_binding_id": "rb-happy", "pause_action": "pause", "reason": "investigation"},
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(OPERATOR_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_execute_rollback_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ExecuteRollback",
                "target": {"type": "DeploymentPlan", "id": "dp-rollback"},
                "action": "rollback",
                "params": {
                    "rollback_target_type": "deployment",
                    "target_id": "dp-rollback",
                    "rollback_to_version": "v1.2.3",
                },
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(ADMIN_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_approve_rollback_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ApproveRollback",
                "target": {"type": "Rollback", "id": "rollback-rb-001"},
                "action": "approve",
                "params": {
                    "rollback_id": "rollback-rb-001",
                    "approval_notes": "Approved after manual review",
                },
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(APPROVER_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_reject_rollback_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "RejectRollback",
                "target": {"type": "Rollback", "id": "rollback-rb-001"},
                "action": "reject",
                "params": {
                    "rollback_id": "rollback-rb-001",
                    "rejection_reason": "Position evidence is incomplete",
                },
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(APPROVER_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_approve_evolution_decision_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ApproveEvolutionDecision",
                "target": {"type": "EvolutionDecision", "id": "evo-001"},
                "action": "approve",
                "params": {
                    "evolution_decision_id": "evo-001",
                    "approval_action": "approve",
                    "approval_rationale": "Looks good",
                    "approved_by_role": "reviewer",
                },
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(REVIEWER_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_execute_evolution_action_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ExecuteEvolutionAction",
                "target": {"type": "EvolutionDecision", "id": "evo-002"},
                "action": "execute",
                "params": {
                    "evolution_decision_id": "evo-002",
                    "action_type": "freeze",
                    "target_scope": {"type": "strategy", "id": "strat-1"},
                },
                "audit_context": {"reason": "test"},
            },
            headers=_command_headers(ADMIN_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)

    def test_execute_evolution_revalidate_action_submit(self):
        r = self.client.post(
            "/api/v1/operator/commands",
            json={
                "command": "ExecuteEvolutionAction",
                "target": {"type": "EvolutionDecision", "id": "evo-reval-002"},
                "action": "execute",
                "params": {
                    "evolution_decision_id": "evo-reval-002",
                    "action_type": "revalidate",
                    "target_scope": {"type": "strategy", "id": "strat-reval-1"},
                },
                "audit_context": {"reason": "test revalidate dispatch"},
            },
            headers=_command_headers(ADMIN_TOKEN),
        )
        self.assertEqual(r.status_code, 202, r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
