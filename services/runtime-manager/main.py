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

GET   /__health__
    Liveness probe.

Authentication
--------------
All write routes require a Bearer token in the Authorization header.
Token content is not validated in v1 (stub — integration tests use any
non-empty token).  Add JWT validation before production.

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

# ---------------------------------------------------------------------------
# App and service bootstrap
# ---------------------------------------------------------------------------

app = Flask(__name__)

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
# Auth helper
# ---------------------------------------------------------------------------

def _require_bearer():
    """Return (token, None) on success or (None, error_response) on failure."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None, (
            jsonify({"error": {"code": "401", "message": "Unauthorized: missing Bearer token"}}),
            401,
        )
    token = auth.split(None, 1)[1]
    if not token:
        return None, (
            jsonify({"error": {"code": "401", "message": "Unauthorized: empty token"}}),
            401,
        )
    return token, None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/__health__", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "runtime-manager"}), 200


@app.route("/api/runtimes/deploy", methods=["POST"])
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
    _, err = _require_bearer()
    if err:
        return err

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
def list_bindings():
    """List RuntimeBindings, optionally filtered by pool_id or plan_id."""
    _, err = _require_bearer()
    if err:
        return err

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
def get_binding(binding_id):
    """Read a single RuntimeBinding by id."""
    _, err = _require_bearer()
    if err:
        return err

    svc = _get_service()
    binding = svc.get(binding_id)
    if binding is None:
        return (
            jsonify({"error": {"code": "NOT_FOUND", "message": f"RuntimeBinding {binding_id!r} not found"}}),
            404,
        )
    return jsonify(binding.to_dict()), 200


@app.route("/api/runtime-bindings/<binding_id>/retire", methods=["POST"])
def retire_binding(binding_id):
    """Retire (terminal-terminate) a RuntimeBinding."""
    _, err = _require_bearer()
    if err:
        return err

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
def transition_binding(binding_id):
    """Advance a binding through the allowed status state machine.

    Body: { "new_status": "pending_pause" | "paused" | "active" | "failed" | "retired" }
    """
    _, err = _require_bearer()
    if err:
        return err

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


@app.route("/api/runtimes/<pool_id>/active", methods=["GET"])
def get_active_binding(pool_id):
    """Return the single active RuntimeBinding for a capital pool."""
    _, err = _require_bearer()
    if err:
        return err

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
    _, err = _require_bearer()
    if err:
        return err

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
def rollback_history():
    """Return all bindings that have a rollback_parent set (i.e. replacement bindings).

    Optional query params:
      pool_id — filter by capital_pool_id
    """
    _, err = _require_bearer()
    if err:
        return err

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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
