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
    "RollbackRequest",
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


class RollbackRequest:
    """Typed descriptor for rollback() input — passed as a plain dict.

    The rollback() method creates a replacement RuntimeBinding that follows
    the action semantics from ROLLBACK_AND_POSITION_SEMANTICS.md §3.

    Fields the caller must supply
    --------------------------------
    current_binding_id                   str  — the RuntimeBinding to replace
    action_type                          str  — replace | pause_then_replace |
                                                liquidate_then_replace
    replacement_plan_id                  str  — DeploymentPlan for the replacement
    replacement_artifact_id              str  — fallback artifact to activate
    replacement_artifact_version         str
    replacement_persona_capital_binding_id  str
    replacement_allowed_deployment_scope    str

    Optional fields
    ---------------
    replacement_plan_status              str  — default 'approved'
    replacement_persona_capital_binding_status  str  — default 'active'
    replacement_deployment_mode          str  — default: inherits old binding's stage
    replacement_runtime_id               str  — auto-generated if absent
    replacement_start_paused             bool — for liquidate_then_replace, start
                                               replacement in paused/guarded mode;
                                               default False
    loader_checks_passed                 bool — default True
    opened_by_artifact_id                str  — original position opener artifact;
                                               included in position_lineage output
                                               (default: old binding's artifact_id)
    """


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

    def deploy(
        self,
        request: Dict[str, Any],
        _allow_cutover_bypass: bool = False,
    ) -> RuntimeBinding:
        """Create a RuntimeBinding from a validated DeploymentPlan descriptor.

        Pre-conditions (RUN-001):
        1. plan_status must be 'approved' or 'executing'
        2. persona_capital_binding_status must equal 'active'
        3. allowed_deployment_scope >= target_stage
        4. loader_checks_passed must be True
        5. stage consistency: target_stage must be a valid DeploymentMode value
        6. Single-runtime rule enforced by the store

        ``_allow_cutover_bypass`` is an internal-only flag used by the REPLACE
        rollback path to bypass the single-runtime guard for exactly this one
        binding creation during the hot-swap cutover window.  Callers outside
        this class must never set it; it avoids the race condition that arises
        when the old approach temporarily mutated the service-wide
        ``_single_runtime_enforced`` flag.
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

        # Store create — enforces single-runtime rule unless this specific call
        # has been granted a per-call cutover bypass by the REPLACE rollback path.
        effective_enforce = self._single_runtime_enforced and not _allow_cutover_bypass
        return self._store.create(
            binding,
            single_runtime_enforced=effective_enforce,
        )

    def retire(self, binding_id: str, retired_at: Optional[str] = None) -> RuntimeBinding:
        """Retire a binding (terminal transition)."""
        return self._store.retire(binding_id, retired_at=retired_at)

    def rollback(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a canonical rollback through the runtime-manager.

        Implements the three strategies from ROLLBACK_AND_POSITION_SEMANTICS.md §3:

        replace
            Hot-swap: retire the old binding then create the replacement.
            The existing book is inherited by the new artifact.  Old binding core
            fields are never rewritten — only the status transitions to 'retired'.

        pause_then_replace
            Drain-then-swap: transition old binding active → pending_pause → paused,
            create replacement binding (single-runtime rule does not fire because the
            old binding is no longer active), then retire the paused old binding.
            Cutover occurs after open orders are stabilised.

        liquidate_then_replace
            Flatten-then-swap: retire old binding with liquidation metadata, then
            create replacement binding.  When replacement_start_paused=True the new
            binding starts in guarded / paused mode, letting the operator confirm
            zero-position state before re-enabling entries.

        Position lineage (ROLLBACK_AND_POSITION_SEMANTICS.md §7):
            opened_by_artifact_id is immutable (carried from the original opener).
            current_managed_by_binding_id is updated to the new binding_id in the
            returned position_lineage record once cutover completes.
            For liquidate_then_replace the position_lineage note flags that
            ownership must not be transferred until zero-position confirmation.

        Returns
        -------
        dict with keys:
            action_type        : str — strategy used
            old_binding        : dict — the retired RuntimeBinding
            new_binding        : dict — the replacement RuntimeBinding
            cutover_at         : str — ISO-8601 UTC timestamp of cutover
            position_lineage   : dict — lineage record per §7 semantics
        """
        current_binding_id = request.get("current_binding_id", "")
        if not current_binding_id:
            raise RuntimeManagerError("current_binding_id is required for rollback.")

        action_type = request.get("action_type", "replace")
        try:
            RollbackActionType(action_type)
        except ValueError:
            raise RuntimeManagerError(
                f"Unknown rollback action_type={action_type!r}. "
                f"Must be one of {[e.value for e in RollbackActionType]}."
            )

        replacement_start_paused = request.get("replacement_start_paused", False)

        # Verify the current binding exists and is not already terminal
        old_binding = self._store.require(current_binding_id)
        if old_binding.is_terminal():
            raise RuntimeManagerError(
                f"Cannot roll back binding {current_binding_id!r}: "
                f"already in terminal state {old_binding.status!r}."
            )

        cutover_at = utc_now()

        # Build the replacement deploy request
        deploy_req: Dict[str, Any] = {
            "plan_id": request.get("replacement_plan_id", ""),
            "plan_status": request.get("replacement_plan_status", "approved"),
            "target_stage": request.get(
                "replacement_deployment_mode", old_binding.deployment_mode
            ),
            "artifact_id": request.get("replacement_artifact_id", ""),
            "artifact_version": request.get("replacement_artifact_version", ""),
            "capital_pool_id": old_binding.capital_pool_id,
            "persona_capital_binding_id": request.get(
                "replacement_persona_capital_binding_id", ""
            ),
            "persona_capital_binding_status": request.get(
                "replacement_persona_capital_binding_status", "active"
            ),
            "allowed_deployment_scope": request.get("replacement_allowed_deployment_scope", ""),
            "loader_checks_passed": request.get("loader_checks_passed", True),
            "runtime_id": request.get("replacement_runtime_id"),
            "rollback_parent": current_binding_id,
            "rollback_action_type": action_type,
        }

        if action_type == RollbackActionType.REPLACE.value:
            # Hot-swap per L1 §3.1 and §9: replacement binding must exist before the
            # old binding is retired (cutover boundary = create new + retire old, in
            # that order).  The single-runtime guard is bypassed only for this specific
            # deploy() call via the per-call _allow_cutover_bypass flag so that concurrent
            # deploy() calls on other threads still see the full guard.
            # Per §8: old binding core fields are not rewritten; only status -> retired.
            new_binding = self.deploy(deploy_req, _allow_cutover_bypass=True)
            self._store.retire(current_binding_id, retired_at=cutover_at)

        elif action_type == RollbackActionType.PAUSE_THEN_REPLACE.value:
            # Step 1: Drain — active → pending_pause → paused
            if old_binding.status == RuntimeBindingStatus.ACTIVE.value:
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                )
                self._store.transition_status(
                    current_binding_id, RuntimeBindingStatus.PAUSED.value
                )
            elif old_binding.status not in (
                RuntimeBindingStatus.PENDING_PAUSE.value,
                RuntimeBindingStatus.PAUSED.value,
            ):
                raise RuntimeManagerError(
                    f"pause_then_replace requires the current binding to be active, "
                    f"pending_pause, or paused; current status={old_binding.status!r}."
                )
            # Step 2: Create replacement while old is paused.
            # single-runtime rule does not fire because the old binding is no longer active.
            new_binding = self.deploy(deploy_req)
            # Step 3: Retire old paused binding post-cutover.
            self._store.retire(current_binding_id, retired_at=cutover_at)

        elif action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value:
            # Step 1: Retire old binding (real position flattening is the execution
            # layer's responsibility; this service records the cutover boundary).
            # Per §3.3 and §9: liquidation / cancel telemetry remains on the old
            # binding/artifact until the runtime is confirmed flat.
            self._store.retire(current_binding_id, retired_at=cutover_at)
            # Step 2: Create replacement, optionally starting in guarded / paused mode.
            new_binding = self.deploy(deploy_req)
            if replacement_start_paused:
                self._store.transition_status(
                    new_binding.binding_id, RuntimeBindingStatus.PENDING_PAUSE.value
                )
                new_binding = self._store.transition_status(
                    new_binding.binding_id, RuntimeBindingStatus.PAUSED.value
                )

        else:
            # Unreachable after enum validation above, but kept for safety.
            raise RuntimeManagerError(f"Unhandled action_type: {action_type!r}")

        # Re-fetch the retired old binding for its final state.
        retired_old = self._store.require(current_binding_id)

        # Position lineage record per ROLLBACK_AND_POSITION_SEMANTICS.md §7.
        # opened_by_artifact_id: immutable — reflects original position opener.
        # current_managed_by_binding_id: updated to new binding after cutover.
        opened_by_artifact_id = request.get("opened_by_artifact_id", old_binding.artifact_id)
        lineage_note: str
        if action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value:
            lineage_note = (
                "liquidate_then_replace: positions must be confirmed zero before "
                "current_managed_by_binding_id transfer is valid per "
                "ROLLBACK_AND_POSITION_SEMANTICS.md §7. "
                "opened_by_artifact_id is immutable."
            )
        else:
            lineage_note = (
                "current_managed_by_binding_id updated to new binding after cutover. "
                "opened_by_artifact_id is immutable per ROLLBACK_AND_POSITION_SEMANTICS.md §7."
            )

        # §7: current_managed_by_binding_id may only update once the replacement
        # binding is the active owner.  For liquidate_then_replace with
        # replacement_start_paused=True the new binding starts paused, so the
        # position is not yet under active management — keep the old binding ID
        # as the current owner until zero-position is confirmed and the operator
        # activates the new binding.
        if (
            action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value
            and replacement_start_paused
        ):
            lineage_current_owner = current_binding_id
        else:
            lineage_current_owner = new_binding.binding_id

        position_lineage = {
            "opened_by_artifact_id": opened_by_artifact_id,
            "prev_binding_id": current_binding_id,
            "prev_artifact_id": old_binding.artifact_id,
            "new_binding_id": new_binding.binding_id,
            "new_artifact_id": new_binding.artifact_id,
            "current_managed_by_binding_id": lineage_current_owner,
            "cutover_at": cutover_at,
            "note": lineage_note,
        }

        return {
            "action_type": action_type,
            "old_binding": retired_old.to_dict(),
            "new_binding": new_binding.to_dict(),
            "cutover_at": cutover_at,
            "position_lineage": position_lineage,
        }

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
