"""
Idempotent plan-to-binding dispatch adapter for the deployment saga.

LOOP-AUTO-DEP-002: Add runtime-manager dispatch adapter.

This adapter is the single integration point that the deployment saga (DEP-001
outbox consumer) calls to create or verify a RuntimeBinding for an approved
DeploymentPlan.

Design invariants:
- If the saga already has a binding_id set, the adapter verifies the binding
  exists via the runtime-manager and returns it without calling deploy() again.
- On a new dispatch, the adapter calls RuntimeManagerClient.deploy() and
  returns a structured DispatchResult with the outcome classified as
  ``success``, ``retryable_error``, or ``terminal_error``.
- The adapter does not write saga state; callers must invoke
  record_binding_created or record_failure on the saga service based on the
  returned outcome.

Error classification:
- ``terminal_error``: pre-condition violations, invalid plan state, or
  HTTP 4xx from runtime-manager.  Retrying without operator intervention
  will not help.
- ``retryable_error``: transient network failures, runtime-manager
  unavailable, or HTTP 5xx/429.  A subsequent attempt may succeed.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

_RM_DIR = str(Path(__file__).resolve().parent.parent / "runtime-manager")
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_RM_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from runtime_manager_client import RuntimeManagerClient, RuntimeManagerClientError  # noqa: E402

# HTTP status codes from the runtime-manager that are transient and retryable.
_RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# RuntimeManagerError keywords that indicate a permanent blocking condition
# (not a transient failure).  Matching any of these in the error message
# classifies the failure as terminal.
_TERMINAL_KEYWORDS = (
    "must be approved",
    "must be 'approved'",
    "not 'approved'",
    "not 'active'",
    "allowed_deployment_scope",
    "loader_checks_passed must be True",
    "Deploy is blocked",
    "not a valid DeploymentMode",
    "is required when rollback_parent",
    "activation is blocked",
)


class DispatchOutcome:
    SUCCESS = "success"
    RETRYABLE_ERROR = "retryable_error"
    TERMINAL_ERROR = "terminal_error"


@dataclass
class DispatchResult:
    """Structured result returned by dispatch_to_runtime_manager()."""

    outcome: str
    binding_id: Optional[str] = None
    binding: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    idempotent_replay: bool = False

    def succeeded(self) -> bool:
        return self.outcome == DispatchOutcome.SUCCESS

    def is_retryable(self) -> bool:
        return self.outcome == DispatchOutcome.RETRYABLE_ERROR

    def is_terminal(self) -> bool:
        return self.outcome == DispatchOutcome.TERMINAL_ERROR


def _classify_client_error(exc: RuntimeManagerClientError) -> str:
    """Return ``TERMINAL_ERROR`` or ``RETRYABLE_ERROR`` for a client exception."""
    if exc.error_code == "RUNTIME_MANAGER_UNAVAILABLE":
        return DispatchOutcome.RETRYABLE_ERROR
    status = exc.status_code
    if status is not None:
        if status in _RETRYABLE_HTTP_STATUS_CODES:
            return DispatchOutcome.RETRYABLE_ERROR
        # 4xx (except 429) are permanent rejections.
        return DispatchOutcome.TERMINAL_ERROR
    # No HTTP status — could be a connection error or local validation error.
    msg = str(exc).lower()
    for keyword in _TERMINAL_KEYWORDS:
        if keyword.lower() in msg:
            return DispatchOutcome.TERMINAL_ERROR
    return DispatchOutcome.RETRYABLE_ERROR


def _classify_runtime_manager_error(exc: Exception) -> str:
    """Return ``TERMINAL_ERROR`` or ``RETRYABLE_ERROR`` for a non-client exception."""
    msg = str(exc).lower()
    for keyword in _TERMINAL_KEYWORDS:
        if keyword.lower() in msg:
            return DispatchOutcome.TERMINAL_ERROR
    return DispatchOutcome.RETRYABLE_ERROR


def _build_deploy_request(
    *,
    saga: Dict[str, Any],
    deploy_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Compose the RuntimeManagerClient.deploy() request from saga + caller context.

    ``saga`` must contain the fields written by DeploymentSagaStore:
      plan_id, artifact_id, artifact_version, capital_pool_id,
      target_stage, strategy_id, approval_decision_id

    ``deploy_context`` must contain:
      persona_capital_binding_id   — active PersonaCapitalBinding id
      persona_capital_binding_status — must be "active"
      allowed_deployment_scope     — must be >= target_stage
      loader_checks_passed         — must be True (bool)

    Optional fields in deploy_context:
      plan_status (default "approved"), runtime_id, idempotency_key,
      metadata, rollback_parent, rollback_action_type
    """
    return {
        "plan_id": saga["plan_id"],
        "plan_status": deploy_context.get("plan_status") or "approved",
        "target_stage": saga["target_stage"],
        "artifact_id": saga["artifact_id"],
        "artifact_version": saga["artifact_version"],
        "capital_pool_id": saga["capital_pool_id"],
        "strategy_id": saga.get("strategy_id") or "",
        "persona_capital_binding_id": deploy_context["persona_capital_binding_id"],
        "persona_capital_binding_status": deploy_context["persona_capital_binding_status"],
        "allowed_deployment_scope": deploy_context["allowed_deployment_scope"],
        "loader_checks_passed": deploy_context["loader_checks_passed"],
        "runtime_id": deploy_context.get("runtime_id"),
        "idempotency_key": deploy_context.get("idempotency_key"),
        "rollback_parent": deploy_context.get("rollback_parent"),
        "rollback_action_type": deploy_context.get("rollback_action_type"),
        "metadata": deploy_context.get("metadata") or {},
    }


def dispatch_to_runtime_manager(
    *,
    saga: Dict[str, Any],
    deploy_context: Dict[str, Any],
    client: Optional[RuntimeManagerClient] = None,
) -> DispatchResult:
    """Create or verify a RuntimeBinding for an approved DeploymentSaga.

    Parameters
    ----------
    saga:
        Dict representation of the current DeploymentSaga (saga.to_dict() or
        the raw dict stored in the saga store).  Must include plan_id,
        artifact_id, artifact_version, capital_pool_id, target_stage,
        and strategy_id.
    deploy_context:
        Caller-supplied context that the deployment saga received from the
        approval workflow.  Required keys: persona_capital_binding_id,
        persona_capital_binding_status, allowed_deployment_scope,
        loader_checks_passed.  Optional: plan_status, runtime_id,
        idempotency_key, metadata, rollback_parent, rollback_action_type.
    client:
        RuntimeManagerClient instance.  If None, one is constructed using
        environment defaults (PANTHEON_RUNTIME_MANAGER_URL).

    Returns
    -------
    DispatchResult
        outcome is one of DispatchOutcome.SUCCESS, RETRYABLE_ERROR,
        TERMINAL_ERROR.  On SUCCESS, binding_id and binding are set.
        On idempotent replay (binding already existed), idempotent_replay=True.
    """
    if client is None:
        client = RuntimeManagerClient()

    existing_binding_id: Optional[str] = saga.get("binding_id")

    # --- Idempotency check: saga already has a binding ---
    if existing_binding_id:
        try:
            binding = client.get(existing_binding_id)
        except RuntimeManagerClientError as exc:
            outcome = _classify_client_error(exc)
            return DispatchResult(
                outcome=outcome,
                error_message=str(exc),
                error_code=exc.error_code or f"HTTP_{exc.status_code}",
            )
        except Exception as exc:  # noqa: BLE001
            return DispatchResult(
                outcome=_classify_runtime_manager_error(exc),
                error_message=str(exc),
                error_code="RUNTIME_MANAGER_ERROR",
            )

        if binding is None:
            # Binding id recorded in saga but not found in runtime-manager.
            # This is a permanent inconsistency — operator must intervene.
            return DispatchResult(
                outcome=DispatchOutcome.TERMINAL_ERROR,
                binding_id=existing_binding_id,
                error_message=(
                    f"Saga has binding_id={existing_binding_id!r} but runtime-manager "
                    "returned 404; state is inconsistent and requires operator review."
                ),
                error_code="BINDING_NOT_FOUND_AFTER_SAGA_RECORDED",
            )

        return DispatchResult(
            outcome=DispatchOutcome.SUCCESS,
            binding_id=existing_binding_id,
            binding=binding,
            idempotent_replay=True,
        )

    # --- New dispatch: call deploy() ---
    try:
        request = _build_deploy_request(saga=saga, deploy_context=deploy_context)
        binding = client.deploy(request)
    except RuntimeManagerClientError as exc:
        outcome = _classify_client_error(exc)
        return DispatchResult(
            outcome=outcome,
            error_message=str(exc),
            error_code=exc.error_code or (f"HTTP_{exc.status_code}" if exc.status_code else "CLIENT_ERROR"),
        )
    except Exception as exc:  # noqa: BLE001
        # Includes RuntimeManagerError raised by the local service path.
        outcome = _classify_runtime_manager_error(exc)
        return DispatchResult(
            outcome=outcome,
            error_message=str(exc),
            error_code="RUNTIME_MANAGER_ERROR",
        )

    binding_id = binding.get("binding_id") if isinstance(binding, dict) else None
    return DispatchResult(
        outcome=DispatchOutcome.SUCCESS,
        binding_id=binding_id,
        binding=binding if isinstance(binding, dict) else None,
    )
