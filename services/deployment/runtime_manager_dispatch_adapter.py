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
from typing import Any, Dict, Mapping, Optional

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
    # Single-runtime rule violation from RuntimeBindingStore.create() in local mode.
    # Retrying without retiring the existing binding will always fail.
    "single-runtime rule violation",
    "retire the existing binding",
)

_ACTIVE_BINDING_STATUS = "active"


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
      sponsor_persona_id             — exact plan sponsor for governance binding
      persona_capital_binding_id   — active PersonaCapitalBinding id
      persona_capital_binding_status — must be "active"
      allowed_deployment_scope     — must be >= target_stage
      loader_checks_passed         — must be True (bool)

    Optional fields in deploy_context:
      plan_status (default "approved"), runtime_id, idempotency_key,
      metadata, promotion_gate, rollback_parent, rollback_action_type
    """
    return {
        "plan_id": saga["plan_id"],
        "approval_decision_id": saga["approval_decision_id"],
        "plan_status": deploy_context.get("plan_status") or "approved",
        "target_stage": saga["target_stage"],
        "artifact_id": saga["artifact_id"],
        "artifact_version": saga["artifact_version"],
        "capital_pool_id": saga["capital_pool_id"],
        "strategy_id": saga.get("strategy_id") or "",
        "sponsor_persona_id": deploy_context["sponsor_persona_id"],
        "persona_capital_binding_id": deploy_context["persona_capital_binding_id"],
        "persona_capital_binding_status": deploy_context["persona_capital_binding_status"],
        "allowed_deployment_scope": deploy_context["allowed_deployment_scope"],
        "loader_checks_passed": deploy_context["loader_checks_passed"],
        "runtime_id": deploy_context.get("runtime_id"),
        "idempotency_key": deploy_context.get("idempotency_key"),
        "rollback_parent": deploy_context.get("rollback_parent"),
        "rollback_action_type": deploy_context.get("rollback_action_type"),
        "metadata": deploy_context.get("metadata") or {},
        "promotion_gate": deploy_context.get("promotion_gate") or {},
    }


def validate_authoritative_readback(
    *,
    saga: Dict[str, Any],
    binding: Dict[str, Any],
    expected_binding_id: str,
    expected_persona_capital_binding_id: str | None = None,
    expected_sponsor_persona_id: str | None = None,
    expected_authority_report: Mapping[str, Any] | None = None,
) -> Optional[str]:
    """Return a fail-closed mismatch description for a RuntimeBinding readback.

    A successful POST response is only a dispatch receipt.  The deployment saga
    may advance after a separate GET proves that runtime-manager persisted the
    expected active binding and that its immutable deployment identity matches
    the approved saga.
    """
    expected = {
        "binding_id": expected_binding_id,
        "plan_id": saga.get("plan_id"),
        "capital_pool_id": saga.get("capital_pool_id"),
        "artifact_id": saga.get("artifact_id"),
        "artifact_version": saga.get("artifact_version"),
        "deployment_mode": saga.get("target_stage"),
        "execution_mode": saga.get("target_stage"),
        "status": _ACTIVE_BINDING_STATUS,
    }
    if expected_persona_capital_binding_id:
        expected["persona_capital_binding_id"] = expected_persona_capital_binding_id
    mismatches = [
        f"{field} expected {expected_value!r}, got {binding.get(field)!r}"
        for field, expected_value in expected.items()
        if binding.get(field) != expected_value
    ]
    metadata = binding.get("metadata")
    if not isinstance(metadata, Mapping):
        mismatches.append("RuntimeBinding.metadata is required for authority readback")
        return "; ".join(mismatches)
    if metadata.get("strategy_id") != saga.get("strategy_id"):
        mismatches.append(
            f"metadata.strategy_id expected {saga.get('strategy_id')!r}, "
            f"got {metadata.get('strategy_id')!r}"
        )
    attestation = metadata.get("authoritative_loader_attestation")
    if not isinstance(attestation, Mapping):
        mismatches.append("metadata.authoritative_loader_attestation is required")
        return "; ".join(mismatches)
    expected_attestation = {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": saga.get("plan_id"),
        "target_stage": saga.get("target_stage"),
        "artifact_id": saga.get("artifact_id"),
        "artifact_version": saga.get("artifact_version"),
        "strategy_id": saga.get("strategy_id"),
        "approval_decision_id": saga.get("approval_decision_id"),
        "capital_pool_id": saga.get("capital_pool_id"),
    }
    if expected_persona_capital_binding_id:
        expected_attestation["persona_capital_binding_id"] = (
            expected_persona_capital_binding_id
        )
    if expected_sponsor_persona_id:
        expected_attestation["sponsor_persona_id"] = expected_sponsor_persona_id
    mismatches.extend(
        f"authority attestation {field} expected {expected_value!r}, "
        f"got {attestation.get(field)!r}"
        for field, expected_value in expected_attestation.items()
        if attestation.get(field) != expected_value
    )
    digest_fields = (
        "deployment_plan_sha256",
        "registry_entry_sha256",
        "approval_decision_sha256",
        "capital_pool_sha256",
        "capital_admissibility_sha256",
        "persona_capital_binding_sha256",
    )
    for field in digest_fields:
        value = str(attestation.get(field) or "")
        if not value.startswith("sha256:") or len(value) != 71:
            mismatches.append(f"authority attestation {field} is missing or invalid")
        if (
            expected_authority_report is not None
            and expected_authority_report.get(field) != attestation.get(field)
        ):
            mismatches.append(
                f"authority attestation {field} differs from pre-dispatch proof"
            )
    return "; ".join(mismatches) or None


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
        idempotency_key, metadata, promotion_gate, rollback_parent,
        rollback_action_type.
    client:
        RuntimeManagerClient instance.  If None, one is constructed using
        the required remote PANTHEON_RUNTIME_MANAGER_URL. Durable dispatch
        never falls back to an in-process RuntimeManagerService.

    Returns
    -------
    DispatchResult
        outcome is one of DispatchOutcome.SUCCESS, RETRYABLE_ERROR,
        TERMINAL_ERROR.  On SUCCESS, binding_id and binding are set.
        On idempotent replay (binding already existed), idempotent_replay=True.
    """
    if client is None:
        client = RuntimeManagerClient(require_remote=True)

    deploy_metadata = deploy_context.get("metadata")
    expected_authority_report = (
        deploy_metadata.get("authoritative_loader_attestation")
        if isinstance(deploy_metadata, Mapping)
        else None
    )
    if not isinstance(expected_authority_report, Mapping):
        return DispatchResult(
            outcome=DispatchOutcome.TERMINAL_ERROR,
            error_message=(
                "canonical authoritative_loader_attestation is required before "
                "runtime-manager dispatch"
            ),
            error_code="DEPLOY_AUTHORITY_REPORT_REQUIRED",
        )

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

        mismatch = validate_authoritative_readback(
            saga=saga,
            binding=binding,
            expected_binding_id=existing_binding_id,
            expected_persona_capital_binding_id=deploy_context.get(
                "persona_capital_binding_id"
            ),
            expected_sponsor_persona_id=deploy_context.get("sponsor_persona_id"),
            expected_authority_report=expected_authority_report,
        )
        if mismatch:
            return DispatchResult(
                outcome=DispatchOutcome.TERMINAL_ERROR,
                binding_id=existing_binding_id,
                binding=binding,
                error_message=f"RuntimeBinding authoritative readback mismatch: {mismatch}",
                error_code="BINDING_READBACK_MISMATCH",
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
    if not binding_id:
        return DispatchResult(
            outcome=DispatchOutcome.TERMINAL_ERROR,
            error_message="runtime-manager deploy response did not include binding_id",
            error_code="DEPLOY_RESPONSE_MISSING_BINDING_ID",
        )

    # A POST body is a receipt, not authoritative post-state.  Read the
    # RuntimeBinding back from its sole write owner before reporting success.
    try:
        authoritative = client.get(binding_id)
    except RuntimeManagerClientError as exc:
        return DispatchResult(
            outcome=_classify_client_error(exc),
            binding_id=binding_id,
            error_message=str(exc),
            error_code=exc.error_code
            or (f"HTTP_{exc.status_code}" if exc.status_code else "READBACK_CLIENT_ERROR"),
        )
    except Exception as exc:  # noqa: BLE001
        return DispatchResult(
            outcome=_classify_runtime_manager_error(exc),
            binding_id=binding_id,
            error_message=str(exc),
            error_code="BINDING_READBACK_ERROR",
        )

    if authoritative is None:
        return DispatchResult(
            outcome=DispatchOutcome.RETRYABLE_ERROR,
            binding_id=binding_id,
            error_message=(
                f"runtime-manager accepted binding_id={binding_id!r} but authoritative "
                "GET readback did not find it"
            ),
            error_code="BINDING_READBACK_PENDING",
        )

    mismatch = validate_authoritative_readback(
        saga=saga,
        binding=authoritative,
        expected_binding_id=binding_id,
        expected_persona_capital_binding_id=deploy_context.get(
            "persona_capital_binding_id"
        ),
        expected_sponsor_persona_id=deploy_context.get("sponsor_persona_id"),
        expected_authority_report=expected_authority_report,
    )
    if mismatch:
        return DispatchResult(
            outcome=DispatchOutcome.TERMINAL_ERROR,
            binding_id=binding_id,
            binding=authoritative,
            error_message=f"RuntimeBinding authoritative readback mismatch: {mismatch}",
            error_code="BINDING_READBACK_MISMATCH",
        )

    return DispatchResult(
        outcome=DispatchOutcome.SUCCESS,
        binding_id=binding_id,
        binding=authoritative,
    )
