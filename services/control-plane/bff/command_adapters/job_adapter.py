"""Job Domain Command Adapter.

Routes ``JobAction`` commands for the unified Management ``Job`` projection
(see ``ports/job_read.py``, ``ports/research_commands.py``, and
``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md``).

U10B (RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001) implements real backend
execution for research orchestrator runs within the 13-path scope:
- ``cancel``: triggers ``cancel_run`` on research orchestrator, records
  cancellation fence, and returns authoritative receipt with canceled status
- ``retry``: triggers ``retry_run`` on research orchestrator, verifies
  eligibility (failed/canceled/timeout only), creates linked attempt lineage,
  and returns domain receipt linking new job ID
- ``archive`` / ``promote`` on orchestrator runs fail closed with
  ``ActionUnavailableError`` (requires Operator Policy Decision / Governance)
- Actions on worker-gateway, training-session, source-ingest, policy-learning
  fail closed citing their explicit follow-up tasks (GW-STOP-FENCE-001,
  TS-CANCEL-001, SI-CANCEL-001, PL-CANCEL-001, GOV-PROMOTE-001)
- OpenClaw workflow jobs fail closed with HTTP 400 (permanently read-only)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
)

try:
    from services.control_plane.bff.ports.research_commands import (
        ResearchCommandConflictError,
        ResearchCommandError,
        ResearchCommandNotFoundError,
        ResearchCommandUnavailableError,
        ResearchCommandsPort,
        create_research_commands_port,
    )
except (ImportError, ValueError):
    from ..ports.research_commands import (
        ResearchCommandConflictError,
        ResearchCommandError,
        ResearchCommandNotFoundError,
        ResearchCommandUnavailableError,
        ResearchCommandsPort,
        create_research_commands_port,
    )

log = logging.getLogger(__name__)

_JOB_PREFIX_TO_SOURCE = {
    "job-worker-": "research_worker_gateway",
    "job-orchestrator-": "research_orchestrator",
    "job-trainer-": "training_session",
    "job-ingest-": "source_ingestion",
    "job-policy-": "policy_learning",
    "job-openclaw-": "openclaw_gateway_adapter",
}


def _source_for_job_id(job_id: str) -> str:
    for prefix, source in _JOB_PREFIX_TO_SOURCE.items():
        if job_id.startswith(prefix):
            return source
    return "unknown"


class JobCommandAdapter(DomainCommandAdapter):
    """Adapter for the unified ``Job`` projection's action dispatch."""

    _HANDLED_COMMANDS = {"JobAction"}
    _HANDLED_ENTITIES = {"job"}

    def __init__(
        self,
        *,
        research_commands_port: Optional[ResearchCommandsPort] = None,
    ) -> None:
        self._research_commands_port = research_commands_port

    def _get_commands_port(self) -> ResearchCommandsPort:
        if self._research_commands_port is None:
            self._research_commands_port = create_research_commands_port()
        return self._research_commands_port

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or "").strip()
        job_id = str(params.get("job_id") or params.get("entity_id") or "").strip()
        if not job_id:
            raise ValueError("JobAction requires job_id.")

        source = _source_for_job_id(job_id)
        normalized_action = action_id.strip().lower()

        if source == "openclaw_gateway_adapter":
            _raise_job_action_unavailable(
                action_id=action_id,
                job_id=job_id,
                source=source,
                reason="owner_operation_not_in_scope",
                detail=(
                    "OpenClaw workflow jobs are permanently read-only diagnostics for the product BFF; "
                    "all write actions (cancel/retry/archive/promote) are excluded by architecture."
                ),
            )
        elif source == "unknown":
            _raise_job_action_unavailable(
                action_id=action_id,
                job_id=job_id,
                source=source,
                reason="owner_operation_not_in_scope",
                detail=f"Job id {job_id!r} does not match any qualified job source prefix.",
            )

        if source == "research_orchestrator":
            native_id = job_id[len("job-orchestrator-"):]
            if normalized_action == "cancel":
                return self._execute_orchestrator_cancel(
                    command_id=command_id,
                    job_id=job_id,
                    native_id=native_id,
                    action_id=action_id,
                    params=params,
                )
            if normalized_action == "retry":
                return self._execute_orchestrator_retry(
                    command_id=command_id,
                    job_id=job_id,
                    native_id=native_id,
                    action_id=action_id,
                    params=params,
                )
            if normalized_action == "archive":
                _raise_job_action_unavailable(
                    action_id=action_id,
                    job_id=job_id,
                    source=source,
                    reason="owner_operation_unsupported",
                    detail=(
                        "Research orchestrator run archive requires an explicit Operator Decision on "
                        "artifact retention schedules and archival purge authority (D-JOBS §1/§3)."
                    ),
                    downstream_status=409,
                )
            if normalized_action == "promote":
                _raise_job_action_unavailable(
                    action_id=action_id,
                    job_id=job_id,
                    source=source,
                    reason="owner_operation_unsupported",
                    detail=(
                        "Research orchestrator run promotion requires Governance review with signed "
                        "operator authorization, target stage binding, and registry readback verification. "
                        "Direct promotion without Governance gate is rejected (D-JOBS §1/§3)."
                    ),
                    downstream_status=409,
                )
            _raise_job_action_unavailable(
                action_id=action_id,
                job_id=job_id,
                source=source,
                reason="owner_operation_unsupported",
                detail=f"Action {action_id!r} is not a recognized action for research orchestrator runs.",
                downstream_status=422,
            )

        # Other 4 sources: worker-gateway, trainer, ingest, policy
        detail_map = {
            "research_worker_gateway": (
                f"Worker gateway action {action_id!r} requires worker process kill and cancellation "
                "fence implementation in services/research-worker-gateway/, tracked as GW-STOP-FENCE-001."
            ),
            "training_session": (
                f"Training session action {action_id!r} requires preview evaluation cancellation "
                "in services/training-session/, tracked as TS-CANCEL-001."
            ),
            "source_ingestion": (
                f"Source ingestion action {action_id!r} requires connector extraction cancellation "
                "in services/source_ingestion/, tracked as SI-CANCEL-001."
            ),
            "policy_learning": (
                f"Policy learning action {action_id!r} requires worker process halt in "
                "services/policy-learning/, tracked as PL-CANCEL-001."
            ),
        }
        detail = detail_map.get(
            source,
            f"No verified backend action exists yet for source={source!r}, action={action_id!r}.",
        )
        _raise_job_action_unavailable(
            action_id=action_id,
            job_id=job_id,
            source=source,
            reason="owner_operation_unsupported",
            detail=detail,
        )

    def _execute_orchestrator_cancel(
        self,
        *,
        command_id: str,
        job_id: str,
        native_id: str,
        action_id: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        port = self._get_commands_port()
        reason = str(params.get("reason") or "Canceled via Management Job Action")
        actor_id = str(params.get("actor_id") or "operator")
        canceled_at = params.get("canceled_at")

        try:
            result = port.cancel_run(
                native_id,
                reason=reason,
                actor_id=actor_id,
                canceled_at=canceled_at,
            )
        except ResearchCommandNotFoundError as exc:
            err = ActionUnavailableError(
                f"Cannot cancel {job_id!r}: {exc.message}",
                action_id=action_id,
                entity_type="Job",
                error_code="JOB_NOT_FOUND",
                suggestion="Verify the job_id exists in research orchestrator runs.",
                retryable=False,
                downstream_status=404,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc
        except ResearchCommandConflictError as exc:
            err = ActionUnavailableError(
                f"Cannot cancel {job_id!r}: {exc.message}",
                action_id=action_id,
                entity_type="Job",
                error_code="JOB_NOT_CANCELABLE",
                suggestion="Only active research runs (queued, dispatched, running) can be canceled.",
                retryable=False,
                downstream_status=409,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc
        except ResearchCommandUnavailableError as exc:
            err = ActionUnavailableError(
                f"Cannot cancel {job_id!r}: research orchestrator is unreachable ({exc.message}).",
                action_id=action_id,
                entity_type="Job",
                error_code="RESEARCH_ORCHESTRATOR_UNAVAILABLE",
                suggestion="Check research-orchestrator service health and network connectivity.",
                retryable=True,
                downstream_status=503,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc
        except ResearchCommandError as exc:
            err = ActionUnavailableError(
                f"Failed to cancel {job_id!r}: {exc.message}",
                action_id=action_id,
                entity_type="Job",
                error_code="JOB_CANCEL_FAILED",
                suggestion="Check research orchestrator service logs.",
                retryable=False,
                downstream_status=exc.status_code,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Job",
            entity_id=job_id,
            action_id=action_id,
            status=result.get("status") or "canceled",
            dispatch_path="research_orchestrator.cancel_run",
            domain_receipt=result,
            authoritative_readback={
                "job_id": job_id,
                "status": result.get("status"),
                "cancellation_fence": result.get("cancellation_fence"),
                "completed_at": result.get("completed_at"),
            },
            extra={
                "job_id": job_id,
                "native_id": native_id,
                "cancellation_fence": result.get("cancellation_fence"),
            },
        )

    def _execute_orchestrator_retry(
        self,
        *,
        command_id: str,
        job_id: str,
        native_id: str,
        action_id: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        port = self._get_commands_port()
        actor_id = str(params.get("actor_id") or "operator")
        idempotency_key = params.get("idempotency_key")
        requested_at = params.get("requested_at")

        try:
            result = port.retry_run(
                native_id,
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                requested_at=requested_at,
            )
        except ResearchCommandNotFoundError as exc:
            err = ActionUnavailableError(
                f"Cannot retry {job_id!r}: {exc.message}",
                action_id=action_id,
                entity_type="Job",
                error_code="JOB_NOT_FOUND",
                suggestion="Verify the job_id exists in research orchestrator runs.",
                retryable=False,
                downstream_status=404,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc
        except ResearchCommandConflictError as exc:
            err = ActionUnavailableError(
                f"Cannot retry {job_id!r}: {exc.message}",
                action_id=action_id,
                entity_type="Job",
                error_code="JOB_NOT_RETRYABLE",
                suggestion="Only terminal research runs (failed, canceled, timeout) can be retried.",
                retryable=False,
                downstream_status=409,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc
        except ResearchCommandUnavailableError as exc:
            err = ActionUnavailableError(
                f"Cannot retry {job_id!r}: research orchestrator is unreachable ({exc.message}).",
                action_id=action_id,
                entity_type="Job",
                error_code="RESEARCH_ORCHESTRATOR_UNAVAILABLE",
                suggestion="Check research-orchestrator service health and network connectivity.",
                retryable=True,
                downstream_status=503,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc
        except ResearchCommandError as exc:
            err = ActionUnavailableError(
                f"Failed to retry {job_id!r}: {exc.message}",
                action_id=action_id,
                entity_type="Job",
                error_code="JOB_RETRY_FAILED",
                suggestion="Check research orchestrator service logs.",
                retryable=False,
                downstream_status=exc.status_code,
            )
            err.job_id = job_id  # type: ignore[attr-defined]
            err.source = "research_orchestrator"  # type: ignore[attr-defined]
            err.reason = "owner_operation_unsupported"  # type: ignore[attr-defined]
            raise err from exc

        new_run_id = result.get("run_id") or result.get("id")
        new_job_id = f"job-orchestrator-{new_run_id}"

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Job",
            entity_id=job_id,
            action_id=action_id,
            status=result.get("status") or "queued",
            dispatch_path="research_orchestrator.retry_run",
            domain_receipt=result,
            authoritative_readback={
                "job_id": new_job_id,
                "status": result.get("status"),
                "attempt_number": result.get("attempt_number"),
                "parent_run_id": result.get("parent_run_id"),
            },
            extra={
                "previous_job_id": job_id,
                "new_job_id": new_job_id,
                "attempt_number": result.get("attempt_number"),
                "parent_run_id": result.get("parent_run_id"),
                "root_run_id": result.get("root_run_id"),
            },
        )


def _raise_job_action_unavailable(
    *,
    action_id: str,
    job_id: str,
    source: str,
    reason: str,
    detail: str,
    downstream_status: Optional[int] = None,
) -> None:
    """Raise ``ActionUnavailableError`` carrying ``action_id``/``job_id``/``reason``."""
    if downstream_status is None:
        downstream_status = 400 if reason == "owner_operation_not_in_scope" else 503
    error = ActionUnavailableError(
        f"Job action {action_id!r} on {job_id!r} ({source}) is not available: {detail}",
        action_id=action_id,
        entity_type="Job",
        error_code="JOB_ACTION_UNAVAILABLE",
        suggestion="Submit an action supported by the domain owner or check job state.",
        retryable=False,
        downstream_status=downstream_status,
    )
    error.job_id = job_id  # type: ignore[attr-defined]
    error.source = source  # type: ignore[attr-defined]
    error.reason = reason  # type: ignore[attr-defined]
    raise error

