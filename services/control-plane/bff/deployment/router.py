"""Deployment domain canonical router.

Design unit:
- OPGAP-DEPLOY-RELIABILITY-V2-20260830: Full domain router extraction handling 12
  deployment domain routes:
  1. GET /api/v1/deployment-plans: Deployment plan list (DP-01)
  2. POST /api/v1/deployment-plans: Create a deployment plan (persona onboarding wizard step 3)
  3. GET /api/v1/deployment-plans/{plan_id}: Deployment plan detail with stage truth
  4. GET /api/v1/operator/deployment-plans: PKT-001 operator deployment-plan review console list
  5. GET /api/v1/operator/deployment-review/{plan_id}: Deployment review detail composition
  6. GET /api/v1/operator/deployment-diff/{plan_id}: Deployment diff detail (PKT-007)
  7. GET /bff/sse/deployment/events: SSE alias for artifact-channel deployment events
  8. GET /bff/deployments: BFF deployment-plan list (execute-plans compatibility surface)
  9. GET /bff/deployments/{deployment_id}: BFF deployment-plan detail
  10. POST /bff/deployments/{deployment_id}/actions/{action_id}: BFF deployment action (deprecated
      passthrough to /bff/actions/deployment/{deployment_id}/{action_id})
  11. POST /bff/deployments: SEM deployment create command
  12. PATCH /bff/deployments/{deployment_id}: SEM deployment patch command

  Note: POST /bff/incidents/{id}/rollback-deployment is intentionally NOT extracted here.
  It shares a single generic handler (``sem_final_generic_id_command_alias``) with four
  other incident/alert routes (escalate-incident, append-postmortem, resolve,
  start-mitigation) that operate on incidents, not deployments, so it remains in
  ``main.py`` alongside its siblings.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..models import CommandType, ErrorCode, ObjectType

from .service import DeploymentService


def create_deployment_router(
    *,
    queries: Optional[Any] = None,
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    require_operator_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]],
    snapshot_meta: Callable[[str], Dict[str, Any]],
    dataset_surface_status: Callable[..., Dict[str, Any]],
    composed_surface_status: Callable[..., Dict[str, Any]],
    read_surface_meta: Callable[..., Dict[str, Any]],
    raise_if_read_surface_unavailable: Callable[..., None],
    aggregate_group_surface: Callable[..., Dict[str, Any]],
    split_csv_query: Callable[[Optional[str]], Optional[List[str]]],
    meta_staleness: Callable[[], Optional[Dict[str, Any]]],
    stable_json_hash: Callable[[Dict[str, Any]], str],
    resolve_final_idempotency_key: Callable[[Optional[str], Optional[str]], str],
    reject_body_idempotency_key: Callable[[Dict[str, Any]], None],
    request_dry_run_requested: Callable[..., bool],
    gov_bff_idempotency: Dict[str, Dict[str, Any]],
    publish_event: Callable[..., str],
    sse_buffers: Dict[str, Any],
    sse_subscribers: Dict[str, Any],
    gov_bff_action_command: Callable[..., Dict[str, Any]],
    deprecated_bff_path_response: Callable[..., Any],
    sem_command_response: Callable[..., Any],
    stream_generic_events: Callable[..., Any],
    surface_degradation_reason: Callable[..., Optional[str]],
) -> APIRouter:
    """Build the dedicated Deployment domain router with injected BFF ports."""
    router = APIRouter()
    service = DeploymentService(
        queries=queries,
        bff_error=bff_error,
        dataset_surface_status=dataset_surface_status,
        composed_surface_status=composed_surface_status,
        aggregate_group_surface=aggregate_group_surface,
        split_csv_query=split_csv_query,
        snapshot_meta=snapshot_meta,
        surface_degradation_reason=surface_degradation_reason,
    )

    _DEPLOYMENT_PLAN_CREATE_REQUIRED_FIELDS = ("binding_id", "artifact_id", "capital_pool_id")
    _VALID_DEPLOYMENT_MODES = {"paper", "live"}
    _DEPLOYMENT_PLAN_APPROVED_ARTIFACT_STATES = {
        "approved",
        "promoted",
        "published",
        "active",
        "registered",
    }

    def _deployment_plan_create_required_string(payload: Dict[str, Any], field: str) -> str:
        value = str(payload.get(field) or "").strip()
        if value:
            return value
        raise bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{field} is required",
            f"Deployment plan create requires a non-empty {field}.",
            precondition_failed=field,
        )

    def _deployment_plan_registry_entry(artifact_id: str) -> Optional[Dict[str, Any]]:
        for entry in service.queries.list_registry_entries():
            if not isinstance(entry, dict):
                continue
            candidates = {
                str(entry.get("artifact_id") or "").strip(),
                str(entry.get("id") or "").strip(),
                str(entry.get("artifact_ref") or "").strip(),
            }
            if artifact_id in candidates:
                return entry
        return None

    def _raise_if_deployment_artifact_not_approved(artifact_id: str) -> None:
        """Block plan creation when a known artifact is not in an approved state.

        The canonical registry can be unavailable in the BFF local dev store, so an
        unknown artifact is treated as permissive rather than a hard failure.
        """
        entry = _deployment_plan_registry_entry(artifact_id)
        if entry is None:
            return
        state = str(
            entry.get("status")
            or entry.get("approval_status")
            or entry.get("admission_status")
            or ""
        ).strip().lower()
        if state and state not in _DEPLOYMENT_PLAN_APPROVED_ARTIFACT_STATES:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Artifact is not approved for deployment",
                f"Artifact {artifact_id} is in state {state!r}; an approved artifact is required.",
                precondition_failed="artifact_id",
                suggestion="Promote the artifact to an approved state before creating a deployment plan.",
            )

    def _deployment_plan_persona_id(binding_id: str) -> Optional[str]:
        binding = service.queries.get_binding(binding_id)
        if isinstance(binding, dict):
            persona_id = str(binding.get("persona_id") or "").strip()
            return persona_id or None
        return None

    def _project_deployment_plan_create_response(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record.get("plan_id") or record.get("id"),
            "binding_id": record.get("binding_id"),
            "artifact_id": record.get("artifact_id"),
            "deployment_mode": record.get("deployment_mode") or record.get("deployment_stage"),
            "status": record.get("status") or "pending_approval",
            "capital_pool_id": record.get("capital_pool_id") or record.get("target_pool_id"),
            "locked": bool(record.get("locked", False)),
            "created_at": record.get("created_at"),
        }

    @router.get("/api/v1/deployment-plans")
    async def list_deployment_plans(
        status: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        """DP-01: Deployment plan list."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        plans = service.queries.list_deployment_plans(
            status=status,
            capital_pool_id=capital_pool_id,
            include_fixture_pack=False,
        )
        snapshot_at = utc_now()
        return {
            "data": plans,
            "meta": read_surface_meta(
                "deployment_plans",
                "deployment_plan_list",
                snapshot_at=snapshot_at,
                total=len(plans),
            ),
        }

    @router.post("/api/v1/deployment-plans", status_code=201)
    async def create_deployment_plan_v1(
        response: Response,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF write-gap P0-6: create a deployment plan (persona onboarding wizard step 3)."""
        identity = extract_identity(authorization)
        require_operator_role(identity)
        reject_body_idempotency_key(payload)

        fields = {
            field: _deployment_plan_create_required_string(payload, field)
            for field in _DEPLOYMENT_PLAN_CREATE_REQUIRED_FIELDS
        }
        deployment_mode = str(payload.get("deployment_mode") or "").strip().lower()
        if deployment_mode not in _VALID_DEPLOYMENT_MODES:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "deployment_mode is invalid",
                "deployment_mode must be one of: paper, live.",
                precondition_failed="deployment_mode",
            )
        locked = bool(payload.get("locked", False))
        _raise_if_deployment_artifact_not_approved(fields["artifact_id"])

        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        correlation_id = str(x_correlation_id or "").strip() or str(uuid.uuid4())
        response.headers["X-Correlation-Id"] = correlation_id
        request_id = str(x_request_id or "").strip() or None
        dry_run = request_dry_run_requested(x_dry_run)
        request_hash = stable_json_hash(
            {"route": "POST /api/v1/deployment-plans", "payload": payload}
        )

        if not dry_run:
            existing = gov_bff_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    raise bff_error(
                        409,
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Idempotency key already used with a different payload",
                        f"Key {resolved_key!r} is bound to a different request hash",
                        precondition_failed="idempotency_conflict",
                        suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                    )
                cached = existing["result"]
                cached_meta = cached.get("meta") if isinstance(cached, dict) else {}
                response.headers["X-Correlation-Id"] = str(
                    cached_meta.get("correlationId") or correlation_id
                )
                return cached

        snapshot_at = utc_now()
        client_plan_id = str(payload.get("plan_id") or payload.get("id") or "").strip()
        plan_id = client_plan_id or f"plan-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"

        if dry_run:
            preview = {
                "id": plan_id,
                "binding_id": fields["binding_id"],
                "artifact_id": fields["artifact_id"],
                "deployment_mode": deployment_mode,
                "status": "pending_approval",
                "capital_pool_id": fields["capital_pool_id"],
                "locked": locked,
                "created_at": snapshot_at,
            }
            return JSONResponse(
                status_code=200,
                content=jsonable_encoder(
                    {
                        "data": preview,
                        "meta": {
                            "snapshot_at": snapshot_at,
                            "dryRun": True,
                            "correlationId": correlation_id,
                            "requestId": request_id,
                            "evidenceKind": "deployment_plan.create",
                        },
                    }
                ),
                headers={"X-Correlation-Id": correlation_id},
            )

        if service.queries.get_deployment_plan(plan_id) is not None:
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Deployment plan id already exists",
                f"Deployment plan {plan_id} already exists",
                precondition_failed="plan_id",
                suggestion="Replay with the original Idempotency-Key or choose a new plan id",
                correlation_id=correlation_id,
            )

        record = service.queries.create_deployment_plan(
            plan_id=plan_id,
            binding_id=fields["binding_id"],
            artifact_id=fields["artifact_id"],
            deployment_mode=deployment_mode,
            capital_pool_id=fields["capital_pool_id"],
            actor_id=identity.operator_id,
            created_at=snapshot_at,
            params=payload.get("params") if isinstance(payload.get("params"), dict) else {},
            locked=locked,
        )
        data = _project_deployment_plan_create_response(record)
        persona_id = _deployment_plan_persona_id(fields["binding_id"])
        surface = dataset_surface_status("deployment_plans", snapshot_at=snapshot_at)
        meta = snapshot_meta(snapshot_at)
        meta["surfaces"] = {"deployment_plans": surface}
        meta["dryRun"] = False
        meta["correlationId"] = correlation_id
        meta["requestId"] = request_id
        meta["evidenceKind"] = "deployment_plan.create"
        meta["evidence_kind"] = "deployment_plan.create"

        event_payload = {
            "deployment_plan_id": data["id"],
            "id": data["id"],
            "binding_id": data["binding_id"],
            "persona_id": persona_id,
            "artifact_id": data["artifact_id"],
            "deployment_mode": data["deployment_mode"],
            "status": data["status"],
            "created_at": data["created_at"],
        }
        publish_event(
            sse_buffers["audit"],
            sse_subscribers["audit"],
            "deployment-plan.created",
            event_payload,
        )

        result = {"data": data, "meta": meta}
        gov_bff_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    @router.get("/api/v1/deployment-plans/{plan_id}")
    async def get_deployment_plan(plan_id: str, authorization: Optional[str] = Header(default=None)):
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        plan_surface = dataset_surface_status("deployment_plans", snapshot_at=snapshot_at)
        plan = service.queries.get_deployment_plan(plan_id)
        if not plan:
            raise_if_read_surface_unavailable(plan_surface, label="Deployment plan")
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Deployment plan not found",
                f"Deployment plan {plan_id} does not exist",
            )

        payload = service.deployment_plan_with_stage_truth(plan, snapshot_at=snapshot_at)
        decision = service.queries.get_approval_decision(plan.get("approval_decision_id"))
        if decision:
            payload["approval_decision"] = decision
        stage_surfaces = service.deployment_stage_truth_surfaces(
            payload["stage_truth"],
            snapshot_at=snapshot_at,
        )
        meta = read_surface_meta(
            "deployment_plans",
            "deployment_plan_detail",
            snapshot_at=snapshot_at,
            surface=plan_surface,
        )
        meta["surfaces"].update(stage_surfaces)

        return {
            "data": payload,
            "meta": meta,
        }

    @router.get("/api/v1/operator/deployment-plans")
    async def list_operator_deployment_plans(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = extract_identity(authorization)
        require_read_role(identity)

        requested_statuses = service.pkt001_requested_plan_statuses(status)
        snapshot_at = utc_now()
        deployment_plans_surface = dataset_surface_status(
            "deployment_plans",
            snapshot_at=snapshot_at,
        )

        matched_items: List[Dict[str, Any]] = []
        allowed_actions_complete = True
        for plan in service.queries.list_deployment_plans():
            plan_id = str(plan.get("plan_id") or plan.get("id") or "")
            approval_decision = service.queries.get_approval_decision(plan.get("approval_decision_id"))
            review = service.queries.get_review_summary(plan_id)
            derived_status = service.pkt001_plan_filter_status(plan, approval_decision, review)
            if requested_statuses and derived_status not in requested_statuses:
                continue
            matched_items.append(service.pkt001_plan_list_item(plan, approval_decision, review))
            if not service.pkt001_allowed_actions_present(service.queries.get_allowed_actions(plan_id)):
                allowed_actions_complete = False

        matched_items.sort(
            key=lambda item: (item.get("submitted_at") or "", item.get("plan_id") or ""),
            reverse=True,
        )

        if deployment_plans_surface.get("status") == "unavailable":
            items = []
            next_page_token = None
        else:
            items, next_page_token = page_slice(matched_items, page_token, page_size)

        allowed_actions_surface = dataset_surface_status(
            "approval_decisions",
            snapshot_at=snapshot_at,
            has_data=allowed_actions_complete if matched_items else None,
            missing_message="Deployment action authority unavailable for this deployment-plan snapshot.",
        )

        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "deployment_plans": deployment_plans_surface,
                "allowedActions": allowed_actions_surface,
            },
        }
        staleness = meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        degradation = service.pkt001_degradation_meta(meta["surfaces"])
        if degradation:
            meta["degradation"] = degradation

        return {
            "items": items,
            "page_info": {
                "next_page_token": next_page_token,
            },
            "meta": meta,
        }

    @router.get("/api/v1/operator/deployment-review/{plan_id}")
    async def get_deployment_review(plan_id: str, authorization: Optional[str] = Header(default=None)):
        identity = extract_identity(authorization)
        require_read_role(identity)

        plan = service.queries.get_deployment_plan(plan_id)
        if not plan:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Deployment plan not found",
                f"Deployment plan {plan_id} does not exist",
            )

        pool = service.queries.get_capital_pool(plan.get("capital_pool_id"))
        bindings = service.queries.get_bindings_for_pool(plan.get("capital_pool_id"))
        runtime_binding = service.queries.get_runtime_binding(plan.get("runtime_binding_id"))
        approval_decision = service.queries.get_approval_decision(plan.get("approval_decision_id"))
        rollbacks = service.queries.get_rollbacks(
            runtime_binding.get("runtime_id") if runtime_binding else None
        )
        allowed_actions = service.queries.get_allowed_actions(plan_id)
        latest_run = service.queries.get_latest_run(plan_id)
        review = service.queries.get_review_summary(plan_id)

        snapshot_at = utc_now()

        deployment_plan_payload = {
            "id": plan.get("id"),
            "stage": plan.get("stage"),
            "artifact_id": plan.get("artifact_id"),
            "approval_decision_id": plan.get("approval_decision_id"),
        }
        for optional_key in ["current_stage", "target_stage", "status", "artifact_version", "transition_type"]:
            if plan.get(optional_key) is not None:
                deployment_plan_payload[optional_key] = plan.get(optional_key)
        if approval_decision:
            deployment_plan_payload["approval_decision"] = approval_decision

        stage_truth = service.deployment_stage_truth(plan)
        data = {
            "deployment_plan": deployment_plan_payload,
            "approval_decision": approval_decision or {},
            "capital_pool": pool or {},
            "bindings": bindings,
            "runtime_binding": runtime_binding or {},
            "rollbacks": rollbacks,
            "allowedActions": allowed_actions,
            "latestRun": latest_run,
            "review": review,
            "stage_truth": stage_truth,
        }

        surfaces = {
            "deployment_plan": dataset_surface_status(
                "deployment_plans",
                snapshot_at=snapshot_at,
                has_data=plan is not None,
            ),
            "approval_decision": dataset_surface_status(
                "approval_decisions",
                snapshot_at=snapshot_at,
                has_data=approval_decision is not None,
                missing_message="Approval decision unavailable for this deployment plan.",
            ),
            "capital_pool": dataset_surface_status(
                "capital_pools",
                snapshot_at=snapshot_at,
                has_data=pool is not None,
                missing_message="Capital pool detail unavailable for this deployment plan.",
            ),
            "bindings": dataset_surface_status(
                "persona_bindings",
                snapshot_at=snapshot_at,
                has_data=bindings is not None,
            ),
            "runtime_binding": dataset_surface_status(
                "runtime_bindings",
                snapshot_at=snapshot_at,
                has_data=runtime_binding is not None,
                missing_message="Runtime binding unavailable for this deployment plan.",
            ),
            "rollbacks": dataset_surface_status("rollbacks", snapshot_at=snapshot_at),
            "allowedActions": dataset_surface_status(
                "approval_decisions",
                snapshot_at=snapshot_at,
                has_data=service.pkt001_allowed_actions_present(allowed_actions),
                missing_message="Deployment action authority unavailable for this deployment plan.",
            ),
            "latestRun": dataset_surface_status(
                "latest_runs",
                snapshot_at=snapshot_at,
                has_data=latest_run is not None,
            ),
            "review": dataset_surface_status(
                "review_summaries",
                snapshot_at=snapshot_at,
                has_data=review is not None,
            ),
        }

        surfaces.update(service.deployment_stage_truth_surfaces(stage_truth, snapshot_at=snapshot_at))

        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        }
        staleness = meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        degradation = service.pkt001_degradation_meta(surfaces)
        if degradation:
            meta["degradation"] = degradation

        return {
            "data": data,
            "meta": meta,
        }

    @router.get("/api/v1/operator/deployment-diff/{plan_id}")
    async def get_deployment_diff(plan_id: str, authorization: Optional[str] = Header(default=None)):
        identity = extract_identity(authorization)
        require_read_role(identity)

        diff = service.queries.get_deployment_diff(plan_id)
        diff_source = service.queries.dataset_source("deployment_diffs")
        if not diff:
            if diff_source == "missing":
                return service.unavailable_deployment_diff_payload(plan_id, utc_now())
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Deployment diff not found",
                f"Deployment diff for plan {plan_id} does not exist",
            )

        snapshot_at = (((diff.get("meta") or {}).get("snapshot_at"))) or utc_now()
        payload = dict(diff)
        payload["plan_id"] = payload.get("plan_id") or plan_id
        payload["changes"] = list(payload.get("changes") or [])

        summary = dict(payload.get("change_summary") or {})
        summary["total_changes"] = int(summary.get("total_changes") or len(payload["changes"]))
        by_category = dict(summary.get("by_category") or {})
        for category in service.deployment_diff_categories:
            category_summary = dict(by_category.get(category) or {})
            category_summary.setdefault("count", 0)
            category_summary.setdefault("highest_risk_tier", None)
            by_category[category] = category_summary
        summary["by_category"] = by_category
        payload["change_summary"] = summary

        allowed_actions = dict(payload.get("allowedActions") or {})
        allowed_actions.setdefault("canProceedToApproval", False)
        allowed_actions.setdefault("canEscalateDiff", False)
        payload["allowedActions"] = allowed_actions

        deployment_diff_surface = dataset_surface_status(
            "deployment_diffs",
            snapshot_at=snapshot_at,
            has_data=True,
        )
        allowed_actions_surface = composed_surface_status(
            snapshot_at=snapshot_at,
            available=service.deployment_diff_allowed_actions_present(payload),
            missing_message="Deployment diff authority unavailable.",
        )
        if deployment_diff_surface.get("status") == "degraded":
            allowed_actions_surface["status"] = "degraded"
        elif deployment_diff_surface.get("status") == "unavailable":
            allowed_actions_surface["status"] = "unavailable"

        meta = dict(payload.get("meta") or {})
        meta["snapshot_at"] = snapshot_at
        meta["surfaces"] = {
            "deployment_diff": deployment_diff_surface,
            "allowedActions": allowed_actions_surface,
        }
        payload["meta"] = meta
        return payload

    @router.get("/bff/sse/deployment/events")
    async def bff_sse_deployment_events_alias(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ):
        """Alias for artifact channel deployment events."""
        return await stream_generic_events("artifact", last_event_id, authorization)

    @router.get("/bff/deployments")
    async def bff_list_deployments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: list deployment plans (execute-plans compatibility surface)."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        snapshot_at = utc_now()
        plans = service.queries.list_deployment_plans()
        if status:
            requested_statuses = {v.strip().lower() for v in status.split(",") if v.strip()}
            plans = [p for p in plans if str(p.get("status") or "").lower() in requested_statuses]
        total = len(plans)

        surface = dataset_surface_status("deployment_plans", snapshot_at=snapshot_at)
        if surface.get("status") == "unavailable":
            plans = []
            next_page_token = None
            total = 0
        else:
            plans, next_page_token = page_slice(plans, page_token, page_size)
            plans = [
                service.deployment_plan_with_stage_truth(plan, snapshot_at=snapshot_at)
                for plan in plans
            ]

        meta = snapshot_meta(snapshot_at)
        stage_surfaces = (
            service.deployment_stage_truth_collection_surfaces(
                [plan["stage_truth"] for plan in plans],
                snapshot_at=snapshot_at,
            )
            if plans
            else {}
        )
        meta["surfaces"] = {"deployments": surface, **stage_surfaces}
        staleness = meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness

        return {
            "data": plans,
            "items": plans,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": meta,
        }

    @router.get("/bff/deployments/{deployment_id}")
    async def bff_get_deployment(
        deployment_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: get a deployment plan detail."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        clean_id = deployment_id.strip()
        plan = service.queries.get_deployment_plan(clean_id)
        if not plan:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Deployment not found",
                f"Deployment plan {deployment_id} does not exist",
            )

        snapshot_at = utc_now()
        plan_payload = service.deployment_plan_with_stage_truth(plan, snapshot_at=snapshot_at)
        decision = service.queries.get_approval_decision(plan.get("approval_decision_id"))
        review = service.queries.get_review_summary(clean_id)
        stage_surfaces = service.deployment_stage_truth_surfaces(
            plan_payload["stage_truth"],
            snapshot_at=snapshot_at,
        )

        return {
            "data": {
                **plan_payload,
                "approval_decision": decision or {},
                "review": review or {},
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "deployments": dataset_surface_status("deployment_plans", snapshot_at=snapshot_at),
                    **stage_surfaces,
                },
                "staleness": meta_staleness(),
            },
        }

    @router.post("/bff/deployments/{deployment_id}/actions/{action_id}", status_code=202)
    async def bff_deployment_action(
        deployment_id: str,
        action_id: str,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: submit an action against a deployment plan."""
        return deprecated_bff_path_response(
            route="/bff/deployments/{deployment_id}/actions/{action_id}",
            replacement="/bff/actions/deployment/{deployment_id}/{action_id}",
        )
        identity = extract_identity(authorization)
        require_operator_role(identity)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        clean_id = deployment_id.strip()
        plan = service.queries.get_deployment_plan(clean_id)
        if not plan:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Deployment not found",
                f"Deployment plan {deployment_id} does not exist",
            )
        return gov_bff_action_command(
            ObjectType.DEPLOYMENT, clean_id, action_id, resolved_key, identity, payload, CommandType.DEPLOYMENT_ACTION
        )

    @router.post("/bff/deployments", status_code=201)
    async def sem_create_deployment_command(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = extract_identity(authorization)
        require_operator_role(identity)
        client_provided_id = payload.get("deployment_id") or payload.get("deploymentId") or payload.get("id")
        deployment_id = str(client_provided_id or f"deployment-{uuid.uuid4().hex[:8]}")
        return sem_command_response(
            command_type=CommandType.DEPLOYMENT_CREATE,
            target_type=ObjectType.DEPLOYMENT,
            target_id=deployment_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            status_code=201,
            server_generated_target=not client_provided_id,
        )

    @router.patch("/bff/deployments/{deployment_id}", status_code=202)
    async def sem_patch_deployment_command(
        deployment_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        identity = extract_identity(authorization)
        require_operator_role(identity)
        return sem_command_response(
            command_type=CommandType.DEPLOYMENT_PATCH,
            target_type=ObjectType.DEPLOYMENT,
            target_id=deployment_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )

    return router
