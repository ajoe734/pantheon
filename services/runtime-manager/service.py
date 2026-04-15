"""RuntimeManagerService — pure service layer (no HTTP).

This module is the sole authorised writer for RuntimeBinding records within the
Execution Plane.  It enforces the pre-conditions documented in:

    services/execution/runtime-manager/contract.md
    BINDING_AND_DEPLOYMENT_SEMANTICS.md  §19 (RUN-001)

Write authority
---------------
Only this service may call RuntimeBindingStore write methods.
Governance Plane, Capital Pool Plane, and BFF layers are read-only.

Pre-conditions for deploy()
----------------------------
1. DeploymentPlan exists and status ∈ {approved, executing}
2. PersonaCapitalBinding exists and status = active, with
   allowed_deployment_scope >= target_stage
3. PersonaCapitalBinding.status must equal "active" — revoked/suspended
   bindings are rejected at the service layer (caller must pass
   persona_capital_binding_status="active")
4. Loader-check proof must be present and true — caller must pass
   loader_checks_passed=True; False is rejected (RUN-001)
5. Single-runtime rule: pool must have no existing active binding
   (enforced by RuntimeBindingStore.create with single_runtime_enforced=True)
6. stage consistency: binding.deployment_mode == plan.target_stage
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Locate and import the execution-plane runtime_binding module
# ---------------------------------------------------------------------------

_EXEC_RM_DIR = os.getenv(
    "PANTHEON_EXEC_RUNTIME_MANAGER_DIR",
    str(Path(__file__).resolve().parent.parent.parent
        / "services" / "execution" / "runtime-manager"),
)

if _EXEC_RM_DIR not in sys.path:
    sys.path.insert(0, _EXEC_RM_DIR)

from runtime_binding import (  # noqa: E402
    RuntimeBinding,
    RuntimeBindingError,
    RuntimeBindingStatus,
    RuntimeBindingStore,
    DeploymentMode,
    RollbackActionType,
    validate_binding,
    utc_now,
)

__all__ = [
    "RuntimeManagerService",
    "RuntimeManagerError",
    "DeployPlanRequest",
]

# ---------------------------------------------------------------------------
# Stage ordering — used to verify allowed_deployment_scope >= target_stage
# ---------------------------------------------------------------------------

_STAGE_ORDER = {
    "none": 0,
    "paper": 1,
    "canary": 2,
    "live": 3,
}


def _scope_allows_stage(allowed_deployment_scope: str, target_stage: str) -> bool:
    return _STAGE_ORDER.get(allowed_deployment_scope, -1) >= _STAGE_ORDER.get(target_stage, 999)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class RuntimeManagerError(RuntimeError):
    """Raised when a RuntimeManager pre-condition or write guard fails."""


# ---------------------------------------------------------------------------
# Input shapes (plain dicts in practice; typed classes for documentation)
# ---------------------------------------------------------------------------

class DeployPlanRequest:
    """Typed descriptor for deploy() input — passed as a plain dict in HTTP layer."""
    # Fields the caller must supply:
    #   plan_id                          str   — DeploymentPlan.plan_id
    #   plan_status                      str   — must be 'approved' or 'executing'
    #   target_stage                     str   — paper / canary / live / frozen
    #   artifact_id                      str
    #   artifact_version                 str
    #   capital_pool_id                  str
    #   persona_capital_binding_id       str   — PersonaCapitalBinding.binding_id
    #   persona_capital_binding_status   str   — must be 'active' (RUN-001)
    #   allowed_deployment_scope         str   — from PersonaCapitalBinding
    #   loader_checks_passed             bool  — must be True (RUN-001)
    #   runtime_id                       str   — optional; auto-generated if absent
    #   rollback_parent                  str   — optional; present when this is a rollback
    #   rollback_action_type             str   — optional; required when rollback_parent set


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class RuntimeManagerService:
    """
    Deployable service layer for RuntimeBinding write operations.

    Consumers (Flask routes, CLI, integration tests) call this service
    instead of touching RuntimeBindingStore directly.  This is the single
    enforcement point for all pre-conditions and write-authority rules.

    Parameters
    ----------
    store_path : Path | None
        Optional filesystem path for RuntimeBindingStore persistence.
        When None the store is in-memory only (suitable for testing).
    single_runtime_enforced : bool
        Mirror of CapitalPool.single_runtime_enforced — default True.
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        single_runtime_enforced: bool = True,
    ) -> None:
        self._store = RuntimeBindingStore(path=store_path)
        self._single_runtime_enforced = single_runtime_enforced

    # ------------------------------------------------------------------ #
    # Primary write operations (Execution Plane only)                     #
    # ------------------------------------------------------------------ #

    def deploy(self, request: Dict[str, Any]) -> RuntimeBinding:
        """Create a RuntimeBinding from a validated DeploymentPlan descriptor.

        Pre-conditions (RUN-001):
        1. plan_status must be 'approved' or 'executing'
        2. persona_capital_binding_status must equal 'active'
        3. allowed_deployment_scope >= target_stage
        4. loader_checks_passed must be True
        5. stage consistency: target_stage must be a valid DeploymentMode value
        6. Single-runtime rule enforced by the store
        """
        plan_id = request.get("plan_id", "")
        plan_status = request.get("plan_status", "")
        target_stage = request.get("target_stage", "")
        artifact_id = request.get("artifact_id", "")
        artifact_version = request.get("artifact_version", "")
        capital_pool_id = request.get("capital_pool_id", "")
        persona_capital_binding_id = request.get("persona_capital_binding_id", "")
        persona_capital_binding_status = request.get("persona_capital_binding_status", "")
        allowed_deployment_scope = request.get("allowed_deployment_scope", "")
        loader_checks_passed = request.get("loader_checks_passed")
        runtime_id = request.get("runtime_id") or f"rt-{uuid.uuid4().hex[:8]}"
        rollback_parent = request.get("rollback_parent")
        rollback_action_type = request.get("rollback_action_type")

        # Pre-condition 1: plan status
        if plan_status not in ("approved", "executing"):
            raise RuntimeManagerError(
                f"DeploymentPlan {plan_id!r} status {plan_status!r} is not 'approved' or 'executing'. "
                "A RuntimeBinding cannot be created without an approved or executing plan."
            )

        # Pre-condition 2: PersonaCapitalBinding must be active
        if persona_capital_binding_status != "active":
            raise RuntimeManagerError(
                f"PersonaCapitalBinding {persona_capital_binding_id!r} status "
                f"{persona_capital_binding_status!r} is not 'active'. "
                "A RuntimeBinding cannot be created against a revoked or non-active capital binding."
            )

        # Pre-condition 3: scope allows stage
        if not _scope_allows_stage(allowed_deployment_scope, target_stage):
            raise RuntimeManagerError(
                f"PersonaCapitalBinding allowed_deployment_scope={allowed_deployment_scope!r} "
                f"does not permit target_stage={target_stage!r}. "
                "Scope must be >= target stage."
            )

        # Pre-condition 4: loader-check proof must be present and true
        if loader_checks_passed is not True:
            raise RuntimeManagerError(
                "loader_checks_passed must be True. "
                "Deploy is blocked until all loader checks have passed (RUN-001)."
            )

        # Pre-condition 5: stage consistency — target_stage must be a valid DeploymentMode
        try:
            DeploymentMode(target_stage)
        except ValueError:
            raise RuntimeManagerError(
                f"target_stage={target_stage!r} is not a valid DeploymentMode. "
                f"Must be one of {[e.value for e in DeploymentMode]}."
            )

        # Pre-condition 6: rollback fields consistency
        if rollback_parent and not rollback_action_type:
            raise RuntimeManagerError(
                "rollback_action_type is required when rollback_parent is set."
            )

        binding_id = f"rb-{uuid.uuid4().hex}"
        binding = RuntimeBinding(
            binding_id=binding_id,
            runtime_id=runtime_id,
            capital_pool_id=capital_pool_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            deployment_mode=target_stage,
            effective_at=utc_now(),
            status=RuntimeBindingStatus.ACTIVE.value,
            plan_id=plan_id,
            persona_capital_binding_id=persona_capital_binding_id,
            rollback_parent=rollback_parent,
            rollback_action_type=rollback_action_type,
        )

        # Semantic validation (field-level)
        errors = validate_binding(binding)
        if errors:
            raise RuntimeManagerError(f"RuntimeBinding validation failed: {errors}")

        # Store create — enforces single-runtime rule
        return self._store.create(
            binding,
            single_runtime_enforced=self._single_runtime_enforced,
        )

    def retire(self, binding_id: str, retired_at: Optional[str] = None) -> RuntimeBinding:
        """Retire a binding (terminal transition)."""
        return self._store.retire(binding_id, retired_at=retired_at)

    def transition(self, binding_id: str, new_status: str) -> RuntimeBinding:
        """Transition a binding to a new status via the allowed state machine."""
        return self._store.transition_status(binding_id, new_status)

    # ------------------------------------------------------------------ #
    # Read operations (open to authorised consumers)                      #
    # ------------------------------------------------------------------ #

    def get(self, binding_id: str) -> Optional[RuntimeBinding]:
        return self._store.get(binding_id)

    def require(self, binding_id: str) -> RuntimeBinding:
        return self._store.require(binding_id)

    def list_all(self) -> List[RuntimeBinding]:
        return self._store.list_all()

    def list_by_pool(self, capital_pool_id: str) -> List[RuntimeBinding]:
        return self._store.find_by_pool(capital_pool_id)

    def get_active_for_pool(self, capital_pool_id: str) -> Optional[RuntimeBinding]:
        return self._store.get_active_for_pool(capital_pool_id)

    def list_by_plan(self, plan_id: str) -> List[RuntimeBinding]:
        return self._store.find_by_plan(plan_id)
