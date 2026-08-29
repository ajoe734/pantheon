"""SVC-RUNTIME-HARDENING targeted tests.

Acceptance coverage (in order):

* protected runtime and internal command routes validate JWT claims, RBAC, and
  MFA policy
* legacy internal-API kill-switch path uses the durable foundation idempotency
  record path (no divergent side-effect replay)
* ``ApproveDeployment`` no longer creates placeholder approval ids and instead
  routes through the authoritative deployment service plan-status API
* targeted tests cover auth denial, MFA denial, idempotent replay, and
  deployment plan-status integration (including a real-payload validation that
  the wire body matches the deployment service's ``UpdatePlanStatusRequest``
  / ``PlanStatusBody`` enum so a permissive mock cannot hide a contract drift)
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = Path(__file__).resolve().parent

for path in (REPO_ROOT, SERVICE_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402


def _valid_deploy_request(**overrides):
    request = {
        "plan_id": "plan-hardening-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-hardening",
        "artifact_version": "1.0.0",
        "strategy_id": "strategy-hardening",
        "approval_decision_id": "approval-hardening",
        "sponsor_persona_id": "persona-hardening",
        "capital_pool_id": "pool-hardening-001",
        "persona_capital_binding_id": "pcb-hardening-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-hardening-001",
    }
    request.update(overrides)
    return request


def _load_main_module(store_path: Path, command_state_path: Path):
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(store_path)
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"
    os.environ["PANTHEON_COMMAND_STATE_FILE"] = str(command_state_path)
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    module._svc = None
    return module


class HardeningEnvScope:
    """Snapshot/restore env vars touched by these tests."""

    KEYS = (
        "PANTHEON_RUNTIME_AUTH_MODE",
        "PANTHEON_RUNTIME_JWT_SECRET",
        "PANTHEON_RUNTIME_JWT_ISSUER",
        "PANTHEON_RUNTIME_JWT_AUDIENCE",
        "PANTHEON_RUNTIME_MFA_REQUIRED",
        "PANTHEON_RUNTIME_DEFAULT_ROLE",
        "PANTHEON_DEPLOYMENT_API_URL",
        "PANTHEON_DEPLOYMENT_SERVICE_URL",
    )

    def __enter__(self):
        self._saved = {key: os.environ.get(key) for key in self.KEYS}
        return self

    def __exit__(self, *_):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AuthAndRbacTests(unittest.TestCase):
    """Bearer presence, JWT verification, RBAC, and MFA gating."""

    def setUp(self):
        self.env = HardeningEnvScope().__enter__()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.command_state_path = Path(self.tempdir.name) / "commands.json"
        self.main = _load_main_module(self.store_path, self.command_state_path)
        self.authority_patcher = mock.patch.object(
            self.main,
            "verify_deploy_authorities",
            return_value={
                "status": "passed",
                "authority": "test-canonical",
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": "live",
            },
        )
        self.authority_patcher.start()
        self.client = self.main.app.test_client()

    def tearDown(self):
        self.authority_patcher.stop()
        self.tempdir.cleanup()
        self.env.__exit__(None, None, None)

    # --- Bearer / RBAC -------------------------------------------------- #

    def test_missing_bearer_token_returns_401(self):
        response = self.client.post(
            "/api/runtimes/deploy",
            json=_valid_deploy_request(),
        )
        self.assertEqual(response.status_code, 401)
        body = response.get_json()
        self.assertEqual(body["error"]["code"], "401")

    def test_structured_token_without_required_role_returns_403(self):
        response = self.client.post(
            "/api/kill-switch/dispatch",
            headers={
                "Authorization": "Bearer alice:reviewer",
                "X-MFA-Token": "123456",
            },
            json={
                "reason": "operator_emergency_stop",
                "capital_pool_id": "pool-hardening-002",
                "actor_id": "alice",
            },
        )
        self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["error"]["code"], "AUTH_FORBIDDEN")

    def test_strict_mode_rejects_unsigned_bearer(self):
        os.environ["PANTHEON_RUNTIME_AUTH_MODE"] = "strict"
        response = self.client.post(
            "/api/runtimes/deploy",
            headers={"Authorization": "Bearer plain-token"},
            json=_valid_deploy_request(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTH_TOKEN_FORMAT")

    def test_permissive_mode_rejects_unverified_jwt_shape_without_secret(self):
        os.environ["PANTHEON_RUNTIME_AUTH_MODE"] = "permissive"
        os.environ.pop("PANTHEON_RUNTIME_JWT_SECRET", None)
        response = self.client.post(
            "/api/runtimes/deploy",
            headers={"Authorization": "Bearer unsigned.jwt.token"},
            json=_valid_deploy_request(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTH_JWT_UNVERIFIED")

    def test_jwt_with_bad_signature_rejected(self):
        os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = "test-secret"
        token = encode_jwt_hs256(
            {"sub": "alice", "roles": ["operator"]},
            secret="other-secret",
        )
        response = self.client.post(
            "/api/runtimes/deploy",
            headers={"Authorization": f"Bearer {token}"},
            json=_valid_deploy_request(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTH_JWT_BAD_SIGNATURE")

    def test_expired_jwt_rejected(self):
        os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = "test-secret"
        token = encode_jwt_hs256(
            {"sub": "alice", "roles": ["operator"], "exp": int(time.time()) - 60},
            secret="test-secret",
        )
        response = self.client.post(
            "/api/runtimes/deploy",
            headers={"Authorization": f"Bearer {token}"},
            json=_valid_deploy_request(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "AUTH_JWT_EXPIRED")

    def test_jwt_with_required_claims_accepted(self):
        os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = "test-secret"
        os.environ["PANTHEON_RUNTIME_JWT_ISSUER"] = "pantheon-control-plane"
        os.environ["PANTHEON_RUNTIME_JWT_AUDIENCE"] = "runtime-manager"
        token = encode_jwt_hs256(
            {
                "sub": "alice",
                "roles": ["operator", "approver"],
                "iss": "pantheon-control-plane",
                "aud": "runtime-manager",
                "exp": int(time.time()) + 600,
            },
            secret="test-secret",
        )
        response = self.client.post(
            "/api/runtimes/deploy",
            headers={"Authorization": f"Bearer {token}"},
            json=_valid_deploy_request(),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))

    # --- MFA gating ------------------------------------------------------ #

    def test_mfa_required_route_rejects_missing_otp(self):
        os.environ["PANTHEON_RUNTIME_MFA_REQUIRED"] = "true"
        response = self.client.post(
            "/api/kill-switch/dispatch",
            headers={"Authorization": "Bearer alice:operator"},
            json={
                "reason": "operator_emergency_stop",
                "capital_pool_id": "pool-hardening-003",
                "actor_id": "alice",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "MFA_REQUIRED")

    def test_mfa_invalid_format_rejected(self):
        response = self.client.post(
            "/api/kill-switch/dispatch",
            headers={
                "Authorization": "Bearer alice:operator",
                "X-MFA-Token": "abcdef",
            },
            json={
                "reason": "operator_emergency_stop",
                "capital_pool_id": "pool-hardening-004",
                "actor_id": "alice",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "MFA_VALIDATION_FAILED")


class LegacyKillSwitchIdempotencyTests(unittest.TestCase):
    """The legacy /api/internal/v1/kill-switch path now flows through the
    runtime-manager service.execute_kill_switch foundation idempotency record
    path. Replays of the same logical request must not produce divergent
    side-effects.
    """

    def setUp(self):
        self.env = HardeningEnvScope().__enter__()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.command_state_path = Path(self.tempdir.name) / "commands.json"
        self.main = _load_main_module(self.store_path, self.command_state_path)
        self.client = self.main.app.test_client()
        self.headers = {
            "Authorization": "Bearer alice:operator",
            "X-MFA-Token": "654321",
        }

    def tearDown(self):
        self.tempdir.cleanup()
        self.env.__exit__(None, None, None)

    def _activate(self, **overrides):
        body = {
            "action": "activate",
            "scope": "pool",
            "scope_id": "pool-legacy-shared",
            "reason": "drift_above_warning_threshold",
        }
        body.update(overrides)
        return self.client.post(
            "/api/internal/v1/kill-switch", headers=self.headers, json=body
        )

    def test_replayed_legacy_kill_switch_uses_foundation_idempotency_record(self):
        first = self._activate()
        first_payload = first.get_json()
        self.assertEqual(first.status_code, 202, first_payload)
        first_audit_id = first_payload["audit_id"]

        # Replay the exact same logical request: the foundation idempotency
        # path must collapse this onto the existing record without spawning a
        # new audit entry.
        second = self._activate()
        second_payload = second.get_json()
        self.assertEqual(second.status_code, 202, second_payload)
        self.assertEqual(second_payload["audit_id"], first_audit_id)

        svc = self.main._get_service()
        self.assertEqual(len(svc.get_kill_switch_audit_log()), 1)

    def test_legacy_kill_switch_persists_via_durable_foundation_record(self):
        response = self._activate(reason="severity_1_incident", scope_id="pool-legacy-002")
        payload = response.get_json()
        self.assertEqual(response.status_code, 202, payload)

        # Inspect the durable kill-switch snapshot — the foundation idempotency
        # block must contain the legacy operator command's idempotency record.
        snapshot_path = self.store_path.with_name("kill_switch.json")
        # The runtime-manager writes its kill-switch snapshot beside the binding
        # store; locate by globbing whichever is present.
        candidates = list(self.store_path.parent.glob("**/*.json"))
        snapshot_data: dict = {}
        for candidate in candidates:
            try:
                with candidate.open() as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if "foundation_idempotency" in data:
                snapshot_data = data
                snapshot_path = candidate
                break
        self.assertTrue(
            snapshot_data.get("foundation_idempotency"),
            f"foundation_idempotency missing from durable snapshot {snapshot_path}",
        )
        # With no RuntimeBinding to contain, the admitted command must remain
        # EXECUTING.  Recording SUCCEEDED here would turn a fail-closed
        # telemetry result into a misleading terminal acknowledgement.
        statuses = [
            (record.get("idempotency_record") or {}).get("status")
            for record in snapshot_data["foundation_idempotency"].values()
        ]
        self.assertIn("executing", statuses)
        self.assertNotIn("succeeded", statuses)


class ApproveDeploymentDeploymentAuthorityTests(unittest.TestCase):
    """ApproveDeployment must call the authoritative deployment service
    plan-status API. No placeholder approval ids may be minted, and the wire
    payload must satisfy the deployment service's UpdatePlanStatusRequest
    schema (so a permissive mock cannot hide a contract drift).
    """

    def setUp(self):
        self.env = HardeningEnvScope().__enter__()
        self.tempdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tempdir.name) / "bindings.json"
        self.command_state_path = Path(self.tempdir.name) / "commands.json"
        self.main = _load_main_module(self.store_path, self.command_state_path)
        self.client = self.main.app.test_client()
        self.legacy = importlib.import_module("services.control_plane.internal.internal_api")
        self.headers = {
            "Authorization": "Bearer alice:approver",
            "X-MFA-Token": "234567",
        }

    def tearDown(self):
        self.tempdir.cleanup()
        self.env.__exit__(None, None, None)

    def test_unconfigured_deployment_api_returns_503(self):
        response = self.client.post(
            "/api/internal/v1/deployments/plan-001/approve",
            headers=self.headers,
            json={"approval_decision": "approve"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"], "DEPLOYMENT_API_UNCONFIGURED"
        )

    def test_invalid_decision_value_returns_400(self):
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://deployment:8095"
        response = self.client.post(
            "/api/internal/v1/deployments/plan-001/approve",
            headers=self.headers,
            json={"approval_decision": "abstain"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"], "INVALID_APPROVAL_DECISION"
        )

    def test_approve_deployment_calls_deployment_status_with_real_payload_shape(self):
        """Validates the real deployment service contract — not just a mock.

        We assert the call URL and payload shape the route emits is exactly
        what the deployment service's ``UpdatePlanStatusRequest`` /
        ``PlanStatusBody`` enum accepts. This catches regressions where the
        wire body would fail Pydantic validation against the real service.
        """
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://deployment:8095"

        # Import the real deployment models so the test asserts against the
        # canonical schema rather than a hand-rolled expected shape.
        deployment_dir = REPO_ROOT / "services" / "deployment"
        if str(deployment_dir) not in sys.path:
            sys.path.insert(0, str(deployment_dir))
        from services.deployment.models import (  # noqa: E402
            PlanStatusBody,
            UpdatePlanStatusRequest,
        )

        calls = []

        def fake_request(method, url, payload=None):
            calls.append((method, url, payload))
            return {
                "plan_id": "plan-real-001",
                "approval_decision_id": "apv-real-001",
                "artifact_id": "art-001",
                "artifact_version": "1.0.0",
                "artifact_type": "strategy_spec",
                "strategy_id": "strat-001",
                "capital_pool_id": "pool-001",
                "current_stage": "paper",
                "target_stage": "paper",
                "transition_type": "promotion",
                "runtime_action": "deploy",
                "status": "approved",
                "created_at": "2026-04-28T17:00:00Z",
            }

        with mock.patch.object(
            self.legacy, "_deployment_request", side_effect=fake_request
        ):
            response = self.client.post(
                "/api/internal/v1/deployments/plan-real-001/approve",
                headers=self.headers,
                json={
                    "approval_decision": "approve",
                    "rationale": "Operator confirmed verification",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 202, payload)
        # No placeholder ad-* prefix; we use the upstream governance approval id
        # carried on the deployment plan record.
        self.assertEqual(payload["approval_decision_id"], "apv-real-001")
        self.assertFalse(payload["approval_decision_id"].startswith("ad-"))
        self.assertEqual(payload["state_after"], "approved")
        self.assertEqual(payload["target_plan_id"], "plan-real-001")

        # The route must hit the deployment service's plan-status endpoint with
        # a body the real service can parse.
        self.assertEqual(len(calls), 1)
        method, url, body = calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            url,
            "http://deployment:8095/api/deployment/plans/plan-real-001/status",
        )
        # The body must satisfy the real Pydantic request model — this is the
        # contract test the previous mock-based suite was missing.
        validated = UpdatePlanStatusRequest(**body)
        self.assertEqual(validated.status, PlanStatusBody.APPROVED)

    def test_approve_with_conditions_collapses_to_approved_status(self):
        """``approved_with_conditions`` must collapse to plan-status ``approved``.

        The plan-level state machine has no conditional state; conditions are
        recorded on the upstream governance ApprovalDecision (already created
        before the plan exists). Sending ``approved_with_conditions`` to the
        deployment service would 422.
        """
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://deployment:8095"

        deployment_dir = REPO_ROOT / "services" / "deployment"
        if str(deployment_dir) not in sys.path:
            sys.path.insert(0, str(deployment_dir))
        from services.deployment.models import (  # noqa: E402
            PlanStatusBody,
            UpdatePlanStatusRequest,
        )

        recorded = {}

        def fake_request(method, url, payload=None):
            recorded["payload"] = payload
            return {
                "plan_id": "plan-cond-001",
                "approval_decision_id": "apv-cond-001",
                "artifact_id": "art-cond",
                "artifact_version": "1.0.0",
                "artifact_type": "strategy_spec",
                "strategy_id": "strat-cond",
                "capital_pool_id": "pool-cond",
                "current_stage": "draft",
                "target_stage": "paper",
                "transition_type": "promotion",
                "runtime_action": "deploy",
                "status": "approved",
                "created_at": "2026-04-28T17:00:00Z",
            }

        with mock.patch.object(
            self.legacy, "_deployment_request", side_effect=fake_request
        ):
            response = self.client.post(
                "/api/internal/v1/deployments/plan-cond-001/approve",
                headers=self.headers,
                json={"approval_decision": "approved_with_conditions"},
            )
        self.assertEqual(response.status_code, 202, response.get_json())
        validated = UpdatePlanStatusRequest(**recorded["payload"])
        self.assertEqual(validated.status, PlanStatusBody.APPROVED)

    def test_approve_deployment_returns_404_when_plan_unknown(self):
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://deployment:8095"

        def fake_request(*_args, **_kwargs):
            raise self.legacy._DeploymentApiError(
                "DeploymentPlan 'plan-missing-001' not found",
                status_code=404,
                error_code="DEPLOYMENT_PLAN_NOT_FOUND",
            )

        with mock.patch.object(
            self.legacy, "_deployment_request", side_effect=fake_request
        ):
            response = self.client.post(
                "/api/internal/v1/deployments/plan-missing-001/approve",
                headers=self.headers,
                json={"approval_decision": "approve"},
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.get_json()["error"]["code"], "DEPLOYMENT_PLAN_NOT_FOUND"
        )

    def test_approve_deployment_propagates_deployment_unavailable(self):
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://deployment:8095"

        def fake_request(*_args, **_kwargs):
            raise self.legacy._DeploymentApiError(
                "deployment API unavailable: connection refused",
                status_code=503,
                error_code="DEPLOYMENT_API_UNAVAILABLE",
            )

        with mock.patch.object(
            self.legacy, "_deployment_request", side_effect=fake_request
        ):
            response = self.client.post(
                "/api/internal/v1/deployments/plan-down-001/approve",
                headers=self.headers,
                json={"approval_decision": "approve"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"], "DEPLOYMENT_API_UNAVAILABLE"
        )

    def test_invalid_status_transition_returns_409(self):
        """Re-approving a plan that is already executing must surface 409."""
        os.environ["PANTHEON_DEPLOYMENT_API_URL"] = "http://deployment:8095"

        def fake_request(*_args, **_kwargs):
            raise self.legacy._DeploymentApiError(
                "Invalid plan status transition: executing -> approved",
                status_code=409,
                error_code="DEPLOYMENT_PLAN_INVALID_TRANSITION",
            )

        with mock.patch.object(
            self.legacy, "_deployment_request", side_effect=fake_request
        ):
            response = self.client.post(
                "/api/internal/v1/deployments/plan-exec-001/approve",
                headers=self.headers,
                json={"approval_decision": "approve"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "DEPLOYMENT_PLAN_INVALID_TRANSITION",
        )


if __name__ == "__main__":
    unittest.main()
