"""
BP5-SVC-005: Deployable deployment planning and orchestration service.

This service wraps the canonical control-plane deployment-plan and
deployment-saga domains with a file-backed FastAPI surface so callers can:

- create and validate deployment plans
- bootstrap the DEP-002 deployment saga and first outbox event
- record saga progress and compensation decisions
- inspect outbox / inbox state for replay and idempotency verification
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from services.foundation import (  # noqa: E402
    ActorRef,
    ActorType,
    AuditAction,
    AuthorityScope,
    CommandEnvelope,
    EnvironmentScope,
    ErrorEnvelope,
    ErrorKind,
    IdempotencyRecord,
    PolicyDecision,
    PolicyDecisionValue,
    TraceContext,
    foundation_id,
)
from services.foundation.health import register_fastapi_health_routes  # noqa: E402
from services.deployment.auth import (  # noqa: E402
    AUTHENTICATED_SERVICE_ROLES,
    AuthError,
    AuthenticatedTenant,
    TenantBoundaryError,
    authenticate_tenant,
)
from services.deployment.outbox_lease import (  # noqa: E402
    DeploymentOutboxLeaseStore,
    OutboxLeaseError,
)

from deployment_plan import (  # type: ignore
    DeploymentPlan,
    DeploymentPlanError,
    DeploymentPlanStore,
    DeploymentScale,
    DeploymentStage,
    PlanStatus,
    RollbackRef,
    ScheduleWindow,
    StagePlanner,
)
from deployment_saga import (  # type: ignore
    CompensationDecision,
    DeploymentSaga,
    DeploymentSagaBootstrap,
    DeploymentSagaError,
    DeploymentSagaStore,
    InboxReceipt,
    OutboxStatus,
    OutboxRecord,
    ReceiptStatus,
    SagaStatus,
)
from persona_capital_binding import (  # type: ignore
    PersonaCapitalBinding,
    PersonaCapitalBindingError,
)

try:
    from .models import (
        CompensationDecisionBody,
        ClaimedOutboxRecordBody,
        ClaimOutboxEventsRequest,
        ConsumeOutboxEventRequest,
        CreateDeploymentPlanRequest,
        DeploymentDispatchResponse,
        DeploymentExecutionProjectionBody,
        DeploymentPlanBody,
        DeploymentScaleBody,
        DeploymentPlanSummary,
        DeploymentProjectionReadModelResponse,
        DeploymentOutboxRetryStateBody,
        DeploymentSagaProgressBody,
        DeploymentSagaRetryPolicyBody,
        DeploymentSagaBody,
        DeploymentSagaBootstrapBody,
        DispatchDeploymentPlanRequest,
        FinalizeCompensationRequest,
        InboxReceiptBody,
        OutboxRecordBody,
        OutboxLeaseHealthBody,
        PoolCompatibilityRequest,
        PoolCompatibilityResponse,
        PlanStatusBody,
        RecordBindingCreatedRequest,
        RecordOutboxFailureRequest,
        RecordRuntimeActiveRequest,
        RecordSagaFailureRequest,
        ReplayOutboxEventRequest,
        ReplayOutboxEventResponse,
        SagaProgressStatusBody,
        StagePlannerCheckRequest,
        StagePlannerCheckResponse,
        StrategyReadModelResponse,
        UpdatePlanStatusRequest,
        ValidateDeploymentPlanResponse,
    )
except ImportError:
    from models import (  # type: ignore
        CompensationDecisionBody,
        ClaimedOutboxRecordBody,
        ClaimOutboxEventsRequest,
        ConsumeOutboxEventRequest,
        CreateDeploymentPlanRequest,
        DeploymentDispatchResponse,
        DeploymentExecutionProjectionBody,
        DeploymentPlanBody,
        DeploymentScaleBody,
        DeploymentPlanSummary,
        DeploymentProjectionReadModelResponse,
        DeploymentOutboxRetryStateBody,
        DeploymentSagaProgressBody,
        DeploymentSagaRetryPolicyBody,
        DeploymentSagaBody,
        DeploymentSagaBootstrapBody,
        DispatchDeploymentPlanRequest,
        FinalizeCompensationRequest,
        InboxReceiptBody,
        OutboxRecordBody,
        OutboxLeaseHealthBody,
        PoolCompatibilityRequest,
        PoolCompatibilityResponse,
        PlanStatusBody,
        RecordBindingCreatedRequest,
        RecordOutboxFailureRequest,
        RecordRuntimeActiveRequest,
        RecordSagaFailureRequest,
        ReplayOutboxEventRequest,
        ReplayOutboxEventResponse,
        SagaProgressStatusBody,
        StagePlannerCheckRequest,
        StagePlannerCheckResponse,
        StrategyReadModelResponse,
        UpdatePlanStatusRequest,
        ValidateDeploymentPlanResponse,
    )

log = logging.getLogger(__name__)

_DEPLOYMENT_FOUNDATION_POLICY_VERSION = "deployment.dispatch.v1"
_DEPLOYMENT_FOUNDATION_ROUTE = "deployment.plan.dispatch"
_OUTBOX_DEFAULT_MAX_ATTEMPTS = 3
_OUTBOX_DEFAULT_RETRY_DELAY_SECONDS = 30


def _iso_sort_key(plan: DeploymentPlan) -> str:
    return str(plan.created_at or "")


def _resolve_governance_dir() -> Path:
    base = (
        os.getenv("DEPLOYMENT_DATA_DIR")
        or os.getenv("PANTHEON_GOVERNANCE_DATA_DIR")
        or "/tmp/pantheon/governance"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_runtime_binding_store_path() -> Path:
    explicit = os.getenv("PANTHEON_RUNTIME_BINDING_STORE_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    runtime_dir = os.getenv("PANTHEON_RUNTIME_DATA_DIR", "").strip()
    if runtime_dir:
        return Path(runtime_dir).expanduser() / "runtime_bindings.json"
    return Path("/tmp/pantheon/runtime-manager/bindings.json")


DATA_DIR = _resolve_governance_dir()
PLAN_STORE_PATH = DATA_DIR / "deployment_plans.json"
SAGA_STORE_PATH = DATA_DIR / "deployment_sagas.json"
OUTBOX_LEASE_STORE_PATH = DATA_DIR / "deployment_outbox_leases.json"
APPROVAL_STORE_PATH = DATA_DIR / "approval_decisions.json"
RUNTIME_BINDING_STORE_PATH = _resolve_runtime_binding_store_path()
_REGISTRY_SNAPSHOT_ENV = os.getenv("PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH", "").strip()
REGISTRY_SNAPSHOT_PATH = Path(_REGISTRY_SNAPSHOT_ENV).expanduser() if _REGISTRY_SNAPSHOT_ENV else None


def _resolve_capital_pool_store_path() -> Path | None:
    explicit = os.getenv("PANTHEON_CAPITAL_POOL_STORE_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    data_dir = (
        os.getenv("CAPITAL_DATA_DIR")
        or os.getenv("DEPLOYMENT_DATA_DIR")
        or os.getenv("PANTHEON_GOVERNANCE_DATA_DIR")
        or "/tmp/pantheon/governance"
    )
    return Path(data_dir).expanduser() / "capital_pools.json"


def _resolve_persona_binding_store_path() -> Path | None:
    explicit = os.getenv("PANTHEON_PERSONA_BINDING_STORE_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    data_dir = (
        os.getenv("CAPITAL_DATA_DIR")
        or os.getenv("DEPLOYMENT_DATA_DIR")
        or os.getenv("PANTHEON_GOVERNANCE_DATA_DIR")
        or "/tmp/pantheon/governance"
    )
    return Path(data_dir).expanduser() / "persona_capital_bindings.json"


CAPITAL_POOL_STORE_PATH = _resolve_capital_pool_store_path()
PERSONA_BINDING_STORE_PATH = _resolve_persona_binding_store_path()

store = DeploymentPlanStore(str(PLAN_STORE_PATH))
saga_store = DeploymentSagaStore(str(SAGA_STORE_PATH))
outbox_lease_store = DeploymentOutboxLeaseStore(OUTBOX_LEASE_STORE_PATH)


def _metadata_tenant_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        tenant_id = str(value.get("tenant_id") or "").strip()
        return tenant_id or None
    return None


def _plan_tenant_id(plan: DeploymentPlan | Mapping[str, Any]) -> str | None:
    metadata = (
        plan.get("metadata")
        if isinstance(plan, Mapping)
        else getattr(plan, "metadata", None)
    )
    return _metadata_tenant_id(metadata)


def _saga_tenant_id(saga: DeploymentSaga | Mapping[str, Any]) -> str | None:
    metadata = (
        saga.get("metadata")
        if isinstance(saga, Mapping)
        else getattr(saga, "metadata", None)
    )
    return _metadata_tenant_id(metadata)


def _require_tenant_match(
    *,
    expected_tenant_id: str,
    actual_tenant_id: str | None,
    object_label: str,
) -> None:
    if not actual_tenant_id:
        raise HTTPException(
            status_code=409,
            detail=f"{object_label} has no authoritative tenant_id.",
        )
    if actual_tenant_id != expected_tenant_id:
        raise HTTPException(status_code=404, detail=f"{object_label} not found.")


def _request_identity(request: Request) -> AuthenticatedTenant:
    identity = getattr(request.state, "deployment_identity", None)
    if not isinstance(identity, AuthenticatedTenant):
        raise HTTPException(status_code=401, detail="Authenticated tenant is required.")
    return identity


def _outbox_lease_required() -> bool:
    return (
        os.getenv("PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED", "true")
        .strip()
        .lower()
        not in {"0", "false", "no"}
    )


def _require_plan_access(plan_id: str, identity: AuthenticatedTenant) -> DeploymentPlan:
    plan = planner_service.get_plan(plan_id)
    _require_tenant_match(
        expected_tenant_id=identity.tenant_id,
        actual_tenant_id=_plan_tenant_id(plan),
        object_label=f"DeploymentPlan '{plan_id}'",
    )
    return plan


def _require_saga_access(
    saga_id: str, identity: AuthenticatedTenant
) -> DeploymentSaga:
    saga = orchestration_service.get_saga(saga_id)
    _require_tenant_match(
        expected_tenant_id=identity.tenant_id,
        actual_tenant_id=_saga_tenant_id(saga),
        object_label=f"DeploymentSaga '{saga_id}'",
    )
    return saga


def _outbox_event_tenant_id(record: OutboxRecord) -> str | None:
    saga = saga_store.get(record.event.aggregate_id)
    return _saga_tenant_id(saga) if saga is not None else None


def _require_outbox_access(
    event_id: str, identity: AuthenticatedTenant
) -> OutboxRecord:
    record = next(
        (
            item
            for item in saga_store.outbox_records()
            if item.event.event_id == event_id
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Outbox event '{event_id}' not found.")
    _require_tenant_match(
        expected_tenant_id=identity.tenant_id,
        actual_tenant_id=_outbox_event_tenant_id(record),
        object_label=f"Outbox event '{event_id}'",
    )
    return record


def _outbox_retry_due(record: OutboxRecord) -> bool:
    if not record.next_retry_at:
        return True
    try:
        next_retry = datetime.fromisoformat(
            str(record.next_retry_at).replace("Z", "+00:00")
        )
    except ValueError:
        return True
    if next_retry.tzinfo is None:
        next_retry = next_retry.replace(tzinfo=timezone.utc)
    return next_retry <= datetime.now(timezone.utc)


class DeploymentPlannerService:
    """Thin service layer over StagePlanner + file-backed stores."""

    def __init__(
        self,
        *,
        plan_store: DeploymentPlanStore,
        approval_store_path: Path,
        registry_snapshot_path: Path | None = None,
        registry_reader=None,
        approval_reader=None,
    ) -> None:
        self.plan_store = plan_store
        self.approval_store_path = approval_store_path
        self.registry_snapshot_path = registry_snapshot_path
        self.registry_reader = registry_reader
        self.approval_reader = approval_reader
        self.planner = StagePlanner()

    def create_plan(
        self,
        request: CreateDeploymentPlanRequest,
        *,
        persist: bool,
        actor_id: str,
        tenant_id: str,
    ) -> DeploymentPlan:
        registry_entry = self._resolve_registry_entry(request)
        approval_decision = self._resolve_approval_decision(request, registry_entry, tenant_id)
        if registry_entry.get('owner_tenant') != tenant_id:
            raise DeploymentPlanError('Registry artifact belongs to a different tenant')
        if registry_entry.get('artifact_state') != 'approved' or registry_entry.get('approval_decision_id') != request.approval_decision_id:
            raise DeploymentPlanError('Registry requires artifact_state=approved and matching approval decision reference')
        approval_tenant_id = str(approval_decision.get("tenant_id") or "").strip()
        if not approval_tenant_id:
            raise DeploymentPlanError(
                f"ApprovalDecision '{request.approval_decision_id}' has no authoritative tenant_id"
            )
        if approval_tenant_id != tenant_id:
            raise DeploymentPlanError(
                f"ApprovalDecision '{request.approval_decision_id}' belongs to a different tenant"
            )
        metadata = dict(request.metadata or {})
        declared_tenant_id = str(metadata.get("tenant_id") or "").strip()
        if declared_tenant_id and declared_tenant_id != tenant_id:
            raise DeploymentPlanError(
                "DeploymentPlan metadata.tenant_id does not match the authenticated tenant"
            )
        metadata["tenant_id"] = tenant_id
        metadata["authenticated_actor_id"] = actor_id
        capital_pool_id = request.capital_pool_id or str(approval_decision.get("capital_pool_id") or "").strip()
        if not capital_pool_id:
            raise DeploymentPlanError(
                "capital_pool_id is required unless approval_decision.capital_pool_id is present"
            )
        if request.plan_id and self.plan_store.get(request.plan_id) is not None:
            raise DeploymentPlanError(f"DeploymentPlan '{request.plan_id}' already exists")

        schedule_window = (
            ScheduleWindow(**request.schedule_window.model_dump())
            if request.schedule_window is not None
            else None
        )
        scale = (
            DeploymentScale(**request.scale.model_dump())
            if request.scale is not None
            else None
        )
        rollback = (
            RollbackRef(**request.rollback.model_dump(mode="json"))
            if request.rollback is not None
            else None
        )

        plan = self.planner.create_plan(
            plan_id=request.plan_id or f"plan-{uuid.uuid4().hex[:12]}",
            approval_decision_id=request.approval_decision_id,
            approval_decision=approval_decision,
            registry_entry=registry_entry,
            capital_pool_id=capital_pool_id,
            target_stage=request.target_stage.value,
            current_stage=request.current_stage.value if request.current_stage else None,
            created_by=actor_id,
            sponsor_persona_id=request.sponsor_persona_id,
            runtime_config_ref=request.runtime_config_ref,
            binding_id=request.binding_id,
            schedule_window=schedule_window,
            scale=scale,
            rollback=rollback,
            pre_checks=list(request.pre_checks),
            post_checks=list(request.post_checks),
            metadata=metadata,
            supersedes_plan_id=request.supersedes_plan_id,
            status=request.status.value,
            risk_policy=request.risk_policy,
            risk_policy_context=request.risk_policy_context,
        )
        if persist:
            self.plan_store.put(plan)
        return plan

    def list_plans(
        self,
        *,
        tenant_id: str | None = None,
        strategy_id: str | None = None,
        capital_pool_id: str | None = None,
        target_stage: str | None = None,
        status: str | None = None,
    ) -> list[DeploymentPlan]:
        plans = self.plan_store.list_all()
        if tenant_id:
            plans = [plan for plan in plans if _plan_tenant_id(plan) == tenant_id]
        if strategy_id:
            plans = [plan for plan in plans if plan.strategy_id == strategy_id]
        if capital_pool_id:
            plans = [plan for plan in plans if plan.capital_pool_id == capital_pool_id]
        if target_stage:
            plans = [
                plan for plan in plans
                if str(plan.target_stage) == target_stage
                or getattr(plan.target_stage, "value", None) == target_stage
            ]
        if status:
            plans = [
                plan for plan in plans
                if str(plan.status) == status or getattr(plan.status, "value", None) == status
            ]
        return sorted(plans, key=_iso_sort_key, reverse=True)

    def get_plan(self, plan_id: str) -> DeploymentPlan:
        plan = self.plan_store.get(plan_id)
        if plan is None:
            raise DeploymentPlanError(f"DeploymentPlan '{plan_id}' not found")
        return plan

    def update_status(self, plan_id: str, target_status: PlanStatusBody) -> DeploymentPlan:
        plan = self.get_plan(plan_id)
        current_status = PlanStatus(plan.status)
        next_status = PlanStatus(target_status.value)
        if next_status == current_status:
            return plan

        allowed = {
            PlanStatus.DRAFT: {PlanStatus.APPROVED, PlanStatus.REJECTED, PlanStatus.ABORTED},
            PlanStatus.APPROVED: {PlanStatus.EXECUTING, PlanStatus.REJECTED, PlanStatus.ABORTED},
            PlanStatus.EXECUTING: {PlanStatus.EXECUTED, PlanStatus.FAILED, PlanStatus.ABORTED},
            PlanStatus.EXECUTED: set(),
            PlanStatus.ABORTED: set(),
            PlanStatus.REJECTED: set(),
            PlanStatus.FAILED: set(),
        }
        if next_status not in allowed[current_status]:
            allowed_values = ", ".join(sorted(status.value for status in allowed[current_status])) or "<none>"
            raise DeploymentPlanError(
                f"Invalid plan status transition: {current_status.value} -> {next_status.value} "
                f"(expected one of: {allowed_values})"
            )

        plan.status = next_status
        self.plan_store.put(plan)
        return plan

    def check_stage_transition(self, request: StagePlannerCheckRequest) -> StagePlannerCheckResponse:
        current_stage = DeploymentStage(request.current_stage.value)
        target_stage = DeploymentStage(request.target_stage.value)
        rollback_required = target_stage in {
            DeploymentStage.PAPER,
            DeploymentStage.CANARY,
            DeploymentStage.LIVE,
        }
        rollback = (
            RollbackRef(
                target_artifact_id="stage-check-rollback-artifact",
                target_version="0.9.0",
                action_type=request.rollback_action.value,
            )
            if request.rollback_action is not None
            else None
        )
        default_scale: DeploymentScale | None = None
        effective_scale: DeploymentScale | None = (
            DeploymentScale(**request.scale.model_dump())
            if request.scale is not None
            else None
        )
        transition_type = None
        runtime_action = None
        errors: list[str] = []

        try:
            transition_type = self.planner.derive_transition_type(current_stage, target_stage)
            runtime_action = self.planner.default_runtime_action(transition_type, rollback)
            default_scale = self.planner.default_scale(target_stage)
            if effective_scale is None:
                effective_scale = default_scale
        except DeploymentPlanError as exc:
            errors.append(str(exc))

        if transition_type is not None and runtime_action is not None and effective_scale is not None:
            plan = DeploymentPlan(
                plan_id="stage-check-plan",
                approval_decision_id="stage-check-approval",
                artifact_id="stage-check-artifact",
                artifact_version="1.0.0",
                artifact_type="model_artifact",
                strategy_id="stage-check-strategy",
                capital_pool_id="stage-check-pool",
                current_stage=current_stage,
                target_stage=target_stage,
                transition_type=transition_type,
                runtime_action=runtime_action,
                status=PlanStatus.APPROVED,
                created_at="2026-05-16T00:00:00Z",
                scale=effective_scale,
                rollback=rollback,
            )
            errors.extend(plan.validate())

        return StagePlannerCheckResponse(
            ok=not errors,
            current_stage=current_stage.value,
            target_stage=target_stage.value,
            transition_type=_enum_value(transition_type) if transition_type is not None else None,
            runtime_action=_enum_value(runtime_action) if runtime_action is not None else None,
            rollback_required=rollback_required,
            default_scale=(
                DeploymentScaleBody(**default_scale.to_dict())
                if default_scale is not None
                else None
            ),
            effective_scale=(
                DeploymentScaleBody(**effective_scale.to_dict())
                if effective_scale is not None
                else None
            ),
            errors=errors,
        )

    def strategy_read_model(
        self,
        *,
        tenant_id: str,
        strategy_id: str,
        capital_pool_id: str | None = None,
    ) -> StrategyReadModelResponse:
        plans = self.list_plans(
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            capital_pool_id=capital_pool_id,
        )
        latest = plans[0] if plans else None
        executed = next(
            (plan for plan in plans if PlanStatus(plan.status) == PlanStatus.EXECUTED),
            None,
        )
        active = next(
            (
                plan
                for plan in plans
                if PlanStatus(plan.status) in {PlanStatus.APPROVED, PlanStatus.EXECUTING}
            ),
            None,
        )

        current_stage = DeploymentStage.NONE.value
        if executed is not None:
            current_stage = _enum_value(executed.target_stage)
        elif latest is not None:
            current_stage = _enum_value(latest.current_stage)

        plan_summaries = [_plan_summary(plan) for plan in plans]
        return StrategyReadModelResponse(
            strategy_id=strategy_id,
            capital_pool_id=capital_pool_id,
            current_stage=current_stage,
            latest_plan_id=latest.plan_id if latest else None,
            active_plan_id=active.plan_id if active else None,
            latest_target_stage=_enum_value(latest.target_stage) if latest else None,
            latest_transition_type=_enum_value(latest.transition_type) if latest else None,
            latest_status=_enum_value(latest.status) if latest else None,
            plan_count=len(plans),
            plans=plan_summaries,
        )

    def _resolve_registry_entry(self, request: CreateDeploymentPlanRequest) -> Mapping[str, Any]:
        if self.registry_reader is not None:
            return self.registry_reader(request.registry_id)
        import httpx
        from urllib.parse import quote
        url = os.getenv('DEPLOYMENT_REGISTRY_BASE_URL', '').rstrip('/')
        token = os.getenv('DEPLOYMENT_REGISTRY_SERVICE_TOKEN', '')
        if not url or not token:
            raise DeploymentPlanError('Registry owner URL and scoped read principal required')
        try:
            response = httpx.get(url + '/api/registry/entries/' + quote(request.registry_id, safe=''),
                                 headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
                                 timeout=float(os.getenv('DEPLOYMENT_REGISTRY_TIMEOUT_SECONDS', '5')),
                                 follow_redirects=False)
            response.raise_for_status()
            entry = response.json()['entry']
            if not isinstance(entry, dict) or entry.get('registry_id') != request.registry_id:
                raise ValueError('Wrong Registry identity')
            return entry
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise DeploymentPlanError('Registry exact owner read unavailable or malformed') from exc

    def _resolve_approval_decision(self, request, registry_entry, tenant_id) -> Mapping[str, Any]:
        from services.governance.approval_authority import configured_approval_reader, ApprovalInvalid
        try:
            reader = self.approval_reader or configured_approval_reader('deployment')
            return reader.verify(request.approval_decision_id, expected={
                'tenant_id': tenant_id, 'target_type': 'registry_entry',
                'target_id': request.registry_id, 'target_version': registry_entry.get('version'),
                'candidate_digest': registry_entry.get('checksum'),
            }).model_dump()
        except ApprovalInvalid as exc:
            raise DeploymentPlanError(str(exc)) from exc


class DeploymentProjectionReadModelService:
    """Derived-only read model over DeploymentPlan, approval, saga, and runtime state."""

    def __init__(
        self,
        *,
        planner_service: DeploymentPlannerService,
        saga_store: DeploymentSagaStore,
        approval_store_path: Path,
        registry_snapshot_path: Path | None,
        runtime_binding_store_path: Path,
    ) -> None:
        self.planner_service = planner_service
        self.saga_store = saga_store
        self.approval_store_path = approval_store_path
        self.registry_snapshot_path = registry_snapshot_path
        self.runtime_binding_store_path = runtime_binding_store_path

    def list_projections(
        self,
        *,
        tenant_id: str | None = None,
        strategy_id: str | None = None,
        capital_pool_id: str | None = None,
        target_stage: str | None = None,
        status: str | None = None,
    ) -> list[DeploymentProjectionReadModelResponse]:
        return [
            self._build_projection(plan)
            for plan in self.planner_service.list_plans(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                capital_pool_id=capital_pool_id,
                target_stage=target_stage,
                status=status,
            )
        ]

    def get_projection(self, plan_id: str) -> DeploymentProjectionReadModelResponse:
        return self._build_projection(self.planner_service.get_plan(plan_id))

    def _build_projection(self, plan: DeploymentPlan) -> DeploymentProjectionReadModelResponse:
        plan_payload = plan.to_dict()
        approval_decision = self._find_approval_decision(plan.approval_decision_id)
        runtime_binding = self._find_runtime_binding_for_plan(plan.plan_id)
        deployment_saga = self._find_latest_saga_for_plan(plan.plan_id)
        registry_entry = self._find_registry_entry(plan.artifact_id)

        source_status = {
            "deployment_plan": "canonical",
            "approval_decision": "canonical" if approval_decision else "missing",
            "runtime_binding": "canonical" if runtime_binding else "missing",
            "deployment_saga": "canonical" if deployment_saga else "missing",
            "registry_entry": "canonical" if registry_entry else "missing",
        }

        execution_projection = None
        projection_error = None
        if registry_entry is not None:
            try:
                projected = self.planner_service.planner.build_execution_projection(
                    plan,
                    registry_entry,
                )
                execution_projection = DeploymentExecutionProjectionBody(**projected.__dict__)
                source_status["execution_projection"] = "derived"
            except DeploymentPlanError as exc:
                projection_error = str(exc)
                source_status["registry_entry"] = "invalid"
                source_status["execution_projection"] = "invalid_source"
        else:
            source_status["execution_projection"] = "missing_source"

        runtime_stage = _runtime_binding_stage(runtime_binding)
        plan_status = _enum_value(plan.status)
        target_stage = _enum_value(plan.target_stage)
        current_stage = _enum_value(plan.current_stage)
        actual_stage = runtime_stage or (target_stage if plan_status == PlanStatus.EXECUTED.value else current_stage)
        approval_outcome = _approval_outcome(approval_decision)
        approval_state = _approval_state(approval_decision)
        runtime_binding_id = _runtime_binding_id(runtime_binding)
        runtime_id = _runtime_id(runtime_binding)
        runtime_status = _runtime_status(runtime_binding)
        saga_status = _enum_value(deployment_saga.status) if deployment_saga else None
        saga_progress = (
            _saga_progress_body(deployment_saga, self.saga_store.outbox_records())
            if deployment_saga
            else None
        )
        lifecycle_state = _projection_lifecycle_state(
            plan_status=plan_status,
            runtime_status=runtime_status,
            deployment_saga_status=saga_status,
        )
        summary: Dict[str, Any] = {
            "has_approval_authority": self._has_approval_authority(plan, registry_entry, approval_decision),
            "runtime_backing_present": runtime_binding is not None,
            "execution_projection_available": execution_projection is not None,
            "rollback_action_type": _rollback_action_type(plan),
            "scale": plan_payload.get("scale"),
            "created_at": plan_payload.get("created_at"),
        }
        if projection_error:
            summary["execution_projection_error"] = projection_error
        if saga_progress is not None:
            summary["saga_progress_status"] = saga_progress.progress_status.value
            summary["blocked_reason"] = saga_progress.blocked_reason
            summary["retry_state"] = [
                item.model_dump(mode="json", exclude_none=True)
                for item in saga_progress.retry_state
            ]

        return DeploymentProjectionReadModelResponse(
            plan_id=plan.plan_id,
            strategy_id=plan.strategy_id,
            artifact_id=plan.artifact_id,
            artifact_version=plan.artifact_version,
            capital_pool_id=plan.capital_pool_id,
            approval_decision_id=plan.approval_decision_id,
            current_stage=current_stage,
            target_stage=target_stage,
            projected_stage=target_stage,
            actual_stage=actual_stage,
            plan_status=plan_status,
            approval_outcome=approval_outcome,
            approval_state=approval_state,
            runtime_binding_id=runtime_binding_id,
            runtime_id=runtime_id,
            runtime_status=runtime_status,
            deployment_saga_id=deployment_saga.saga_id if deployment_saga else None,
            deployment_saga_status=saga_status,
            deployment_saga_progress=saga_progress,
            lifecycle_state=lifecycle_state,
            source_status=source_status,
            summary=summary,
            plan=_plan_body(plan),
            approval_decision=approval_decision,
            runtime_binding=runtime_binding,
            deployment_saga=_saga_body(deployment_saga) if deployment_saga else None,
            execution_projection=execution_projection,
        )

    def _has_approval_authority(self, plan, entry, decision) -> bool:
        from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid
        if not entry or not decision:
            return False
        try:
            ApprovalEvidence.model_validate(decision).require_valid(expected={
                'tenant_id': _plan_tenant_id(plan), 'target_type': 'registry_entry',
                'target_id': plan.artifact_id, 'target_version': plan.artifact_version,
                'candidate_digest': entry.get('checksum'),
            })
            return True
        except (ApprovalInvalid, ValueError):
            return False

    def _find_approval_decision(self, approval_decision_id: str) -> Optional[Dict[str, Any]]:
        from services.governance.approval_authority import configured_approval_reader, ApprovalInvalid
        try:
            reader = self.planner_service.approval_reader or configured_approval_reader('deployment')
            return reader.get(approval_decision_id).model_dump()
        except ApprovalInvalid:
            return None

    def _find_registry_entry(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        from types import SimpleNamespace
        try:
            return dict(self.planner_service._resolve_registry_entry(SimpleNamespace(registry_id=artifact_id)))
        except DeploymentPlanError:
            return None

    def _find_runtime_binding_for_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        for record in _load_records(self.runtime_binding_store_path):
            if str(record.get("plan_id") or record.get("deployment_plan_id") or "") == plan_id:
                return record
        return None

    def _find_latest_saga_for_plan(self, plan_id: str) -> DeploymentSaga | None:
        sagas = [saga for saga in self.saga_store.list_all() if saga.plan_id == plan_id]
        if not sagas:
            return None
        return sorted(sagas, key=lambda saga: (saga.updated_at, saga.created_at), reverse=True)[0]


_DEPLOYABLE_TARGET_STAGES = {"paper", "canary", "live"}
_SCOPE_ORDER = {"none": 0, "paper": 1, "canary": 2, "live": 3}
_ACTIVE_RUNTIME_STATUSES = {"active"}


class PoolRuntimeCompatibilityService:
    """Read-only preflight over pool, persona binding, and runtime snapshots."""

    def __init__(
        self,
        *,
        capital_pool_store_path: Path | None,
        persona_binding_store_path: Path | None,
        runtime_binding_store_path: Path,
    ) -> None:
        self.capital_pool_store_path = capital_pool_store_path
        self.persona_binding_store_path = persona_binding_store_path
        self.runtime_binding_store_path = runtime_binding_store_path

    def check(self, request: PoolCompatibilityRequest) -> PoolCompatibilityResponse:
        target_stage = request.target_stage.value
        errors: list[str] = []
        warnings: list[str] = []

        pool = self._find_capital_pool(request.capital_pool_id)
        pool_found = pool is not None
        pool_status = _optional_str(pool.get("status")) if pool else None
        pool_active = pool_status == "active" if pool_status else False
        single_runtime_enforced = _truthy(pool.get("single_runtime_enforced"), default=True) if pool else None

        if not pool_found:
            errors.append(f"CapitalPool '{request.capital_pool_id}' not found")
        elif not pool_active:
            errors.append(f"CapitalPool '{request.capital_pool_id}' must be active; got '{pool_status}'")

        if target_stage not in _DEPLOYABLE_TARGET_STAGES:
            errors.append(
                "Pool/runtime compatibility check supports deployment targets "
                "paper, canary, and live"
            )

        persona_binding_found = False
        persona_scope_ok = False
        persona_binding_id: str | None = None
        allowed_deployment_scope: str | None = None

        sponsor_persona_id = str(request.sponsor_persona_id or "").strip()
        if not sponsor_persona_id:
            errors.append("sponsor_persona_id is required for persona binding compatibility")
        elif target_stage in _DEPLOYABLE_TARGET_STAGES:
            candidates = self._active_persona_bindings(
                persona_id=sponsor_persona_id,
                capital_pool_id=request.capital_pool_id,
            )
            persona_binding_found = bool(candidates)
            permitted = []
            for binding in candidates:
                allowed_deployment_scope = _max_scope(
                    allowed_deployment_scope,
                    _optional_str(binding.get("allowed_deployment_scope")),
                )
                try:
                    if PersonaCapitalBinding.from_dict(binding).permits_deployment_to(target_stage):
                        permitted.append(binding)
                except (PersonaCapitalBindingError, ValueError, TypeError) as exc:
                    warnings.append(
                        "Ignored invalid PersonaCapitalBinding "
                        f"{binding.get('binding_id') or '<unknown>'}: {exc}"
                    )
            persona_scope_ok = bool(permitted)
            if permitted:
                chosen = max(
                    permitted,
                    key=lambda binding: _SCOPE_ORDER.get(
                        str(binding.get("allowed_deployment_scope") or "none"),
                        -1,
                    ),
                )
                persona_binding_id = _record_identity(chosen, "binding_id")
                allowed_deployment_scope = _optional_str(chosen.get("allowed_deployment_scope"))
            elif persona_binding_found:
                errors.append(
                    f"No active PersonaCapitalBinding for persona '{sponsor_persona_id}' "
                    f"permits target_stage '{target_stage}'"
                )
            else:
                errors.append(
                    f"No active PersonaCapitalBinding found for persona '{sponsor_persona_id}' "
                    f"and capital pool '{request.capital_pool_id}'"
                )

        active_runtime_bindings = self._active_runtime_bindings(request.capital_pool_id)
        active_runtime_binding_ids = [
            binding_id
            for binding in active_runtime_bindings
            if (binding_id := _record_identity(binding, "binding_id", "id", "runtime_binding_id"))
        ]
        active_runtime_binding_count = len(active_runtime_bindings)

        single_runtime_ok: bool | None
        if single_runtime_enforced is None:
            single_runtime_ok = None
        elif single_runtime_enforced:
            single_runtime_ok = active_runtime_binding_count <= 1
            if active_runtime_binding_count > 1:
                errors.append(
                    f"CapitalPool '{request.capital_pool_id}' violates single-runtime policy: "
                    f"{active_runtime_binding_count} active RuntimeBindings found"
                )
            elif active_runtime_binding_count == 1:
                warnings.append(
                    "CapitalPool already has an active RuntimeBinding; dispatch must use a "
                    "replace, freeze, resume, or rollback path instead of creating a second active binding."
                )
        else:
            single_runtime_ok = True

        if self.runtime_binding_store_path and not self.runtime_binding_store_path.exists():
            warnings.append(
                f"RuntimeBinding store '{self.runtime_binding_store_path}' does not exist; "
                "active runtime count treated as zero."
            )

        return PoolCompatibilityResponse(
            ok=not errors,
            capital_pool_id=request.capital_pool_id,
            target_stage=target_stage,
            pool_found=pool_found,
            pool_status=pool_status,
            pool_active=pool_active,
            single_runtime_enforced=single_runtime_enforced,
            persona_binding_found=persona_binding_found,
            persona_scope_ok=persona_scope_ok,
            persona_binding_id=persona_binding_id,
            allowed_deployment_scope=allowed_deployment_scope,
            active_runtime_binding_count=active_runtime_binding_count,
            active_runtime_binding_ids=active_runtime_binding_ids,
            single_runtime_ok=single_runtime_ok,
            errors=errors,
            warnings=warnings,
        )

    def _find_capital_pool(self, pool_id: str) -> Optional[Dict[str, Any]]:
        if self.capital_pool_store_path is None or not self.capital_pool_store_path.exists():
            return None
        return _load_record(
            self.capital_pool_store_path,
            key_candidates=("pool_id", "capital_pool_id", "id"),
            target_key=pool_id,
        )

    def _active_persona_bindings(self, *, persona_id: str, capital_pool_id: str) -> list[Dict[str, Any]]:
        if self.persona_binding_store_path is None or not self.persona_binding_store_path.exists():
            return []
        return [
            record
            for record in _load_records(self.persona_binding_store_path)
            if str(record.get("persona_id") or "") == persona_id
            and str(record.get("capital_pool_id") or "") == capital_pool_id
            and str(record.get("status") or "").lower() == "active"
        ]

    def _active_runtime_bindings(self, capital_pool_id: str) -> list[Dict[str, Any]]:
        return [
            record
            for record in _load_records(self.runtime_binding_store_path)
            if str(record.get("capital_pool_id") or "") == capital_pool_id
            and str(record.get("status") or "").lower() in _ACTIVE_RUNTIME_STATUSES
        ]


class FoundationDeploymentError(Exception):
    def __init__(self, *, status_code: int, detail: Dict[str, Any]) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail.get("message") or str(detail))


class DeploymentOrchestrationService:
    """Deployment-facing orchestration facade over the canonical DEP-002 store."""

    def __init__(
        self,
        *,
        planner_service: DeploymentPlannerService,
        saga_store: DeploymentSagaStore,
    ) -> None:
        self.planner_service = planner_service
        self.saga_store = saga_store

    def dispatch_plan(
        self,
        plan_id: str,
        request: DispatchDeploymentPlanRequest,
    ) -> tuple[DeploymentPlan, Dict[str, Any], DeploymentSagaBootstrap, bool]:
        plan = self.planner_service.get_plan(plan_id)
        foundation_context = _build_dispatch_foundation_context(plan=plan, request=request)
        existing = self.saga_store.get(request.saga_id or f"deployment-saga-{plan.plan_id}")
        if existing is None and PlanStatus(plan.status) != PlanStatus.APPROVED:
            raise _foundation_policy_denial(
                foundation_context=foundation_context,
                reason=f"DeploymentPlan '{plan_id}' must be approved before dispatch; got '{plan.status}'",
            )

        registry_entry = self._resolve_registry_entry_for_plan(plan)
        projection = self.planner_service.planner.build_execution_projection(plan, registry_entry)

        if existing is not None:
            _ensure_dispatch_replay_matches_foundation(existing, foundation_context)
            first_outbox = self._find_outbox_event(existing.saga_id, sequence_no=1)
            if first_outbox is None:
                raise DeploymentSagaError(
                    f"DeploymentSaga '{existing.saga_id}' exists without the bootstrap outbox event"
                )
            bootstrap = DeploymentSagaBootstrap(saga=existing, outbox_event=first_outbox)
            return plan, projection.__dict__, bootstrap, True

        metadata = dict(request.metadata or {})
        plan_tenant_id = _plan_tenant_id(plan)
        if not plan_tenant_id:
            raise DeploymentSagaError(
                f"DeploymentPlan '{plan.plan_id}' has no authoritative tenant_id"
            )
        declared_tenant_id = str(metadata.get("tenant_id") or "").strip()
        if declared_tenant_id and declared_tenant_id != plan_tenant_id:
            raise DeploymentSagaError(
                "Dispatch metadata.tenant_id does not match DeploymentPlan tenant"
            )
        metadata["tenant_id"] = plan_tenant_id
        metadata["approval_decision_id"] = plan.approval_decision_id
        metadata["correlation_id"] = foundation_context["trace_context"].correlation_id
        if request.workflow_id:
            metadata.setdefault("workflow_id", request.workflow_id)
        if request.source_task_id:
            metadata.setdefault("source_task_id", request.source_task_id)

        foundation_context["idempotency_record"] = foundation_context["idempotency_record"].with_status(
            "succeeded",
            result_ref=f"deployment_saga:{request.saga_id or f'deployment-saga-{plan.plan_id}'}",
        )
        metadata["foundation"] = _serialize_foundation_context(foundation_context)

        bootstrap = self.saga_store.bootstrap_for_plan(
            plan,
            trace_id=foundation_context["trace_context"].trace_id,
            saga_id=request.saga_id,
            metadata=metadata or None,
        )
        return plan, projection.__dict__, bootstrap, False

    def list_sagas(
        self,
        *,
        plan_id: str | None = None,
        status: str | None = None,
    ) -> list[DeploymentSaga]:
        sagas = self.saga_store.list_all()
        if plan_id:
            sagas = [saga for saga in sagas if saga.plan_id == plan_id]
        if status:
            sagas = [saga for saga in sagas if _enum_value(saga.status) == status]
        return sorted(sagas, key=lambda saga: saga.created_at, reverse=True)

    def get_saga(self, saga_id: str) -> DeploymentSaga:
        saga = self.saga_store.get(saga_id)
        if saga is None:
            raise DeploymentSagaError(f"DeploymentSaga '{saga_id}' not found")
        return saga

    def get_saga_progress(self, saga_id: str) -> DeploymentSagaProgressBody:
        saga = self.get_saga(saga_id)
        return _saga_progress_body(
            saga,
            self.saga_store.outbox_records(),
        )

    def get_plan_saga_progress(self, plan_id: str) -> DeploymentSagaProgressBody:
        sagas = [saga for saga in self.saga_store.list_all() if saga.plan_id == plan_id]
        if not sagas:
            raise DeploymentSagaError(f"DeploymentSaga for plan '{plan_id}' not found")
        saga = sorted(sagas, key=lambda item: (item.updated_at, item.created_at), reverse=True)[0]
        return _saga_progress_body(
            saga,
            self.saga_store.outbox_records(),
        )

    def record_binding_created(
        self,
        saga_id: str,
        request: RecordBindingCreatedRequest,
    ) -> OutboxRecord:
        outbox = self.saga_store.record_binding_created(
            saga_id,
            binding_id=request.binding_id,
            runtime_id=request.runtime_id,
            note=request.note,
        )
        saga = self._require_saga(saga_id)
        self._mark_plan_binding_created(
            saga=saga,
            binding_id=request.binding_id,
            runtime_id=request.runtime_id,
        )
        return outbox

    def record_runtime_active(
        self,
        saga_id: str,
        request: RecordRuntimeActiveRequest,
    ) -> OutboxRecord:
        outbox = self.saga_store.record_runtime_active(
            saga_id,
            binding_id=request.binding_id,
            runtime_id=request.runtime_id,
            note=request.note,
        )
        saga = self._require_saga(saga_id)
        self._mark_plan_runtime_active(
            saga=saga,
            binding_id=request.binding_id or saga.binding_id,
            runtime_id=request.runtime_id or saga.runtime_id,
        )
        return outbox

    def record_failure(
        self,
        saga_id: str,
        request: RecordSagaFailureRequest,
    ) -> CompensationDecision:
        return self.saga_store.record_failure(
            saga_id,
            reason=request.reason,
            failed_step=request.failed_step.value if request.failed_step else None,
        )

    def finalize_compensation(
        self,
        saga_id: str,
        request: FinalizeCompensationRequest,
    ) -> OutboxRecord:
        return self.saga_store.finalize_compensation(
            saga_id,
            note=request.note,
            terminal_status=request.terminal_status.value if request.terminal_status else None,
        )

    def list_outbox(
        self,
        *,
        owner_service: str | None = None,
        aggregate_id: str | None = None,
        status: str | None = None,
    ) -> list[OutboxRecord]:
        records = self.saga_store.outbox_records(status=status)
        if owner_service:
            records = [record for record in records if record.owner_service == owner_service]
        if aggregate_id:
            records = [record for record in records if record.event.aggregate_id == aggregate_id]
        return sorted(records, key=lambda record: (record.event.aggregate_id, record.event.sequence_no))

    def list_inbox(
        self,
        *,
        consumer_name: str | None = None,
        aggregate_id: str | None = None,
        status: str | None = None,
    ) -> list[InboxReceipt]:
        receipts = self.saga_store.inbox_receipts(consumer_name=consumer_name)
        if aggregate_id:
            receipts = [receipt for receipt in receipts if receipt.aggregate_id == aggregate_id]
        if status:
            receipts = [receipt for receipt in receipts if _enum_value(receipt.status) == status]
        return sorted(receipts, key=lambda receipt: (receipt.aggregate_id, receipt.processed_at))

    def consume_outbox_event(
        self,
        event_id: str,
        *,
        consumer_name: str,
    ) -> InboxReceipt:
        event = self._find_outbox_event_by_event_id(event_id)
        if event is None:
            raise DeploymentSagaError(f"Outbox event '{event_id}' not found")
        if OutboxStatus(event.status) == OutboxStatus.DEAD_LETTERED:
            raise DeploymentSagaError(
                f"Outbox event '{event_id}' is dead-lettered; replay it before consuming."
            )
        receipt = self.saga_store.consume_event(consumer_name, event.event)
        if ReceiptStatus(receipt.status) in {ReceiptStatus.APPLIED, ReceiptStatus.DUPLICATE}:
            self.saga_store.mark_outbox_published(event_id)
        return receipt

    def record_outbox_failure(
        self,
        event_id: str,
        request: RecordOutboxFailureRequest,
    ) -> OutboxRecord:
        max_attempts = request.max_attempts or _OUTBOX_DEFAULT_MAX_ATTEMPTS
        retry_delay_seconds = (
            request.retry_delay_seconds
            if request.retry_delay_seconds is not None
            else _OUTBOX_DEFAULT_RETRY_DELAY_SECONDS
        )
        return self.saga_store.record_outbox_failure(
            event_id,
            reason=request.reason,
            consumer_name=request.consumer_name,
            retryable=request.retryable,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
        )

    def replay_outbox_event(
        self,
        event_id: str,
        request: ReplayOutboxEventRequest,
    ) -> tuple[OutboxRecord, bool]:
        return self.saga_store.replay_outbox_event(event_id, reason=request.reason)

    def _mark_plan_binding_created(
        self,
        *,
        saga: DeploymentSaga,
        binding_id: str,
        runtime_id: str | None,
    ) -> None:
        plan = self._mutable_plan_copy(saga.plan_id)
        plan.binding_id = binding_id
        plan.status = self._next_plan_status_for_binding_created(plan)
        plan.metadata = self._with_runtime_lifecycle_metadata(
            plan.metadata,
            binding_id=binding_id,
            runtime_id=runtime_id,
            runtime_status=None,
            activated_stage=None,
        )
        self._validate_and_store_plan(plan)

    def _mark_plan_runtime_active(
        self,
        *,
        saga: DeploymentSaga,
        binding_id: str | None,
        runtime_id: str | None,
    ) -> None:
        plan = self._mutable_plan_copy(saga.plan_id)
        if _enum_value(plan.target_stage) != str(saga.target_stage):
            raise DeploymentPlanError(
                f"DeploymentSaga '{saga.saga_id}' target_stage={saga.target_stage!r} "
                f"does not match DeploymentPlan '{plan.plan_id}' target_stage={_enum_value(plan.target_stage)!r}"
            )
        if binding_id:
            plan.binding_id = binding_id
        plan.current_stage = DeploymentStage(saga.target_stage)
        plan.status = PlanStatus.EXECUTED
        plan.metadata = self._with_runtime_lifecycle_metadata(
            plan.metadata,
            binding_id=binding_id,
            runtime_id=runtime_id,
            runtime_status="active",
            activated_stage=str(saga.target_stage),
        )
        self._validate_and_store_plan(plan)

    def _mutable_plan_copy(self, plan_id: str) -> DeploymentPlan:
        return DeploymentPlan.from_dict(self.planner_service.get_plan(plan_id).to_dict())

    def _next_plan_status_for_binding_created(self, plan: DeploymentPlan) -> PlanStatus | str:
        current = PlanStatus(plan.status)
        if current == PlanStatus.APPROVED:
            return PlanStatus.EXECUTING
        if current in {PlanStatus.EXECUTING, PlanStatus.EXECUTED}:
            return current
        raise DeploymentPlanError(
            f"DeploymentPlan '{plan.plan_id}' cannot record binding-created from status '{current.value}'"
        )

    def _validate_and_store_plan(self, plan: DeploymentPlan) -> None:
        errors = plan.validate()
        if errors:
            raise DeploymentPlanError("; ".join(errors))
        self.planner_service.plan_store.put(plan)

    @staticmethod
    def _with_runtime_lifecycle_metadata(
        metadata: Dict[str, Any] | None,
        *,
        binding_id: str | None,
        runtime_id: str | None,
        runtime_status: str | None,
        activated_stage: str | None,
    ) -> Dict[str, Any]:
        updated = dict(metadata or {})
        lifecycle = dict(updated.get("runtime_lifecycle") or {})
        if binding_id:
            lifecycle["binding_id"] = binding_id
        if runtime_id:
            lifecycle["runtime_id"] = runtime_id
        if runtime_status:
            lifecycle["runtime_status"] = runtime_status
        if activated_stage:
            lifecycle["activated_stage"] = activated_stage
        if lifecycle:
            updated["runtime_lifecycle"] = lifecycle
        return updated

    def _require_saga(self, saga_id: str) -> DeploymentSaga:
        saga = self.saga_store.get(saga_id)
        if saga is None:
            raise DeploymentSagaError(f"Unknown saga: {saga_id}")
        return saga

    def _resolve_registry_entry_for_plan(self, plan: DeploymentPlan) -> Mapping[str, Any]:
        from types import SimpleNamespace
        reference = SimpleNamespace(registry_id=plan.artifact_id,
                                    approval_decision_id=plan.approval_decision_id)
        entry = self.planner_service._resolve_registry_entry(reference)
        if (entry.get('owner_tenant') != _plan_tenant_id(plan)
                or entry.get('version') != plan.artifact_version
                or entry.get('artifact_state') != 'approved'
                or entry.get('approval_decision_id') != plan.approval_decision_id):
            raise DeploymentPlanError('Registry authority no longer matches the approved plan')
        self.planner_service._resolve_approval_decision(reference, entry, _plan_tenant_id(plan))
        return entry

    def _find_outbox_event(self, saga_id: str, *, sequence_no: int) -> OutboxRecord | None:
        for record in self.saga_store.outbox_records():
            if record.event.aggregate_id == saga_id and record.event.sequence_no == sequence_no:
                return record
        return None

    def _find_outbox_event_by_event_id(self, event_id: str) -> OutboxRecord | None:
        for record in self.saga_store.outbox_records():
            if record.event.event_id == event_id:
                return record
        return None


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _load_record(path: Path, *, key_candidates: Iterable[str], target_key: str) -> Optional[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    payload = json.loads(text)
    if isinstance(payload, dict):
        if target_key in payload and isinstance(payload[target_key], dict):
            return payload[target_key]
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            for key in key_candidates:
                if str(item.get(key) or "") == target_key:
                    return item
        return None
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            for key in key_candidates:
                if str(item.get(key) or "") == target_key:
                    return item
    return None


def _load_records(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    payload = json.loads(text)
    if isinstance(payload, dict):
        records = payload.get("records") if isinstance(payload.get("records"), list) else payload
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        return [item for item in records.values() if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _optional_str(value: Any) -> Optional[str]:
    return str(value) if value not in (None, "") else None


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _record_identity(record: Mapping[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _max_scope(current: Optional[str], candidate: Optional[str]) -> Optional[str]:
    if candidate is None:
        return current
    if current is None:
        return candidate
    return candidate if _SCOPE_ORDER.get(candidate, -1) > _SCOPE_ORDER.get(current, -1) else current


def _approval_outcome(approval_decision: Optional[Dict[str, Any]]) -> Optional[str]:
    if not approval_decision:
        return None
    value = approval_decision.get("decision") or approval_decision.get("outcome")
    return str(value) if value not in (None, "") else None


def _approval_state(approval_decision: Optional[Dict[str, Any]]) -> Optional[str]:
    if not approval_decision:
        return None
    value = approval_decision.get("decision_state") or approval_decision.get("state")
    return str(value) if value not in (None, "") else None


def _runtime_binding_id(runtime_binding: Optional[Dict[str, Any]]) -> Optional[str]:
    if not runtime_binding:
        return None
    value = runtime_binding.get("binding_id") or runtime_binding.get("id") or runtime_binding.get("runtime_binding_id")
    return str(value) if value not in (None, "") else None


def _runtime_id(runtime_binding: Optional[Dict[str, Any]]) -> Optional[str]:
    if not runtime_binding:
        return None
    value = runtime_binding.get("runtime_id")
    return str(value) if value not in (None, "") else None


def _runtime_status(runtime_binding: Optional[Dict[str, Any]]) -> Optional[str]:
    if not runtime_binding:
        return None
    value = runtime_binding.get("status")
    return str(value) if value not in (None, "") else None


def _runtime_binding_stage(runtime_binding: Optional[Dict[str, Any]]) -> Optional[str]:
    if not runtime_binding:
        return None
    value = runtime_binding.get("deployment_mode") or runtime_binding.get("deployment_stage")
    return str(value) if value not in (None, "") else None


def _rollback_action_type(plan: DeploymentPlan) -> Optional[str]:
    if not plan.rollback:
        return None
    return _enum_value(plan.rollback.action_type)


def _projection_lifecycle_state(
    *,
    plan_status: str,
    runtime_status: Optional[str],
    deployment_saga_status: Optional[str],
) -> str:
    if plan_status in {PlanStatus.REJECTED.value, PlanStatus.ABORTED.value, PlanStatus.FAILED.value}:
        return "terminal"
    if runtime_status == "active" or plan_status == PlanStatus.EXECUTED.value:
        return "active"
    if deployment_saga_status:
        return f"saga:{deployment_saga_status}"
    if plan_status == PlanStatus.APPROVED.value:
        return "ready_for_dispatch"
    return plan_status


def _foundation_environment_for_plan(plan: DeploymentPlan) -> EnvironmentScope:
    target_stage = _enum_value(plan.target_stage)
    if target_stage in {"paper", "canary", "live"}:
        environment_name = target_stage
    else:
        environment_name = os.getenv("PANTHEON_ENV", "dev").strip() or "dev"
        if environment_name not in {"dev", "sandbox", "paper", "canary", "live"}:
            environment_name = "dev"
    return EnvironmentScope(name=environment_name)


def _dispatch_actor_ref(plan: DeploymentPlan, request: DispatchDeploymentPlanRequest) -> ActorRef:
    actor_id = (
        str(request.actor_id or "").strip()
        or str(getattr(plan, "created_by", "") or "").strip()
        or str(request.source_task_id or "").strip()
        or "deployment-orchestrator"
    )
    return ActorRef(
        actor_type=ActorType.SERVICE,
        actor_id=actor_id,
        roles=("deployment-dispatcher",),
        persona_id=str(getattr(plan, "sponsor_persona_id", "") or "").strip() or None,
    )


def _dispatch_request_payload(plan: DeploymentPlan, request: DispatchDeploymentPlanRequest) -> Dict[str, Any]:
    payload = request.model_dump(mode="json", exclude_none=True)
    payload.pop("trace_id", None)
    payload.pop("correlation_id", None)
    payload.pop("idempotency_key", None)
    return {
        "plan_id": plan.plan_id,
        "approval_decision_id": plan.approval_decision_id,
        "target_stage": _enum_value(plan.target_stage),
        "runtime_action": _enum_value(plan.runtime_action),
        "dispatch": payload,
    }


def _build_dispatch_foundation_context(
    *,
    plan: DeploymentPlan,
    request: DispatchDeploymentPlanRequest,
) -> Dict[str, Any]:
    environment = _foundation_environment_for_plan(plan)
    actor_ref = _dispatch_actor_ref(plan, request)
    authority_scope = AuthorityScope(
        action="deployment.dispatch_plan",
        target_type="DeploymentPlan",
        target_id=plan.plan_id,
        environment=environment,
        persona_id=str(getattr(plan, "sponsor_persona_id", "") or "").strip() or None,
        capital_pool_id=str(getattr(plan, "capital_pool_id", "") or "").strip() or None,
        attributes={"route": _DEPLOYMENT_FOUNDATION_ROUTE},
    )
    request_payload = _dispatch_request_payload(plan, request)
    trace_id = str(request.trace_id or "").strip()
    idempotency_key = str(request.idempotency_key or "").strip() or None
    if trace_id:
        trace = TraceContext(
            trace_id=trace_id,
            correlation_id=str(request.correlation_id or trace_id).strip(),
            environment=environment,
            actor_ref=actor_ref,
            source_system="pantheon-deployment",
            idempotency_key=idempotency_key,
        )
    else:
        trace = TraceContext.new(
            environment=environment,
            actor_ref=actor_ref,
            source_system="pantheon-deployment",
            correlation_id=str(request.correlation_id or "").strip() or None,
            idempotency_key=idempotency_key,
        )
    command_envelope = CommandEnvelope.new(
        command_type="deployment.dispatch_plan",
        actor_ref=actor_ref,
        authority_scope=authority_scope,
        payload=request_payload,
        trace=trace,
        idempotency_key=idempotency_key,
    )
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=command_envelope.idempotency_key,
        operation_type="deployment.dispatch_plan",
        target_ref=authority_scope.target_ref,
        request_payload=request_payload,
        trace_id=command_envelope.trace.trace_id,
    )
    policy_decision = PolicyDecision.make(
        policy_id="deployment.dispatch.admission",
        policy_version=_DEPLOYMENT_FOUNDATION_POLICY_VERSION,
        decision=PolicyDecisionValue.ALLOW,
        actor_ref=actor_ref,
        action=command_envelope.command_type,
        target_ref=authority_scope.target_ref,
        environment=environment,
        trace_id=command_envelope.trace.trace_id,
    )
    audit_action = AuditAction.record(
        actor_ref=actor_ref,
        action_type="deployment.dispatch.accepted",
        target_ref=authority_scope.target_ref,
        environment=environment,
        reason="deployment plan dispatch accepted",
        trace=command_envelope.trace,
        payload=request_payload,
        policy_decision_ref=policy_decision.decision_id,
        metadata={"route": _DEPLOYMENT_FOUNDATION_ROUTE},
    )
    return {
        "command_envelope": command_envelope,
        "trace_context": command_envelope.trace,
        "idempotency_record": idempotency_record,
        "policy_decision": policy_decision,
        "audit_action": audit_action,
        "request_payload": request_payload,
    }


def _serialize_foundation_context(context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trace_context": context["trace_context"].to_dict(),
        "command_envelope": context["command_envelope"].to_dict(),
        "idempotency_record": context["idempotency_record"].to_dict(),
        "policy_decision": context["policy_decision"].to_dict(),
        "audit_action": context["audit_action"].to_dict(),
    }


def _foundation_error_detail(
    *,
    foundation_error: ErrorEnvelope,
    audit_action: AuditAction,
    policy_decision: PolicyDecision | None = None,
) -> Dict[str, Any]:
    detail: Dict[str, Any] = {
        "message": foundation_error.message,
        "foundation_error": foundation_error.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    if policy_decision is not None:
        detail["policy_decision"] = policy_decision.to_dict()
    return detail


def _foundation_policy_denial(*, foundation_context: Dict[str, Any], reason: str) -> FoundationDeploymentError:
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    policy_decision = PolicyDecision.make(
        policy_id="deployment.dispatch.admission",
        policy_version=_DEPLOYMENT_FOUNDATION_POLICY_VERSION,
        decision=PolicyDecisionValue.DENY,
        actor_ref=command_envelope.actor_ref,
        action=command_envelope.command_type,
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        trace_id=command_envelope.trace.trace_id,
        reasons=[reason],
    )
    foundation_error = ErrorEnvelope.policy_denial(
        message=reason,
        trace=command_envelope.trace,
        policy_decision_ref=policy_decision.decision_id,
        details={"route": _DEPLOYMENT_FOUNDATION_ROUTE},
    )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="deployment.dispatch.policy_denied",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=reason,
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        policy_decision_ref=policy_decision.decision_id,
        metadata={"route": _DEPLOYMENT_FOUNDATION_ROUTE},
    )
    return FoundationDeploymentError(
        status_code=403,
        detail=_foundation_error_detail(
            foundation_error=foundation_error,
            audit_action=audit_action,
            policy_decision=policy_decision,
        ),
    )


def _foundation_idempotency_conflict(
    *,
    foundation_context: Dict[str, Any],
    existing_saga_id: str,
) -> FoundationDeploymentError:
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    reason = (
        f"idempotency_key={idempotency_record.idempotency_key} is already bound "
        f"to deployment saga {existing_saga_id}"
    )
    foundation_error = ErrorEnvelope(
        error_id=foundation_id("err"),
        error_code="IDEMPOTENCY_CONFLICT",
        message="Idempotency key was already used with a different deployment dispatch payload",
        error_kind=ErrorKind.IDEMPOTENCY_CONFLICT,
        trace=command_envelope.trace,
        status_code=409,
        details={
            "reason": reason,
            "existing_saga_id": existing_saga_id,
            "idempotency_key": idempotency_record.idempotency_key,
        },
    )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="deployment.dispatch.idempotency_conflict",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=reason,
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata={"route": _DEPLOYMENT_FOUNDATION_ROUTE},
    )
    return FoundationDeploymentError(
        status_code=409,
        detail=_foundation_error_detail(foundation_error=foundation_error, audit_action=audit_action),
    )


def _ensure_dispatch_replay_matches_foundation(
    existing: DeploymentSaga,
    foundation_context: Dict[str, Any],
) -> None:
    foundation = (existing.metadata or {}).get("foundation") if isinstance(existing.metadata, dict) else None
    if not isinstance(foundation, dict):
        return
    existing_record = foundation.get("idempotency_record")
    if not isinstance(existing_record, dict):
        return
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    if existing_record.get("idempotency_key") != idempotency_record.idempotency_key:
        return
    if existing_record.get("request_hash") != idempotency_record.request_hash:
        raise _foundation_idempotency_conflict(
            foundation_context=foundation_context,
            existing_saga_id=existing.saga_id,
        )


def _plan_body(plan: DeploymentPlan) -> DeploymentPlanBody:
    payload = plan.to_dict()
    payload["tenant_id"] = _plan_tenant_id(plan)
    return DeploymentPlanBody(**payload)


def _plan_summary(plan: DeploymentPlan) -> DeploymentPlanSummary:
    payload = plan.to_dict()
    return DeploymentPlanSummary(
        plan_id=payload["plan_id"],
        artifact_id=payload["artifact_id"],
        artifact_version=payload["artifact_version"],
        strategy_id=payload["strategy_id"],
        capital_pool_id=payload["capital_pool_id"],
        current_stage=payload["current_stage"],
        target_stage=payload["target_stage"],
        transition_type=payload["transition_type"],
        runtime_action=payload["runtime_action"],
        status=payload["status"],
        created_at=payload["created_at"],
        approval_decision_id=payload["approval_decision_id"],
    )


def _saga_body(saga: DeploymentSaga) -> DeploymentSagaBody:
    payload = saga.to_dict()
    payload["tenant_id"] = _saga_tenant_id(saga)
    return DeploymentSagaBody(**payload)


def _outbox_body(record: OutboxRecord) -> OutboxRecordBody:
    return OutboxRecordBody(**record.to_dict())


def _inbox_body(receipt: InboxReceipt) -> InboxReceiptBody:
    return InboxReceiptBody(**receipt.to_dict())


def _compensation_body(decision: CompensationDecision) -> CompensationDecisionBody:
    return CompensationDecisionBody(**decision.to_dict())


def _bootstrap_body(bootstrap: DeploymentSagaBootstrap) -> DeploymentSagaBootstrapBody:
    return DeploymentSagaBootstrapBody(
        saga=_saga_body(bootstrap.saga),
        outbox_event=_outbox_body(bootstrap.outbox_event),
    )


def _retry_policy_body(records: list[OutboxRecord]) -> DeploymentSagaRetryPolicyBody:
    policies = [
        record.retry_policy
        for record in records
        if isinstance(record.retry_policy, dict)
    ]
    if policies:
        policy = policies[-1]
        return DeploymentSagaRetryPolicyBody(
            max_attempts=int(policy.get("max_attempts") or _OUTBOX_DEFAULT_MAX_ATTEMPTS),
            retry_delay_seconds=int(policy.get("retry_delay_seconds") or 0),
            retryable=bool(policy.get("retryable", True)),
        )
    return DeploymentSagaRetryPolicyBody(
        max_attempts=_OUTBOX_DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds=_OUTBOX_DEFAULT_RETRY_DELAY_SECONDS,
        retryable=True,
    )


def _retry_state_body(record: OutboxRecord) -> DeploymentOutboxRetryStateBody:
    return DeploymentOutboxRetryStateBody(
        event_id=record.event.event_id,
        event_type=record.event.event_type,
        sequence_no=record.event.sequence_no,
        status=_enum_value(record.status),
        delivery_attempts=record.delivery_attempts,
        replay_count=record.replay_count,
        published_at=record.published_at,
        last_error=record.last_error,
        last_attempt_at=record.last_attempt_at,
        next_retry_at=record.next_retry_at,
        blocked_reason=record.blocked_reason,
        dlq_at=record.dlq_at,
        last_replayed_at=record.last_replayed_at,
        retry_policy=record.retry_policy,
    )


def _saga_progress_status(saga: DeploymentSaga, records: list[OutboxRecord]) -> SagaProgressStatusBody:
    saga_status = SagaStatus(saga.status)
    if saga_status == SagaStatus.COMPLETED:
        return SagaProgressStatusBody.COMPLETED
    if saga_status in {SagaStatus.FAILED, SagaStatus.ABORTED}:
        return SagaProgressStatusBody.FAILED
    if any(OutboxStatus(record.status) == OutboxStatus.DEAD_LETTERED for record in records):
        return SagaProgressStatusBody.BLOCKED
    if saga_status == SagaStatus.AWAITING_BINDING and saga.last_sequence_no <= 1:
        return SagaProgressStatusBody.PENDING
    return SagaProgressStatusBody.RUNNING


def _saga_progress_body(
    saga: DeploymentSaga,
    outbox_records: list[OutboxRecord],
) -> DeploymentSagaProgressBody:
    records = [
        record
        for record in outbox_records
        if record.event.aggregate_id == saga.saga_id
    ]
    retry_state = [_retry_state_body(record) for record in records]
    dlq_records = [
        record
        for record in records
        if OutboxStatus(record.status) == OutboxStatus.DEAD_LETTERED
    ]
    blocked_reason = None
    if dlq_records:
        blocked = sorted(dlq_records, key=lambda item: item.event.sequence_no)[-1]
        blocked_reason = blocked.blocked_reason or blocked.last_error
    elif saga.failure_reason:
        blocked_reason = saga.failure_reason

    return DeploymentSagaProgressBody(
        saga_id=saga.saga_id,
        plan_id=saga.plan_id,
        progress_status=_saga_progress_status(saga, records),
        saga_status=_enum_value(saga.status),
        current_step=_enum_value(saga.current_step),
        blocked_reason=blocked_reason,
        retry_policy=_retry_policy_body(records),
        retry_state=retry_state,
        completed_steps=[_enum_value(item.step) for item in saga.history],
        pending_event_count=sum(
            1
            for record in records
            if OutboxStatus(record.status) == OutboxStatus.PENDING
        ),
        dlq_count=len(dlq_records),
    )


def _execution_context_for_stage(target_stage: DeploymentStage | str) -> str:
    stage = DeploymentStage(target_stage)
    if stage == DeploymentStage.PAPER:
        return "paper"
    if stage in {DeploymentStage.CANARY, DeploymentStage.LIVE}:
        return "live"
    return "status"


planner_service = DeploymentPlannerService(
    plan_store=store,
    approval_store_path=APPROVAL_STORE_PATH,
    registry_snapshot_path=REGISTRY_SNAPSHOT_PATH,
)
orchestration_service = DeploymentOrchestrationService(
    planner_service=planner_service,
    saga_store=saga_store,
)
projection_service = DeploymentProjectionReadModelService(
    planner_service=planner_service,
    saga_store=saga_store,
    approval_store_path=APPROVAL_STORE_PATH,
    registry_snapshot_path=REGISTRY_SNAPSHOT_PATH,
    runtime_binding_store_path=RUNTIME_BINDING_STORE_PATH,
)
compatibility_service = PoolRuntimeCompatibilityService(
    capital_pool_store_path=CAPITAL_POOL_STORE_PATH,
    persona_binding_store_path=PERSONA_BINDING_STORE_PATH,
    runtime_binding_store_path=RUNTIME_BINDING_STORE_PATH,
)

app = FastAPI(
    title="Pantheon Deployment Service",
    description="DeploymentPlan and DEP-002 deployment saga API per BP5-SVC-004/BP5-SVC-005",
    version="0.2.0",
)


@app.middleware("http")
async def deployment_authenticated_tenant_boundary(request: Request, call_next):
    if request.url.path.startswith("/api/deployment"):
        try:
            request.state.deployment_identity = authenticate_tenant(
                authorization=request.headers.get("Authorization"),
                tenant_id=request.headers.get("X-Tenant-Id"),
                service_prefix="DEPLOYMENT",
                required_roles=AUTHENTICATED_SERVICE_ROLES,
                mfa_header=request.headers.get("X-MFA-Token"),
            )
        except AuthError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.message, "error_code": exc.code},
            )
        except TenantBoundaryError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": str(exc), "error_code": "TENANT_BOUNDARY_DENIED"},
            )
    return await call_next(request)


register_fastapi_health_routes(
    app,
    "pantheon-deployment",
    dependencies=lambda: {
        "governance_store": {"status": "ok", "path": str(DATA_DIR)},
        "outbox_leases": outbox_lease_store.health(),
    },
    metrics=lambda: {
        "plan_count": len(store.list_all()),
        "saga_count": len(saga_store.list_all()),
        "outbox_lease_recovered_count": outbox_lease_store.health()[
            "recovered_claim_count"
        ],
    },
    details=lambda: {
        "data_dir": str(DATA_DIR),
        "outbox_lease_store": str(OUTBOX_LEASE_STORE_PATH),
    },
)


@app.exception_handler(DeploymentPlanError)
async def deployment_plan_error_handler(request: Request, exc: DeploymentPlanError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(DeploymentSagaError)
async def deployment_saga_error_handler(request: Request, exc: DeploymentSagaError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.post("/api/deployment/plans", response_model=DeploymentPlanBody, status_code=201)
async def create_deployment_plan(request: Request, body: CreateDeploymentPlanRequest):
    identity = _request_identity(request)
    try:
        plan = planner_service.create_plan(
            body,
            persist=True,
            actor_id=identity.actor_id,
            tenant_id=identity.tenant_id,
        )
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _plan_body(plan)


@app.post("/api/deployment/plans/validate", response_model=ValidateDeploymentPlanResponse)
async def validate_deployment_plan(request: Request, body: CreateDeploymentPlanRequest):
    identity = _request_identity(request)
    try:
        plan = planner_service.create_plan(
            body,
            persist=False,
            actor_id=identity.actor_id,
            tenant_id=identity.tenant_id,
        )
    except DeploymentPlanError as exc:
        return ValidateDeploymentPlanResponse(ok=False, errors=[str(exc)])
    return ValidateDeploymentPlanResponse(ok=True, plan=_plan_body(plan), errors=[])


@app.post(
    "/api/deployment/stage-planner/check",
    response_model=StagePlannerCheckResponse,
)
async def check_deployment_stage_planner(body: StagePlannerCheckRequest):
    return planner_service.check_stage_transition(body)


@app.post(
    "/api/deployment/plans/compatibility-check",
    response_model=PoolCompatibilityResponse,
)
async def check_pool_runtime_compatibility(body: PoolCompatibilityRequest):
    return compatibility_service.check(body)


@app.get("/api/deployment/plans", response_model=List[DeploymentPlanBody])
async def list_deployment_plans(
    request: Request,
    strategy_id: str | None = Query(default=None),
    capital_pool_id: str | None = Query(default=None),
    target_stage: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    plans = planner_service.list_plans(
        tenant_id=_request_identity(request).tenant_id,
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
        target_stage=target_stage,
        status=status,
    )
    return [_plan_body(plan) for plan in plans]


@app.get("/api/deployment/plans/{plan_id}", response_model=DeploymentPlanBody)
async def get_deployment_plan(request: Request, plan_id: str):
    try:
        return _plan_body(_require_plan_access(plan_id, _request_identity(request)))
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get(
    "/api/deployment/projections",
    response_model=List[DeploymentProjectionReadModelResponse],
)
async def list_deployment_projections(
    request: Request,
    strategy_id: str | None = Query(default=None),
    capital_pool_id: str | None = Query(default=None),
    target_stage: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return projection_service.list_projections(
        tenant_id=_request_identity(request).tenant_id,
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
        target_stage=target_stage,
        status=status,
    )


@app.get(
    "/api/deployment/projections/{plan_id}",
    response_model=DeploymentProjectionReadModelResponse,
)
async def get_deployment_projection(request: Request, plan_id: str):
    try:
        _require_plan_access(plan_id, _request_identity(request))
        return projection_service.get_projection(plan_id)
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get(
    "/api/deployment/plans/{plan_id}/projection",
    response_model=DeploymentProjectionReadModelResponse,
)
async def get_deployment_plan_projection(request: Request, plan_id: str):
    return await get_deployment_projection(request, plan_id)


@app.post("/api/deployment/plans/{plan_id}/status", response_model=DeploymentPlanBody)
async def update_deployment_plan_status(
    request: Request, plan_id: str, body: UpdatePlanStatusRequest
):
    try:
        _require_plan_access(plan_id, _request_identity(request))
        return _plan_body(planner_service.update_status(plan_id, body.status))
    except DeploymentPlanError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post(
    "/api/deployment/plans/{plan_id}/dispatch",
    response_model=DeploymentDispatchResponse,
)
async def dispatch_deployment_plan(
    request: Request, plan_id: str, body: DispatchDeploymentPlanRequest
):
    identity = _request_identity(request)
    try:
        _require_plan_access(plan_id, identity)
        body = body.model_copy(update={"actor_id": identity.actor_id})
        plan, execution_projection, bootstrap, replayed = orchestration_service.dispatch_plan(plan_id, body)
    except FoundationDeploymentError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except (DeploymentPlanError, DeploymentSagaError) as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)

    projection = DeploymentExecutionProjectionBody(**execution_projection)
    return DeploymentDispatchResponse(
        plan=_plan_body(plan),
        strategy_id=plan.strategy_id,
        version=plan.artifact_version,
        target_stage=_enum_value(plan.target_stage),
        execution_context=_execution_context_for_stage(plan.target_stage),
        artifact_loader_contract="EX-001",
        deployment_contract="DEP-001",
        consistency_contract="DEP-002",
        execution_projection=projection,
        deployment_saga=_bootstrap_body(bootstrap),
        replayed=replayed,
    )


@app.get(
    "/api/deployment/strategies/{strategy_id}/read-model",
    response_model=StrategyReadModelResponse,
)
async def get_strategy_read_model(
    request: Request,
    strategy_id: str,
    capital_pool_id: str | None = Query(default=None),
):
    return planner_service.strategy_read_model(
        tenant_id=_request_identity(request).tenant_id,
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
    )


@app.get("/api/deployment/sagas", response_model=List[DeploymentSagaBody])
async def list_deployment_sagas(
    request: Request,
    plan_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    identity = _request_identity(request)
    if plan_id:
        _require_plan_access(plan_id, identity)
    return [
        _saga_body(saga)
        for saga in orchestration_service.list_sagas(plan_id=plan_id, status=status)
        if _saga_tenant_id(saga) == identity.tenant_id
    ]


@app.get("/api/deployment/sagas/{saga_id}", response_model=DeploymentSagaBody)
async def get_deployment_saga(request: Request, saga_id: str):
    try:
        return _saga_body(_require_saga_access(saga_id, _request_identity(request)))
    except DeploymentSagaError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get(
    "/api/deployment/sagas/{saga_id}/progress",
    response_model=DeploymentSagaProgressBody,
)
async def get_deployment_saga_progress(request: Request, saga_id: str):
    try:
        _require_saga_access(saga_id, _request_identity(request))
        return orchestration_service.get_saga_progress(saga_id)
    except DeploymentSagaError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get(
    "/api/deployment/plans/{plan_id}/saga-progress",
    response_model=DeploymentSagaProgressBody,
)
async def get_deployment_plan_saga_progress(request: Request, plan_id: str):
    try:
        _require_plan_access(plan_id, _request_identity(request))
        return orchestration_service.get_plan_saga_progress(plan_id)
    except DeploymentSagaError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/deployment/sagas/{saga_id}/binding-created", response_model=OutboxRecordBody)
async def record_saga_binding_created(
    request: Request, saga_id: str, body: RecordBindingCreatedRequest
):
    try:
        _require_saga_access(saga_id, _request_identity(request))
        return _outbox_body(orchestration_service.record_binding_created(saga_id, body))
    except (DeploymentPlanError, DeploymentSagaError) as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post("/api/deployment/sagas/{saga_id}/runtime-active", response_model=OutboxRecordBody)
async def record_saga_runtime_active(
    request: Request, saga_id: str, body: RecordRuntimeActiveRequest
):
    try:
        _require_saga_access(saga_id, _request_identity(request))
        return _outbox_body(orchestration_service.record_runtime_active(saga_id, body))
    except (DeploymentPlanError, DeploymentSagaError) as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post("/api/deployment/sagas/{saga_id}/failure", response_model=CompensationDecisionBody)
async def record_saga_failure(
    request: Request, saga_id: str, body: RecordSagaFailureRequest
):
    try:
        _require_saga_access(saga_id, _request_identity(request))
        return _compensation_body(orchestration_service.record_failure(saga_id, body))
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post(
    "/api/deployment/sagas/{saga_id}/compensation/finalize",
    response_model=OutboxRecordBody,
)
async def finalize_saga_compensation(
    request: Request, saga_id: str, body: FinalizeCompensationRequest
):
    try:
        _require_saga_access(saga_id, _request_identity(request))
        return _outbox_body(orchestration_service.finalize_compensation(saga_id, body))
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.get("/api/deployment/outbox", response_model=List[OutboxRecordBody])
async def list_deployment_outbox(
    request: Request,
    owner_service: str | None = Query(default=None),
    aggregate_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    identity = _request_identity(request)
    records = orchestration_service.list_outbox(
        owner_service=owner_service,
        aggregate_id=aggregate_id,
        status=status,
    )
    return [
        _outbox_body(record)
        for record in records
        if _outbox_event_tenant_id(record) == identity.tenant_id
    ]


@app.post(
    "/api/deployment/outbox/claim",
    response_model=List[ClaimedOutboxRecordBody],
)
async def claim_deployment_outbox(
    request: Request, body: ClaimOutboxEventsRequest
):
    identity = _request_identity(request)
    records = [
        record
        for record in orchestration_service.list_outbox(
            aggregate_id=body.aggregate_id,
            status=OutboxStatus.PENDING.value,
        )
        if _outbox_event_tenant_id(record) == identity.tenant_id
        and _outbox_retry_due(record)
    ]
    claimed = outbox_lease_store.claim(
        [record.to_dict() for record in records],
        tenant_id=identity.tenant_id,
        consumer_name=body.consumer_name,
        lease_seconds=body.lease_seconds,
        limit=body.limit,
        aggregate_id=body.aggregate_id,
    )
    return [ClaimedOutboxRecordBody(**record) for record in claimed]


@app.get(
    "/api/deployment/outbox/lease-health",
    response_model=OutboxLeaseHealthBody,
)
async def get_deployment_outbox_lease_health(request: Request):
    _request_identity(request)
    return OutboxLeaseHealthBody(**outbox_lease_store.health())


@app.post(
    "/api/deployment/outbox/{event_id}/failure",
    response_model=OutboxRecordBody,
)
async def record_deployment_outbox_failure(
    request: Request, event_id: str, body: RecordOutboxFailureRequest
):
    identity = _request_identity(request)
    try:
        _require_outbox_access(event_id, identity)
        if _outbox_lease_required():
            if not body.claim_token:
                raise OutboxLeaseError("claim_token is required for delivery failure.")
            outbox_lease_store.require_active(
                event_id=event_id,
                claim_token=body.claim_token,
                tenant_id=identity.tenant_id,
                consumer_name=body.consumer_name,
            )
        record = orchestration_service.record_outbox_failure(event_id, body)
        if _outbox_lease_required() and body.claim_token:
            outbox_lease_store.release(
                event_id=event_id,
                claim_token=body.claim_token,
                tenant_id=identity.tenant_id,
                consumer_name=body.consumer_name,
                reason=body.reason,
            )
        return _outbox_body(record)
    except (DeploymentSagaError, OutboxLeaseError) as exc:
        message = str(exc)
        status_code = getattr(
            exc,
            "status_code",
            404 if "not found" in message.lower() else 400,
        )
        raise HTTPException(status_code=status_code, detail=message)


@app.post(
    "/api/deployment/outbox/{event_id}/replay",
    response_model=ReplayOutboxEventResponse,
)
async def replay_deployment_outbox_event(
    request: Request, event_id: str, body: ReplayOutboxEventRequest
):
    try:
        _require_outbox_access(event_id, _request_identity(request))
        event, replayed = orchestration_service.replay_outbox_event(event_id, body)
        return ReplayOutboxEventResponse(event=_outbox_body(event), replayed=replayed)
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post("/api/deployment/outbox/{event_id}/consume", response_model=InboxReceiptBody)
async def consume_deployment_outbox_event(
    request: Request, event_id: str, body: ConsumeOutboxEventRequest
):
    identity = _request_identity(request)
    try:
        _require_outbox_access(event_id, identity)
        if _outbox_lease_required():
            if not body.claim_token:
                raise OutboxLeaseError("claim_token is required for outbox acknowledgement.")
            outbox_lease_store.require_active(
                event_id=event_id,
                claim_token=body.claim_token,
                tenant_id=identity.tenant_id,
                consumer_name=body.consumer_name,
            )
        receipt = orchestration_service.consume_outbox_event(
            event_id,
            consumer_name=body.consumer_name,
        )
        if _outbox_lease_required() and body.claim_token:
            if ReceiptStatus(receipt.status) in {
                ReceiptStatus.APPLIED,
                ReceiptStatus.DUPLICATE,
            }:
                outbox_lease_store.acknowledge(
                    event_id=event_id,
                    claim_token=body.claim_token,
                    tenant_id=identity.tenant_id,
                    consumer_name=body.consumer_name,
                )
            else:
                outbox_lease_store.release(
                    event_id=event_id,
                    claim_token=body.claim_token,
                    tenant_id=identity.tenant_id,
                    consumer_name=body.consumer_name,
                    reason=f"inbox_receipt_{_enum_value(receipt.status)}",
                )
        return _inbox_body(receipt)
    except (DeploymentSagaError, OutboxLeaseError) as exc:
        message = str(exc)
        status_code = getattr(
            exc,
            "status_code",
            404 if "not found" in message.lower() else 400,
        )
        raise HTTPException(status_code=status_code, detail=message)


@app.get("/api/deployment/inbox", response_model=List[InboxReceiptBody])
async def list_deployment_inbox(
    request: Request,
    consumer_name: str | None = Query(default=None),
    aggregate_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    identity = _request_identity(request)
    receipts = orchestration_service.list_inbox(
        consumer_name=consumer_name,
        aggregate_id=aggregate_id,
        status=status,
    )
    return [
        _inbox_body(receipt)
        for receipt in receipts
        if (
            (saga := saga_store.get(receipt.aggregate_id)) is not None
            and _saga_tenant_id(saga) == identity.tenant_id
        )
    ]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-deployment"}
