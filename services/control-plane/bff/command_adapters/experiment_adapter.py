"""Research Experiment Domain Command Adapter.

Routes ``ExperimentAction`` commands to the authoritative
``ResearchWriteOwner`` (Postgres-backed, ``research.research_experiments``).
See ``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md``
§2 "Strict distinction of domain aggregates" and §3 "Owner contracts, gaps,
and action semantics".

U10A scope (BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001) is read-plumbing
and minimal write wiring: only ``cancel`` has a real, already-existing owner
mutation (``ResearchWriteOwner.cancel_research_experiment``). The design
mentions four further experiment obligations (``invalidated``,
``attached_to_review``, ``archived``, ``retry``) — none of them have a
corresponding owner mutation method today, and inventing one here would
silently broaden this task's scope beyond what was accepted. Every action
other than ``cancel`` fails closed with ``ActionUnavailableError`` and is
never faked as a 200/202 "executed" receipt.
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

_UNAVAILABLE_ACTIONS = {"invalidated", "attached_to_review", "archived", "retry"}


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

        if normalized_action != "cancel":
            if normalized_action in _UNAVAILABLE_ACTIONS:
                reason_suffix = (
                    "the corresponding ResearchWriteOwner mutation does not exist yet; "
                    "implementing it is a separate, explicitly-scoped follow-up task"
                )
            else:
                reason_suffix = "it is not a recognized experiment action"
            raise ActionUnavailableError(
                f"Experiment action {action_id!r} on {experiment_id!r} is not available: {reason_suffix}.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="EXPERIMENT_ACTION_UNAVAILABLE",
                suggestion="Only 'cancel' is implemented for experiments in U10A.",
                retryable=False,
                downstream_status=422,
            )

        owner = self._get_owner()
        if owner is None:
            raise ActionUnavailableError(
                f"Cannot cancel experiment {experiment_id!r}: ResearchWriteOwner (Postgres) is not configured.",
                action_id=action_id,
                entity_type="Experiment",
                error_code="RESEARCH_WRITE_OWNER_UNCONFIGURED",
                suggestion="Configure DATABASE_URL or RESEARCH_STORE_DSN for the research write owner.",
                retryable=True,
                downstream_status=503,
            )

        completed_at = params.get("completed_at") or utc_now()
        result = owner.cancel_research_experiment(experiment_id, completed_at=completed_at)
        if result is None:
            # Either the experiment does not exist, or it is already in a
            # terminal (non-cancelable) state. ResearchWriteOwner does not
            # distinguish the two in its return value, so this is reported as
            # a business-rule conflict rather than a fabricated success.
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
            },
            extra={"experiment_id": experiment_id},
        )
