"""Dedicated Runtime BFF routes.

The composition root injects BFF-owned ports so these handlers preserve the
existing RuntimeBinding, deployment, and SSE behavior without importing it.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import APIRouter, Body, Header, Query, Request

from ..models import CommandType, ErrorCode, ObjectType

from .service import RuntimeRouterService


def create_runtime_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    dependencies: Optional[Mapping[str, Any]] = None,
) -> APIRouter:
    """Build Runtime routes from composition-root supplied BFF ports."""
    router = APIRouter()
    service = RuntimeRouterService(
        read_surface=read_surface,
        get_read_store=get_read_store,
        dependencies=dependencies,
    )
    read_store = service.read_store
    _GOVERNANCE_APPROVAL_QUEUE_ROUTE = service.dependency('_GOVERNANCE_APPROVAL_QUEUE_ROUTE')
    _GOV_BFF_IDEMPOTENCY = service.dependency('_GOV_BFF_IDEMPOTENCY')
    _aggregate_group_surface = service.dependency('_aggregate_group_surface')
    _alert_target_ref = service.dependency('_alert_target_ref')
    _bff_error = service.dependency('_bff_error')
    _build_persona_health_items = service.dependency('_build_persona_health_items')
    _capital_bff_idempotency_check = service.dependency('_capital_bff_idempotency_check')
    _capital_bff_idempotency_store = service.dependency('_capital_bff_idempotency_store')
    _capital_owner_role = service.dependency('_capital_owner_role')
    _composed_dataset_surface_status = service.dependency('_composed_dataset_surface_status')
    _composed_surface_status = service.dependency('_composed_surface_status')
    _dataset_surface_status = service.dependency('_dataset_surface_status')
    _deployment_review_href = service.dependency('_deployment_review_href')
    _deprecated_bff_path_response = service.dependency('_deprecated_bff_path_response')
    _dry_run_success_response = service.dependency('_dry_run_success_response')
    _extract_identity = service.dependency('_extract_identity')
    _gov_bff_action_command = service.dependency('_gov_bff_action_command')
    _handle_sse_stream = service.dependency('_handle_sse_stream')
    _incident_detail_href = service.dependency('_incident_detail_href')
    _meta_staleness = service.dependency('_meta_staleness')
    _ooda_packet_list_payload = service.dependency('_ooda_packet_list_payload')
    _page_slice = service.dependency('_page_slice')
    _project_operator_runtime_state_row = service.dependency('_project_operator_runtime_state_row')
    _publish_event = service.dependency('_publish_event')
    _raise_capital_owner_error = service.dependency('_raise_capital_owner_error')
    _raise_if_read_surface_unavailable = service.dependency('_raise_if_read_surface_unavailable')
    _read_surface_meta = service.dependency('_read_surface_meta')
    _reject_body_idempotency_key = service.dependency('_reject_body_idempotency_key')
    _request_dry_run_requested = service.dependency('_request_dry_run_requested')
    _require_ooda_packet_routes_enabled = service.dependency('_require_ooda_packet_routes_enabled')
    _require_operator_role = service.dependency('_require_operator_role')
    _require_read_role = service.dependency('_require_read_role')
    _resolve_final_idempotency_key = service.dependency('_resolve_final_idempotency_key')
    _snapshot_meta = service.dependency('_snapshot_meta')
    _sort_key = service.dependency('_sort_key')
    _split_csv_query = service.dependency('_split_csv_query')
    _sse_buffers = service.dependency('_sse_buffers')
    _sse_subscribers = service.dependency('_sse_subscribers')
    _stable_capital_resource_id = service.dependency('_stable_capital_resource_id')
    _stable_json_hash = service.dependency('_stable_json_hash')
    create_capital_binding = service.dependency('create_capital_binding')
    utc_now = service.dependency('utc_now')
    _runtime_events, _runtime_subscribers = service.runtime_event_stream()

    _OPERATOR_DEPLOYMENT_PLAN_ROUTE_PREFIX = "/operator/deployment-plans"

    _OPERATOR_POST_INCIDENT_REVIEW_ROUTE = "/operator/post-incident-review"

    def _deployment_plan_href(plan_id: str) -> str:
        return f"{_OPERATOR_DEPLOYMENT_PLAN_ROUTE_PREFIX}/{plan_id}"

    def _post_incident_review_href(incident_id: str) -> str:
        return f"{_OPERATOR_POST_INCIDENT_REVIEW_ROUTE}?incident={incident_id}"

    _RUNTIME_STATE_SORT_FIELDS = {"last_updated_at", "runtime_id", "deployment_stage", "status"}

    _RUNTIME_STATE_SORT_ORDERS = {"asc", "desc"}

    def _runtime_state_support_surface_ref(
        surface_key: str,
        surface: Dict[str, Any],
    ) -> Dict[str, Any]:
        ref = {
            "surface_key": surface_key,
            "status": surface.get("status"),
            "source": surface.get("source"),
        }
        for key in ("message", "note", "staleness"):
            if key in surface:
                ref[key] = surface.get(key)
        return ref

    def _runtime_state_degraded_support_surfaces(
        surfaces: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []
        for surface_key, surface in surfaces.items():
            if surface.get("status") == "ok":
                continue
            refs.append(_runtime_state_support_surface_ref(surface_key, surface))
        return refs

    def _sort_runtime_state_rows(
        rows: List[Dict[str, Any]],
        *,
        sort_by: str,
        sort_order: str,
    ) -> List[Dict[str, Any]]:
        reverse = sort_order == "desc"

        def _sort_key(row: Dict[str, Any]) -> tuple[str, str]:
            primary = row.get(sort_by)
            if primary is None:
                primary = ""
            return (str(primary), str(row.get("runtime_id") or ""))

        ordered_rows = sorted(rows, key=_sort_key, reverse=reverse)
        present = [row for row in ordered_rows if row.get(sort_by)]
        missing = [row for row in ordered_rows if not row.get(sort_by)]
        return present + missing

    def _unavailable_surface(
        dataset: str,
        *,
        snapshot_at: str,
        message: str,
    ) -> Dict[str, Any]:
        surface = _dataset_surface_status(dataset, snapshot_at=snapshot_at)
        surface["status"] = "unavailable"
        surface["message"] = message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )
        return surface

    def _browser_href_for_drift_evidence_ref(
        ref: Dict[str, Any],
        *,
        plan_id: Optional[str],
        primary_incident_id: Optional[str],
    ) -> Optional[str]:
        ref_type = str(ref.get("type") or "").strip().lower()
        ref_id = str(ref.get("ref_id") or "").strip()
        if ref_type == "approvaldecision":
            return _GOVERNANCE_APPROVAL_QUEUE_ROUTE
        if ref_type == "incidentcase" and ref_id:
            return _incident_detail_href(ref_id)
        if ref_type == "evolutiondecision" and primary_incident_id:
            return _post_incident_review_href(primary_incident_id)
        if ref_type == "drift_report":
            return None
        if plan_id and str(ref.get("href") or "").startswith("/api/v1/operator/deployment-review/"):
            return _deployment_review_href(plan_id)
        return ref.get("href")

    def _normalize_drift_evidence_refs(
        refs: List[Dict[str, Any]],
        *,
        plan_id: Optional[str],
        primary_incident_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        normalized = json.loads(json.dumps(refs))
        for ref in normalized:
            if not isinstance(ref, dict):
                continue
            ref["href"] = _browser_href_for_drift_evidence_ref(
                ref,
                plan_id=plan_id,
                primary_incident_id=primary_incident_id,
            )
        return normalized

    def _normalize_drift_recommended_actions(
        actions: List[Dict[str, Any]],
        *,
        plan_id: Optional[str],
        primary_incident_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        normalized = json.loads(json.dumps(actions))
        for action in normalized:
            if not isinstance(action, dict):
                continue
            target_ref = action.get("target_ref")
            if not isinstance(target_ref, dict):
                continue
            surface_id = str(target_ref.get("surface_id") or "").strip()
            target_id = str(target_ref.get("target_id") or "").strip()
            if surface_id == "PKT-001" and plan_id:
                target_ref["href"] = _deployment_review_href(plan_id)
            elif surface_id == "PKT-002" and target_id:
                target_ref["href"] = _incident_detail_href(target_id)
            elif surface_id == "PKT-003" and target_id:
                target_ref["href"] = _post_incident_review_href(target_id)
        return normalized

    def _build_operator_paper_live_drift_payload(
        runtime_id: str,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
        report = read_store.get_paper_live_drift_report(runtime_id)
        plan_id = None
        artifact_id = None
        artifact_version = None

        if runtime_binding:
            plan_id = runtime_binding.get("plan_id")
            artifact_id = runtime_binding.get("artifact_id")
            artifact_version = runtime_binding.get("artifact_version")
        if report:
            plan_id = report.get("plan_id") or plan_id
            artifact_id = report.get("artifact_id") or artifact_id
            artifact_version = report.get("artifact_version") or artifact_version

        plan = read_store.get_deployment_plan(plan_id) if plan_id else None
        approval_decision = (
            read_store.get_approval_decision(plan.get("approval_decision_id"))
            if plan
            else None
        )
        telemetry_summary = read_store.get_telemetry_summary(runtime_id)
        telemetry_performance = (
            read_store.get_telemetry_performance(str(artifact_id))
            if artifact_id
            else None
        )
        incidents = [
            incident
            for incident in read_store.list_incidents()
            if str(incident.get("runtime_id") or "") == runtime_id
            and str(incident.get("status") or "").lower() in {"open", "in_progress"}
        ]
        evolution_decisions = []
        for incident in incidents:
            evolution_decisions.extend(
                read_store.get_evolution_decisions_by_incident(
                    str(incident.get("incident_id") or "")
                )
            )
        primary_incident_id = (
            str(incidents[0].get("incident_id") or "").strip() if incidents else None
        )

        report_surface = (
            _dataset_surface_status("paper_live_drift_reports", snapshot_at=snapshot_at)
            if report is not None
            else _unavailable_surface(
                "paper_live_drift_reports",
                snapshot_at=snapshot_at,
                message="Paper/live drift report unavailable for this runtime.",
            )
        )
        runtime_surface = (
            _dataset_surface_status(
                "runtime_bindings",
                snapshot_at=snapshot_at,
                has_data=runtime_binding is not None,
                missing_message="Runtime binding unavailable for this drift view.",
            )
            if runtime_binding is not None
            else _unavailable_surface(
                "runtime_bindings",
                snapshot_at=snapshot_at,
                message="Runtime binding unavailable for this drift view.",
            )
        )
        telemetry_surface = (
            _dataset_surface_status(
                "telemetry_summaries",
                snapshot_at=snapshot_at,
                has_data=telemetry_summary is not None,
                missing_message="Observed telemetry summary unavailable for this drift view.",
            )
            if telemetry_summary is not None
            else _unavailable_surface(
                "telemetry_summaries",
                snapshot_at=snapshot_at,
                message="Observed telemetry summary unavailable for this drift view.",
            )
        )
        performance_surface = (
            _dataset_surface_status(
                "telemetry_performance",
                snapshot_at=snapshot_at,
                has_data=telemetry_performance is not None,
                missing_message="Paper baseline performance unavailable for this drift view.",
            )
            if telemetry_performance is not None
            else _unavailable_surface(
                "telemetry_performance",
                snapshot_at=snapshot_at,
                message="Paper baseline performance unavailable for this drift view.",
            )
        )
        approval_surface = (
            _dataset_surface_status(
                "approval_decisions",
                snapshot_at=snapshot_at,
                has_data=approval_decision is not None,
                missing_message="Approval decision unavailable for this drift view.",
            )
            if approval_decision is not None
            else _unavailable_surface(
                "approval_decisions",
                snapshot_at=snapshot_at,
                message="Approval decision unavailable for this drift view.",
            )
        )
        incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
        evolution_surface = _dataset_surface_status(
            "evolution_decisions",
            snapshot_at=snapshot_at,
        )

        source_surfaces = [
            report_surface,
            runtime_surface,
            telemetry_surface,
            performance_surface,
            approval_surface,
            incident_surface,
            evolution_surface,
        ]
        paper_live_drift_surface = _aggregate_group_surface(
            "paper_live_drift",
            source_surfaces,
            snapshot_at=snapshot_at,
            unavailable_message="Paper/live drift view unavailable.",
            degraded_message="Paper/live drift view is available, but one or more supporting surfaces are degraded.",
        )
        if report is None:
            paper_live_drift_surface["status"] = "unavailable"
            paper_live_drift_surface["message"] = "Paper/live drift view unavailable."
            paper_live_drift_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at},
            )

        recommended_actions = []
        if report:
            recommended_actions = _normalize_drift_recommended_actions(
                report.get("recommended_actions") or [],
                plan_id=str(plan_id) if plan_id else None,
                primary_incident_id=primary_incident_id,
            )
        elif plan_id:
            recommended_actions = [
                {
                    "action_id": "open-deployment-review",
                    "label": "Open deployment review",
                    "reason": "A drift report is not yet available; inspect the current deployment context first.",
                    "target_ref": _alert_target_ref(
                        surface_id="PKT-001",
                        label="Open deployment review",
                        href=_deployment_review_href(str(plan_id)),
                        target_id=plan_id,
                    ),
                }
            ]

        return {
            "runtime_id": runtime_id,
            "plan_ref": (
                {
                    "plan_id": plan_id,
                    "href": _deployment_plan_href(str(plan_id)),
                }
                if plan_id
                else None
            ),
            "artifact_ref": (
                {
                    "artifact_id": artifact_id,
                    "artifact_version": artifact_version,
                }
                if artifact_id or artifact_version
                else None
            ),
            "paper_baseline": json.loads(json.dumps((report or {}).get("paper_baseline")))
            if report
            else None,
            "observed_state": json.loads(json.dumps((report or {}).get("observed_state")))
            if report
            else None,
            "drift_groups": json.loads(json.dumps((report or {}).get("drift_groups") or [])),
            "threshold_evaluation": json.loads(
                json.dumps(
                    (report or {}).get("threshold_evaluation")
                    or {
                        "overall_status": "unavailable",
                        "summary": "Paper/live drift report unavailable for this runtime.",
                        "breached_metric_ids": [],
                    }
                )
            ),
            "evidence_refs": _normalize_drift_evidence_refs(
                (report or {}).get("evidence_refs") or [],
                plan_id=str(plan_id) if plan_id else None,
                primary_incident_id=primary_incident_id,
            ),
            "recommended_actions": recommended_actions,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "surfaces": {
                    "paper_live_drift": paper_live_drift_surface,
                    "drift_report": report_surface,
                    "runtime_binding": runtime_surface,
                    "telemetry_summary": telemetry_surface,
                    "telemetry_performance": performance_surface,
                    "approval_decision": approval_surface,
                    "incident": incident_surface,
                    "evolution": evolution_surface,
                },
                "supporting_counts": {
                    "active_incident_count": len(incidents),
                    "evolution_decision_count": len(evolution_decisions),
                },
            },
        }

    @router.get("/api/v1/bindings")
    async def list_bindings(
        persona_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        role: Optional[str] = None,
        validity: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        """CP-03: Persona capital binding list."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        bindings = read_store.list_bindings(
            persona_id=persona_id,
            capital_pool_id=capital_pool_id,
            role=role,
            validity=validity,
        )
        snapshot_at = utc_now()
        return {
            "data": bindings,
            "meta": _read_surface_meta(
                "persona_bindings",
                "binding_list",
                snapshot_at=snapshot_at,
                total=len(bindings),
            ),
        }

    @router.post("/api/v1/bindings", status_code=201)
    async def create_binding(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Create a pending PersonaCapitalBinding through Capital owner authority."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash(
            {"route": "POST /api/v1/bindings", "payload": payload}
        )
        cached = _capital_bff_idempotency_check(
            identity.operator_id, resolved_key, request_hash
        )
        if cached is not None:
            return cached

        persona_id = str(payload.get("persona_id") or "").strip()
        capital_pool_id = str(payload.get("capital_pool_id") or "").strip()
        if not persona_id or not capital_pool_id:
            missing = "persona_id" if not persona_id else "capital_pool_id"
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"{missing} is required",
                f"Persona capital binding requires a non-empty {missing}",
                precondition_failed=missing,
            )
        binding_id = _stable_capital_resource_id(
            "binding",
            operator_id=identity.operator_id,
            idempotency_key=resolved_key,
            requested_id=payload.get("binding_id") or payload.get("id"),
        )
        role = str(payload.get("role") or "live_owner").strip()
        allowed_scope = str(
            payload.get("allowed_deployment_scope") or "live"
        ).strip()
        if "capital_sleeve_id" in payload:
            capital_sleeve_id = (
                str(payload.get("capital_sleeve_id") or "").strip() or None
            )
        elif "sleeve_id" in payload:
            capital_sleeve_id = (
                str(payload.get("sleeve_id") or "").strip() or None
            )
        elif role == "paper_owner" and allowed_scope == "paper":
            capital_sleeve_id = None
        else:
            # Legacy live-binding callers omitted the sleeve and relied on a stable
            # binding-scoped default. Paper authority must stay sleeve-less.
            capital_sleeve_id = binding_id
        metadata = {
            **(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
            "capital_sleeve_id": capital_sleeve_id,
            "_pantheon_owner_create": {
                "actor_id": identity.operator_id,
                "idempotency_key": resolved_key,
                "request_hash": request_hash,
            },
        }
        owner_payload = {
            "actor_id": identity.operator_id,
            "actor_role": _capital_owner_role(identity),
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
            "binding_id": binding_id,
            "persona_id": persona_id,
            "capital_pool_id": capital_pool_id,
            "capital_sleeve_id": capital_sleeve_id,
            "role": role,
            "allowed_deployment_scope": allowed_scope,
            "mandate": payload.get("mandate"),
            "budget": payload.get("budget"),
            "effective_from": payload.get("effective_from"),
            "effective_to": payload.get("effective_to"),
            "created_by": identity.operator_id,
            "metadata": metadata,
        }
        try:
            result = create_capital_binding(owner_payload)
        except Exception as exc:
            _raise_capital_owner_error(exc, operation="create persona capital binding")
            raise
        result = {
            **result,
            "capital_sleeve_id": (
                result.get("capital_sleeve_id")
                or (result.get("metadata") or {}).get("capital_sleeve_id")
                or capital_sleeve_id
            ),
            "status": result.get("status") or "pending",
        }
        _capital_bff_idempotency_store(
            identity.operator_id, resolved_key, request_hash, result
        )
        return result

    @router.get("/api/v1/runtime-bindings")
    async def list_runtime_bindings(
        deployment_mode: Optional[str] = None,
        version: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        """RT-01: Runtime binding list."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        bindings = read_store.list_runtime_bindings(
            deployment_mode=deployment_mode,
            version=version,
        )
        snapshot_at = utc_now()
        return {
            "data": bindings,
            "meta": _read_surface_meta(
                "runtime_bindings",
                "runtime_binding_list",
                snapshot_at=snapshot_at,
                total=len(bindings),
            ),
        }

    @router.get("/api/v1/runtimes/{runtime_id}/status")
    async def get_runtime_status(
        runtime_id: str, authorization: Optional[str] = Header(default=None),
    ):
        """RT-03: Runtime status detail."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        runtime_surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
        runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
        if not runtime_binding:
            _raise_if_read_surface_unavailable(runtime_surface, label="Runtime")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime not found",
                f"Runtime {runtime_id} does not exist",
            )

        return {
            "data": runtime_binding,
            "meta": _read_surface_meta(
                "runtime_bindings",
                "runtime_status",
                snapshot_at=snapshot_at,
                surface=runtime_surface,
            ),
        }

    @router.get("/api/v1/bindings/{binding_id}")
    async def get_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        binding_surface = _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at)
        binding = read_store.get_binding(binding_id)
        if not binding:
            _raise_if_read_surface_unavailable(binding_surface, label="Binding")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Binding not found",
                f"Binding {binding_id} does not exist",
            )

        persona = read_store.get_persona(binding.get("persona_id"))
        payload = dict(binding)
        if persona:
            payload["persona"] = persona

        return {
            "data": payload,
            "meta": _read_surface_meta(
                "persona_bindings",
                "binding_detail",
                snapshot_at=snapshot_at,
                surface=binding_surface,
            ),
        }

    @router.get("/api/v1/runtime-bindings/{binding_id}")
    async def get_runtime_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        runtime_binding_surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
        runtime_binding = read_store.get_runtime_binding(binding_id)
        if not runtime_binding:
            _raise_if_read_surface_unavailable(runtime_binding_surface, label="Runtime binding")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime binding not found",
                f"Runtime binding {binding_id} does not exist",
            )

        plan = read_store.get_deployment_plan(runtime_binding.get("plan_id", ""))
        payload = dict(runtime_binding)
        if plan:
            payload["deployment_plan"] = plan

        return {
            "data": payload,
            "meta": _read_surface_meta(
                "runtime_bindings",
                "runtime_binding_detail",
                snapshot_at=snapshot_at,
                surface=runtime_binding_surface,
            ),
        }

    @router.get("/api/v1/runtimes/{runtime_id}/rollbacks")
    async def get_runtime_rollbacks(runtime_id: str, authorization: Optional[str] = Header(default=None)):
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        rollbacks = read_store.get_rollbacks(runtime_id)
        return {
            "data": rollbacks,
            "meta": {
                "staleness": _meta_staleness(),
            },
        }

    @router.get("/api/v1/operator/runtime-state")
    async def list_operator_runtime_state(
        deployment_stage: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = Query(default="last_updated_at"),
        sort_order: str = Query(default="desc"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        if sort_by not in _RUNTIME_STATE_SORT_FIELDS:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid sort_by",
                f"sort_by must be one of {sorted(_RUNTIME_STATE_SORT_FIELDS)}",
            )
        if sort_order not in _RUNTIME_STATE_SORT_ORDERS:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid sort_order",
                f"sort_order must be one of {sorted(_RUNTIME_STATE_SORT_ORDERS)}",
            )

        requested_stages = {
            value.lower() for value in (_split_csv_query(deployment_stage) or [])
        }
        requested_statuses = {
            value.lower() for value in (_split_csv_query(status) or [])
        }
        snapshot_at = utc_now()

        bindings = read_store.list_runtime_bindings()
        if requested_stages:
            bindings = [
                binding
                for binding in bindings
                if str(
                    binding.get("deployment_stage") or binding.get("deployment_mode") or ""
                ).lower() in requested_stages
            ]
        if requested_statuses:
            bindings = [
                binding
                for binding in bindings
                if str(binding.get("status") or "").lower() in requested_statuses
            ]

        runtimes = [
            _project_operator_runtime_state_row(binding)
            for binding in bindings
        ]
        runtimes = _sort_runtime_state_rows(
            runtimes,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        runtime_roster_surface = _dataset_surface_status(
            "runtime_bindings",
            snapshot_at=snapshot_at,
        )
        telemetry_surface = _dataset_surface_status(
            "telemetry_summaries",
            snapshot_at=snapshot_at,
        )
        if runtimes and any(row.get("telemetry_summary") is None for row in runtimes):
            if telemetry_surface.get("status") == "ok":
                telemetry_surface["status"] = "degraded"
            telemetry_surface.setdefault(
                "message",
                "Telemetry summary unavailable for one or more runtimes on the runtime-state board.",
            )
            telemetry_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at},
            )

        monitoring_surface = _dataset_surface_status(
            "paper_runtime_monitoring_sessions",
            snapshot_at=snapshot_at,
        )
        paper_runtime_rows = [
            row for row in runtimes
            if str(row.get("deployment_stage") or "").lower() == "paper"
        ]
        if paper_runtime_rows and any(
            row.get("paper_runtime_monitoring") is None
            for row in paper_runtime_rows
        ):
            if monitoring_surface.get("status") == "ok":
                monitoring_surface["status"] = "degraded"
            monitoring_surface.setdefault(
                "message",
                "Paper runtime monitoring session evidence is unavailable for one or more paper runtimes.",
            )
            monitoring_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at},
            )

        rollback_history_surface = _dataset_surface_status(
            "rollbacks",
            snapshot_at=snapshot_at,
        )
        support_surfaces = {
            "runtime_roster": runtime_roster_surface,
            "telemetry_summary": telemetry_surface,
            "paper_runtime_monitoring": monitoring_surface,
            "rollback_history": rollback_history_surface,
        }
        degraded_support_surfaces = _runtime_state_degraded_support_surfaces(support_surfaces)

        runtime_state_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=runtime_roster_surface.get("status") != "unavailable",
            missing_message="Runtime roster unavailable for the operator runtime-state board.",
        )
        if runtime_roster_surface.get("status") == "degraded":
            runtime_state_surface["status"] = "degraded"
        elif runtime_roster_surface.get("status") == "unavailable":
            runtime_state_surface["status"] = "unavailable"
        elif any(
            surface.get("status") != "ok"
            for surface in (telemetry_surface, monitoring_surface, rollback_history_surface)
        ):
            runtime_state_surface["status"] = "degraded"
            runtime_state_surface.setdefault(
                "message",
                "Runtime-state board is available, but one or more supporting surfaces are degraded or unavailable.",
            )
            runtime_state_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at},
            )
        runtime_state_surface["support_surface_status"] = {
            key: surface.get("status") for key, surface in support_surfaces.items()
        }
        if degraded_support_surfaces:
            runtime_state_surface["degraded_support_surfaces"] = degraded_support_surfaces

        total = len(runtimes)
        if runtime_state_surface.get("status") == "unavailable":
            runtimes = []
            next_page_token = None
        else:
            runtimes, next_page_token = _page_slice(runtimes, page_token, page_size)

        meta = _snapshot_meta(snapshot_at)
        meta["total"] = total
        meta["sort"] = {
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        meta["surfaces"] = {
            "runtime_state": runtime_state_surface,
            "runtime_roster": runtime_roster_surface,
            "telemetry_summary": telemetry_surface,
            "paper_runtime_monitoring": monitoring_surface,
            "rollback_history": rollback_history_surface,
        }

        return {
            "runtimes": runtimes,
            "page_info": {
                "next_page_token": next_page_token,
            },
            "meta": meta,
        }

    @router.get("/api/v1/operator/paper-live-drift/{runtime_id}")
    async def get_operator_paper_live_drift(
        runtime_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
        report = read_store.get_paper_live_drift_report(runtime_id)
        if runtime_binding is None and report is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime drift view not found",
                f"Runtime {runtime_id} does not exist",
            )

        snapshot_at = utc_now()
        return _build_operator_paper_live_drift_payload(runtime_id, snapshot_at)

    @router.get("/bff/runtimes/{runtime_id}/ooda")
    async def bff_list_runtime_ooda_packets(
        runtime_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: list OODA packets linked to a runtime or RuntimeBinding."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _require_ooda_packet_routes_enabled()
        clean_id = runtime_id.strip()
        packets = read_store.list_ooda_packets_for_runtime(clean_id)
        return _ooda_packet_list_payload(
            packets,
            surface_key="runtime_ooda_packets",
            page_token=page_token,
            page_size=page_size,
            related={"type": "Runtime", "id": clean_id},
        )

    @router.get("/api/v1/runtime/{runtime_id}/events/stream")
    async def stream_runtime_events(
        runtime_id: str,
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ):
        """RT-SSE: Server-Sent Events stream for runtime state changes.

        Supports reconnection via ``?last_event_id=`` to replay missed events.
        BFF_API_CONTRACT.md §11.2
        """
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        return _handle_sse_stream("runtime", _runtime_events, _runtime_subscribers, last_event_id)

    _RUNTIME_CREATE_REQUIRED_FIELDS = ("name", "persona_id", "binding_id", "deployment_plan_id")

    _VALID_RUNTIME_KINDS = {"paper", "live"}

    def _runtime_create_required_string(payload: Dict[str, Any], field: str) -> str:
        value = str(payload.get(field) or "").strip()
        if value:
            return value
        raise _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            f"{field} is required",
            f"Runtime create requires a non-empty {field}.",
            precondition_failed=field,
        )

    def _runtime_binding_matches_create(binding: Dict[str, Any], binding_id: str) -> bool:
        candidate_ids = {
            str(binding.get("binding_id") or "").strip(),
            str(binding.get("runtime_binding_id") or "").strip(),
            str(binding.get("persona_capital_binding_id") or "").strip(),
        }
        return binding_id in candidate_ids

    def _raise_if_runtime_binding_conflict(binding_id: str) -> None:
        existing = next(
            (binding for binding in read_store.list_runtime_bindings() if _runtime_binding_matches_create(binding, binding_id)),
            None,
        )
        if existing is None:
            return
        runtime_id = existing.get("runtime_id") or existing.get("id") or binding_id
        raise _bff_error(
            409,
            ErrorCode.RESOURCE_CONFLICT,
            "Binding already has a runtime",
            f"Binding {binding_id} is already attached to runtime {runtime_id}.",
            precondition_failed="binding_id",
            suggestion="Use the existing runtime or choose an unbound binding.",
        )

    def _project_runtime_create_response(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record.get("runtime_id") or record.get("id"),
            "name": record.get("name"),
            "state": record.get("state") or "stopped",
            "persona_id": record.get("persona_id"),
            "binding_id": record.get("binding_id"),
            "deployment_plan_id": record.get("deployment_plan_id") or record.get("plan_id"),
            "runtime_kind": record.get("runtime_kind") or record.get("deployment_mode"),
            "created_at": record.get("created_at"),
        }

    @router.post("/bff/runtimes", status_code=201)
    async def bff_create_runtime(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: create a runtime binding in stopped state."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({"route": "POST /bff/runtimes", "payload": payload})
        dry_run = _request_dry_run_requested()
        if not dry_run:
            existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    raise _bff_error(
                        409,
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Idempotency key already used with a different payload",
                        f"Key {resolved_key!r} is bound to a different request hash",
                        precondition_failed="idempotency_conflict",
                        suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                    )
                return existing["result"]

        fields = {
            field: _runtime_create_required_string(payload, field)
            for field in _RUNTIME_CREATE_REQUIRED_FIELDS
        }
        runtime_kind = str(payload.get("runtime_kind") or "").strip().lower()
        if runtime_kind not in _VALID_RUNTIME_KINDS:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "runtime_kind is invalid",
                "runtime_kind must be one of: paper, live.",
                precondition_failed="runtime_kind",
            )

        _raise_if_runtime_binding_conflict(fields["binding_id"])

        snapshot_at = utc_now()
        client_runtime_id = str(payload.get("runtime_id") or payload.get("id") or "").strip()
        runtime_id = client_runtime_id or f"runtime-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        record = {
            "id": runtime_id,
            "runtime_id": runtime_id,
            "name": fields["name"],
            "state": "stopped",
            "status": "stopped",
            "persona_id": fields["persona_id"],
            "binding_id": fields["binding_id"],
            "deployment_plan_id": fields["deployment_plan_id"],
            "runtime_kind": runtime_kind,
            "created_at": snapshot_at,
        }
        if not dry_run:
            record = read_store.create_runtime_binding(
                runtime_id=runtime_id,
                name=fields["name"],
                persona_id=fields["persona_id"],
                binding_id=fields["binding_id"],
                deployment_plan_id=fields["deployment_plan_id"],
                runtime_kind=runtime_kind,
                actor_id=identity.operator_id,
                created_at=snapshot_at,
                params=payload.get("params") if isinstance(payload.get("params"), dict) else {},
            )
        data = _project_runtime_create_response(record)
        surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
        meta = _snapshot_meta(snapshot_at)
        meta["surfaces"] = {"runtimes": surface}
        meta["evidenceKind"] = "runtime.create"
        meta["evidence_kind"] = "runtime.create"
        if dry_run:
            return _dry_run_success_response(
                data,
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="runtime.create",
                extra_meta={"surfaces": {"runtimes": surface}},
            )
        event_payload = {
            "runtime_id": data["id"],
            "binding_id": data["binding_id"],
            "persona_id": data["persona_id"],
            "deployment_plan_id": data["deployment_plan_id"],
            "runtime_kind": data["runtime_kind"],
            "state": data["state"],
            "created_at": data["created_at"],
        }
        _publish_event(
            _sse_buffers["runtime"],
            _sse_subscribers["runtime"],
            "runtime.created",
            event_payload,
        )
        _publish_event(
            _sse_buffers["runtime"],
            _sse_subscribers["runtime"],
            "management.runtime-status",
            event_payload,
        )

        result = {"data": data, "meta": meta}
        _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    @router.get("/bff/runtimes")
    async def bff_list_runtimes(
        status: Optional[str] = None,
        deployment_stage: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: list runtime bindings (execute-plans compatibility surface)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        bindings = read_store.list_runtime_bindings()
        if status:
            requested_statuses = {v.strip().lower() for v in status.split(",") if v.strip()}
            bindings = [b for b in bindings if str(b.get("status") or "").lower() in requested_statuses]
        if deployment_stage:
            requested_stages = {v.strip().lower() for v in deployment_stage.split(",") if v.strip()}
            bindings = [
                b for b in bindings
                if str(b.get("deployment_stage") or b.get("deployment_mode") or "").lower() in requested_stages
            ]

        surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
        if surface.get("status") == "unavailable":
            bindings = []
            next_page_token = None
        else:
            total = len(bindings)
            bindings, next_page_token = _page_slice(bindings, page_token, page_size)

        meta = _snapshot_meta(snapshot_at)
        meta["total"] = 0 if surface.get("status") == "unavailable" else total
        meta["surfaces"] = {"runtimes": surface}
        staleness = _meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness

        return {
            "items": bindings,
            "page_info": {"next_page_token": next_page_token},
            "meta": meta,
        }

    @router.get("/bff/runtimes/{runtime_id}")
    async def bff_get_runtime(
        runtime_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: get a runtime binding detail by runtime_id."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
        clean_id = runtime_id.strip()
        binding = read_store.get_runtime_binding_by_runtime_id(clean_id)
        if not binding:
            # fall back to binding_id lookup
            binding = read_store.get_runtime_binding(clean_id)
        if not binding:
            _raise_if_read_surface_unavailable(surface, label="Runtime")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime not found",
                f"Runtime {runtime_id} does not exist",
            )

        meta = _snapshot_meta(snapshot_at)
        meta["surfaces"] = {"runtime": surface}
        return {
            "data": binding,
            "meta": meta,
        }

    @router.post("/bff/runtimes/{runtime_id}/actions/{action_id}", status_code=202)
    async def bff_runtime_action(
        runtime_id: str,
        action_id: str,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: submit an action against a runtime binding."""
        return _deprecated_bff_path_response(
            route="/bff/runtimes/{runtime_id}/actions/{action_id}",
            replacement="/bff/actions/runtime/{runtime_id}/{action_id}",
        )
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        clean_id = runtime_id.strip()
        binding = read_store.get_runtime_binding_by_runtime_id(clean_id)
        if not binding:
            binding = read_store.get_runtime_binding(clean_id)
        if not binding:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Runtime not found",
                f"Runtime {runtime_id} does not exist",
            )
        return _gov_bff_action_command(
            ObjectType.RUNTIME_BINDING, clean_id, action_id, resolved_key, identity, payload, CommandType.RUNTIME_ACTION
        )

    @router.get("/bff/v5/execution/persona-health")
    async def bff_v5_execution_persona_health(
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF BFF-B2-006: v5 execution persona-health list."""
        _require_read_role(_extract_identity(authorization))
        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        league = read_store.list_persona_league(include_market_persona_defaults=True)
        league_surface = _composed_dataset_surface_status(
            "persona_league",
            league,
            snapshot_at=snapshot_at,
            source="composed_market_persona_defaults",
        )
        health_items = _build_persona_health_items(
            snapshot_at,
            include_market_persona_defaults=True,
        )
        return {
            "data": health_items,
            "items": health_items,
            "page_info": {"next_page_token": None, "total": len(health_items)},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_health": persona_surface,
                    "persona_league": league_surface,
                },
            },
        }

    @router.get("/bff/v5/execution/strategy-health")
    async def bff_v5_execution_strategy_health(
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF BFF-B2-006: v5 execution strategy-health list."""
        _require_read_role(_extract_identity(authorization))
        snapshot_at = utc_now()
        strategy_surface = _dataset_surface_status("strategy_specs", snapshot_at=snapshot_at)
        strategies = read_store.list_strategy_specs()
        health_items = [
            {
                "id": s.get("strategy_id") or s.get("id"),
                "strategy_id": s.get("strategy_id") or s.get("id"),
                "name": s.get("name") or s.get("strategy_id"),
                "health": "healthy" if str(s.get("status") or "") == "active" else "degraded",
                "status": s.get("status"),
            }
            for s in strategies
        ]
        return {
            "items": health_items,
            "meta": {"snapshot_at": snapshot_at, "surfaces": {"strategy_health": strategy_surface}},
        }

    return router
