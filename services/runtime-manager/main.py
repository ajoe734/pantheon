"""services/runtime-manager — deployable Flask service.

This is the authoritative HTTP surface for RuntimeBinding write operations
within the Execution Plane.  All routes enforce the pre-conditions from:

    services/execution/runtime-manager/contract.md
    BINDING_AND_DEPLOYMENT_SEMANTICS.md §19 (RUN-001)
    ROLLBACK_AND_POSITION_SEMANTICS.md

Route summary
-------------
POST  /api/runtimes/deploy
    Create a RuntimeBinding from a validated DeploymentPlan descriptor.
    Body: DeployPlanRequest fields (see service.py for field documentation).
    Optional strategy_id is preserved in RuntimeBinding.metadata for read-side
    adapter binding checks.

GET   /api/runtime-bindings
    List all RuntimeBindings, optionally filtered by pool_id or plan_id.

GET   /api/runtime-bindings/<binding_id>
    Read a single RuntimeBinding by id.

POST  /api/runtime-bindings/<binding_id>/retire
    Retire (terminate) a binding.

POST  /api/runtime-bindings/<binding_id>/transition
    Advance a binding through the allowed status state machine.
    Body: { "new_status": "pending_pause" | "paused" | "active" | "failed" }

POST  /api/rollback
    Execute a rollback action (replace | pause_then_replace | liquidate_then_replace).
    Body: RollbackRequest fields (see service.py for field documentation).
    Returns: old_binding, new_binding, cutover_at, position_lineage.

GET   /api/rollback/history
    List rollback bindings (those with a rollback_parent set).
    Optional query param: pool_id — filter by capital_pool_id.

POST  /api/kill-switch/dispatch
    Emergency kill-switch fast path (KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §8).
    Body: { reason, capital_pool_id, actor_id, [binding_id], [severity],
            [action_override], [fallback_artifact_id], [fallback_artifact_version],
            [context] }
    Returns: { command, audit_entry, safe_mode_after }

GET   /api/kill-switch/<pool_id>/safe-mode
    Return the current SafeModeState for a capital pool.

POST  /api/kill-switch/<pool_id>/safe-mode
    Manually advance the safe-mode state (governance recovery path).
    Body: { target_state, actor_id, [note] }

GET   /api/kill-switch/audit-log
    Return all kill-switch audit entries for this service instance.

POST  /api/evolution/freeze
    Execute freeze runtime follow-through for an approved EvolutionDecision.
    Body: { evolution_decision_id, deployment_plan_id, binding_id, plan_runtime_action, actor_id, [note] }

POST  /api/evolution/retrain
    Record dispatch of an approved retrain/revalidate EvolutionDecision.
    Body: { evolution_decision_id, action_type, artifact_id, actor_id, research_job_id, [note] }

POST  /api/evolution/redeploy
    Execute redeploy follow-through after an approved evolution decision.
    Body: EvolutionRedeployRequest fields (see service.py).

GET   /__health__
    Liveness probe.

Authentication
--------------
All routes require ``Authorization: Bearer <token>``. Tokens are validated by
``services.runtime_auth_inbound`` and may be either a HS256 JWT
(``PANTHEON_RUNTIME_JWT_SECRET`` configured) or a structured legacy token
shaped ``actor_id[:role1,role2]`` when ``PANTHEON_RUNTIME_AUTH_MODE`` is
``permissive`` (default). Strict mode rejects unsigned tokens. Critical write
paths (kill-switch, safe-mode advance, evolution follow-through) additionally
enforce MFA via ``X-MFA-Token`` (six-digit OTP) when
``PANTHEON_RUNTIME_MFA_REQUIRED=true``. RBAC is enforced per route by the
``require_authn`` decorator below.

Environment variables
---------------------
PANTHEON_RUNTIME_BINDING_STORE_PATH
    Filesystem path for persistent RuntimeBindingStore JSON.
    Defaults to /tmp/pantheon/runtime-manager/bindings.json.

PANTHEON_EXEC_RUNTIME_MANAGER_DIR
    Override path to services/execution/runtime-manager/ if the service
    is not run from the repo root.

PANTHEON_SINGLE_RUNTIME_ENFORCED
    Set to "false" to disable single-runtime-per-pool enforcement (testing).
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request

from service import (
    RuntimeManagerService,
    RuntimeManagerError,
)

# Import kill-switch error type for HTTP error mapping
from kill_switch_controller import KillSwitchError  # noqa: E402

# Import the store error type so we can map it to HTTP 404/409
import sys
_EXEC_RM_DIR = os.getenv(
    "PANTHEON_EXEC_RUNTIME_MANAGER_DIR",
    str(Path(__file__).resolve().parent.parent.parent
        / "services" / "execution" / "runtime-manager"),
)
if _EXEC_RM_DIR not in sys.path:
    sys.path.insert(0, _EXEC_RM_DIR)
from runtime_binding import RuntimeBindingError  # noqa: E402

# Make sibling repo modules importable when this file runs as ``main``.
_REPO_ROOT_FOR_AUTH = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT_FOR_AUTH not in sys.path:
    sys.path.insert(0, _REPO_ROOT_FOR_AUTH)
from services.runtime_auth_inbound import require_authn  # noqa: E402
from services.foundation.health import register_flask_health_routes  # noqa: E402

# ---------------------------------------------------------------------------
# App and service bootstrap
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _optional_url_dependency(env_key: str) -> dict[str, object]:
    url = os.getenv(env_key, "").strip()
    return {
        "status": "ok",
        "configured": bool(url),
        "optional": True,
        "url": url,
    }


register_flask_health_routes(
    app,
    "runtime-manager",
    dependencies=lambda: {
        "consultation": _optional_url_dependency("PANTHEON_CONSULTATION_API_URL"),
        "deployment": _optional_url_dependency("PANTHEON_DEPLOYMENT_API_URL"),
        "governance_approval": _optional_url_dependency("PANTHEON_GOVERNANCE_APPROVAL_API_URL"),
    },
    metrics=lambda: {"binding_count": len(_get_service().list_all())},
    details=lambda: {"store_path": _STORE_PATH_ENV, "single_runtime_enforced": _SINGLE_RUNTIME_ENFORCED},
)

_STORE_PATH_ENV = os.getenv(
    "PANTHEON_RUNTIME_BINDING_STORE_PATH",
    "/tmp/pantheon/runtime-manager/bindings.json",
)
_SINGLE_RUNTIME_ENFORCED = os.getenv(
    "PANTHEON_SINGLE_RUNTIME_ENFORCED", "true"
).lower() != "false"

_svc: RuntimeManagerService | None = None


def _get_service() -> RuntimeManagerService:
    global _svc
    if _svc is None:
        store_path = Path(_STORE_PATH_ENV)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        _svc = RuntimeManagerService(
            store_path=store_path,
            single_runtime_enforced=_SINGLE_RUNTIME_ENFORCED,
        )
    return _svc


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

# Role bundles enforced per route. Centralised so operator policy changes touch
# one location instead of every route handler.
_OPERATOR_ROLES = ("operator", "admin", "approver", "reviewer", "risk_owner")
_APPROVER_ROLES = ("approver", "admin", "risk_owner")
_INCIDENT_ROLES = ("operator", "admin", "risk_owner")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/__health__", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "runtime-manager"}), 200


@app.route("/api/runtimes/deploy", methods=["POST"])
@require_authn(roles=_OPERATOR_ROLES)
def deploy():
    """Create a RuntimeBinding from a DeploymentPlan descriptor.

    Enforces all RUN-001 pre-conditions:
      - plan_status ∈ {approved, executing}
      - persona_capital_binding_status == 'active'
      - allowed_deployment_scope >= target_stage
      - loader_checks_passed == True
      - stage consistency
      - single-runtime rule (via store)
    """
    body = request.get_json(force=True) or {}

    required_fields = [
        "plan_id", "plan_status", "target_stage",
        "artifact_id", "artifact_version",
        "capital_pool_id", "persona_capital_binding_id",
        "persona_capital_binding_status",
        "allowed_deployment_scope",
    ]
    missing = [f for f in required_fields if not body.get(f)]
    # loader_checks_passed must be explicitly present (False is a valid but rejected value)
    if "loader_checks_passed" not in body:
        missing.append("loader_checks_passed")
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        binding = svc.deploy(body)
        return jsonify(binding.to_dict()), 201
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "PRECONDITION_FAILED", "message": str(exc)}}), 422
    except RuntimeBindingError as exc:
        status_code = 409 if "single-runtime" in str(exc).lower() else 422
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), status_code
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/runtime-bindings", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def list_bindings():
    """List RuntimeBindings, optionally filtered by pool_id or plan_id."""
    pool_id = request.args.get("pool_id")
    plan_id = request.args.get("plan_id")

    svc = _get_service()
    if pool_id:
        bindings = svc.list_by_pool(pool_id)
    elif plan_id:
        bindings = svc.list_by_plan(plan_id)
    else:
        bindings = svc.list_all()

    return jsonify({
        "bindings": [b.to_dict() for b in bindings],
        "count": len(bindings),
    }), 200


@app.route("/api/runtime-bindings/<binding_id>", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def get_binding(binding_id):
    """Read a single RuntimeBinding by id."""
    svc = _get_service()
    binding = svc.get(binding_id)
    if binding is None:
        return (
            jsonify({"error": {"code": "NOT_FOUND", "message": f"RuntimeBinding {binding_id!r} not found"}}),
            404,
        )
    return jsonify(binding.to_dict()), 200


@app.route("/api/runtime-bindings/<binding_id>/retire", methods=["POST"])
@require_authn(roles=_OPERATOR_ROLES, mfa_required=True)
def retire_binding(binding_id):
    """Retire (terminal-terminate) a RuntimeBinding."""
    body = request.get_json(force=True) or {}
    retired_at = body.get("retired_at")

    svc = _get_service()
    try:
        binding = svc.retire(binding_id, retired_at=retired_at)
        return jsonify(binding.to_dict()), 200
    except RuntimeBindingError as exc:
        code = 404 if "not found" in str(exc).lower() else 409
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), code
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/runtime-bindings/<binding_id>/transition", methods=["POST"])
@require_authn(roles=_OPERATOR_ROLES)
def transition_binding(binding_id):
    """Advance a binding through the allowed status state machine.

    Body: { "new_status": "pending_pause" | "paused" | "active" | "failed" | "retired" }
    """
    body = request.get_json(force=True) or {}
    new_status = body.get("new_status", "")
    if not new_status:
        return jsonify({"error": {"code": "MISSING_FIELDS", "message": "new_status is required"}}), 400

    svc = _get_service()
    try:
        binding = svc.transition(binding_id, new_status)
        return jsonify(binding.to_dict()), 200
    except RuntimeBindingError as exc:
        code = 404 if "not found" in str(exc).lower() else 409
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), code
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/runtime-fleet/desired-state", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def fleet_desired_state():
    """Return the active fleet desired state for paper/canary RuntimeBindings.

    LOOP-AUTO-RT-001: this is the stable desired-state query consumed by the
    fleet reconciler (LOOP-AUTO-RT-002) to drive exactly-one-worker-per-binding
    enforcement.

    Active bindings in fleet-managed stages (paper, canary) are returned in
    ``bindings``.  Retired, failed, pending_pause, and paused bindings are
    excluded (listed in ``excluded`` when include_excluded=true).

    Query params
    ------------
    stage            : optional; filter by "paper" or "canary"
    pool_id          : optional; filter by capital_pool_id
    include_excluded : optional; "true" to include excluded bindings in response
    """
    from fleet_desired_state import build_fleet_desired_state as _build

    stage = request.args.get("stage") or None
    pool_id_filter = request.args.get("pool_id") or None
    include_excluded = request.args.get("include_excluded", "").lower() in {
        "true", "1", "yes"
    }

    svc = _get_service()
    if pool_id_filter:
        bindings = svc.list_by_pool(pool_id_filter)
    else:
        bindings = svc.list_all()

    desired = _build([b.to_dict() for b in bindings], stage_filter=stage)
    return jsonify(desired.to_dict(include_excluded=include_excluded)), 200


@app.route("/api/runtimes/<pool_id>/active", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def get_active_binding(pool_id):
    """Return the single active RuntimeBinding for a capital pool."""
    svc = _get_service()
    try:
        binding = svc.get_active_for_pool(pool_id)
        if binding is None:
            return (
                jsonify({"error": {"code": "NOT_FOUND", "message": f"No active binding for pool {pool_id!r}"}}),
                404,
            )
        return jsonify(binding.to_dict()), 200
    except RuntimeBindingError as exc:
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), 409
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/rollback", methods=["POST"])
@require_authn(roles=_INCIDENT_ROLES, mfa_required=True)
def execute_rollback():
    """Execute a canonical rollback action.

    Implements all three strategies from ROLLBACK_AND_POSITION_SEMANTICS.md §3:
      - replace: hot-swap; retire old, create new replacement
      - pause_then_replace: drain active binding to paused, then replace
      - liquidate_then_replace: retire old with liquidation semantics, create replacement

    Required body fields:
      current_binding_id, action_type,
      replacement_plan_id, replacement_artifact_id, replacement_artifact_version,
      replacement_persona_capital_binding_id, replacement_allowed_deployment_scope

    Optional:
      replacement_plan_status, replacement_persona_capital_binding_status,
      replacement_deployment_mode, replacement_runtime_id,
      replacement_start_paused, loader_checks_passed, opened_by_artifact_id

    Returns 201 with { action_type, old_binding, new_binding, cutover_at, position_lineage }.
    """
    body = request.get_json(force=True) or {}

    required_fields = [
        "current_binding_id",
        "action_type",
        "replacement_plan_id",
        "replacement_artifact_id",
        "replacement_artifact_version",
        "replacement_persona_capital_binding_id",
        "replacement_allowed_deployment_scope",
    ]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        result = svc.rollback(body)
        return jsonify(result), 201
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "PRECONDITION_FAILED", "message": str(exc)}}), 422
    except RuntimeBindingError as exc:
        code = 404 if "not found" in str(exc).lower() else 422
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), code
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/rollback/history", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def rollback_history():
    """Return all bindings that have a rollback_parent set (i.e. replacement bindings).

    Optional query params:
      pool_id — filter by capital_pool_id
    """
    pool_id = request.args.get("pool_id")

    svc = _get_service()
    if pool_id:
        bindings = svc.list_by_pool(pool_id)
    else:
        bindings = svc.list_all()

    rollback_bindings = [b.to_dict() for b in bindings if b.rollback_parent is not None]
    return jsonify({
        "rollbacks": rollback_bindings,
        "count": len(rollback_bindings),
    }), 200


@app.route("/api/kill-switch/dispatch", methods=["POST"])
@require_authn(roles=_INCIDENT_ROLES, mfa_required=True)
def kill_switch_dispatch():
    """Emergency kill-switch fast path.

    KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY §8:
    All triggers (hard or soft) enter via this route and are dispatched by the
    KillSwitchController through the runtime-manager fast path.

    Required body fields: reason, capital_pool_id, actor_id
    Optional: binding_id, severity, action_override,
              fallback_artifact_id, fallback_artifact_version, context

    Returns 200 with { command, audit_entry, safe_mode_after }.
    """
    body = request.get_json(force=True) or {}

    required_fields = ["reason", "capital_pool_id", "actor_id"]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        result = svc.execute_kill_switch(body)
        return jsonify(result), 200
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "KILL_SWITCH_ERROR", "message": str(exc)}}), 422
    except KillSwitchError as exc:
        return jsonify({"error": {"code": "KILL_SWITCH_ERROR", "message": str(exc)}}), 422
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/kill-switch/<pool_id>/safe-mode", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def get_safe_mode(pool_id):
    """Return the current SafeModeState for a capital pool."""
    svc = _get_service()
    try:
        state = svc.get_safe_mode(pool_id)
        return jsonify({"capital_pool_id": pool_id, "safe_mode_state": state}), 200
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/kill-switch/<pool_id>/safe-mode", methods=["POST"])
@require_authn(roles=_INCIDENT_ROLES, mfa_required=True)
def advance_safe_mode(pool_id):
    """Manually advance the safe-mode state for a capital pool (governance recovery path).

    Body: { target_state, actor_id, [note] }
    Returns 200 with { capital_pool_id, safe_mode_state }.
    """
    body = request.get_json(force=True) or {}

    required_fields = ["target_state", "actor_id"]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        new_state = svc.advance_safe_mode(
            pool_id,
            body["target_state"],
            actor_id=body["actor_id"],
            note=body.get("note"),
        )
        return jsonify({"capital_pool_id": pool_id, "safe_mode_state": new_state}), 200
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "SAFE_MODE_ERROR", "message": str(exc)}}), 422
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/kill-switch/audit-log", methods=["GET"])
@require_authn(roles=_OPERATOR_ROLES)
def kill_switch_audit_log():
    """Return all kill-switch audit entries for this service instance."""
    svc = _get_service()
    try:
        entries = svc.get_kill_switch_audit_log()
        return jsonify({"entries": entries, "count": len(entries)}), 200
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/evolution/freeze", methods=["POST"])
@require_authn(roles=_APPROVER_ROLES, mfa_required=True)
def evolution_freeze():
    """Execute runtime follow-through for an approved evolution freeze decision.

    EVOLUTION_REVIEW_AND_THRESHOLDS.md §11 / KILL_SWITCH_AND_SAFE_MODE §3.

    Required body fields: evolution_decision_id, deployment_plan_id, binding_id,
                         plan_runtime_action, actor_id
    Optional: note

    plan_runtime_action values: freeze_binding | pause_then_freeze
    (liquidate_then_freeze must route through execute_kill_switch or rollback)

    Returns 200 with { evolution_decision_id, deployment_plan_id, plan_runtime_action,
                       binding, actor_id, executed_at, note }.
    """
    body = request.get_json(force=True) or {}

    required_fields = [
        "evolution_decision_id", "deployment_plan_id", "binding_id",
        "plan_runtime_action", "actor_id",
    ]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        result = svc.evolution_freeze(body)
        return jsonify(result), 200
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "EVOLUTION_ERROR", "message": str(exc)}}), 422
    except RuntimeBindingError as exc:
        code = 404 if "not found" in str(exc).lower() else 409
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), code
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/evolution/retrain", methods=["POST"])
@require_authn(roles=_APPROVER_ROLES, mfa_required=True)
def evolution_retrain():
    """Record dispatch of an approved retrain/revalidate EvolutionDecision.

    EVOLUTION_REVIEW_AND_THRESHOLDS.md §12.2: executed means the research job
    has been governed-ly created; actual retraining happens in the research plane.

    Required body fields: evolution_decision_id, action_type, artifact_id, actor_id,
                         research_job_id
    action_type values: retrain | revalidate
    Optional: note

    Returns 200 with { evolution_decision_id, action_type, artifact_id, actor_id,
                       dispatched_at, routing_ref, note }.
    """
    body = request.get_json(force=True) or {}

    required_fields = [
        "evolution_decision_id", "action_type", "artifact_id", "actor_id",
        "research_job_id",
    ]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        result = svc.evolution_retrain(body)
        return jsonify(result), 200
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "EVOLUTION_ERROR", "message": str(exc)}}), 422
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


@app.route("/api/evolution/redeploy", methods=["POST"])
@require_authn(roles=_APPROVER_ROLES, mfa_required=True)
def evolution_redeploy():
    """Execute redeploy follow-through after an approved evolution decision.

    EVOLUTION_REVIEW_AND_THRESHOLDS.md §11 (redeploy follow-through row).
    Creates a new RuntimeBinding from the approved replacement artifact.

    Required body fields: evolution_decision_id, deployment_plan (dict containing
        plan_id, target_stage, artifact_id, artifact_version, capital_pool_id,
        persona_capital_binding_id, persona_capital_binding_status,
        allowed_deployment_scope, loader_checks_passed)
    Optional: actor_id, note
    (deployment_plan may also include plan_status, runtime_id)

    Returns 201 with { evolution_decision_id, binding, actor_id,
                       redeployed_at, note }.
    """
    body = request.get_json(force=True) or {}

    required_fields = ["evolution_decision_id", "deployment_plan"]
    missing = [f for f in required_fields if not body.get(f)]
    if missing:
        return (
            jsonify({"error": {"code": "MISSING_FIELDS", "message": f"Missing required fields: {missing}"}}),
            400,
        )

    svc = _get_service()
    try:
        result = svc.evolution_redeploy(body)
        return jsonify(result), 201
    except RuntimeManagerError as exc:
        return jsonify({"error": {"code": "EVOLUTION_ERROR", "message": str(exc)}}), 422
    except RuntimeBindingError as exc:
        code = 409 if "single-runtime" in str(exc).lower() else 422
        return jsonify({"error": {"code": "BINDING_ERROR", "message": str(exc)}}), code
    except Exception as exc:
        return jsonify({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}), 500


# ---------------------------------------------------------------------------
# Legacy /api/internal/v1/... operator command surface
#
# The deployable runtime-manager owns both the canonical /api/runtimes/... and
# the operator-facing /api/internal/v1/... command paths the BFF dispatches
# against. The legacy `services.control_plane.internal.internal_api` module is
# mounted on this Flask app so command, pause, rollback, kill-switch, and
# consultation sponsor-decision routes share the same in-process service and
# kill-switch state. See internal_api_routes.py for the shared-state wiring.
# ---------------------------------------------------------------------------

from internal_api_routes import register_internal_api_routes  # noqa: E402

register_internal_api_routes(app, _get_service)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
