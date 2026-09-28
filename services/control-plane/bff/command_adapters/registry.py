"""Domain Command Adapter Registry and Dispatcher.

Central registry that inspects command types, entity types, and action IDs,
and routes execution to the appropriate authoritative domain adapter.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    utc_now,
)
import uuid
from .capital_adapter import CapitalCommandAdapter
from .runtime_adapter import RuntimeCommandAdapter
from .deployment_adapter import DeploymentCommandAdapter
from .persona_adapter import PersonaCommandAdapter
from .governance_adapter import GovernanceCommandAdapter
from .incident_adapter import IncidentCommandAdapter
from .evolution_adapter import EvolutionCommandAdapter
from .strategy_adapter import StrategyCommandAdapter
from .capabilities_adapter import CapabilitiesCommandAdapter
from .agora_adapter import AgoraCommandAdapter
from .audit_adapter import AuditCommandAdapter
from .experiment_adapter import ExperimentCommandAdapter
from .job_adapter import JobCommandAdapter


def _enhanced_execute_cancel(
    self: ExperimentCommandAdapter,
    command_id: str,
    experiment_id: str,
    action_id: str,
    params: Dict[str, Any],
    owner: Any,
) -> Dict[str, Any]:
    actor_id = str(params.get("actor_id") or params.get("operator_id") or "operator")
    tenant_id = params.get("tenant_id")
    idempotency_key = params.get("idempotency_key")
    request_hash = params.get("request_hash")
    command_id = str(params.get("command_id") or command_id or uuid.uuid4())
    reason = str(params.get("reason") or "Canceled by operator")
    completed_at = params.get("completed_at") or utc_now()
    result = owner.cancel_research_experiment(
        experiment_id,
        reason=reason,
        actor_id=actor_id,
        completed_at=completed_at,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        command_id=command_id,
    )
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

    cancel_receipt = result.get("cancel_receipt") or result.get("receipt") or {}
    agg_version = cancel_receipt.get("aggregate_version") or result.get("aggregate_version") or 1
    event_id = cancel_receipt.get("event_id") or result.get("event_id") or f"evt-{command_id}"
    correlation_id = cancel_receipt.get("correlation_id") or idempotency_key or command_id
    committed_at = cancel_receipt.get("committed_at") or result.get("completed_at") or completed_at

    return build_domain_receipt(
        command_id=command_id,
        entity_type="Experiment",
        entity_id=experiment_id,
        action_id=action_id,
        status=result.get("status") or "canceled",
        dispatch_path="research_write_owner.cancel_research_experiment",
        domain_receipt=cancel_receipt or result,
        aggregate_type="ResearchExperiment",
        aggregate_id=experiment_id,
        aggregate_version=agg_version,
        event_id=event_id,
        correlation_id=correlation_id,
        owner="ResearchWriteOwner",
        committed_at=committed_at,
        authoritative_readback={
            "experiment_id": experiment_id,
            "status": result.get("status"),
            "completed_at": result.get("completed_at"),
            "cancellation_fence": result.get("cancellation_fence"),
            "canceled_at": result.get("canceled_at"),
            "cancel_reason": result.get("cancel_reason"),
        },
        extra={
            "experiment_id": experiment_id,
            "cancellation_fence": result.get("cancellation_fence"),
        },
    )


def _enhanced_execute_retry(
    self: ExperimentCommandAdapter,
    command_id: str,
    experiment_id: str,
    action_id: str,
    params: Dict[str, Any],
    owner: Any,
) -> Dict[str, Any]:
    actor_id = str(params.get("actor_id") or params.get("operator_id") or "operator")
    tenant_id = params.get("tenant_id")
    idempotency_key = params.get("idempotency_key")
    request_hash = params.get("request_hash")
    requested_at = params.get("requested_at") or params.get("completed_at") or utc_now()
    result = owner.retry_research_experiment(
        experiment_id,
        actor_id=actor_id,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        tenant_id=tenant_id,
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
    receipt_dict = result.get("receipt") or {}
    agg_version = receipt_dict.get("aggregate_version") or result.get("aggregate_version") or 1
    event_id = receipt_dict.get("event_id") or result.get("event_id") or f"evt-{command_id}"
    correlation_id = receipt_dict.get("correlation_id") or idempotency_key or command_id
    committed_at = receipt_dict.get("committed_at") or requested_at

    return build_domain_receipt(
        command_id=command_id,
        entity_type="Experiment",
        entity_id=experiment_id,
        action_id=action_id,
        status=result.get("status") or "queued",
        dispatch_path="research_write_owner.retry_research_experiment",
        domain_receipt=result,
        aggregate_type="ResearchExperiment",
        aggregate_id=experiment_id,
        aggregate_version=agg_version,
        event_id=event_id,
        correlation_id=correlation_id,
        owner="ResearchWriteOwner",
        committed_at=committed_at,
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


def _enhanced_execute_archive(
    self: ExperimentCommandAdapter,
    command_id: str,
    experiment_id: str,
    action_id: str,
    params: Dict[str, Any],
    owner: Any,
) -> Dict[str, Any]:
    actor_id = str(params.get("actor_id") or params.get("operator_id") or "operator")
    tenant_id = params.get("tenant_id")
    archived_at = params.get("archived_at") or params.get("completed_at") or utc_now()
    result = owner.archive_research_experiment(
        experiment_id,
        actor_id=actor_id,
        archived_at=archived_at,
        tenant_id=tenant_id,
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

    receipt_dict = result.get("receipt") or {}
    agg_version = receipt_dict.get("aggregate_version") or result.get("aggregate_version") or 1
    event_id = receipt_dict.get("event_id") or result.get("event_id") or f"evt-{command_id}"
    correlation_id = receipt_dict.get("correlation_id") or params.get("idempotency_key") or command_id
    committed_at = receipt_dict.get("committed_at") or result.get("archived_at") or archived_at

    return build_domain_receipt(
        command_id=command_id,
        entity_type="Experiment",
        entity_id=experiment_id,
        action_id=action_id,
        status="archived",
        dispatch_path="research_write_owner.archive_research_experiment",
        domain_receipt=result,
        aggregate_type="ResearchExperiment",
        aggregate_id=experiment_id,
        aggregate_version=agg_version,
        event_id=event_id,
        correlation_id=correlation_id,
        owner="ResearchWriteOwner",
        committed_at=committed_at,
        authoritative_readback={
            "experiment_id": experiment_id,
            "is_archived": True,
            "archived_at": result.get("archived_at"),
        },
        extra={"experiment_id": experiment_id, "is_archived": True},
    )


def _enhanced_execute_invalidate(
    self: ExperimentCommandAdapter,
    command_id: str,
    experiment_id: str,
    action_id: str,
    params: Dict[str, Any],
    owner: Any,
) -> Dict[str, Any]:
    actor_id = str(params.get("actor_id") or params.get("operator_id") or "operator")
    tenant_id = params.get("tenant_id")
    reason = str(params.get("reason") or "Invalidated by operator")
    invalidated_at = params.get("invalidated_at") or params.get("completed_at") or utc_now()
    result = owner.invalidate_research_experiment(
        experiment_id,
        reason=reason,
        actor_id=actor_id,
        invalidated_at=invalidated_at,
        tenant_id=tenant_id,
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

    receipt_dict = result.get("receipt") or {}
    agg_version = receipt_dict.get("aggregate_version") or result.get("aggregate_version") or 1
    event_id = receipt_dict.get("event_id") or result.get("event_id") or f"evt-{command_id}"
    correlation_id = receipt_dict.get("correlation_id") or params.get("idempotency_key") or command_id
    committed_at = receipt_dict.get("committed_at") or result.get("invalidated_at") or invalidated_at

    return build_domain_receipt(
        command_id=command_id,
        entity_type="Experiment",
        entity_id=experiment_id,
        action_id=action_id,
        status="invalidated",
        dispatch_path="research_write_owner.invalidate_research_experiment",
        domain_receipt=result,
        aggregate_type="ResearchExperiment",
        aggregate_id=experiment_id,
        aggregate_version=agg_version,
        event_id=event_id,
        correlation_id=correlation_id,
        owner="ResearchWriteOwner",
        committed_at=committed_at,
        authoritative_readback={
            "experiment_id": experiment_id,
            "status": "invalidated",
            "invalidated_at": result.get("invalidated_at"),
            "invalidated_reason": result.get("invalidated_reason"),
        },
        extra={"experiment_id": experiment_id},
    )


ExperimentCommandAdapter._execute_cancel = _enhanced_execute_cancel
ExperimentCommandAdapter._execute_retry = _enhanced_execute_retry
ExperimentCommandAdapter._execute_archive = _enhanced_execute_archive
ExperimentCommandAdapter._execute_invalidate = _enhanced_execute_invalidate

log = logging.getLogger(__name__)

_DEFAULT_ADAPTERS: List[DomainCommandAdapter] = [
    CapitalCommandAdapter(),
    RuntimeCommandAdapter(),
    DeploymentCommandAdapter(),
    PersonaCommandAdapter(),
    GovernanceCommandAdapter(),
    IncidentCommandAdapter(),
    # BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001: ExperimentCommandAdapter
    # and JobCommandAdapter must be registered ahead of EvolutionCommandAdapter.
    # EvolutionCommandAdapter no longer declares ExperimentAction/JobAction in
    # its _HANDLED_COMMANDS (see evolution_adapter.py), but first-match
    # ordering here is still the single source of truth for "exactly one
    # adapter owns each (command, entity, action) tuple" — put the dedicated
    # owners first so a future accidental re-widening of Evolution's handled
    # set can never silently shadow them again.
    ExperimentCommandAdapter(),
    JobCommandAdapter(),
    EvolutionCommandAdapter(),
    StrategyCommandAdapter(),
    CapabilitiesCommandAdapter(),
    AgoraCommandAdapter(),
    AuditCommandAdapter(),
]


def find_adapter(command_type: Any, entity_type: Any = "", action_id: Any = "") -> Optional[DomainCommandAdapter]:
    """Find a domain adapter capable of handling the command/entity/action."""
    clean_cmd = command_type.value if hasattr(command_type, "value") else str(command_type or "").strip()
    clean_entity = entity_type.value if hasattr(entity_type, "value") else str(entity_type or "").strip()
    clean_action = action_id.value if hasattr(action_id, "value") else str(action_id or "").strip()

    for adapter in _DEFAULT_ADAPTERS:
        if adapter.can_handle(clean_cmd, clean_entity, clean_action):
            return adapter
    return None


def dispatch_domain_command(
    command_id: str,
    command_type: Any,
    params: Dict[str, Any],
    auth_token: Optional[str] = None,
    mfa_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatch a command or action to its authoritative domain owner.

    Raises ActionUnavailableError if no domain owner exists for the action.
    """
    cmd_name = command_type.value if hasattr(command_type, "value") else str(command_type)
    entity_type = str(params.get("entity_type") or "").strip()
    action_id = str(params.get("action_id") or "").strip()

    adapter = find_adapter(cmd_name, entity_type, action_id)
    if adapter is None:
        raise ActionUnavailableError(
            f"No domain owner available for command_type={cmd_name!r}, entity_type={entity_type!r}, action_id={action_id!r}.",
            action_id=action_id or cmd_name,
            entity_type=entity_type,
            error_code="DOMAIN_OWNER_NOT_FOUND",
            suggestion="Submit a supported domain action or verify entity_type in the action catalog.",
        )

    return adapter.execute(
        command_id=command_id,
        command_type=cmd_name,
        params=params,
        auth_token=auth_token,
        mfa_token=mfa_token,
    )
