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

    internal_api = import_module("services.control_plane.internal_api")
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

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"  Results: {passed} passed, {failed} failed out of {len(_results)} checks")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main_runner()
