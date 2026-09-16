"""Research Experiment Domain Command Adapter.

Routes ``ExperimentAction`` commands to the authoritative
``ResearchWriteOwner`` (Postgres-backed, ``research.research_experiments``).
See ``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md``
§2 "Strict distinction of domain aggregates" and §3 "Owner contracts, gaps,
and action semantics".

U10B (RESEARCH-JOBS-ACTIONS-CLOSURE-CORRECTIVE-001) implements real backend
execution for experiment actions:
- ``cancel``: triggers ``ResearchWriteOwner.cancel_research_experiment``,
  setting status="canceled" and recording ``cancellation_fence``
- ``retry``: triggers ``ResearchWriteOwner.retry_research_experiment``,
  verifying terminal state, incrementing attempt_number, and linking lineage
- ``archive`` / ``archived``: triggers ``ResearchWriteOwner.archive_research_experiment``,
  setting ``is_archived=True`` for retention visibility without physical deletion
- ``invalidate`` / ``invalidated``: triggers ``ResearchWriteOwner.invalidate_research_experiment``,
  marking invalid with reason
- ``promote``: fails closed with 409 (Governance review tokens required under GOV-PROMOTE-001)
- other unhandled actions fail closed with ``ActionUnavailableError``
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    utc_now,
)

log = logging.getLogger(__name__)


class ExperimentCommandAdapter(DomainCommandAdapter):
    """Adapter for ``Experiment`` (``ResearchExperiment``) lifecycle actions."""

    _HANDLED_COMMANDS = {"ExperimentAction"}
    _HANDLED_ENTITIES = {"experiment", "researchexperiment", "research-experiment"}

    def __init__(self, *, research_write_owner_factory: Optional[Any] = None) -> None:
        self._research_write_owner_factory = research_write_owner_factory
        self._research_write_owner: Optional[Any] = None
        self._research_write_owner_resolved = False

    def _get_owner(self) -> Optional[Any]:
        if self._research_write_owner is not None:
            return self._research_write_owner
        if self._research_write_owner_resolved:
            return None
        self._research_write_owner_resolved = True
        try:
            if self._research_write_owner_factory is not None:
                self._research_write_owner = self._research_write_owner_factory()
            else:
                from services.research.write_owner import build_research_write_owner

                self._research_write_owner = build_research_write_owner()
        except Exception as exc:  # noqa: BLE001 - any failure means "unavailable"
            log.warning("ResearchWriteOwner unavailable for ExperimentCommandAdapter: %s", exc)
            self._research_write_owner = None
        return self._research_write_owner

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
        experiment_id = str(
            params.get("experiment_id") or params.get("entity_id") or ""
        ).strip()
        normalized_action = action_id.strip().lower()

        if not experiment_id:
            raise ValueError("ExperimentAction requires experiment_id.")

        owner = self._get_owner()
        if owner is None:
            raise ActionUnavailableError(
                f"Cannot execute {action_id!r} on experiment {experiment_id!r}: ResearchWriteOwner (Postgres) is not configured.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="RESEARCH_WRITE_OWNER_UNCONFIGURED",
                suggestion="Configure DATABASE_URL or RESEARCH_STORE_DSN for the research write owner.",
                retryable=True,
                downstream_status=503,
            )

        if normalized_action == "cancel":
            return self._execute_cancel(command_id, experiment_id, action_id, params, owner)
        if normalized_action == "retry":
            return self._execute_retry(command_id, experiment_id, action_id, params, owner)
        if normalized_action in {"archive", "archived"}:
            return self._execute_archive(command_id, experiment_id, action_id, params, owner)
        if normalized_action in {"invalidate", "invalidated"}:
            return self._execute_invalidate(command_id, experiment_id, action_id, params, owner)
        if normalized_action == "promote":
            raise ActionUnavailableError(
                f"Experiment {experiment_id!r} promotion requires Governance review tokens and target registry verification "
                "(GOV-PROMOTE-001). Direct promotion without Governance gate is rejected.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_PROMOTION_UNSUPPORTED",
                suggestion="Submit experiment through the Governance promotion review workflow (GOV-PROMOTE-001).",
                retryable=False,
                downstream_status=409,
            )
        if normalized_action == "attached_to_review":
            raise ActionUnavailableError(
                f"Experiment action {action_id!r} on {experiment_id!r} is not available: "
                "attached_to_review requires review package binding and is not directly executable as a state transition.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_ACTION_UNAVAILABLE",
                suggestion="Attach experiment through the formal Review submission workflow.",
                retryable=False,
                downstream_status=422,
            )

        raise ActionUnavailableError(
            f"Experiment action {action_id!r} on {experiment_id!r} is not available: it is not a recognized experiment action.",
            action_id=action_id,
            entity_type="Experiment",
            error_code="EXPERIMENT_ACTION_UNAVAILABLE",
            suggestion="Supported actions are 'cancel', 'retry', 'archive', and 'invalidate'.",
            retryable=False,
            downstream_status=422,
        )

    def _execute_cancel(
        self,
        command_id: str,
        experiment_id: str,
        action_id: str,
        params: Dict[str, Any],
        owner: Any,
    ) -> Dict[str, Any]:
        completed_at = params.get("completed_at") or utc_now()
        result = owner.cancel_research_experiment(experiment_id, completed_at=completed_at)
        if result is None:
            if hasattr(owner, "get_research_experiment") and owner.get_research_experiment(experiment_id) is None:
                raise ActionUnavailableError(
                    f"Experiment {experiment_id!r} could not be canceled: it does not exist.",
                    action_id=action_id,
                    entity_type="Experiment",
                    error_code="EXPERIMENT_NOT_FOUND",
                    suggestion="Verify that the experiment_id exists in ResearchWriteOwner.",
                    retryable=False,
                    downstream_status=404,
                )
            raise ActionUnavailableError(
                f"Experiment {experiment_id!r} could not be canceled: it does not exist or is not "
                "in a cancelable state (queued/running).",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_NOT_CANCELABLE",
                suggestion="Only experiments in 'queued' or 'running' state can be canceled.",
                retryable=False,
                downstream_status=409,
            )

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Experiment",
            entity_id=experiment_id,
            action_id=action_id,
            status=result.get("status") or "canceled",
            dispatch_path="research_write_owner.cancel_research_experiment",
            domain_receipt=result,
            authoritative_readback={
                "experiment_id": experiment_id,
                "status": result.get("status"),
                "completed_at": result.get("completed_at"),
                "cancellation_fence": result.get("cancellation_fence"),
            },
            extra={
                "experiment_id": experiment_id,
                "cancellation_fence": result.get("cancellation_fence"),
            },
        )

    def _execute_retry(
        self,
        command_id: str,
        experiment_id: str,
        action_id: str,
        params: Dict[str, Any],
        owner: Any,
    ) -> Dict[str, Any]:
        actor_id = str(params.get("actor_id") or "operator")
        idempotency_key = params.get("idempotency_key")
        requested_at = params.get("requested_at") or params.get("completed_at") or utc_now()
        result = owner.retry_research_experiment(
            experiment_id,
            actor_id=actor_id,
            requested_at=requested_at,
            idempotency_key=idempotency_key,
        )
        if result is None:
            if hasattr(owner, "get_research_experiment") and owner.get_research_experiment(experiment_id) is None:
                raise ActionUnavailableError(
                    f"Experiment {experiment_id!r} could not be retried: it does not exist.",
                    action_id=action_id,
                    entity_type="Experiment",
                    error_code="EXPERIMENT_NOT_FOUND",
                    suggestion="Verify that the experiment_id exists in ResearchWriteOwner.",
                    retryable=False,
                    downstream_status=404,
                )
            raise ActionUnavailableError(
                f"Experiment {experiment_id!r} could not be retried: only experiments in terminal "
                "states ('failed', 'canceled', 'timeout') are eligible for retry.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_NOT_RETRYABLE",
                suggestion="Only experiments in 'failed', 'canceled', or 'timeout' state can be retried.",
                retryable=False,
                downstream_status=409,
            )

        new_exp_id = result.get("experiment_id") or result.get("id")
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Experiment",
            entity_id=experiment_id,
            action_id=action_id,
            status=result.get("status") or "queued",
            dispatch_path="research_write_owner.retry_research_experiment",
            domain_receipt=result,
            authoritative_readback={
                "experiment_id": new_exp_id,
                "status": result.get("status"),
                "attempt_number": result.get("attempt_number"),
                "parent_experiment_id": result.get("parent_experiment_id"),
            },
            extra={
                "previous_experiment_id": experiment_id,
                "new_experiment_id": new_exp_id,
                "attempt_number": result.get("attempt_number"),
                "parent_experiment_id": result.get("parent_experiment_id"),
                "root_experiment_id": result.get("root_experiment_id"),
            },
        )

    def _execute_archive(
        self,
        command_id: str,
        experiment_id: str,
        action_id: str,
        params: Dict[str, Any],
        owner: Any,
    ) -> Dict[str, Any]:
        actor_id = str(params.get("actor_id") or "operator")
        archived_at = params.get("archived_at") or params.get("completed_at") or utc_now()
        result = owner.archive_research_experiment(
            experiment_id,
            actor_id=actor_id,
            archived_at=archived_at,
        )
        if result is None:
            if hasattr(owner, "get_research_experiment") and owner.get_research_experiment(experiment_id) is None:
                raise ActionUnavailableError(
                    f"Experiment {experiment_id!r} could not be archived: it does not exist.",
                    action_id=action_id,
                    entity_type="Experiment",
                    error_code="EXPERIMENT_NOT_FOUND",
                    suggestion="Verify that the experiment_id exists in ResearchWriteOwner.",
                    retryable=False,
                    downstream_status=404,
                )
            raise ActionUnavailableError(
                f"Experiment {experiment_id!r} could not be archived: it is not in an archivable state.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_NOT_ARCHIVABLE",
                suggestion="Only experiments in terminal states can be archived.",
                retryable=False,
                downstream_status=409,
            )

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Experiment",
            entity_id=experiment_id,
            action_id=action_id,
            status="archived",
            dispatch_path="research_write_owner.archive_research_experiment",
            domain_receipt=result,
            authoritative_readback={
                "experiment_id": experiment_id,
                "is_archived": True,
                "archived_at": result.get("archived_at"),
            },
            extra={"experiment_id": experiment_id, "is_archived": True},
        )

    def _execute_invalidate(
        self,
        command_id: str,
        experiment_id: str,
        action_id: str,
        params: Dict[str, Any],
        owner: Any,
    ) -> Dict[str, Any]:
        actor_id = str(params.get("actor_id") or "operator")
        reason = str(params.get("reason") or "Invalidated by operator")
        invalidated_at = params.get("invalidated_at") or params.get("completed_at") or utc_now()
        result = owner.invalidate_research_experiment(
            experiment_id,
            reason=reason,
            actor_id=actor_id,
            invalidated_at=invalidated_at,
        )
        if result is None:
            if hasattr(owner, "get_research_experiment") and owner.get_research_experiment(experiment_id) is None:
                raise ActionUnavailableError(
                    f"Experiment {experiment_id!r} could not be invalidated: it does not exist.",
                    action_id=action_id,
                    entity_type="Experiment",
                    error_code="EXPERIMENT_NOT_FOUND",
                    suggestion="Verify that the experiment_id exists in ResearchWriteOwner.",
                    retryable=False,
                    downstream_status=404,
                )
            raise ActionUnavailableError(
                f"Experiment {experiment_id!r} could not be invalidated: it is already canceled or invalidated.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_NOT_INVALIDATABLE",
                suggestion="Only active or completed experiments can be invalidated.",
                retryable=False,
                downstream_status=409,
            )

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Experiment",
            entity_id=experiment_id,
            action_id=action_id,
            status="invalidated",
            dispatch_path="research_write_owner.invalidate_research_experiment",
            domain_receipt=result,
            authoritative_readback={
                "experiment_id": experiment_id,
                "status": "invalidated",
                "invalidated_at": result.get("invalidated_at"),
                "invalidated_reason": result.get("invalidated_reason"),
            },
            extra={"experiment_id": experiment_id},
        )
