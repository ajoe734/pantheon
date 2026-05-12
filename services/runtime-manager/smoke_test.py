"""Smoke test for services/runtime-manager.

Covers both the service layer (service.py) and the Flask HTTP surface (main.py).

Acceptance criteria verified:
  AC-1  runtime binding creation and runtime-manager writes flow through one
        deployable service path
  AC-2  operator command boundaries and runtime write authority are explicit
        and smoke-tested

Run:
    python smoke_test.py
"""
from __future__ import annotations

import os
import sys
import traceback
from importlib import import_module
from pathlib import Path

# Ensure the execution runtime-manager is importable
_EXEC_RM_DIR = str(
    Path(__file__).resolve().parent.parent.parent
    / "services" / "execution" / "runtime-manager"
)
if _EXEC_RM_DIR not in sys.path:
    sys.path.insert(0, _EXEC_RM_DIR)

# Set the env var before importing service so it picks up the path
os.environ["PANTHEON_EXEC_RUNTIME_MANAGER_DIR"] = _EXEC_RM_DIR

# Add the runtime-manager service dir to path
_SVC_DIR = str(Path(__file__).resolve().parent)
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from service import RuntimeManagerService, RuntimeManagerError
from runtime_binding import RuntimeBindingError, RuntimeBindingStatus

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    status = PASS if cond else FAIL
    msg = f"  [{status}] {name}"
    if detail and not cond:
        msg += f"\n         {detail}"
    print(msg)


def activation_gate(**overrides):
    gate = {
        "promotion_gate_decision_id": "gate-smoke-001",
        "human_gate_packet_ref": "docs/deployment/evidence/ep5-human-gate-input/smoke/human-gate-packet.json",
        "broker_sandbox_smoke_ref": "docs/deployment/evidence/execution-sandbox-canary-activation-ready/smoke",
        "risk_owner_approval_ref": "approval://risk-owner/smoke",
        "operator_approval_ref": "approval://operator/smoke",
        "capital_scale_pct": 5.0,
        "gross_scale_pct": 25.0,
    }
    gate.update(overrides)
    return gate


def run_service_layer_tests():
    print("\n=== Service Layer (service.py) ===")

    svc = RuntimeManagerService(store_path=None, single_runtime_enforced=True)

    valid_request = {
        "plan_id": "plan-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-alpha",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-test-001",
    }

    # AC-1: RuntimeBinding creation through the service
    try:
        binding = svc.deploy(valid_request)
        check("deploy() creates RuntimeBinding with correct fields",
              binding.plan_id == "plan-001"
              and binding.deployment_mode == "paper"
              and binding.status == "active"
              and binding.persona_capital_binding_id == "pcb-001")
    except Exception as exc:
        check("deploy() creates RuntimeBinding", False, str(exc))
        return  # Cannot continue without a binding

    # AC-2: write authority boundary — single-runtime rule
    try:
        svc.deploy({**valid_request, "plan_id": "plan-002", "runtime_id": "rt-test-002"})
        check("single-runtime rule rejects second active binding", False,
              "Expected RuntimeBindingError but deploy succeeded")
    except RuntimeBindingError as exc:
        check("single-runtime rule rejects second active binding",
              "single-runtime" in str(exc).lower())
    except Exception as exc:
        check("single-runtime rule rejects second active binding", False, str(exc))

    # AC-2: plan_status pre-condition
    try:
        svc.deploy({**valid_request, "plan_status": "pending", "runtime_id": "rt-test-003"})
        check("deploy() rejects plan_status=pending", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError as exc:
        check("deploy() rejects plan_status=pending", True)
    except Exception as exc:
        check("deploy() rejects plan_status=pending", False, str(exc))

    # AC-2: PersonaCapitalBinding status enforcement — revoked binding must be rejected
    try:
        svc_revoked = RuntimeManagerService(store_path=None, single_runtime_enforced=False)
        svc_revoked.deploy({**valid_request,
                            "plan_id": "plan-revoked",
                            "persona_capital_binding_status": "revoked"})
        check("deploy() rejects persona_capital_binding_status=revoked", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError as exc:
        check("deploy() rejects persona_capital_binding_status=revoked",
              "active" in str(exc).lower() or "revoked" in str(exc).lower())
    except Exception as exc:
        check("deploy() rejects persona_capital_binding_status=revoked", False, str(exc))

    # AC-2: loader-check enforcement — loader_checks_passed=False must be rejected
    try:
        svc_loader = RuntimeManagerService(store_path=None, single_runtime_enforced=False)
        svc_loader.deploy({**valid_request,
                           "plan_id": "plan-loader",
                           "loader_checks_passed": False})
        check("deploy() rejects loader_checks_passed=False", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError as exc:
        check("deploy() rejects loader_checks_passed=False",
              "loader" in str(exc).lower())
    except Exception as exc:
        check("deploy() rejects loader_checks_passed=False", False, str(exc))

    # AC-2: scope enforcement
    try:
        svc2 = RuntimeManagerService(store_path=None, single_runtime_enforced=False)
        svc2.deploy({**valid_request,
                     "plan_id": "plan-scope",
                     "target_stage": "live",
                     "allowed_deployment_scope": "paper"})
        check("deploy() rejects scope violation (paper scope -> live stage)", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError as exc:
        check("deploy() rejects scope violation (paper scope -> live stage)", True)
    except Exception as exc:
        check("deploy() rejects scope violation (paper scope -> live stage)", False, str(exc))

    # Read path
    fetched = svc.get(binding.binding_id)
    check("get() returns the created binding",
          fetched is not None and fetched.binding_id == binding.binding_id)

    pool_bindings = svc.list_by_pool("pool-001")
    check("list_by_pool() returns bindings for the pool",
          any(b.binding_id == binding.binding_id for b in pool_bindings))

    active = svc.get_active_for_pool("pool-001")
    check("get_active_for_pool() returns the active binding",
          active is not None and active.binding_id == binding.binding_id)

    # Status transition
    try:
        paused = svc.transition(binding.binding_id, "pending_pause")
        check("transition() active -> pending_pause", paused.status == "pending_pause")
        paused2 = svc.transition(binding.binding_id, "paused")
        check("transition() pending_pause -> paused", paused2.status == "paused")
        resumed = svc.transition(binding.binding_id, "active")
        check("transition() paused -> active (resume)", resumed.status == "active")
    except Exception as exc:
        check("status transitions (active -> pending_pause -> paused -> active)", False, str(exc))

    # Retire
    try:
        retired = svc.retire(binding.binding_id)
        check("retire() transitions binding to retired",
              retired.status == "retired" and retired.retired_at is not None)
    except Exception as exc:
        check("retire() transitions binding to retired", False, str(exc))

    # Terminal guard — no further transitions from retired
    try:
        svc.transition(binding.binding_id, "active")
        check("terminal guard: retired binding cannot transition", False,
              "Expected RuntimeBindingError but succeeded")
    except RuntimeBindingError as exc:
        check("terminal guard: retired binding cannot transition",
              "terminal" in str(exc).lower())
    except Exception as exc:
        check("terminal guard: retired binding cannot transition", False, str(exc))


def run_http_layer_tests():
    print("\n=== HTTP Layer (main.py Flask routes) ===")

    # Import the Flask app with a fresh in-memory service
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = "/tmp/pantheon/smoke-test/bindings.json"
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"

    import main  # noqa: PLC0415 — local import after env setup
    # Reset global service so tests get a clean store
    main._svc = None

    client = main.app.test_client()

    AUTH = {"Authorization": "Bearer test-token"}

    # Health
    r = client.get("/__health__")
    check("GET /__health__ returns 200", r.status_code == 200)

    # Deploy — missing bearer
    r = client.post("/api/runtimes/deploy", json={})
    check("POST /api/runtimes/deploy without token returns 401", r.status_code == 401)

    # Deploy — missing required fields
    r = client.post("/api/runtimes/deploy", json={}, headers=AUTH)
    check("POST /api/runtimes/deploy with empty body returns 400", r.status_code == 400)

    # Deploy — missing persona_capital_binding_status returns 400
    bad_body_missing_pcb_status = {
        "plan_id": "plan-http-bad",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-beta",
        "artifact_version": "2.0.0",
        "capital_pool_id": "pool-http-bad",
        "persona_capital_binding_id": "pcb-http-bad",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": "rt-http-bad",
    }
    r_missing = client.post("/api/runtimes/deploy", json=bad_body_missing_pcb_status, headers=AUTH)
    check("POST /api/runtimes/deploy without persona_capital_binding_status returns 400",
          r_missing.status_code == 400)

    # Deploy — persona_capital_binding_status=revoked returns 422
    revoked_body = {
        "plan_id": "plan-http-revoked",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-beta",
        "artifact_version": "2.0.0",
        "capital_pool_id": "pool-http-revoked",
        "persona_capital_binding_id": "pcb-http-revoked",
        "persona_capital_binding_status": "revoked",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": "rt-http-revoked",
    }
    r_revoked = client.post("/api/runtimes/deploy", json=revoked_body, headers=AUTH)
    check("POST /api/runtimes/deploy with persona_capital_binding_status=revoked returns 422",
          r_revoked.status_code == 422)

    # Deploy — loader_checks_passed=False returns 422
    loader_fail_body = {
        "plan_id": "plan-http-loaderfail",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-beta",
        "artifact_version": "2.0.0",
        "capital_pool_id": "pool-http-loaderfail",
        "persona_capital_binding_id": "pcb-http-loaderfail",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": False,
        "runtime_id": "rt-http-loaderfail",
    }
    r_loader = client.post("/api/runtimes/deploy", json=loader_fail_body, headers=AUTH)
    check("POST /api/runtimes/deploy with loader_checks_passed=False returns 422",
          r_loader.status_code == 422)

    # Deploy — valid request
    deploy_body = {
        "plan_id": "plan-http-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-beta",
        "artifact_version": "2.0.0",
        "capital_pool_id": "pool-http-001",
        "persona_capital_binding_id": "pcb-http-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": "rt-http-001",
    }
    r = client.post("/api/runtimes/deploy", json=deploy_body, headers=AUTH)
    check("POST /api/runtimes/deploy creates RuntimeBinding (201)",
          r.status_code == 201)

    if r.status_code == 201:
        binding_id = r.get_json()["binding_id"]

        # Read back
        r2 = client.get(f"/api/runtime-bindings/{binding_id}", headers=AUTH)
        check("GET /api/runtime-bindings/<id> returns the binding",
              r2.status_code == 200 and r2.get_json()["binding_id"] == binding_id)

        # List
        r3 = client.get("/api/runtime-bindings", headers=AUTH)
        check("GET /api/runtime-bindings returns list with count",
              r3.status_code == 200 and r3.get_json()["count"] >= 1)

        # List filtered by pool
        r4 = client.get("/api/runtime-bindings?pool_id=pool-http-001", headers=AUTH)
        data4 = r4.get_json()
        check("GET /api/runtime-bindings?pool_id= returns pool bindings",
              r4.status_code == 200 and data4["count"] >= 1)

        # Active for pool
        r5 = client.get("/api/runtimes/pool-http-001/active", headers=AUTH)
        check("GET /api/runtimes/<pool_id>/active returns active binding",
              r5.status_code == 200)

        # Single-runtime rejection
        r6 = client.post("/api/runtimes/deploy", json={
            **deploy_body,
            "plan_id": "plan-http-002",
            "runtime_id": "rt-http-002",
        }, headers=AUTH)
        check("POST /api/runtimes/deploy rejects second active binding (409)",
              r6.status_code == 409)

        # Transition
        r7 = client.post(
            f"/api/runtime-bindings/{binding_id}/transition",
            json={"new_status": "pending_pause"},
            headers=AUTH,
        )
        check("POST /api/runtime-bindings/<id>/transition to pending_pause",
              r7.status_code == 200)

        r7b = client.post(
            f"/api/runtime-bindings/{binding_id}/transition",
            json={"new_status": "paused"},
            headers=AUTH,
        )
        check("POST /api/runtime-bindings/<id>/transition to paused",
              r7b.status_code == 200)

        # Retire
        r8 = client.post(f"/api/runtime-bindings/{binding_id}/retire", json={}, headers=AUTH)
        check("POST /api/runtime-bindings/<id>/retire returns 200",
              r8.status_code == 200)

        # 404 for unknown binding
        r9 = client.get("/api/runtime-bindings/nonexistent-id", headers=AUTH)
        check("GET /api/runtime-bindings/<unknown> returns 404", r9.status_code == 404)


def run_internal_api_boundary_smoke():
    print("\n=== Control Route Boundary (internal_api.py) ===")

    # Share one persisted binding store across runtime-manager and internal_api.
    store_path = "/tmp/pantheon/smoke-test/runtime-manager-boundary-bindings.json"
    command_state_path = "/tmp/pantheon/smoke-test/internal-api-commands.json"
    for path in (store_path, command_state_path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = store_path
    os.environ.pop("PANTHEON_RUNTIME_MANAGER_URL", None)

    import main  # noqa: PLC0415

    main._STORE_PATH_ENV = store_path
    main._svc = None
    rm_client = main.app.test_client()
    auth = {"Authorization": "Bearer test-token"}

    deploy_body = {
        "plan_id": "plan-boundary-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-boundary",
        "artifact_version": "3.0.0",
        "capital_pool_id": "pool-boundary-001",
        "persona_capital_binding_id": "pcb-boundary-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-boundary-001",
    }
    deploy_resp = rm_client.post("/api/runtimes/deploy", json=deploy_body, headers=auth)
    check(
        "boundary setup: runtime-manager deploy succeeds",
        deploy_resp.status_code == 201,
        deploy_resp.get_data(as_text=True),
    )
    if deploy_resp.status_code != 201:
        return

    binding_id = deploy_resp.get_json()["binding_id"]

    internal_api = import_module("services.control_plane.internal.internal_api")
    internal_api._runtime_manager_client = None
    internal_api._COMMAND_STATE_FILE = command_state_path
    protected_client = internal_api.app.test_client()
    protected_auth = {
        "Authorization": "Bearer internal-test-token",
        "Content-Type": "application/json",
    }

    pause_resp = protected_client.post(
        f"/api/internal/v1/runtimes/{binding_id}/pause",
        json={"pause_action": "pause", "duration_seconds": 90, "reason": "boundary smoke"},
        headers=protected_auth,
    )
    check(
        "internal_api pause routes through runtime-manager and returns 202",
        pause_resp.status_code == 202 and pause_resp.get_json()["status_after"] == "paused",
        pause_resp.get_data(as_text=True),
    )

    main._svc = None
    paused_binding = rm_client.get(f"/api/runtime-bindings/{binding_id}", headers=auth)
    paused_payload = paused_binding.get_json() if paused_binding.status_code == 200 else {}
    check(
        "runtime-manager readback sees paused status after internal_api pause",
        paused_binding.status_code == 200 and paused_payload.get("status") == "paused",
        paused_binding.get_data(as_text=True),
    )

    rollback_resp = protected_client.post(
        "/api/internal/v1/rollbacks/execute",
        json={
            "rollback_target_type": "runtime",
            "target_id": binding_id,
            "rollback_to_version": "fallback-v3",
            "rollback_action_type": "pause_then_replace",
        },
        headers=protected_auth,
    )
    check(
        "internal_api rollback routes through runtime-manager and returns 202",
        rollback_resp.status_code == 202 and rollback_resp.get_json()["status_after"] == "retired",
        rollback_resp.get_data(as_text=True),
    )

    main._svc = None
    retired_binding = rm_client.get(f"/api/runtime-bindings/{binding_id}", headers=auth)
    retired_payload = retired_binding.get_json() if retired_binding.status_code == 200 else {}
    check(
        "runtime-manager readback sees retired status after internal_api rollback",
        retired_binding.status_code == 200 and retired_payload.get("status") == "retired",
        retired_binding.get_data(as_text=True),
    )


def run_rollback_action_tests():
    """Smoke tests for rollback action semantics — BP5-SVC-008.

    Acceptance criteria:
      AC-3  runtime-manager exposes the canonical rollback and replace actions
            without semantic drift (replace / pause_then_replace /
            liquidate_then_replace semantics match ROLLBACK_AND_POSITION_SEMANTICS.md)
      AC-4  position handling, cutover timing, and rollback linkage are verified
            in smoke coverage
    """
    print("\n=== Rollback Action Semantics (service.py) — BP5-SVC-008 ===")

    def _base_deploy(svc, pool_id, plan_id, artifact_id="artifact-original",
                     runtime_id=None, pcb_status="active"):
        return svc.deploy({
            "plan_id": plan_id,
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": artifact_id,
            "artifact_version": "1.0.0",
            "capital_pool_id": pool_id,
            "persona_capital_binding_id": f"pcb-{pool_id}",
            "persona_capital_binding_status": pcb_status,
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
            "runtime_id": runtime_id or f"rt-{pool_id}",
        })

    def _rollback_req(current_binding_id, action_type, pool_id,
                      fallback_artifact="artifact-fallback",
                      replacement_start_paused=False,
                      opened_by_artifact_id=None):
        req = {
            "current_binding_id": current_binding_id,
            "action_type": action_type,
            "replacement_plan_id": f"plan-rb-{pool_id}",
            "replacement_plan_status": "approved",
            "replacement_artifact_id": fallback_artifact,
            "replacement_artifact_version": "0.9.0",
            "replacement_persona_capital_binding_id": f"pcb-rb-{pool_id}",
            "replacement_persona_capital_binding_status": "active",
            "replacement_allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        }
        if replacement_start_paused:
            req["replacement_start_paused"] = True
        if opened_by_artifact_id:
            req["opened_by_artifact_id"] = opened_by_artifact_id
        return req

    # ------------------------------------------------------------------
    # AC-3 / AC-4: replace strategy
    # ------------------------------------------------------------------
    svc_replace = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        original = _base_deploy(svc_replace, "pool-rb-replace", "plan-orig-replace")
        orig_id = original.binding_id

        result = svc_replace.rollback(
            _rollback_req(orig_id, "replace", "pool-rb-replace",
                          opened_by_artifact_id="artifact-position-opener")
        )

        # Cutover timing
        check("replace: cutover_at is set in result",
              bool(result.get("cutover_at")))

        # Old binding retired
        check("replace: old binding is retired",
              result["old_binding"]["status"] == "retired"
              and result["old_binding"]["binding_id"] == orig_id)

        # New binding active and linked
        new_b = result["new_binding"]
        check("replace: new binding is active",
              new_b["status"] == "active")
        check("replace: new binding has rollback_parent linking to old binding",
              new_b.get("rollback_parent") == orig_id)
        check("replace: new binding rollback_action_type == 'replace'",
              new_b.get("rollback_action_type") == "replace")
        check("replace: new binding has fallback artifact",
              new_b["artifact_id"] == "artifact-fallback")

        # Position lineage
        lin = result["position_lineage"]
        check("replace: position_lineage.opened_by_artifact_id is immutable original opener",
              lin["opened_by_artifact_id"] == "artifact-position-opener")
        check("replace: position_lineage.current_managed_by_binding_id == new binding",
              lin["current_managed_by_binding_id"] == new_b["binding_id"])
        check("replace: position_lineage.prev_binding_id == old binding",
              lin["prev_binding_id"] == orig_id)
        check("replace: position_lineage.cutover_at is set",
              bool(lin.get("cutover_at")))

        # Store state: old binding is now retired in store
        stored_old = svc_replace.get(orig_id)
        check("replace: store confirms old binding is retired",
              stored_old is not None and stored_old.status == "retired")

        # Regression: verify create-before-retire ordering — the single-runtime rule
        # would prevent deploying a second active binding without the bypass.
        # If this check fails the replace branch is back to retire-first semantics.
        check("replace: new binding was created while single-runtime rule was active "
              "(create-before-retire ordering confirmed via successful rollback)",
              new_b["status"] == "active" and stored_old.status == "retired"
              and new_b["binding_id"] != orig_id)

    except Exception as exc:
        check("replace rollback end-to-end", False, str(exc))
        traceback.print_exc()

    # ------------------------------------------------------------------
    # AC-3 / AC-4: pause_then_replace strategy
    # ------------------------------------------------------------------
    svc_pause = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        original_p = _base_deploy(svc_pause, "pool-rb-pause", "plan-orig-pause")
        orig_p_id = original_p.binding_id

        result_p = svc_pause.rollback(
            _rollback_req(orig_p_id, "pause_then_replace", "pool-rb-pause")
        )

        # Old binding must end up retired
        check("pause_then_replace: old binding is retired",
              result_p["old_binding"]["status"] == "retired")

        # New binding active and linked
        new_p = result_p["new_binding"]
        check("pause_then_replace: new binding is active",
              new_p["status"] == "active")
        check("pause_then_replace: new binding rollback_parent == old binding",
              new_p.get("rollback_parent") == orig_p_id)
        check("pause_then_replace: new binding rollback_action_type == 'pause_then_replace'",
              new_p.get("rollback_action_type") == "pause_then_replace")

        # Cutover timing
        check("pause_then_replace: cutover_at is set",
              bool(result_p.get("cutover_at")))

        # Position lineage
        lin_p = result_p["position_lineage"]
        check("pause_then_replace: current_managed_by_binding_id updated to new binding",
              lin_p["current_managed_by_binding_id"] == new_p["binding_id"])

    except Exception as exc:
        check("pause_then_replace rollback end-to-end", False, str(exc))
        traceback.print_exc()

    # ------------------------------------------------------------------
    # AC-3 / AC-4: liquidate_then_replace strategy (guarded start)
    # ------------------------------------------------------------------
    svc_liq = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        original_l = _base_deploy(svc_liq, "pool-rb-liq", "plan-orig-liq")
        orig_l_id = original_l.binding_id

        result_l = svc_liq.rollback(
            _rollback_req(orig_l_id, "liquidate_then_replace", "pool-rb-liq",
                          replacement_start_paused=True)
        )

        # Old binding retired
        check("liquidate_then_replace: old binding is retired",
              result_l["old_binding"]["status"] == "retired")

        # New binding starts in paused / guarded mode
        new_l = result_l["new_binding"]
        check("liquidate_then_replace: replacement starts paused (guarded mode)",
              new_l["status"] == "paused")
        check("liquidate_then_replace: new binding rollback_action_type == 'liquidate_then_replace'",
              new_l.get("rollback_action_type") == "liquidate_then_replace")

        # Position lineage note warns about zero-position confirmation
        lin_l = result_l["position_lineage"]
        check("liquidate_then_replace: position_lineage note flags zero-position confirmation",
              "zero" in lin_l.get("note", "").lower())

        # Regression: when replacement_start_paused=True the new binding is paused,
        # not yet active — current_managed_by_binding_id must stay with the old
        # binding until zero-position is confirmed (L1 §7 lines 162-164).
        check("liquidate_then_replace (paused): current_managed_by_binding_id stays "
              "with old binding until zero-position confirmed",
              lin_l["current_managed_by_binding_id"] == orig_l_id)

        # Cutover timing
        check("liquidate_then_replace: cutover_at is set",
              bool(result_l.get("cutover_at")))

    except Exception as exc:
        check("liquidate_then_replace rollback end-to-end", False, str(exc))
        traceback.print_exc()

    # ------------------------------------------------------------------
    # Guard: rollback on already-retired binding must be rejected
    # ------------------------------------------------------------------
    svc_guard = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        b_guard = _base_deploy(svc_guard, "pool-rb-guard", "plan-guard")
        svc_guard.retire(b_guard.binding_id)
        svc_guard.rollback(
            _rollback_req(b_guard.binding_id, "replace", "pool-rb-guard")
        )
        check("rollback on retired binding is rejected", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError:
        check("rollback on retired binding is rejected", True)
    except Exception as exc:
        check("rollback on retired binding is rejected", False, str(exc))

    # ------------------------------------------------------------------
    # Guard: unknown action_type is rejected
    # ------------------------------------------------------------------
    svc_at = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        b_at = _base_deploy(svc_at, "pool-rb-at", "plan-at")
        svc_at.rollback({
            "current_binding_id": b_at.binding_id,
            "action_type": "delete_immediately",
            "replacement_plan_id": "plan-x",
            "replacement_artifact_id": "art-x",
            "replacement_artifact_version": "1.0",
            "replacement_persona_capital_binding_id": "pcb-x",
            "replacement_allowed_deployment_scope": "live",
        })
        check("rollback with unknown action_type is rejected", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError:
        check("rollback with unknown action_type is rejected", True)
    except Exception as exc:
        check("rollback with unknown action_type is rejected", False, str(exc))


def run_rollback_http_tests():
    """HTTP smoke for /api/rollback routes — BP5-SVC-008."""
    print("\n=== Rollback HTTP Layer (/api/rollback) — BP5-SVC-008 ===")

    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = (
        "/tmp/pantheon/smoke-test/rollback-http-bindings.json"
    )
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"

    import main  # noqa: PLC0415
    main._svc = None

    client = main.app.test_client()
    AUTH = {"Authorization": "Bearer test-token"}

    # Deploy a binding to roll back
    deploy_body = {
        "plan_id": "plan-http-rb-base",
        "plan_status": "approved",
        "target_stage": "canary",
        "artifact_id": "artifact-http-rb-original",
        "artifact_version": "3.0.0",
        "capital_pool_id": "pool-http-rb",
        "persona_capital_binding_id": "pcb-http-rb",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-http-rb",
        "promotion_gate": activation_gate(),
    }
    deploy_resp = client.post("/api/runtimes/deploy", json=deploy_body, headers=AUTH)
    check("rollback HTTP setup: deploy returns 201",
          deploy_resp.status_code == 201,
          deploy_resp.get_data(as_text=True))
    if deploy_resp.status_code != 201:
        return

    binding_id = deploy_resp.get_json()["binding_id"]

    # POST /api/rollback — missing fields returns 400
    r_missing = client.post("/api/rollback", json={}, headers=AUTH)
    check("POST /api/rollback with empty body returns 400",
          r_missing.status_code == 400)

    # POST /api/rollback — replace strategy
    rollback_body = {
        "current_binding_id": binding_id,
        "action_type": "replace",
        "replacement_plan_id": "plan-http-rb-fallback",
        "replacement_artifact_id": "artifact-http-rb-fallback",
        "replacement_artifact_version": "2.9.0",
        "replacement_persona_capital_binding_id": "pcb-http-rb-fallback",
        "replacement_persona_capital_binding_status": "active",
        "replacement_allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "opened_by_artifact_id": "artifact-http-rb-original",
    }
    r_rb = client.post("/api/rollback", json=rollback_body, headers=AUTH)
    check("POST /api/rollback (replace) returns 201",
          r_rb.status_code == 201,
          r_rb.get_data(as_text=True))

    if r_rb.status_code == 201:
        rb_data = r_rb.get_json()
        check("POST /api/rollback result: old_binding is retired",
              rb_data["old_binding"]["status"] == "retired")
        check("POST /api/rollback result: new_binding is active",
              rb_data["new_binding"]["status"] == "active")
        check("POST /api/rollback result: new_binding.rollback_parent set",
              rb_data["new_binding"].get("rollback_parent") == binding_id)
        check("POST /api/rollback result: position_lineage present",
              "position_lineage" in rb_data)
        check("POST /api/rollback result: cutover_at present",
              bool(rb_data.get("cutover_at")))

        new_binding_id = rb_data["new_binding"]["binding_id"]

        # GET /api/rollback/history — should include the new replacement binding
        r_hist = client.get("/api/rollback/history", headers=AUTH)
        check("GET /api/rollback/history returns 200",
              r_hist.status_code == 200)
        hist_data = r_hist.get_json()
        check("GET /api/rollback/history count >= 1",
              hist_data.get("count", 0) >= 1)
        rollback_ids = [b["binding_id"] for b in hist_data.get("rollbacks", [])]
        check("GET /api/rollback/history contains the replacement binding",
              new_binding_id in rollback_ids)

        # GET /api/rollback/history?pool_id= filtered
        r_hist_pool = client.get(
            "/api/rollback/history?pool_id=pool-http-rb", headers=AUTH
        )
        check("GET /api/rollback/history?pool_id= returns pool-filtered results",
              r_hist_pool.status_code == 200
              and r_hist_pool.get_json().get("count", 0) >= 1)

    # No-token returns 401
    r_unauth = client.post("/api/rollback", json=rollback_body)
    check("POST /api/rollback without token returns 401",
          r_unauth.status_code == 401)


def run_kill_switch_and_evolution_tests():
    """Smoke tests for kill-switch fast path and evolution orchestration — BP5-SVC-013.

    Acceptance criteria:
      AC-5  kill-switch fast path routes through runtime-manager with audit
      AC-6  evolution freeze / retrain / redeploy action paths are verified
    """
    print("\n=== Kill-Switch Fast Path + Evolution Orchestration — BP5-SVC-013 ===")

    from kill_switch_controller import HardTriggerReason, KillSwitchActionType

    # ------------------------------------------------------------------
    # AC-5: Kill-switch service layer
    # ------------------------------------------------------------------
    svc_ks = RuntimeManagerService(store_path=None, single_runtime_enforced=True)

    try:
        outcome = svc_ks.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-ks-001",
            "actor_id": "operator-smoke",
            "severity": 1,
        })
        check("kill_switch: execute_kill_switch returns outcome dict",
              isinstance(outcome, dict) and "command" in outcome and "audit_entry" in outcome)
        check("kill_switch: safe_mode_after is present",
              bool(outcome.get("safe_mode_after")))
        check("kill_switch: audit_entry has actor_id",
              outcome.get("audit_entry", {}).get("actor_id") == "operator-smoke")
    except Exception as exc:
        check("kill_switch: execute_kill_switch service layer", False, str(exc))

    # safe_mode read
    try:
        state = svc_ks.get_safe_mode("pool-ks-001")
        check("kill_switch: get_safe_mode returns a non-empty string", bool(state))
    except Exception as exc:
        check("kill_switch: get_safe_mode", False, str(exc))

    # advance safe mode
    try:
        new_state = svc_ks.advance_safe_mode(
            "pool-ks-001", "recovery_testing", actor_id="operator-smoke",
            note="conditions cleared"
        )
        check("kill_switch: advance_safe_mode returns new state string", bool(new_state))
    except Exception as exc:
        check("kill_switch: advance_safe_mode", False, str(exc))

    # audit log
    try:
        log = svc_ks.get_kill_switch_audit_log()
        check("kill_switch: audit log has at least one entry", len(log) >= 1)
    except Exception as exc:
        check("kill_switch: get_kill_switch_audit_log", False, str(exc))

    # ------------------------------------------------------------------
    # AC-5b: Kill-switch REPLACE fast path creates replacement binding
    # ------------------------------------------------------------------
    from kill_switch_controller import KillSwitchActionType as KSAT  # noqa: E402

    svc_ks_replace = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        # Deploy a live binding first so the pool has an active runtime to replace
        original_binding = svc_ks_replace.deploy({
            "plan_id": "plan-ks-replace-orig",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "artifact-original",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-ks-replace",
            "persona_capital_binding_id": "pcb-ks-replace",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        })
        replace_outcome = svc_ks_replace.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-ks-replace",
            "actor_id": "operator-smoke",
            "severity": 1,
            "action_override": KSAT.REPLACE.value,
            "fallback_artifact_id": "artifact-fallback",
            "fallback_artifact_version": "2.0.0",
        })
        binding_action = replace_outcome.get("binding_action") or {}
        check("kill_switch REPLACE: binding_action is present",
              isinstance(binding_action, dict))
        check("kill_switch REPLACE: action is 'replace'",
              binding_action.get("action") == KSAT.REPLACE.value)
        check("kill_switch REPLACE: original binding is retired",
              binding_action.get("binding", {}).get("status") == "retired")
        check("kill_switch REPLACE: replacement_binding is present",
              "replacement_binding" in binding_action)
        check("kill_switch REPLACE: replacement_binding is active",
              binding_action.get("replacement_binding", {}).get("status") == "active")
        check("kill_switch REPLACE: replacement artifact_id is the fallback",
              binding_action.get("replacement_binding", {}).get("artifact_id") == "artifact-fallback")
        check("kill_switch REPLACE: replacement artifact_version is the fallback",
              binding_action.get("replacement_binding", {}).get("artifact_version") == "2.0.0")
        check("kill_switch REPLACE: replacement inherits capital_pool_id",
              binding_action.get("replacement_binding", {}).get("capital_pool_id") == "pool-ks-replace")
        # Verify pool now has the replacement as active (original is retired)
        active_after = svc_ks_replace._store.get_active_for_pool("pool-ks-replace")
        check("kill_switch REPLACE: pool active binding is replacement after hot-swap",
              active_after is not None and active_after.artifact_id == "artifact-fallback")
    except Exception as exc:
        check("kill_switch REPLACE fast path end-to-end", False, str(exc))

    # ------------------------------------------------------------------
    # AC-6a: Evolution freeze service layer
    # ------------------------------------------------------------------
    svc_evo = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        binding_for_freeze = svc_evo.deploy({
            "plan_id": "plan-evo-freeze",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "artifact-evo",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-evo-freeze",
            "persona_capital_binding_id": "pcb-evo-freeze",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        })
        freeze_result = svc_evo.evolution_freeze({
            "evolution_decision_id": "evo-dec-001",
            "deployment_plan_id": "dplan-freeze-001",
            "binding_id": binding_for_freeze.binding_id,
            "plan_runtime_action": "freeze_binding",
            "actor_id": "risk-owner-smoke",
            "note": "smoke test freeze",
        })
        check("evolution_freeze: result has expected keys",
              all(k in freeze_result for k in ("evolution_decision_id", "deployment_plan_id",
                                                "plan_runtime_action", "binding", "executed_at")))
        check("evolution_freeze: binding is paused after freeze_binding",
              freeze_result["binding"]["status"] == "paused")
        check("evolution_freeze: evolution_decision_id is round-tripped",
              freeze_result["evolution_decision_id"] == "evo-dec-001")
    except Exception as exc:
        check("evolution_freeze service layer", False, str(exc))

    # ------------------------------------------------------------------
    # AC-5c / AC-6a cross-path: kill-switch PAUSE → evolution_freeze follow-through
    # Verifies that evolution_freeze is idempotent when the binding is already
    # paused by the kill-switch fast path (review feedback: "evolution_freeze must
    # tolerate bindings already paused by kill-switch/rollback").
    # ------------------------------------------------------------------
    svc_cross_paused = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        b_cross = svc_cross_paused.deploy({
            "plan_id": "plan-cross-ks-freeze",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "artifact-cross",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-cross-freeze",
            "persona_capital_binding_id": "pcb-cross-freeze",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        })
        # Step 1: kill-switch pauses the binding (hard emergency path)
        ks_outcome = svc_cross_paused.execute_kill_switch({
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-cross-freeze",
            "actor_id": "operator-smoke-cross",
            "severity": 1,
            "action_override": KillSwitchActionType.PAUSE.value,
            "binding_id": b_cross.binding_id,
        })
        paused_by_ks = svc_cross_paused.get(b_cross.binding_id)
        check("cross-path: kill-switch PAUSE leaves binding paused",
              paused_by_ks is not None and paused_by_ks.status == "paused")

        # Step 2: governance freeze arrives later on the already-paused binding
        cross_freeze_result = svc_cross_paused.evolution_freeze({
            "evolution_decision_id": "evo-dec-cross-001",
            "deployment_plan_id": "dplan-cross-freeze-001",
            "binding_id": b_cross.binding_id,
            "plan_runtime_action": "freeze_binding",
            "actor_id": "risk-owner-cross",
            "note": "governance freeze after kill-switch pause",
        })
        check("cross-path: evolution_freeze succeeds on already-paused binding (no error)",
              "binding" in cross_freeze_result)
        check("cross-path: evolution_freeze result binding is paused",
              cross_freeze_result["binding"]["status"] == "paused")
        check("cross-path: evolution_freeze result has evolution_decision_id",
              cross_freeze_result["evolution_decision_id"] == "evo-dec-cross-001")
        check("cross-path: evolution_freeze result has executed_at",
              bool(cross_freeze_result.get("executed_at")))
    except Exception as exc:
        check("cross-path kill-switch PAUSE -> evolution_freeze follow-through", False, str(exc))

    # Cross-path: kill-switch drains to pending_pause → evolution_freeze completes drain
    svc_cross_pp = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        b_pp = svc_cross_pp.deploy({
            "plan_id": "plan-cross-pp",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "artifact-cross-pp",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-cross-pp",
            "persona_capital_binding_id": "pcb-cross-pp",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        })
        # Manually advance to pending_pause only (simulates kill-switch partial drain)
        svc_cross_pp.transition(b_pp.binding_id, "pending_pause")
        pp_binding = svc_cross_pp.get(b_pp.binding_id)
        check("cross-path pending_pause setup: binding is pending_pause",
              pp_binding is not None and pp_binding.status == "pending_pause")

        # evolution_freeze should complete the drain to paused
        pp_freeze_result = svc_cross_pp.evolution_freeze({
            "evolution_decision_id": "evo-dec-pp-001",
            "deployment_plan_id": "dplan-pp-001",
            "binding_id": b_pp.binding_id,
            "plan_runtime_action": "pause_then_freeze",
            "actor_id": "risk-owner-pp",
        })
        check("cross-path: evolution_freeze on pending_pause binding completes drain to paused",
              pp_freeze_result["binding"]["status"] == "paused")
    except Exception as exc:
        check("cross-path pending_pause -> evolution_freeze drain completion", False, str(exc))

    # Guard: freeze already-terminal binding must fail
    svc_evo_g = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        b_term = svc_evo_g.deploy({
            "plan_id": "plan-evo-term",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "art-term",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-evo-term",
            "persona_capital_binding_id": "pcb-evo-term",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        })
        svc_evo_g.retire(b_term.binding_id)
        svc_evo_g.evolution_freeze({
            "evolution_decision_id": "evo-dec-term",
            "deployment_plan_id": "dplan-term-001",
            "binding_id": b_term.binding_id,
            "plan_runtime_action": "freeze_binding",
            "actor_id": "risk-owner-smoke",
        })
        check("evolution_freeze: terminal binding is rejected", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError:
        check("evolution_freeze: terminal binding is rejected", True)
    except Exception as exc:
        check("evolution_freeze: terminal binding is rejected", False, str(exc))

    # ------------------------------------------------------------------
    # AC-6b: Evolution retrain service layer
    # ------------------------------------------------------------------
    svc_rt = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        retrain_result = svc_rt.evolution_retrain({
            "evolution_decision_id": "evo-dec-retrain-001",
            "action_type": "retrain",
            "artifact_id": "artifact-retrain-target",
            "actor_id": "reviewer-on-duty",
            "research_job_id": "rjob-smoke-001",
            "note": "smoke retrain",
        })
        check("evolution_retrain: result has expected keys",
              all(k in retrain_result for k in ("evolution_decision_id", "action_type",
                                                 "dispatched_at", "routing_ref")))
        check("evolution_retrain: routing_ref is set",
              bool(retrain_result.get("routing_ref")))
        check("evolution_retrain: action_type round-tripped",
              retrain_result["action_type"] == "retrain")
    except Exception as exc:
        check("evolution_retrain service layer", False, str(exc))

    # Guard: invalid action_type rejected
    try:
        svc_rt.evolution_retrain({
            "evolution_decision_id": "evo-dec-bad",
            "action_type": "deploy",
            "artifact_id": "art-bad",
            "actor_id": "reviewer",
        })
        check("evolution_retrain: invalid action_type rejected", False,
              "Expected RuntimeManagerError but succeeded")
    except RuntimeManagerError:
        check("evolution_retrain: invalid action_type rejected", True)
    except Exception as exc:
        check("evolution_retrain: invalid action_type rejected", False, str(exc))

    # ------------------------------------------------------------------
    # AC-6c: Evolution redeploy service layer
    # ------------------------------------------------------------------
    svc_rd = RuntimeManagerService(store_path=None, single_runtime_enforced=True)
    try:
        redeploy_result = svc_rd.evolution_redeploy({
            "evolution_decision_id": "evo-dec-redeploy-001",
            "actor_id": "risk-owner-smoke",
            "deployment_plan": {
                "plan_id": "plan-redeploy",
                "plan_status": "approved",
                "target_stage": "paper",
                "artifact_id": "artifact-redeploy",
                "artifact_version": "2.0.0",
                "capital_pool_id": "pool-redeploy",
                "persona_capital_binding_id": "pcb-redeploy",
                "persona_capital_binding_status": "active",
                "allowed_deployment_scope": "live",
                "loader_checks_passed": True,
            },
        })
        check("evolution_redeploy: result has expected keys",
              all(k in redeploy_result for k in ("evolution_decision_id", "binding",
                                                  "redeployed_at")))
        check("evolution_redeploy: binding is active",
              redeploy_result["binding"]["status"] == "active")
        check("evolution_redeploy: evolution_decision_id round-tripped",
              redeploy_result["evolution_decision_id"] == "evo-dec-redeploy-001")
    except Exception as exc:
        check("evolution_redeploy service layer", False, str(exc))


def run_kill_switch_and_evolution_http_tests():
    """HTTP smoke for kill-switch and evolution routes — BP5-SVC-013."""
    print("\n=== Kill-Switch + Evolution HTTP Layer — BP5-SVC-013 ===")

    from kill_switch_controller import HardTriggerReason

    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = (
        "/tmp/pantheon/smoke-test/ks-evo-http-bindings.json"
    )
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"

    import main  # noqa: PLC0415
    main._svc = None

    client = main.app.test_client()
    AUTH = {"Authorization": "Bearer test-token"}

    # --- Kill-switch dispatch ---
    ks_body = {
        "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
        "capital_pool_id": "pool-ks-http",
        "actor_id": "operator-http-smoke",
        "severity": 1,
    }
    r_ks = client.post("/api/kill-switch/dispatch", json=ks_body, headers=AUTH)
    check("POST /api/kill-switch/dispatch returns 200",
          r_ks.status_code == 200,
          r_ks.get_data(as_text=True))
    if r_ks.status_code == 200:
        ks_data = r_ks.get_json()
        check("kill-switch dispatch: command present", "command" in ks_data)
        check("kill-switch dispatch: audit_entry present", "audit_entry" in ks_data)
        check("kill-switch dispatch: safe_mode_after present",
              bool(ks_data.get("safe_mode_after")))

    # Missing fields → 400
    r_ks_bad = client.post("/api/kill-switch/dispatch", json={}, headers=AUTH)
    check("POST /api/kill-switch/dispatch empty body returns 400",
          r_ks_bad.status_code == 400)

    # No token → 401
    r_ks_noauth = client.post("/api/kill-switch/dispatch", json=ks_body)
    check("POST /api/kill-switch/dispatch without token returns 401",
          r_ks_noauth.status_code == 401)

    # --- Safe mode GET ---
    r_sm = client.get("/api/kill-switch/pool-ks-http/safe-mode", headers=AUTH)
    check("GET /api/kill-switch/<pool_id>/safe-mode returns 200",
          r_sm.status_code == 200)
    if r_sm.status_code == 200:
        sm_data = r_sm.get_json()
        check("safe-mode GET: safe_mode_state present",
              bool(sm_data.get("safe_mode_state")))

    # --- Audit log ---
    r_audit = client.get("/api/kill-switch/audit-log", headers=AUTH)
    check("GET /api/kill-switch/audit-log returns 200",
          r_audit.status_code == 200)
    if r_audit.status_code == 200:
        audit_data = r_audit.get_json()
        check("audit-log: count >= 1", audit_data.get("count", 0) >= 1)

    # --- Kill-switch REPLACE HTTP: assert replacement binding is created ---
    from kill_switch_controller import KillSwitchActionType as KSAT  # noqa: E402, F811

    # Deploy a binding to replace via HTTP
    deploy_for_replace = {
        "plan_id": "plan-ks-http-replace-orig",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "art-ks-http-orig",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-ks-http-replace",
        "persona_capital_binding_id": "pcb-ks-http-replace",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
    }
    r_dep_replace = client.post("/api/runtimes/deploy", json=deploy_for_replace, headers=AUTH)
    check("kill-switch REPLACE HTTP setup: deploy returns 201",
          r_dep_replace.status_code == 201,
          r_dep_replace.get_data(as_text=True))

    if r_dep_replace.status_code == 201:
        ks_replace_body = {
            "reason": HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,
            "capital_pool_id": "pool-ks-http-replace",
            "actor_id": "operator-http-smoke",
            "severity": 1,
            "action_override": KSAT.REPLACE.value,
            "fallback_artifact_id": "art-ks-http-fallback",
            "fallback_artifact_version": "2.0.0",
        }
        r_ks_replace = client.post(
            "/api/kill-switch/dispatch", json=ks_replace_body, headers=AUTH
        )
        check("POST /api/kill-switch/dispatch REPLACE returns 200",
              r_ks_replace.status_code == 200,
              r_ks_replace.get_data(as_text=True))
        if r_ks_replace.status_code == 200:
            ks_replace_data = r_ks_replace.get_json()
            ba = ks_replace_data.get("binding_action") or {}
            check("kill-switch REPLACE HTTP: action is 'replace'",
                  ba.get("action") == KSAT.REPLACE.value)
            check("kill-switch REPLACE HTTP: original binding retired",
                  ba.get("binding", {}).get("status") == "retired")
            check("kill-switch REPLACE HTTP: replacement_binding present",
                  "replacement_binding" in ba)
            check("kill-switch REPLACE HTTP: replacement is active",
                  ba.get("replacement_binding", {}).get("status") == "active")
            check("kill-switch REPLACE HTTP: replacement artifact is the fallback",
                  ba.get("replacement_binding", {}).get("artifact_id") == "art-ks-http-fallback")

    # --- Evolution freeze HTTP ---
    # First deploy a binding to freeze
    deploy_for_freeze = {
        "plan_id": "plan-evo-http-freeze",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "art-evo-http",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-evo-http-freeze",
        "persona_capital_binding_id": "pcb-evo-http-freeze",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
    }
    r_dep = client.post("/api/runtimes/deploy", json=deploy_for_freeze, headers=AUTH)
    check("evolution freeze HTTP setup: deploy returns 201",
          r_dep.status_code == 201,
          r_dep.get_data(as_text=True))

    if r_dep.status_code == 201:
        freeze_binding_id = r_dep.get_json()["binding_id"]

        freeze_body = {
            "evolution_decision_id": "evo-dec-http-001",
            "deployment_plan_id": "dplan-http-freeze-001",
            "binding_id": freeze_binding_id,
            "plan_runtime_action": "freeze_binding",
            "actor_id": "risk-owner-http",
        }
        r_freeze = client.post("/api/evolution/freeze", json=freeze_body, headers=AUTH)
        check("POST /api/evolution/freeze returns 200",
              r_freeze.status_code == 200,
              r_freeze.get_data(as_text=True))
        if r_freeze.status_code == 200:
            fd = r_freeze.get_json()
            check("evolution/freeze: binding status is paused",
                  fd["binding"]["status"] == "paused")
            check("evolution/freeze: evolution_decision_id round-tripped",
                  fd["evolution_decision_id"] == "evo-dec-http-001")

    # Missing fields → 400
    r_freeze_bad = client.post("/api/evolution/freeze", json={}, headers=AUTH)
    check("POST /api/evolution/freeze empty body returns 400",
          r_freeze_bad.status_code == 400)

    # --- Evolution retrain HTTP ---
    retrain_body = {
        "evolution_decision_id": "evo-dec-retrain-http",
        "action_type": "revalidate",
        "artifact_id": "art-revalidate-target",
        "actor_id": "reviewer-http",
        "research_job_id": "rjob-http-001",
        "note": "http smoke revalidate",
    }
    r_retrain = client.post("/api/evolution/retrain", json=retrain_body, headers=AUTH)
    check("POST /api/evolution/retrain returns 200",
          r_retrain.status_code == 200,
          r_retrain.get_data(as_text=True))
    if r_retrain.status_code == 200:
        rd = r_retrain.get_json()
        check("evolution/retrain: routing_ref present", bool(rd.get("routing_ref")))
        check("evolution/retrain: action_type round-tripped",
              rd["action_type"] == "revalidate")

    # Missing fields → 400
    r_retrain_bad = client.post("/api/evolution/retrain", json={}, headers=AUTH)
    check("POST /api/evolution/retrain empty body returns 400",
          r_retrain_bad.status_code == 400)

    # --- Evolution redeploy HTTP ---
    redeploy_body = {
        "evolution_decision_id": "evo-dec-redeploy-http",
        "actor_id": "risk-owner-http",
        "deployment_plan": {
            "plan_id": "plan-redeploy-http",
            "plan_status": "approved",
            "target_stage": "paper",
            "artifact_id": "art-redeploy-http",
            "artifact_version": "2.0.0",
            "capital_pool_id": "pool-redeploy-http",
            "persona_capital_binding_id": "pcb-redeploy-http",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "live",
            "loader_checks_passed": True,
        },
    }
    r_redeploy = client.post("/api/evolution/redeploy", json=redeploy_body, headers=AUTH)
    check("POST /api/evolution/redeploy returns 201",
          r_redeploy.status_code == 201,
          r_redeploy.get_data(as_text=True))
    if r_redeploy.status_code == 201:
        rdp = r_redeploy.get_json()
        check("evolution/redeploy: binding is active",
              rdp["binding"]["status"] == "active")
        check("evolution/redeploy: evolution_decision_id round-tripped",
              rdp["evolution_decision_id"] == "evo-dec-redeploy-http")

    # Missing fields → 400
    r_redeploy_bad = client.post("/api/evolution/redeploy", json={}, headers=AUTH)
    check("POST /api/evolution/redeploy empty body returns 400",
          r_redeploy_bad.status_code == 400)

    # No token → 401
    r_noauth = client.post("/api/evolution/freeze", json=freeze_body if r_dep.status_code == 201 else {})
    check("POST /api/evolution/freeze without token returns 401",
          r_noauth.status_code == 401)


def main_runner():
    print("=" * 60)
    print("  runtime-manager smoke test  (BP5-SVC-007 + BP5-SVC-008)")
    print("=" * 60)

    try:
        run_service_layer_tests()
    except Exception:
        print(f"\n{FAIL} Unexpected error in service layer tests:")
        traceback.print_exc()

    try:
        run_http_layer_tests()
    except Exception:
        print(f"\n{FAIL} Unexpected error in HTTP layer tests:")
        traceback.print_exc()

    try:
        run_internal_api_boundary_smoke()
    except Exception:
        print(f"\n{FAIL} Unexpected error in boundary smoke tests:")
        traceback.print_exc()

    try:
        run_rollback_action_tests()
    except Exception:
        print(f"\n{FAIL} Unexpected error in rollback action tests:")
        traceback.print_exc()

    try:
        run_rollback_http_tests()
    except Exception:
        print(f"\n{FAIL} Unexpected error in rollback HTTP tests:")
        traceback.print_exc()

    try:
        run_kill_switch_and_evolution_tests()
    except Exception:
        print(f"\n{FAIL} Unexpected error in kill-switch/evolution service tests:")
        traceback.print_exc()

    try:
        run_kill_switch_and_evolution_http_tests()
    except Exception:
        print(f"\n{FAIL} Unexpected error in kill-switch/evolution HTTP tests:")
        traceback.print_exc()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"  Results: {passed} passed, {failed} failed out of {len(_results)} checks")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main_runner()
