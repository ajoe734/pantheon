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
    OutboxRecord,
)
from persona_capital_binding import (  # type: ignore
    PersonaCapitalBinding,
    PersonaCapitalBindingError,
)

try:
    from .models import (
        CompensationDecisionBody,
        ConsumeOutboxEventRequest,
        CreateDeploymentPlanRequest,
        DeploymentDispatchResponse,
        DeploymentExecutionProjectionBody,
        DeploymentPlanBody,
        DeploymentPlanSummary,
        DeploymentProjectionReadModelResponse,
        DeploymentSagaBody,
        DeploymentSagaBootstrapBody,
        DispatchDeploymentPlanRequest,
        FinalizeCompensationRequest,
        InboxReceiptBody,
        OutboxRecordBody,
        PoolCompatibilityRequest,
        PoolCompatibilityResponse,
        PlanStatusBody,
        RecordBindingCreatedRequest,
        RecordRuntimeActiveRequest,
        RecordSagaFailureRequest,
        StrategyReadModelResponse,
        UpdatePlanStatusRequest,
        ValidateDeploymentPlanResponse,
    )
except ImportError:
    from models import (  # type: ignore
        CompensationDecisionBody,
        ConsumeOutboxEventRequest,
        CreateDeploymentPlanRequest,
        DeploymentDispatchResponse,
        DeploymentExecutionProjectionBody,
        DeploymentPlanBody,
        DeploymentPlanSummary,
        DeploymentProjectionReadModelResponse,
        DeploymentSagaBody,
        DeploymentSagaBootstrapBody,
        DispatchDeploymentPlanRequest,
        FinalizeCompensationRequest,
        InboxReceiptBody,
        OutboxRecordBody,
        PoolCompatibilityRequest,
        PoolCompatibilityResponse,
        PlanStatusBody,
        RecordBindingCreatedRequest,
        RecordRuntimeActiveRequest,
        RecordSagaFailureRequest,
        StrategyReadModelResponse,
        UpdatePlanStatusRequest,
        ValidateDeploymentPlanResponse,
    )

log = logging.getLogger(__name__)

_DEPLOYMENT_FOUNDATION_POLICY_VERSION = "deployment.dispatch.v1"
_DEPLOYMENT_FOUNDATION_ROUTE = "deployment.plan.dispatch"


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


class DeploymentPlannerService:
    """Thin service layer over StagePlanner + file-backed stores."""

    def __init__(
        self,
        *,
        plan_store: DeploymentPlanStore,
        approval_store_path: Path,
        registry_snapshot_path: Path | None = None,
    ) -> None:
        self.plan_store = plan_store
        self.approval_store_path = approval_store_path
        self.registry_snapshot_path = registry_snapshot_path
        self.planner = StagePlanner()

    def create_plan(self, request: CreateDeploymentPlanRequest, *, persist: bool) -> DeploymentPlan:
        registry_entry = self._resolve_registry_entry(request)
        approval_decision = self._resolve_approval_decision(request)
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
            created_by=request.created_by,
            sponsor_persona_id=request.sponsor_persona_id,
            runtime_config_ref=request.runtime_config_ref,
            binding_id=request.binding_id,
            schedule_window=schedule_window,
            scale=scale,
            rollback=rollback,
            pre_checks=list(request.pre_checks),
            post_checks=list(request.post_checks),
            metadata=request.metadata,
            supersedes_plan_id=request.supersedes_plan_id,
            status=request.status.value,
        )
        if persist:
            self.plan_store.put(plan)
        return plan

    def list_plans(
        self,
        *,
        strategy_id: str | None = None,
        capital_pool_id: str | None = None,
        target_stage: str | None = None,
        status: str | None = None,
    ) -> list[DeploymentPlan]:
        plans = self.plan_store.list_all()
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

    def strategy_read_model(
        self,
        *,
        strategy_id: str,
        capital_pool_id: str | None = None,
    ) -> StrategyReadModelResponse:
        plans = self.list_plans(strategy_id=strategy_id, capital_pool_id=capital_pool_id)
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
        if request.registry_entry is not None:
            return request.registry_entry
        if request.registry_id and self.registry_snapshot_path and self.registry_snapshot_path.exists():
            record = _load_record(
                self.registry_snapshot_path,
                key_candidates=("registry_id", "id"),
                target_key=request.registry_id,
            )
            if record is not None:
                return record
        raise DeploymentPlanError(
            "registry_entry payload is required unless registry_id resolves from "
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
        )

    def _resolve_approval_decision(self, request: CreateDeploymentPlanRequest) -> Mapping[str, Any]:
        if request.approval_decision is not None:
            return request.approval_decision
        if self.approval_store_path.exists():
            record = _load_record(
                self.approval_store_path,
                key_candidates=("decision_id", "id"),
                target_key=request.approval_decision_id,
            )
            if record is not None:
                return record
        raise DeploymentPlanError(
            "approval_decision payload is required unless approval_decision_id resolves from "
            f"{self.approval_store_path}"
        )


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
        strategy_id: str | None = None,
        capital_pool_id: str | None = None,
        target_stage: str | None = None,
        status: str | None = None,
    ) -> list[DeploymentProjectionReadModelResponse]:
        return [
            self._build_projection(plan)
            for plan in self.planner_service.list_plans(
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
        lifecycle_state = _projection_lifecycle_state(
            plan_status=plan_status,
            runtime_status=runtime_status,
            deployment_saga_status=saga_status,
        )
        summary: Dict[str, Any] = {
            "has_approval_authority": approval_outcome in {"approved", "approved_with_conditions"},
            "runtime_backing_present": runtime_binding is not None,
            "execution_projection_available": execution_projection is not None,
            "rollback_action_type": _rollback_action_type(plan),
            "scale": plan_payload.get("scale"),
            "created_at": plan_payload.get("created_at"),
        }
        if projection_error:
            summary["execution_projection_error"] = projection_error

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
            lifecycle_state=lifecycle_state,
            source_status=source_status,
            summary=summary,
            plan=_plan_body(plan),
            approval_decision=approval_decision,
            runtime_binding=runtime_binding,
            deployment_saga=_saga_body(deployment_saga) if deployment_saga else None,
            execution_projection=execution_projection,
        )

    def _find_approval_decision(self, approval_decision_id: str) -> Optional[Dict[str, Any]]:
        if not self.approval_store_path.exists():
            return None
        return _load_record(
            self.approval_store_path,
            key_candidates=("decision_id", "id"),
            target_key=approval_decision_id,
        )

    def _find_registry_entry(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        if self.registry_snapshot_path is None or not self.registry_snapshot_path.exists():
            return None
        return _load_record(
            self.registry_snapshot_path,
            key_candidates=("registry_id", "id", "artifact_id"),
            target_key=artifact_id,
        )

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

        registry_entry = self._resolve_registry_entry_for_plan(plan, request.registry_entry)
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

    def record_binding_created(
        self,
        saga_id: str,
        request: RecordBindingCreatedRequest,
    ) -> OutboxRecord:
        return self.saga_store.record_binding_created(
            saga_id,
            binding_id=request.binding_id,
            runtime_id=request.runtime_id,
            note=request.note,
        )

    def record_runtime_active(
        self,
        saga_id: str,
        request: RecordRuntimeActiveRequest,
    ) -> OutboxRecord:
        return self.saga_store.record_runtime_active(
            saga_id,
            binding_id=request.binding_id,
            runtime_id=request.runtime_id,
            note=request.note,
        )

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
    ) -> list[OutboxRecord]:
        records = self.saga_store.pending_outbox()
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
        return self.saga_store.consume_event(consumer_name, event.event)

    def _resolve_registry_entry_for_plan(
        self,
        plan: DeploymentPlan,
        explicit_registry_entry: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        if explicit_registry_entry is not None:
            return explicit_registry_entry
        snapshot_path = self.planner_service.registry_snapshot_path
        if snapshot_path and snapshot_path.exists():
            record = _load_record(
                snapshot_path,
                key_candidates=("registry_id", "id"),
                target_key=plan.artifact_id,
            )
            if record is not None:
                return record
        raise DeploymentPlanError(
            "dispatch requires registry_entry payload unless artifact_id resolves from "
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
        )

    def _find_outbox_event(self, saga_id: str, *, sequence_no: int) -> OutboxRecord | None:
        for record in self.saga_store.pending_outbox():
            if record.event.aggregate_id == saga_id and record.event.sequence_no == sequence_no:
                return record
        return None

    def _find_outbox_event_by_event_id(self, event_id: str) -> OutboxRecord | None:
        for record in self.saga_store.pending_outbox():
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
    return DeploymentPlanBody(**plan.to_dict())


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
    return DeploymentSagaBody(**saga.to_dict())


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
register_fastapi_health_routes(
    app,
    "pantheon-deployment",
    dependencies=lambda: {"governance_store": {"status": "ok", "path": str(DATA_DIR)}},
    metrics=lambda: {
        "plan_count": len(store.list_all()),
        "saga_count": len(saga_store.list_all()),
    },
    details=lambda: {"data_dir": str(DATA_DIR)},
)


@app.exception_handler(DeploymentPlanError)
async def deployment_plan_error_handler(request: Request, exc: DeploymentPlanError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(DeploymentSagaError)
async def deployment_saga_error_handler(request: Request, exc: DeploymentSagaError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.post("/api/deployment/plans", response_model=DeploymentPlanBody, status_code=201)
async def create_deployment_plan(body: CreateDeploymentPlanRequest):
    try:
        plan = planner_service.create_plan(body, persist=True)
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _plan_body(plan)


@app.post("/api/deployment/plans/validate", response_model=ValidateDeploymentPlanResponse)
async def validate_deployment_plan(body: CreateDeploymentPlanRequest):
    try:
        plan = planner_service.create_plan(body, persist=False)
    except DeploymentPlanError as exc:
        return ValidateDeploymentPlanResponse(ok=False, errors=[str(exc)])
    return ValidateDeploymentPlanResponse(ok=True, plan=_plan_body(plan), errors=[])


@app.post(
    "/api/deployment/plans/compatibility-check",
    response_model=PoolCompatibilityResponse,
)
async def check_pool_runtime_compatibility(body: PoolCompatibilityRequest):
    return compatibility_service.check(body)


@app.get("/api/deployment/plans", response_model=List[DeploymentPlanBody])
async def list_deployment_plans(
    strategy_id: str | None = Query(default=None),
    capital_pool_id: str | None = Query(default=None),
    target_stage: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    plans = planner_service.list_plans(
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
        target_stage=target_stage,
        status=status,
    )
    return [_plan_body(plan) for plan in plans]


@app.get("/api/deployment/plans/{plan_id}", response_model=DeploymentPlanBody)
async def get_deployment_plan(plan_id: str):
    try:
        return _plan_body(planner_service.get_plan(plan_id))
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get(
    "/api/deployment/projections",
    response_model=List[DeploymentProjectionReadModelResponse],
)
async def list_deployment_projections(
    strategy_id: str | None = Query(default=None),
    capital_pool_id: str | None = Query(default=None),
    target_stage: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return projection_service.list_projections(
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
        target_stage=target_stage,
        status=status,
    )


@app.get(
    "/api/deployment/projections/{plan_id}",
    response_model=DeploymentProjectionReadModelResponse,
)
async def get_deployment_projection(plan_id: str):
    try:
        return projection_service.get_projection(plan_id)
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get(
    "/api/deployment/plans/{plan_id}/projection",
    response_model=DeploymentProjectionReadModelResponse,
)
async def get_deployment_plan_projection(plan_id: str):
    return await get_deployment_projection(plan_id)


@app.post("/api/deployment/plans/{plan_id}/status", response_model=DeploymentPlanBody)
async def update_deployment_plan_status(plan_id: str, body: UpdatePlanStatusRequest):
    try:
        return _plan_body(planner_service.update_status(plan_id, body.status))
    except DeploymentPlanError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post(
    "/api/deployment/plans/{plan_id}/dispatch",
    response_model=DeploymentDispatchResponse,
)
async def dispatch_deployment_plan(plan_id: str, body: DispatchDeploymentPlanRequest):
    try:
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
async def get_strategy_read_model(strategy_id: str, capital_pool_id: str | None = Query(default=None)):
    return planner_service.strategy_read_model(
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
    )


@app.get("/api/deployment/sagas", response_model=List[DeploymentSagaBody])
async def list_deployment_sagas(
    plan_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return [_saga_body(saga) for saga in orchestration_service.list_sagas(plan_id=plan_id, status=status)]


@app.get("/api/deployment/sagas/{saga_id}", response_model=DeploymentSagaBody)
async def get_deployment_saga(saga_id: str):
    try:
        return _saga_body(orchestration_service.get_saga(saga_id))
    except DeploymentSagaError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/deployment/sagas/{saga_id}/binding-created", response_model=OutboxRecordBody)
async def record_saga_binding_created(saga_id: str, body: RecordBindingCreatedRequest):
    try:
        return _outbox_body(orchestration_service.record_binding_created(saga_id, body))
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post("/api/deployment/sagas/{saga_id}/runtime-active", response_model=OutboxRecordBody)
async def record_saga_runtime_active(saga_id: str, body: RecordRuntimeActiveRequest):
    try:
        return _outbox_body(orchestration_service.record_runtime_active(saga_id, body))
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post("/api/deployment/sagas/{saga_id}/failure", response_model=CompensationDecisionBody)
async def record_saga_failure(saga_id: str, body: RecordSagaFailureRequest):
    try:
        return _compensation_body(orchestration_service.record_failure(saga_id, body))
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.post(
    "/api/deployment/sagas/{saga_id}/compensation/finalize",
    response_model=OutboxRecordBody,
)
async def finalize_saga_compensation(saga_id: str, body: FinalizeCompensationRequest):
    try:
        return _outbox_body(orchestration_service.finalize_compensation(saga_id, body))
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "unknown saga" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.get("/api/deployment/outbox", response_model=List[OutboxRecordBody])
async def list_deployment_outbox(
    owner_service: str | None = Query(default=None),
    aggregate_id: str | None = Query(default=None),
):
    records = orchestration_service.list_outbox(
        owner_service=owner_service,
        aggregate_id=aggregate_id,
    )
    return [_outbox_body(record) for record in records]


@app.post("/api/deployment/outbox/{event_id}/consume", response_model=InboxReceiptBody)
async def consume_deployment_outbox_event(event_id: str, body: ConsumeOutboxEventRequest):
    try:
        return _inbox_body(
            orchestration_service.consume_outbox_event(
                event_id,
                consumer_name=body.consumer_name,
            )
        )
    except DeploymentSagaError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.get("/api/deployment/inbox", response_model=List[InboxReceiptBody])
async def list_deployment_inbox(
    consumer_name: str | None = Query(default=None),
    aggregate_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    receipts = orchestration_service.list_inbox(
        consumer_name=consumer_name,
        aggregate_id=aggregate_id,
        status=status,
    )
    return [_inbox_body(receipt) for receipt in receipts]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-deployment"}
