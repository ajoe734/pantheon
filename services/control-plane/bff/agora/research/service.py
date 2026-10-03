"""Agora Research application and use case service layer.

Part of BFF-ROUTER-USECASE-CORRECTIVE-001.
Encapsulates all store accesses, audit logging, outbox dispatch, candidate pool
lifecycle, scoring calculations, and member review logic away from HTTP route handlers.
"""
from __future__ import annotations

import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from fastapi import HTTPException
from .store import MemoryResearchPlanStore, PostgresResearchPlanStore
from services.control_plane.bff.agora.strategy_workshop.store import (
    MemoryWorkshopStore,
    PostgresWorkshopStore,
)
from services.control_plane.bff.agora.dataset_extraction.extractor import AgoraDatasetStore
from services.control_plane.bff.agora.trading_room.store import TradingRoomStore
from services.control_plane.bff.research.client import resolve_orchestrator_base_url
from services.research.constants import ALLOWLISTED_STAGE_BACKENDS

from .routes.common import (
    CandidateDiscussionRequest,
    CandidateMemberReviewRequest,
    CandidateMonitoringRequest,
    CandidatePoolCreateRequest,
    CandidatePoolFilterRequest,
    CandidateScoreRunRequest,
    ResearchPlanCreateRequest,
    _CANDIDATE_NO_ORDER_ROUTE_PROOF,
    _CAPABILITY,
    _MEMBER_ORDER_BY,
    _MEMBER_PAGE_TOKEN_PREFIX,
    _REVIEW_DECISION_TO_LIFECYCLE,
    _build_plan,
    _build_run_projection,
    _candidate_matches_filter,
    _candidate_pool_etag,
    _candidate_public_member,
    _default_registry_candidates,
    _discussion_record,
    _extract_run_artifact_identities,
    _load_default_scoring_recipe,
    _member_truth_projection,
    _normalize_metrics_to_dict,
    _operator_grade_scope,
    _parse_member_page_token,
    _plan_etag,
    _public_candidate_discussion,
    _public_candidate_monitoring,
    _public_candidate_pool,
    _rank_scores,
    _resolve_workshop_publisher,
    _run_projection_with_defaults,
    _score_candidate,
    _score_without_private_explanations,
    _validate_monitoring_body,
    _validate_pool_filter,
    _validate_review_body,
    publish_research_progress,
)

log = logging.getLogger(__name__)


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgoraResearchService:
    """Dedicated application service for Agora Research domain."""

    def __init__(
        self,
        *,
        store: Union[MemoryResearchPlanStore, PostgresResearchPlanStore],
        workshop_store: Optional[Union[MemoryWorkshopStore, PostgresWorkshopStore]] = None,
        dataset_store: Optional[AgoraDatasetStore] = None,
        trading_room_store: Optional[TradingRoomStore] = None,
        utc_now: Optional[Callable[[], str]] = None,
        bff_error: Optional[Callable[..., HTTPException]] = None,
    ) -> None:

        self.store = store
        self.workshop_store = workshop_store
        self.dataset_store = dataset_store
        self.trading_room_store = trading_room_store
        self.utc_now = utc_now or _default_utc_now
        self._bff_error = bff_error

    def bff_error(self, status_code: int, error_code: Any, message: str, detail: Any = None) -> HTTPException:
        if self._bff_error is not None:
            return self._bff_error(status_code, error_code, message, detail)
        code_str = getattr(error_code, "value", str(error_code))
        return HTTPException(
            status_code=status_code,
            detail={"error": {"code": code_str, "message": message, "detail": detail}},
        )

    def _error_code(self, name: str) -> Any:
        try:
            from services.control_plane.bff.models import ErrorCode
            return ErrorCode.__members__.get(name, name)
        except ImportError:
            try:
                from ...models import ErrorCode
                return ErrorCode.__members__.get(name, name)
            except ImportError:
                return name

    def _publish_research_event(
        self,
        workshop_id: str,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        if not workshop_id:
            return
        ws_publish = _resolve_workshop_publisher()
        try:
            ws_publish(workshop_id, event_type, data, utc_now_fn=self.utc_now)
        except Exception as exc:
            log.warning("Failed to publish research event %s: %s", event_type, exc)

    def _check_plan_if_match(self, plan: Dict[str, Any], if_match: Optional[str]) -> None:
        if if_match is None or not if_match.strip():
            return
        token = if_match.strip()
        if token == "*":
            return
        expected = _plan_etag(plan["plan_id"], plan.get("lock_version", 1))
        if token != expected:
            raise self.bff_error(
                412, self._error_code("PRECONDITION_FAILED"),
                f"If-Match condition failed for research plan: expected '{expected}', got '{token}'",
                "etag_mismatch",
            )

    def _check_candidate_pool_if_match(self, pool: Dict[str, Any], if_match: Optional[str]) -> None:
        if if_match is None or not if_match.strip():
            return
        token = if_match.strip()
        if token == "*":
            return
        expected = _candidate_pool_etag(pool["pool_id"], int(pool.get("lock_version", 1)))
        if token != expected:
            raise self.bff_error(
                412, self._error_code("PRECONDITION_FAILED"),
                f"If-Match condition failed for candidate pool: expected '{expected}', got '{token}'",
                "etag_mismatch",
            )

    # -----------------------------------------------------------------------
    # Research Plans Use Cases
    # -----------------------------------------------------------------------

    def list_workshop_plans(self, workshop_id: str, *, scope: Any) -> List[Dict[str, Any]]:
        return self.store.list_plans_for_workshop(
            workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )

    def create_workshop_plan(
        self,
        workshop_id: str,
        body: ResearchPlanCreateRequest,
        *,
        scope: Any,
        trace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        proposed_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self.utc_now()
        plan_id = str(uuid.uuid4())
        plan = _build_plan(
            body,
            workshop_id,
            plan_id,
            now,
            scope,
            workshop_store=self.workshop_store,
            trace_id=trace_id,
            correlation_id=correlation_id,
        )
        plan = self.store.create_plan(plan)
        self.store.record_audit_action({
            "action_type": "research_plan.create",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_plan",
            "subject_id": plan_id,
            "workshop_id": workshop_id,
            "payload": {"status": plan["status"], **({"proposed_by": proposed_by} if proposed_by else {})},
        })
        self._publish_research_event(
            workshop_id,
            "research.plan.created",
            {"plan_id": plan_id, "status": plan["status"]},
        )
        return plan

    def get_plan(self, plan_id: str, *, scope: Any) -> Optional[Dict[str, Any]]:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            return None
        if plan.get("tenant_id") and plan.get("tenant_id") != scope.tenant_id:
            return None
        if plan.get("user_id") and plan.get("user_id") != scope.user_id:
            return None
        return self._project_plan_from_owner(plan, scope)

    def _owner_run_records(self, plan: Dict[str, Any], scope: Any) -> Optional[List[Dict[str, Any]]]:
        base_url = resolve_orchestrator_base_url()
        if not base_url:
            return None
        try:
            from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations
            records = WorkshopCanonicalOperations(research_base_url=base_url).list_research_runs()
        except Exception as exc:
            log.warning("Research owner plan projection unavailable: %s", exc)
            return None
        plan_id = str(plan.get("plan_id") or "")
        return [
            record for record in records
            if isinstance(record, dict)
            and any(ref.get("type") == "research_plan" and str(ref.get("id")) == plan_id
                    for ref in record.get("input_refs") or [] if isinstance(ref, dict))
            and (not record.get("tenant_id") or record.get("tenant_id") == scope.tenant_id)
            and (not record.get("user_id") or record.get("user_id") == scope.user_id)
        ]

    def _project_plan_from_owner(self, plan: Dict[str, Any], scope: Any) -> Dict[str, Any]:
        result = dict(plan)
        records = self._owner_run_records(plan, scope)
        if records is None:
            return result
        latest: Dict[str, Dict[str, Any]] = {}
        for record in records:
            stage_id = str(record.get("stage_id") or "")
            previous = latest.get(stage_id)
            rank = int(record.get("attempt_number") or 1)
            if stage_id and (previous is None or rank >= int(previous.get("attempt_number") or 1)):
                latest[stage_id] = record
        if not records:
            result["stages"] = [{**stage, "status": "pending"} for stage in plan.get("stages") or []]
            result["run_ids"] = []
            result["status"] = "approved" if plan.get("approved_at") else "draft"
            return result
        status_map = {"completed": "succeeded", "succeeded": "succeeded", "failed": "failed", "rejected": "failed", "canceled": "cancelled", "cancelled": "cancelled"}
        stages = []
        for stage in plan.get("stages") or []:
            owner = latest.get(str(stage.get("stage_id") or ""))
            if owner:
                status = str(owner.get("status") or "queued").lower()
                stage = {**stage, "status": status_map.get(status, status)}
            stages.append(stage)
        result["stages"] = stages
        result["run_ids"] = [str(record.get("run_id") or record.get("id")) for record in records]
        statuses = [str(record.get("status") or "").lower() for record in latest.values()]
        if all(status in {"completed", "succeeded"} for status in statuses) and len(latest) == len(stages):
            result["status"] = "completed"
        elif any(status in {"failed", "rejected", "canceled", "cancelled"} for status in statuses):
            result["status"] = "failed"
        else:
            result["status"] = "running"
        return result

    def get_plan_or_404(self, plan_id: str, *, scope: Any) -> Dict[str, Any]:
        plan = self.get_plan(plan_id, scope=scope)
        if plan is None:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Research plan not found", plan_id)
        return plan

    def check_idempotency(self, scope: Any, endpoint: str, key: str) -> None:
        scope_str = f"{scope.user_id}:{scope.tenant_id}:{endpoint}"
        if self.store.check_and_record_idempotency_key(scope_str, key):
            raise self.bff_error(409, self._error_code("IDEMPOTENCY_CONFLICT"), "Duplicate Idempotency-Key", key)

    def _plan_for_decision(self, plan_id: str, scope: Any) -> Dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if plan is None or plan.get("tenant_id") != scope.tenant_id:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Research plan not found", plan_id)
        workshop = self.workshop_store.get_session(plan["workshop_id"]) if self.workshop_store else None
        if workshop is not None and workshop.get("tenant_id") != scope.tenant_id:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Research plan not found", plan_id)
        if not _operator_grade_scope(scope) or (
            "operator" not in scope.roles
            and (workshop is None or workshop.get("user_id") != scope.user_id)
        ):
            raise self.bff_error(403, self._error_code("FORBIDDEN"), "Workshop owner or operator required", plan_id)
        return plan

    def approve_plan(
        self,
        plan_id: str,
        *,
        scope: Any,
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan = self._plan_for_decision(plan_id, scope)
        self._check_plan_if_match(plan, if_match)
        if plan["status"] != "draft":
            raise self.bff_error(
                409, self._error_code("RESOURCE_CONFLICT"),
                f"Plan cannot be approved from status '{plan['status']}'",
                f"expected status 'draft', got '{plan['status']}'",
            )
        now = self.utc_now()
        new_version = plan.get("lock_version", 1) + 1
        self.store.update_plan(
            plan_id,
            {
                "status": "approved",
                "approved_at": now,
                "approval": {
                    "state": "approved",
                    "decided_by": scope.user_id,
                    "decided_at": now,
                },
                "lock_version": new_version,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=plan.get("user_id"),
        )
        self.store.record_audit_action({
            "action_type": "research_plan.approve",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_plan",
            "subject_id": plan_id,
            "payload": {"status": "approved"},
        })
        self._publish_research_event(
            plan.get("workshop_id", ""),
            "research.plan.approved",
            {"plan_id": plan_id, "status": "approved"},
        )
        return {"plan_id": plan_id, "status": "approved", "lock_version": new_version}

    def cancel_plan(
        self,
        plan_id: str,
        *,
        scope: Any,
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan = self._plan_for_decision(plan_id, scope)
        self._check_plan_if_match(plan, if_match)
        cancellable = {"draft", "approved", "running"}
        if plan["status"] not in cancellable:
            raise self.bff_error(
                409, self._error_code("RESOURCE_CONFLICT"),
                f"Plan in status '{plan['status']}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable)}",
            )
        now = self.utc_now()
        new_version = plan.get("lock_version", 1) + 1
        self.store.update_plan(
            plan_id,
            {
                "status": "cancelled",
                "lock_version": new_version,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=plan.get("user_id"),
        )
        self.store.record_audit_action({
            "action_type": "research_plan.cancel",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_plan",
            "subject_id": plan_id,
            "payload": {"status": "cancelled"},
        })
        self._publish_research_event(
            plan.get("workshop_id", ""),
            "research.plan.cancelled",
            {"plan_id": plan_id, "status": "cancelled"},
        )
        return {"plan_id": plan_id, "status": "cancelled", "lock_version": new_version}

    # -----------------------------------------------------------------------
    # Research Runs Use Cases
    # -----------------------------------------------------------------------

    def list_runs_for_plan(self, plan_id: str, *, scope: Any) -> List[Dict[str, Any]]:
        plan = self.get_plan(plan_id, scope=scope)
        if plan is None:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), f"Research plan '{plan_id}' not found", plan_id)
        owner_runs = self._owner_run_records(plan, scope)
        if owner_runs is not None:
            stages = {str(stage.get("stage_id")): stage for stage in plan.get("stages") or []}
            projected = []
            for owner in owner_runs:
                stage = stages.get(str(owner.get("stage_id") or ""), {})
                run = _build_run_projection(plan=plan, stage=stage or {"stage_id": owner.get("stage_id", "unknown"), "stage_type": owner.get("adapter", "unknown")}, run_id=str(owner.get("run_id") or owner.get("id")), now=str(owner.get("created_at") or self.utc_now()), scope=scope)
                status = str(owner.get("status") or "queued").lower()
                run.update({"task_id": owner.get("task_id"), "attempt_number": owner.get("attempt_number", 1), "parent_run_id": owner.get("parent_run_id"), "execution_status": {"completed": "succeeded", "failed": "failed", "rejected": "failed", "canceled": "cancelled"}.get(status, status), "outcome": "pass" if status == "completed" else ("fail" if status in {"failed", "rejected"} else "pending"), "artifact_refs": owner.get("artifact_refs") or [], "updated_at": owner.get("updated_at") or owner.get("created_at")})
                projected.append(run)
            return projected
        runs = self.store.list_runs_for_plan(plan_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
        return [_run_projection_with_defaults(r, store=self.store) for r in runs]

    def dispatch_plan(
        self,
        plan_id: str,
        *,
        scope: Any,
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        plan = self.get_plan(plan_id, scope=scope)
        if plan is None:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), f"Research plan '{plan_id}' not found", plan_id)
        self._check_plan_if_match(plan, if_match)
        if plan["status"] != "approved":
            if plan["status"] in {"running", "completed", "failed"}:
                existing = self.list_runs_for_plan(plan_id, scope=scope)
                if existing:
                    first = existing[0]
                    return {"run_id": first["run_id"], "plan_id": plan_id, "stage_id": first["stage_id"], "stage_type": first["stage_type"]}
            raise self.bff_error(
                409, self._error_code("RESOURCE_CONFLICT"),
                f"Only approved plans may be dispatched; current status: '{plan['status']}'",
                f"expected 'approved', got '{plan['status']}'",
            )
        dispatch_stage = None
        for stage in plan.get("stages", []):
            if stage.get("status") in ("pending", "ready"):
                dispatch_stage = stage
                break
        if dispatch_stage is None:
            raise self.bff_error(
                409, self._error_code("RESOURCE_CONFLICT"),
                "No pending or ready stages to dispatch",
                "all_stages_dispatched_or_blocked",
            )
        now = self.utc_now()
        r_url = resolve_orchestrator_base_url()
        if not r_url:
            raise self.bff_error(
                503,
                self._error_code("DEPENDENCY_UNAVAILABLE"),
                "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL)",
                plan_id,
            )

        routing = dispatch_stage.get("routing") or {}
        stage_type = str(dispatch_stage.get("stage_type") or "")
        preferred_backend = str(routing.get("preferred_backend") or ALLOWLISTED_STAGE_BACKENDS.get(stage_type) or dispatch_stage.get("framework") or dispatch_stage.get("backend") or "stub").strip().lower()
        backend_mode = str(routing.get("backend_mode") or "real").strip().lower()

        if os.getenv(f"AGORA_RESEARCH_{stage_type.upper()}_UNAVAILABLE") == "1" or os.getenv(f"AGORA_RESEARCH_{preferred_backend.upper()}_UNAVAILABLE") == "1":
            raise self.bff_error(503, self._error_code("DEPENDENCY_UNAVAILABLE"), f"Backend execution owner for stage '{stage_type}' ({preferred_backend}) is currently unavailable", plan_id)

        if backend_mode in ("real", "simulation") and preferred_backend not in {"vectorbt", "statsmodels", "quantlib", "stub"}:
            raise self.bff_error(503, self._error_code("DEPENDENCY_UNAVAILABLE"), f"Backend execution owner for stage '{stage_type}' ({preferred_backend}) is absent or not configured", plan_id)

        from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations, CanonicalOperationError

        resolved_ds = dispatch_stage.get("dataset") or plan.get("dataset")
        if not resolved_ds:
            try:
                from .dispatcher import resolve_governed_dataset
                resolved_ds = resolve_governed_dataset(dispatch_stage, plan, dataset_store=getattr(self, "dataset_store", None), tenant_id=getattr(scope, "tenant_id", None), user_id=getattr(scope, "user_id", None))
            except Exception as exc:
                log.warning("Failed to resolve governed dataset for plan %s: %s", plan_id, exc)
                resolved_ds = None

        dispatch_stage_payload = dict(dispatch_stage)
        if resolved_ds and "dataset" not in dispatch_stage_payload:
            dispatch_stage_payload["dataset"] = resolved_ds

        actor = getattr(scope, "user_id", "operator") or "operator"
        task_p = {
            "title": plan.get("title") or f"Plan {plan_id}", "objective": plan.get("objective") or f"Execution {plan_id}",
            "tenant_id": getattr(scope, "tenant_id", None), "user_id": getattr(scope, "user_id", None),
            "source_refs": [{"type": "research_plan", "id": plan_id}, {"type": "strategy", "id": plan.get("strategy_id")}],
            "constraints": {"environment": "research"}, "actor_id": actor, "idempotency_key": f"plan-task-{plan_id}",
        }
        input_refs = [{"type": "research_plan", "id": plan_id}, {"type": "stage", "id": dispatch_stage["stage_id"]}]
        if resolved_ds and isinstance(resolved_ds, dict) and resolved_ds.get("dataset_id"):
            input_refs.append({"type": "dataset", "id": resolved_ds["dataset_id"]})

        run_p = {
            "adapter": preferred_backend, "requested_mode": backend_mode, "dispatch_mode": backend_mode,
            "tenant_id": getattr(scope, "tenant_id", None), "user_id": getattr(scope, "user_id", None),
            "input_refs": input_refs,
            "parameters": {
                **(dispatch_stage.get("parameters") or {}), "stage": dispatch_stage_payload, "plan": plan, "dataset": resolved_ds,
                "tenant_id": getattr(scope, "tenant_id", None), "user_id": getattr(scope, "user_id", None),
                "correlation_id": plan.get("correlation_id") or f"corr-{plan_id}-{dispatch_stage['stage_id']}",
            },
            "actor_id": actor, "idempotency_key": f"plan-run-{plan_id}-{dispatch_stage['stage_id']}",
        }

        try:
            dispatched = WorkshopCanonicalOperations(research_base_url=r_url).dispatch_research_run(
                task_payload=task_p,
                run_payload=run_p,
            )
        except CanonicalOperationError as exc:
            status_code = exc.status_code if exc.status_code and exc.status_code >= 400 else 503
            raise self.bff_error(
                status_code,
                self._error_code("DEPENDENCY_UNAVAILABLE"),
                f"Research orchestrator dispatch failed: {exc}",
                plan_id,
            ) from exc
        except Exception as exc:
            raise self.bff_error(
                503,
                self._error_code("DEPENDENCY_UNAVAILABLE"),
                f"Research orchestrator dispatch error: {exc}",
                plan_id,
            ) from exc

        task_obj = dispatched.get("task") if isinstance(dispatched.get("task"), dict) else {}
        run_obj = dispatched.get("run") if isinstance(dispatched.get("run"), dict) else {}
        task_id = str(task_obj.get("task_id") or task_obj.get("id") or dispatched.get("task_id") or "").strip()
        run_id = str(run_obj.get("run_id") or run_obj.get("id") or dispatched.get("run_id") or "").strip()
        if not task_id or not run_id:
            raise self.bff_error(
                502,
                self._error_code("DEPENDENCY_UNAVAILABLE"),
                "Authoritative research orchestrator returned invalid task or run IDs",
                plan_id,
            )

        run = _build_run_projection(
            plan=plan,
            stage=dispatch_stage,
            run_id=run_id,
            now=now,
            scope=scope,
        )
        run["task_id"] = task_id
        owner_status = str(run_obj.get("status") or "").lower()
        if owner_status == "completed":
            run["execution_status"] = "succeeded"
            run["outcome"] = "pass"
            if run_obj.get("artifact_refs"):
                run["artifact_refs"] = run_obj["artifact_refs"]
            if run_obj.get("metrics"):
                run["metrics"] = run_obj["metrics"]
            if run_obj.get("receipt"):
                run["receipt"] = run_obj["receipt"]
                if hasattr(self.store, "record_execution_receipt"):
                    self.store.record_execution_receipt(run_obj["receipt"])
        elif owner_status in ("failed", "rejected"):
            run["execution_status"] = "failed"
            run["outcome"] = "fail"

        self.store.create_run(run)
        stage_status = "succeeded" if owner_status == "completed" else ("failed" if owner_status in ("failed", "rejected") else "queued")
        updated_stages = [
            {**s, "status": stage_status} if s["stage_id"] == dispatch_stage["stage_id"] else s
            for s in plan.get("stages", [])
        ]
        plan_run_ids = list(plan.get("run_ids") or [])
        if run_id not in plan_run_ids:
            plan_run_ids.append(run_id)
        self.store.update_plan(
            plan_id,
            {
                "stages": updated_stages,
                "run_ids": plan_run_ids,
                "status": "running",
                "lock_version": plan.get("lock_version", 1) + 1,
                "updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        self._publish_research_event(
            plan.get("workshop_id", ""),
            "research.run.queued",
            {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
                "percent": 0,
            },
        )
        self.store.record_audit_action({
            "action_type": "research_plan.dispatch",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "research_run",
            "subject_id": run_id,
            "plan_id": plan_id,
            "stage_id": dispatch_stage["stage_id"],
        })
        return {
            "run_id": run_id,
            "plan_id": plan_id,
            "stage_id": dispatch_stage["stage_id"],
            "stage_type": dispatch_stage["stage_type"],
        }

    def get_run(self, run_id: str, *, scope: Any) -> Optional[Dict[str, Any]]:
        raw_run = self.store.get_run(run_id, tenant_id=scope.tenant_id, user_id=scope.user_id) if hasattr(self.store, "get_run") else None
        r_url = resolve_orchestrator_base_url()
        if r_url:
            try:
                from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations
                owner_run = WorkshopCanonicalOperations(research_base_url=r_url).get_research_run(run_id)
                if owner_run and isinstance(owner_run, dict):
                    owner_status = str(owner_run.get("status") or "").lower()
                    status_map = {
                        "completed": "succeeded",
                        "failed": "failed",
                        "rejected": "failed",
                        "canceled": "cancelled",
                        "cancelled": "cancelled",
                    }
                    mapped_status = status_map.get(owner_status, owner_status or "queued")
                    mapped_outcome = "pass" if owner_status == "completed" else ("fail" if owner_status in ("failed", "rejected") else None)
                    owner_user_id = str(owner_run.get("created_by") or owner_run.get("user_id") or (owner_run.get("parameters") or {}).get("user_id") or "").strip()
                    owner_tenant_id = str(owner_run.get("tenant_id") or (owner_run.get("parameters") or {}).get("tenant_id") or "").strip()
                    if owner_user_id and getattr(scope, "user_id", None) and owner_user_id != scope.user_id:
                        return None
                    if owner_tenant_id and getattr(scope, "tenant_id", None) and owner_tenant_id != scope.tenant_id:
                        return None

                    if raw_run is None:
                        plan_id = "plan-unknown"
                        stage_id = "stage-unknown"
                        for ref in owner_run.get("input_refs") or []:
                            if isinstance(ref, dict):
                                if ref.get("type") == "research_plan" and ref.get("id"):
                                    plan_id = str(ref.get("id"))
                                elif ref.get("type") == "stage" and ref.get("id"):
                                    stage_id = str(ref.get("id"))
                        st_type = owner_run.get("stage_type") or owner_run.get("adapter") or "prototype_backtest"
                        ws_id = ""
                        strat_id = ""
                        reg_id = ""
                        if self.store and hasattr(self.store, "get_plan") and plan_id != "plan-unknown":
                            try:
                                stored_plan = self.store.get_plan(plan_id)
                                if stored_plan:
                                    ws_id = stored_plan.get("workshop_id", "")
                                    strat_id = stored_plan.get("strategy_id", "")
                                    reg_id = stored_plan.get("strategy_spec_registry_id", "")
                                    for stg in stored_plan.get("stages", []):
                                        if stg.get("stage_id") == stage_id:
                                            st_type = stg.get("stage_type") or st_type
                                            break
                            except Exception:
                                pass
                        raw_run = {
                            "run_id": run_id,
                            "task_id": owner_run.get("task_id") or f"task-{run_id}",
                            "plan_id": plan_id,
                            "stage_id": stage_id,
                            "stage_type": st_type,
                            "workshop_id": ws_id,
                            "strategy_id": strat_id,
                            "strategy_spec_registry_id": reg_id,
                            "tenant_id": owner_tenant_id or scope.tenant_id,
                            "user_id": owner_user_id or scope.user_id,
                            "execution_status": mapped_status,
                            "outcome": mapped_outcome,
                            "artifact_refs": owner_run.get("artifact_refs") or [],
                            "metrics": owner_run.get("metrics") or [],
                            "backend": {"mode": owner_run.get("requested_mode") or owner_run.get("dispatch_mode") or "real"},
                            "provenance": owner_run.get("provenance") or "real",
                            "created_at": owner_run.get("created_at") or self.utc_now(),
                            "updated_at": owner_run.get("updated_at") or self.utc_now(),
                        }
                        if owner_run.get("receipt") and hasattr(self.store, "record_execution_receipt"):
                            self.store.record_execution_receipt(owner_run["receipt"])
                        if hasattr(self.store, "create_run"):
                            self.store.create_run(raw_run)
                    else:
                        raw_run["execution_status"] = mapped_status
                        raw_run["outcome"] = mapped_outcome
                        if owner_run.get("artifact_refs"):
                            raw_run["artifact_refs"] = owner_run["artifact_refs"]
                        if owner_run.get("metrics"):
                            raw_run["metrics"] = owner_run["metrics"]
                        if owner_run.get("provenance"):
                            raw_run["provenance"] = owner_run["provenance"]
                            if isinstance(raw_run.get("backend"), dict):
                                raw_run["backend"]["mode"] = owner_run["provenance"]
                        if owner_run.get("receipt") and hasattr(self.store, "record_execution_receipt"):
                            self.store.record_execution_receipt(owner_run["receipt"])
                        if hasattr(self.store, "update_run"):
                            self.store.update_run(run_id, raw_run, tenant_id=scope.tenant_id, user_id=scope.user_id)
            except Exception as exc:
                log.warning("Research orchestrator get_run readback error: %s", exc)

        if raw_run is None:
            return None
        if raw_run.get("user_id") and getattr(scope, "user_id", None) and raw_run.get("user_id") != scope.user_id:
            return None
        if raw_run.get("tenant_id") and getattr(scope, "tenant_id", None) and raw_run.get("tenant_id") != scope.tenant_id:
            return None
        return _run_projection_with_defaults(raw_run, store=self.store)

    def get_run_or_404(self, run_id: str, *, scope: Any) -> Dict[str, Any]:
        run = self.get_run(run_id, scope=scope)
        if run is None or (run.get("tenant_id") and run.get("tenant_id") != scope.tenant_id) or (run.get("user_id") and run.get("user_id") != scope.user_id):
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Research run not found", run_id)
        return run

    def cancel_run(self, run_id: str, *, scope: Any) -> Dict[str, Any]:
        raw_run = self.get_run_or_404(run_id, scope=scope)
        r_url = resolve_orchestrator_base_url()
        if not r_url:
            raise self.bff_error(
                503,
                self._error_code("UPSTREAM_UNAVAILABLE"),
                "Research execution owner is unconfigured or unavailable; run cancellation cannot be processed locally",
                run_id,
            )
        from services.control_plane.bff.agora.strategy_workshop.operations import CanonicalOperationError, WorkshopCanonicalOperations
        try:
            WorkshopCanonicalOperations(research_base_url=r_url).cancel_research_run(run_id)
        except CanonicalOperationError as exc:
            if exc.status_code == 404:
                raise self.bff_error(
                    404, self._error_code("RESOURCE_NOT_FOUND"), exc.reason, run_id
                ) from exc
            if exc.status_code == 409:
                raise self.bff_error(
                    409, self._error_code("RESOURCE_CONFLICT"), exc.reason, run_id
                ) from exc
            raise self.bff_error(
                503, self._error_code("UPSTREAM_UNAVAILABLE"), exc.reason, run_id
            ) from exc
        except Exception as exc:
            raise self.bff_error(
                503, self._error_code("UPSTREAM_UNAVAILABLE"), str(exc), run_id
            ) from exc
        now = self.utc_now()
        if hasattr(self.store, "update_run"):
            self.store.update_run(
                run_id,
                {
                    "execution_status": "cancelled",
                    "progress": {
                        **(raw_run.get("progress") or {}),
                        "phase": "cancelled",
                        "message": "Run cancellation accepted",
                        "updated_at": now,
                    },
                    "completed_at": now,
                    "updated_at": now,
                },
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
        publish_research_progress(
            raw_run.get("workshop_id", ""),
            run_id,
            float((raw_run.get("progress") or {}).get("percent", 0)),
            "Run cancellation accepted",
            phase="cancelled",
            utc_now_fn=self.utc_now,
        )
        if hasattr(self.store, "record_audit_action"):
            self.store.record_audit_action({
                "action_type": "research_run.cancel",
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "subject_type": "research_run",
                "subject_id": run_id,
            })
        return {"run_id": run_id, "execution_status": "cancelled"}

    def get_run_artifacts(self, run_id: str, *, scope: Any) -> List[Dict[str, Any]]:
        raw_run = self.get_run_or_404(run_id, scope=scope)
        r_url = resolve_orchestrator_base_url()
        if r_url:
            try:
                from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations
                owner_artifacts = WorkshopCanonicalOperations(research_base_url=r_url).get_research_artifacts(run_id)
                if owner_artifacts:
                    return owner_artifacts
            except Exception as exc:
                log.warning("Research orchestrator get_run_artifacts error: %s", exc)
        artifact_refs = raw_run.get("artifact_refs") or []
        evidence_refs = raw_run.get("evidence_refs") or []
        return (
            [{"ref_type": "experiment_artifact", "ref_id": a} for a in artifact_refs]
            + list(evidence_refs)
        )

    # -----------------------------------------------------------------------
    # Candidate Pools Use Cases
    # -----------------------------------------------------------------------

    def lookup_strategy_candidate_pool(
        self,
        *,
        scope: Any,
        strategy_id: Optional[str] = None,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        target_id = strategy_id or ""
        return self.store.get_candidate_pool_for_strategy(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            strategy_id=target_id,
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
        )

    def get_candidate_pool_for_strategy(
        self,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.store.get_candidate_pool_for_strategy(
            user_id=user_id,
            tenant_id=tenant_id,
            strategy_id=strategy_id or "",
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
        )

    def get_strategy_candidate_pool(
        self,
        strategy_id: str,
        *,
        scope: Any,
        strategy_version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.store.get_candidate_pool_for_strategy(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        )

    def create_candidate_pool(
        self,
        body: CandidatePoolCreateRequest,
        *,
        scope: Any,
    ) -> Dict[str, Any]:
        operator_id = getattr(scope, "operator_id", scope.user_id)
        if body.operator_id not in {scope.user_id, operator_id}:
            raise self.bff_error(
                422, self._error_code("VALIDATION_FAILED"),
                "operator_id must match the authenticated Agora operator",
                f"operator_id={body.operator_id!r}, authenticated={operator_id!r}",
            )
        recipe = _load_default_scoring_recipe()
        if body.recipe_id and body.recipe_id != recipe["recipe_id"]:
            raise self.bff_error(
                422, self._error_code("VALIDATION_FAILED"),
                "Only the active winner-branch CandidateScoringRecipe is available in this BFF slice",
                body.recipe_id,
            )
        pool_filter = (
            body.filter.model_dump()
            if body.filter is not None
            else CandidatePoolFilterRequest().model_dump()
        )
        _validate_pool_filter(pool_filter, self.bff_error, lambda: self._error_code("VALIDATION_FAILED"))

        now = self.utc_now()
        candidates: List[Dict[str, Any]] = []
        metrics_by_artifact: Dict[str, Dict[str, Any]] = {}
        exclusion_reasons: List[str] = []

        profile = (
            body.profile
            or os.environ.get("AGORA_CANDIDATE_POOL_PROFILE")
            or ("demo" if os.environ.get("PANTHEON_BFF_AUTH_MODE") == "permissive" and not os.environ.get("AGORA_CANDIDATE_POOL_PROFILE") == "production" else "production")
        ).lower()

        if body.candidates is not None:
            for candidate in body.candidates:
                if not _candidate_matches_filter(candidate, pool_filter):
                    continue
                public_candidate = _candidate_public_member(candidate)
                public_candidate["_updated_at"] = str(
                    candidate.get("_updated_at")
                    or public_candidate.get("created_at")
                    or now
                )

                # Mandatory deletion of client-trusted real-provenance flags
                public_candidate.pop("has_real_receipt", None)
                for trust_key in ("trusted", "is_real", "verified", "no_order_route_proof"):
                    public_candidate.pop(trust_key, None)

                # Resolve terminal run and authentic execution receipt server-side
                run_id = candidate.get("run_id")
                if not run_id and candidate.get("run_ref"):
                    ref_str = str(candidate["run_ref"])
                    run_id = ref_str.split("/")[-1] if "/" in ref_str else ref_str

                run = None
                if run_id and self.store and hasattr(self.store, "get_run"):
                    try:
                        run = self.store.get_run(run_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
                    except TypeError:
                        run = self.store.get_run(run_id)

                # Strictly verify tenant isolation
                if run:
                    run_tenant = run.get("tenant_id")
                    if run_tenant and scope.tenant_id and run_tenant != scope.tenant_id:
                        run = None

                receipt = None
                resolved_prov = "simulation"
                if run:
                    status = str(run.get("execution_status") or "").lower()
                    terminal_statuses = {"succeeded", "completed"}

                    plan = None
                    if hasattr(self.store, "get_plan") and run.get("plan_id"):
                        try:
                            plan = self.store.get_plan(run["plan_id"])
                        except Exception:
                            plan = None

                    expected_correlation = (
                        run.get("correlation_id")
                        or run.get("trace_id")
                        or (plan.get("correlation_id") if plan else None)
                        or (plan.get("trace_id") if plan else None)
                    )
                    expected_owner = (
                        run.get("executor")
                        or run.get("owner")
                        or (plan.get("executor") if plan else None)
                        or (plan.get("owner") if plan else None)
                    )

                    try:
                        from .receipt import resolve_run_provenance
                    except ImportError:
                        from agora.research.receipt import resolve_run_provenance
                    prov, rec = resolve_run_provenance(
                        self.store,
                        run,
                        expected_correlation_id=expected_correlation,
                        expected_owner=expected_owner,
                    )

                    # Keep immutable receipt snapshot for client admission verification
                    immutable_rec = rec
                    cand_artifact_id = str(public_candidate.get("artifact_id") or "").strip()

                    if immutable_rec is not None:
                        cand_corr = candidate.get("correlation_id")
                        if cand_corr and str(cand_corr).strip() != str(immutable_rec.get("correlation_id", "")).strip():
                            prov = "unavailable"
                            rec = None

                        cand_owner = candidate.get("executor") or candidate.get("owner")
                        if cand_owner and str(cand_owner).strip() != str(immutable_rec.get("executor", "")).strip():
                            prov = "unavailable"
                            rec = None

                        cand_receipt_id = candidate.get("receipt_id")
                        if cand_receipt_id and str(cand_receipt_id).strip() != str(immutable_rec.get("receipt_id", "")).strip():
                            prov = "unavailable"
                            rec = None

                        cand_digest = candidate.get("artifact_digest")
                        if cand_digest:
                            expected_digest = str(immutable_rec.get("artifact_digest") or "").strip()
                            if not expected_digest or str(cand_digest).strip() != expected_digest:
                                prov = "unavailable"
                                rec = None

                        # Validate candidate artifact_id against canonical run artifacts from owner result
                        canonical_art_ids, known_digests = _extract_run_artifact_identities(run)
                        if not canonical_art_ids or cand_artifact_id not in canonical_art_ids:
                            prov = "unavailable"
                            rec = None
                        else:
                            art_digest = known_digests.get(cand_artifact_id)
                            if immutable_rec.get("artifact_digest"):
                                rec_digest = str(immutable_rec["artifact_digest"]).strip()
                                if art_digest and art_digest != rec_digest:
                                    prov = "unavailable"
                                    rec = None
                            if cand_digest and art_digest and str(cand_digest).strip() != art_digest:
                                prov = "unavailable"
                                rec = None

                    if status not in terminal_statuses and prov == "real":
                        prov = "simulation"
                        rec = None

                    resolved_prov = prov
                    receipt = rec
                else:
                    stored_prov = str(candidate.get("provenance") or "").lower().strip()
                    if stored_prov in ("fixture",):
                        resolved_prov = "fixture"
                    elif stored_prov in ("unavailable",):
                        resolved_prov = "unavailable"
                    else:
                        resolved_prov = "simulation"

                public_candidate["provenance"] = resolved_prov
                public_candidate["has_real_receipt"] = bool(resolved_prov == "real" and receipt is not None)
                if receipt and "receipt_id" in receipt:
                    public_candidate["receipt_id"] = receipt["receipt_id"]
                    if receipt.get("artifact_digest"):
                        public_candidate["artifact_digest"] = receipt["artifact_digest"]
                elif not receipt:
                    public_candidate.pop("receipt_id", None)

                candidates.append(public_candidate)
                cand_metrics: Dict[str, Any] = {}
                if run:
                    if run.get("metrics"):
                        cand_metrics.update(_normalize_metrics_to_dict(run["metrics"]))
                elif profile in ("demo", "test") or getattr(scope, "auth_stub", False):
                    if body.metrics_by_artifact and public_candidate["artifact_id"] in body.metrics_by_artifact:
                        client_art_metrics = body.metrics_by_artifact[public_candidate["artifact_id"]]
                        if isinstance(client_art_metrics, dict):
                            cand_metrics.update(client_art_metrics)
                    elif candidate.get("_metrics") and isinstance(candidate.get("_metrics"), dict):
                        cand_metrics.update(candidate["_metrics"])
                metrics_by_artifact[public_candidate["artifact_id"]] = cand_metrics
        elif profile in ("demo", "test") or getattr(scope, "auth_stub", False):
            try:
                import agora.research.router as _r_router
                _cand_fn = getattr(_r_router, "_default_registry_candidates", _default_registry_candidates)
            except Exception:
                _cand_fn = _default_registry_candidates
            for candidate in _cand_fn(now):
                if not _candidate_matches_filter(candidate, pool_filter):
                    continue
                public_candidate = _candidate_public_member(candidate)
                public_candidate["_updated_at"] = str(
                    candidate.get("_updated_at")
                    or public_candidate.get("created_at")
                    or now
                )
                candidates.append(public_candidate)
                metrics_by_artifact[public_candidate["artifact_id"]] = candidate.get("_metrics") or {}
        else:
            # Production behavior: never insert prototype candidates without authoritative input
            exclusion_reasons = [
                "no_authoritative_registry_candidates_discovered",
                "no_eligible_research_artifacts_match_filter",
            ]

        pool_id = f"cpool-{uuid.uuid4().hex[:16]}"
        strategy_family = (
            pool_filter.get("strategy_families", [None])[0]
            if pool_filter.get("strategy_families")
            else recipe.get("strategy_family")
        )
        metadata: Dict[str, Any] = {
            "strategy_family": strategy_family,
            "recipe_id": recipe["recipe_id"],
            "recipe_version": int(recipe["version"]),
            "data_cutoff": now,
            "last_score_run_at": None,
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        }
        if body.strategy_id:
            metadata["strategy_id"] = body.strategy_id
        if body.strategy_version:
            metadata["strategy_version"] = body.strategy_version
        if body.strategy_ref:
            metadata["strategy_ref"] = body.strategy_ref
        if exclusion_reasons:
            metadata["exclusion_reasons"] = exclusion_reasons

        pool = {
            "spec_version": "1.0",
            "pool_id": pool_id,
            "operator_id": body.operator_id,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "filter": pool_filter,
            "candidates": candidates,
            "total": len(candidates),
            "snapshot_at": now,
            "lock_version": 1,
            "metadata": metadata,
        }
        if exclusion_reasons:
            pool["exclusion_reasons"] = exclusion_reasons
        created_pool = self.store.create_candidate_pool(pool, metrics_by_artifact=metrics_by_artifact)
        self.store.record_audit_action({
            "action_type": "candidate_pool.create",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool",
            "subject_id": pool["pool_id"],
            "payload": {"total": pool.get("total", 0)},
        })
        return created_pool

    def list_candidate_pools(
        self,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
        strategy_family: Optional[str] = None,
        strategy_id: Optional[str] = None,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        return self.store.list_candidate_pools(
            user_id=user_id,
            tenant_id=tenant_id,
            lifecycle_state=lifecycle_state,
            strategy_family=strategy_family,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
            **kwargs,
        )

    def get_candidate_pool(self, pool_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_candidate_pool(pool_id)

    def get_candidate_pool_or_404(self, pool_id: str) -> Dict[str, Any]:
        pool = self.store.get_candidate_pool(pool_id)
        if pool is None:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Candidate pool not found", pool_id)
        return pool

    def require_pool_access(self, pool: Dict[str, Any], scope: Any) -> None:
        if pool.get("tenant_id") != scope.tenant_id or pool.get("user_id") != scope.user_id:
            raise self.bff_error(403, self._error_code("FORBIDDEN"), "Candidate pool not owned by caller", pool["pool_id"])

    def delete_candidate_pool(self, pool_id: str, *, scope: Any) -> bool:
        pool = self.get_candidate_pool_or_404(pool_id)
        self.require_pool_access(pool, scope)
        return bool(self.store.delete_candidate_pool(pool_id))

    def list_candidate_scores(self, pool_id: str) -> List[Dict[str, Any]]:
        return self.store.list_candidate_scores(pool_id)

    def compute_and_store_candidate_scores(
        self,
        pool: Dict[str, Any],
        *,
        recipe_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        recipe = _load_default_scoring_recipe()
        if recipe_id and recipe_id != recipe["recipe_id"]:
            raise self.bff_error(
                422, self._error_code("VALIDATION_FAILED"),
                "Unknown CandidateScoringRecipe for candidate pool score run",
                recipe_id,
            )
        pool_id = pool["pool_id"]
        scored_at = self.utc_now()
        data_cutoff = (pool.get("metadata") or {}).get("data_cutoff") or pool.get("snapshot_at") or scored_at
        scores = _rank_scores([
            _score_candidate(
                pool_id=pool_id,
                candidate=candidate,
                metrics=self.store.get_candidate_metrics(pool_id, candidate["artifact_id"]),
                recipe=recipe,
                data_cutoff=data_cutoff,
                scored_at=scored_at,
            )
            for candidate in pool.get("candidates", [])
        ])
        self.store.replace_candidate_scores(
            pool_id,
            {score["candidate_id"]: score for score in scores},
        )
        metadata = dict(pool.get("metadata") or {})
        metadata.update({
            "recipe_id": recipe["recipe_id"],
            "recipe_version": int(recipe["version"]),
            "last_score_run_at": scored_at,
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        })
        self.store.update_candidate_pool(
            pool_id,
            {
                "metadata": metadata,
                "lock_version": int(pool.get("lock_version", 1)) + 1,
            },
            tenant_id=pool.get("tenant_id"),
            user_id=pool.get("user_id"),
        )
        return scores

    def score_candidate_pool(
        self,
        pool: Dict[str, Any],
        *,
        recipe_id: Optional[str] = None,
        scope: Any,
    ) -> Tuple[List[Dict[str, Any]], str]:
        scores = self.compute_and_store_candidate_scores(pool, recipe_id=recipe_id)
        now = self.utc_now()
        self.store.record_audit_action({
            "action_type": "candidate_pool.score",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool",
            "subject_id": pool["pool_id"],
            "payload": {"score_count": len(scores)},
        })
        return scores, now

    def member_projection(
        self,
        pool: Dict[str, Any],
        member: Dict[str, Any],
        scope: Any,
        recipe: Dict[str, Any],
        *,
        evidence_summary_mode: str = "list_response",
    ) -> Dict[str, Any]:
        pool_id = pool["pool_id"]
        artifact_id = member["artifact_id"]
        score = self.store.get_candidate_score(pool_id, artifact_id)
        projection = _candidate_public_member(member)
        if score is not None:
            projection["current_score"] = _score_without_private_explanations(score)
            projection["band"] = score["band"]
            projection["rank"] = score["rank"]
            projection["effective_score"] = score["effective_score"]
        projection.update(
            _member_truth_projection(
                pool=pool,
                member=member,
                score=score,
                reviews=self.store.list_candidate_reviews(pool_id, artifact_id),
                monitoring=self.store.get_candidate_monitoring(pool_id, artifact_id),
                recipe=recipe,
                evidence_summary_mode=evidence_summary_mode,
                operator_grade=_operator_grade_scope(scope),
            )
        )
        return projection

    def list_candidate_members(
        self,
        pool: Dict[str, Any],
        *,
        scope: Any,
        lifecycle_state: Optional[str] = None,
        band: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], int, Dict[str, Any]]:
        recipe = _load_default_scoring_recipe()
        ordered = sorted(
            pool.get("candidates", []),
            key=lambda member: (
                str(member.get("created_at") or ""),
                str(member.get("artifact_id") or ""),
            ),
        )
        members = []
        for member in ordered:
            if lifecycle_state and member.get("lifecycle_state") != lifecycle_state:
                continue
            projection = self.member_projection(pool, member, scope, recipe, evidence_summary_mode="list_response")
            if band and projection.get("band") != band:
                continue
            members.append(projection)
        offset = _parse_member_page_token(page_token, self.bff_error, lambda: self._error_code("VALIDATION_FAILED"))
        page = members[offset:offset + page_size]
        next_token = (
            f"{_MEMBER_PAGE_TOKEN_PREFIX}{offset + page_size}"
            if offset + page_size < len(members)
            else None
        )
        metadata = pool.get("metadata") or {}
        return page, next_token, len(members), metadata

    def get_member_or_404(self, pool_id: str, artifact_id: str) -> Dict[str, Any]:
        member = self.store.get_candidate_member(pool_id, artifact_id)
        if member is None:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Candidate pool member not found", artifact_id)
        return member

    def get_candidate_member_detail(
        self,
        pool: Dict[str, Any],
        artifact_id: str,
        *,
        scope: Any,
    ) -> Dict[str, Any]:
        pool_id = pool["pool_id"]
        member = self.get_member_or_404(pool_id, artifact_id)
        score = self.store.get_candidate_score(pool_id, artifact_id)
        reviews = self.store.list_candidate_reviews(pool_id, artifact_id)
        monitoring = self.store.get_candidate_monitoring(pool_id, artifact_id)
        operator_grade = _operator_grade_scope(scope)
        truth = _member_truth_projection(
            pool=pool,
            member=member,
            score=score,
            reviews=reviews,
            monitoring=monitoring,
            recipe=_load_default_scoring_recipe(),
            evidence_summary_mode="detail",
            operator_grade=operator_grade,
        )
        return {
            "candidate": _candidate_public_member(member),
            "score": (
                score
                if score is None or operator_grade
                else _score_without_private_explanations(score)
            ),
            "reviews": reviews,
            "monitoring": _public_candidate_monitoring(monitoring) if monitoring is not None else None,
            "negative_examples": [
                review for review in reviews
                if review.get("negative_example") is True
            ],
            "lifecycle_state": member.get("lifecycle_state"),
            **truth,
        }

    def review_candidate_member(
        self,
        pool: Dict[str, Any],
        artifact_id: str,
        body: CandidateMemberReviewRequest,
        *,
        scope: Any,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], str]:
        pool_id = pool["pool_id"]
        member = self.get_member_or_404(pool_id, artifact_id)
        if member.get("lifecycle_state") == "rejected":
            raise self.bff_error(
                409, self._error_code("RESOURCE_CONFLICT"),
                "Rejected candidate members are immutable retained negative examples",
                artifact_id,
            )
        _validate_review_body(body, self.bff_error, lambda: self._error_code("VALIDATION_FAILED"))
        now = self.utc_now()
        next_lifecycle = _REVIEW_DECISION_TO_LIFECYCLE[body.decision]
        review = {
            "review_id": str(uuid.uuid4()),
            "artifact_id": artifact_id,
            "decision": body.decision,
            "rationale": body.rationale,
            "score_override": body.score_override,
            "reviewed_by": body.reviewed_by,
            "reviewed_at": body.reviewed_at or now,
            "negative_example_tags": body.negative_example_tags,
            "negative_example": body.decision in {"reject", "park"},
            "no_order_route_proof": _CANDIDATE_NO_ORDER_ROUTE_PROOF,
        }
        self.store.add_candidate_review(pool_id, artifact_id, review)
        updated_member = self.store.update_candidate_member(
            pool_id,
            artifact_id,
            {
                "lifecycle_state": next_lifecycle,
                "_updated_at": now,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        next_lock_version = int(pool.get("lock_version", 1)) + 1
        metadata = dict(pool.get("metadata") or {})
        metadata["last_reviewed_at"] = now
        self.store.update_candidate_pool(
            pool_id,
            {
                "metadata": metadata,
                "lock_version": next_lock_version,
            },
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        self.store.record_audit_action({
            "action_type": "candidate_member.review",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_pool_member",
            "subject_id": artifact_id,
            "payload": {"decision": body.decision, "lifecycle_state": next_lifecycle},
        })
        self._record_trading_room_decision_event(
            pool=pool,
            member=member,
            artifact_id=artifact_id,
            body=body,
            now=now,
        )
        return updated_member, review, now

    def _record_trading_room_decision_event(
        self,
        pool: Dict[str, Any],
        member: Dict[str, Any],
        artifact_id: str,
        body: CandidateMemberReviewRequest,
        now: str,
    ) -> None:
        try:
            tr_store = self.trading_room_store
            if tr_store is None:
                try:
                    from ..trading_room.router import _get_store as _get_tr_store
                    tr_store = _get_tr_store()
                except Exception:
                    tr_store = None
            if tr_store is None:
                return

            event_decision_state = {
                "approve_for_monitoring": "approved_by_trader",
                "send_to_shadow": "deferred",
                "needs_more_research": "deferred",
                "park": "rejected_by_trader",
                "reject": "rejected_by_trader",
            }.get(body.decision, "pending")
            event_suggested_action = {
                "approve_for_monitoring": "enter",
                "send_to_shadow": "review",
                "needs_more_research": "review",
                "park": "no_action",
                "reject": "no_action",
            }.get(body.decision, "review")
            event_state = "decided" if event_decision_state != "pending" else "pending_review"
            event_kind = "entry" if body.decision == "approve_for_monitoring" else "review"

            member_strategy_id = (
                member.get("strategy_id")
                or (pool.get("metadata") or {}).get("strategy_id")
                or (member.get("strategy_ref") or "").split(":")[-1]
                or "strategy-default"
            )
            member_strategy_registry_id = (
                member.get("strategy_spec_registry_id")
                or member.get("strategy_ref")
                or member_strategy_id
            )
            symbol = str(member.get("symbol") or member.get("title") or artifact_id)
            score_data = self.store.get_candidate_score(pool["pool_id"], artifact_id) or {}
            effective_score = float(score_data.get("effective_score") or 75.0)
            confidence_val = min(1.0, max(0.0, effective_score / 100.0))

            decision_event = {
                "spec_version": "1.0",
                "decision_event_id": f"trevt-cpm-{artifact_id[:12]}-{uuid.uuid4().hex[:8]}",
                "tenant_id": pool.get("tenant_id"),
                "user_id": pool.get("user_id"),
                "event_kind": event_kind,
                "origin": "trader_request",
                "strategy_id": member_strategy_id,
                "strategy_spec_registry_id": member_strategy_registry_id,
                "candidate_ref": artifact_id,
                "subject": {
                    "symbol": symbol,
                    "asset_class": member.get("asset_class") or "equity",
                    "venue": member.get("venue") or "default",
                },
                "state": event_state,
                "decision_state": event_decision_state,
                "triggered_at": now,
                "confidence": {
                    "value": confidence_val,
                    "basis": "mixed",
                    "calibration_state": "calibrated",
                    "sample_size": 100,
                },
                "probability": {
                    "target_outcome": "positive_alpha",
                    "horizon": "20d",
                    "value": confidence_val,
                },
                "expected_value": {
                    "horizon": "20d",
                    "unit": "pct_return",
                    "gross": 0.05,
                    "cost": 0.01,
                    "net": 0.04,
                    "downside": 0.02,
                },
                "rationale": [
                    {
                        "claim": body.rationale or f"Candidate {artifact_id} reviewed with decision {body.decision}",
                        "confidence": confidence_val,
                        "evidence_refs": [
                            {"ref_type": "candidate_pool_member", "ref_id": f"{pool['pool_id']}:{artifact_id}"}
                        ],
                    }
                ],
                "invalidation": {
                    "conditions": ["price_gap_breach", "regime_change"],
                    "current_state": "valid",
                    "last_checked_at": now,
                },
                "suggested_action": event_suggested_action,
                "suggested_size": {
                    "size_hint": "medium",
                    "portfolio_pct": 0.02,
                    "non_binding": True,
                },
                "no_order_route_proof": "agora_decision_support_only",
            }
            tr_store.upsert_decision_event(decision_event)
        except Exception:
            pass

    def list_candidate_discussions(
        self,
        pool_id: str,
        *,
        scope: Any,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        kind: Optional[str] = None,
        resolved: Optional[bool] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {
            "tenant_id": tenant_id or scope.tenant_id,
            "user_id": user_id or scope.user_id,
        }
        if subject_type is not None:
            kwargs["subject_type"] = subject_type
        if subject_id is not None:
            kwargs["subject_id"] = subject_id
        if kind is not None:
            kwargs["kind"] = kind
        if resolved is not None:
            kwargs["resolved"] = resolved
        return self.store.list_candidate_discussions(pool_id, **kwargs)

    def create_candidate_discussion(
        self,
        pool_id: str,
        body: CandidateDiscussionRequest,
        *,
        scope: Any,
        subject_type: str = "pool",
        subject_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self.utc_now()
        record = _discussion_record(
            body=body,
            pool_id=pool_id,
            subject_type=subject_type,
            subject_id=subject_id or pool_id,
            scope=scope,
            now=now,
            bff_error_fn=self.bff_error,
            error_code_enum_fn=lambda: self._error_code("VALIDATION_FAILED"),
        )
        created = self.store.add_candidate_discussion(record)
        action_type = "candidate_member_discussion.create" if subject_type == "member" else "candidate_discussion.create"
        audit_subject_type = "candidate_pool_member" if subject_type == "member" else "candidate_pool"
        audit_subject_id = subject_id if subject_type == "member" else pool_id
        self.store.record_audit_action({
            "action_type": action_type,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": audit_subject_type,
            "subject_id": audit_subject_id,
            "payload": {"discussion_id": created["discussion_id"]},
        })
        return created

    def list_candidate_monitoring(
        self,
        pool_id: str,
        *,
        scope: Optional[Any] = None,
        monitoring_state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.store.list_candidate_monitoring(pool_id, monitoring_state=monitoring_state)

    def get_candidate_monitoring(
        self,
        pool_id: str,
        artifact_id: str,
        *,
        scope: Optional[Any] = None,
    ) -> Dict[str, Any]:
        record = self.store.get_candidate_monitoring(pool_id, artifact_id)
        if record is None:
            raise self.bff_error(404, self._error_code("RESOURCE_NOT_FOUND"), "Candidate monitoring record not found", artifact_id)
        return record

    def upsert_candidate_monitoring(
        self,
        pool: Dict[str, Any],
        artifact_id: str,
        body: CandidateMonitoringRequest,
        *,
        scope: Any,
    ) -> Dict[str, Any]:
        _validate_monitoring_body(
            body,
            pool_id=pool["pool_id"],
            artifact_id=artifact_id,
            bff_error_fn=self.bff_error,
            error_code_enum_fn=lambda: self._error_code("VALIDATION_FAILED"),
        )
        now = self.utc_now()
        monitoring_doc = {
            "artifact_id": artifact_id,
            "pool_id": pool["pool_id"],
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "monitoring_state": body.monitoring_state,
            "trigger_conditions": body.trigger_conditions,
            "last_score_result_id": body.last_score_result_id,
            "review_due_at": body.review_due_at,
            "added_by": body.added_by or scope.user_id,
            "added_at": body.added_at or now,
            "notes": body.notes,
        }
        upserted = self.store.upsert_candidate_monitoring(pool["pool_id"], artifact_id, monitoring_doc)
        next_lock_version = int(pool.get("lock_version", 1)) + 1
        self.store.update_candidate_pool(
            pool["pool_id"],
            {"lock_version": next_lock_version},
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        pool["lock_version"] = next_lock_version
        self.store.record_audit_action({
            "action_type": "candidate_member.monitor_upsert",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_monitoring",
            "subject_id": artifact_id,
            "payload": {"monitoring_state": body.monitoring_state},
        })
        return upserted

    def remove_candidate_monitoring(
        self,
        pool: Dict[str, Any],
        artifact_id: str,
        *,
        scope: Any,
    ) -> Tuple[int, str]:
        now = self.utc_now()
        existing = self.store.get_candidate_monitoring(pool["pool_id"], artifact_id) or {
            "artifact_id": artifact_id,
            "pool_id": pool["pool_id"],
            "added_by": scope.user_id,
            "added_at": now,
        }
        updated = {**existing, "monitoring_state": "removed", "removed_at": now, "tenant_id": scope.tenant_id, "user_id": scope.user_id}
        self.store.upsert_candidate_monitoring(pool["pool_id"], artifact_id, updated)
        next_lock_version = int(pool.get("lock_version", 1)) + 1
        self.store.update_candidate_pool(
            pool["pool_id"],
            {"lock_version": next_lock_version},
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        pool["lock_version"] = next_lock_version
        self.store.record_audit_action({
            "action_type": "candidate_member.monitor_remove",
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "subject_type": "candidate_monitoring",
            "subject_id": artifact_id,
        })
        return next_lock_version, now
