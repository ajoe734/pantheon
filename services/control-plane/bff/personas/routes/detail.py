"""Persona detail, capabilities, audit, and profile routes."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from services.control_plane.bff.models import ErrorCode
from ..service import (
    _PERSONA_BFF_OVERLAY,
    _PERSONA_PATCH_SERVER_MANAGED_FIELDS,
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY,
    _bff_me_tenant_payload,
    _composed_surface_status,
    _ensure_persona_exists,
    _filter_audit_events_by_target,
    _get_persona_directory_snapshot,
    _list_governance_audit_events,
    _merged_skill_records,
    _merged_tool_records,
    _normalize_risk_level,
    _openclaw_agent_reconcile_request,
    _persona_provisioning_store,
    _persona_record_for_provisioning,
    _persona_record_tenant_id,
    _project_persona_dto,
    _project_persona_fleet_item,
    _retrieve_canonical_persona_memory,
    _routed_strategies_for_persona,
    _stable_json_hash,
    _strategy_persona_idempotency_check,
    build_persona_runtime_profile,
    deterministic_provisioning_ids,
)
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)


def build_detail_router(ctx: PersonaRouteContext) -> APIRouter:
    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

    read_store = ctx.read_store
    command_store = ctx.command_store
    _service = ctx.service
    _extract_identity = ctx.extract_identity
    _require_read_role = ctx.require_read_role
    _require_operator_role = ctx.require_operator_role
    _bff_error = ctx.bff_error
    utc_now = ctx.utc_now
    _page_slice = ctx.page_slice
    _snapshot_meta = ctx.snapshot_meta
    _dataset_surface_status = ctx.dataset_surface_status
    _read_surface_meta = ctx.read_surface_meta
    _raise_if_read_surface_unavailable = ctx.raise_if_read_surface_unavailable
    _reject_body_idempotency_key = ctx.reject_body_idempotency_key
    _resolve_final_idempotency_key = ctx.resolve_final_idempotency_key

    @router.get("/api/v1/personas/{persona_id}")
    async def get_persona_detail(persona_id: str, authorization: Optional[str] = Header(default=None)):
        """PS-02: Persona Detail with bindings."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        persona = read_store.get_persona(persona_id)
        if not persona:
            _raise_if_read_surface_unavailable(persona_surface, label="Persona")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        bindings = read_store.get_bindings_for_persona(persona_id) or []
        payload = dict(persona)
        payload["bindings"] = bindings

        return {
            "data": payload,
            "meta": _read_surface_meta(
                "personas",
                "persona_detail",
                snapshot_at=snapshot_at,
                surface=persona_surface,
            ),
        }


    @router.get("/api/v1/personas/{persona_id}/capabilities")
    async def get_persona_capabilities(
        persona_id: str, authorization: Optional[str] = Header(default=None),
    ):
        """PS-06: Capability snapshot for a persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        persona = read_store.get_persona(persona_id)
        if not persona:
            _raise_if_read_surface_unavailable(persona_surface, label="Persona")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        capability_surface = _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at)
        snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
        if not snapshot:
            _raise_if_read_surface_unavailable(capability_surface, label="Capability snapshot")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Capability snapshot not found",
                f"Capability snapshot for persona {persona_id} does not exist",
            )

        return {
            "data": snapshot,
            "meta": _read_surface_meta(
                "capability_snapshots",
                "capability_snapshot",
                snapshot_at=snapshot_at,
                surface=capability_surface,
            ),
        }


    @router.get("/api/v1/operator/persona-management/{persona_id}")
    async def get_persona_management(
        persona_id: str,
        snapshot: str = "preferred",
        authorization: Optional[str] = Header(default=None),
    ):
        """
        Composed view for persona lifecycle management.
        Composes: PS-02 (persona detail + bindings), CP-03/CP-04 (capital pool bindings),
                  PS-03 (persona sessions), PS-05 (teaching sessions).
        """
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        # PS-02: Persona detail
        persona = read_store.get_persona(persona_id)
        if not persona:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        snapshot_at = utc_now()
        surfaces = {}

        # PS-02: Persona bindings (bindings where this persona is the owner)
        persona_bindings = read_store.get_bindings_for_persona(persona_id)
        persona_bindings_available = persona_bindings is not None
        if persona_bindings is None:
            persona_bindings = []
        surfaces["persona_bindings"] = _dataset_surface_status(
            "persona_bindings",
            snapshot_at=snapshot_at,
            has_data=persona_bindings_available,
            missing_message="Persona bindings unavailable for this persona.",
        )

        # CP-04: Enrich each binding with its capital pool detail
        enriched_bindings = []
        for binding in persona_bindings:
            binding_detail = dict(binding)
            pool = read_store.get_capital_pool(binding.get("capital_pool_id"))
            if pool:
                binding_detail["capital_pool"] = pool
            enriched_bindings.append(binding_detail)

        capital_pool_surface = _dataset_surface_status(
            "capital_pools",
            snapshot_at=snapshot_at,
            has_data=bool(enriched_bindings),
            missing_message="No capital pool bindings available for this persona.",
        )
        if surfaces["persona_bindings"]["status"] != "ok":
            surfaces["capital_pool_bindings"] = dict(surfaces["persona_bindings"])
        else:
            surfaces["capital_pool_bindings"] = capital_pool_surface

        # PS-03: Active sessions for this persona
        sessions = read_store.get_sessions_for_persona(persona_id)
        sessions_available = sessions is not None
        if sessions is None:
            sessions = []
        surfaces["persona_sessions"] = _dataset_surface_status(
            "sessions",
            snapshot_at=snapshot_at,
            has_data=sessions_available,
            missing_message="Persona sessions unavailable for this persona.",
        )

        # PS-05: Teaching sessions for this persona
        teaching_sessions = read_store.get_teaching_sessions_for_persona(persona_id)
        teaching_sessions_available = teaching_sessions is not None
        if teaching_sessions is None:
            teaching_sessions = []
        surfaces["teaching_sessions"] = _dataset_surface_status(
            "teaching_sessions",
            snapshot_at=snapshot_at,
            has_data=teaching_sessions_available,
            missing_message="Teaching sessions unavailable for this persona.",
        )

        # Backend-shaped allowed actions (acceptance: backend_shaped_persona_actions)
        allowed_actions = read_store.get_persona_allowed_actions(persona_id)
        allowed_actions_available = allowed_actions is not None
        if allowed_actions is None:
            allowed_actions = {}
        surfaces["allowed_actions"] = _dataset_surface_status(
            "allowed_actions",
            snapshot_at=snapshot_at,
            has_data=allowed_actions_available,
            missing_message="Allowed actions unavailable for this persona.",
        )

        # PERSONA-ONBOARD-2026-05-28 / F4: include readiness health surface so the
        # detail page can render the same gap reasons as /bff/management/persona-fleet.
        # Reuse _project_persona_fleet_item() so health computation stays consistent.
        health_payload = None
        runtime_bindings_for_persona: List[Dict[str, Any]] = []
        capital_pools_for_persona: List[Dict[str, Any]] = []
        active_incidents_for_persona: List[Dict[str, Any]] = []
        latest_telemetry_summary: Optional[Dict[str, Any]] = None
        try:
            fleet_item = _project_persona_fleet_item(
                persona,
                all_runtime_bindings=list(read_store.list_runtime_bindings() or []),
                all_incidents=list(read_store.list_incidents() or []),
                all_evolution_decisions=list(read_store.list_evolution_decisions() or []),
            )
        except Exception:  # pragma: no cover - defensive: never break detail page
            fleet_item = None
            surfaces["persona_health"] = _composed_surface_status(
                snapshot_at=snapshot_at,
                available=False,
                missing_message="Persona health surface failed to compose.",
            )
        else:
            health_payload = fleet_item.get("health")
            runtime_bindings_for_persona = fleet_item.get("runtimeBindings") or []
            capital_pools_for_persona = fleet_item.get("capitalPools") or []
            active_incidents_for_persona = fleet_item.get("activeIncidents") or []
            latest_telemetry_summary = (fleet_item.get("telemetrySummary") or {}).get("latest")
            surfaces["persona_health"] = _composed_surface_status(
                snapshot_at=snapshot_at,
                available=bool(health_payload),
                missing_message="Persona health surface unavailable.",
            )

        # BFF-WRITE-P0-WIZARD-008 / P0-8: deploymentPlans and approvals filtered for
        # this persona via binding_ids so wizard F4 can render plan + approval status.
        persona_binding_ids = {
            str(b.get("id") or b.get("binding_id") or "").strip()
            for b in persona_bindings
            if str(b.get("id") or b.get("binding_id") or "").strip()
        }
        deployment_plans_for_persona: List[Dict[str, Any]] = []
        approvals_for_persona: List[Dict[str, Any]] = []
        try:
            all_plans = read_store.list_deployment_plans() or []
            deployment_plans_for_persona = [
                plan for plan in all_plans
                if persona_binding_ids & {
                    str(bid) for bid in (plan.get("binding_ids") or [])
                }
                or str(plan.get("binding_id") or "") in persona_binding_ids
            ]
            plan_ids = {
                str(plan.get("id") or plan.get("plan_id") or "").strip()
                for plan in deployment_plans_for_persona
                if str(plan.get("id") or plan.get("plan_id") or "").strip()
            }
            all_approvals = read_store.list_approval_decisions() or []
            approvals_for_persona = [
                decision for decision in all_approvals
                if str(decision.get("target_id") or decision.get("plan_id") or "") in plan_ids
            ]
        except Exception:  # pragma: no cover - defensive: never break detail page
            pass
        surfaces["deployment_plans"] = _dataset_surface_status(
            "deployment_plans",
            snapshot_at=snapshot_at,
            has_data=bool(deployment_plans_for_persona),
            missing_message="No deployment plans found for this persona.",
        )
        surfaces["approvals"] = _dataset_surface_status(
            "approval_decisions",
            snapshot_at=snapshot_at,
            has_data=bool(approvals_for_persona),
            missing_message="No approval decisions found for this persona's plans.",
        )

        data = {
            "persona": persona,
            "bindings": enriched_bindings,
            "deploymentPlans": deployment_plans_for_persona,
            "approvals": approvals_for_persona,
            "sessions": sessions,
            "teaching_sessions": teaching_sessions,
            "allowedActions": allowed_actions,
            "health": health_payload,
            "runtimeBindings": runtime_bindings_for_persona,
            "capitalPools": capital_pools_for_persona,
            "activeIncidents": active_incidents_for_persona,
            "latestTelemetry": latest_telemetry_summary,
        }

        return {
            "data": data,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": surfaces,
            },
        }


    @router.get("/bff/personas/{persona_id}")
    async def bff_get_persona(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona detail."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = utc_now()
        caller_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        directory = _get_persona_directory_snapshot(caller_tenant, snapshot_at=snapshot_at)
        raw = directory.records_by_id.get(persona_id)
        if not raw:
            try:
                prov_record = _persona_provisioning_store().get_by_persona(caller_tenant, persona_id)
                if prov_record is not None:
                    persona_proj, meta_proj = _persona_record_for_provisioning(
                        prov_record,
                        payload=prov_record.request_payload,
                        owner=str(prov_record.request_payload.get("requested_by") or identity.operator_id),
                    )
                    raw = persona_proj
                    if persona_id not in _PERSONA_BFF_OVERLAY:
                        ids = deterministic_provisioning_ids(prov_record)
                        _PERSONA_BFF_OVERLAY[persona_id] = _project_persona_dto(
                            persona_proj,
                            overlay={
                                "routedStrategies": int(prov_record.request_payload.get("routedStrategies") or 0),
                                "successRate": float(prov_record.request_payload.get("successRate") or 0.0),
                                "capitalMode": "paper",
                                "paperLedgerId": meta_proj["paper_ledger_id"],
                                "paperLedger": meta_proj["paper_ledger"],
                                "legacyPaperCapitalPoolId": ids.capital_pool_id,
                                "deploymentPlanId": ids.deployment_plan_id,
                                "deploymentStage": "paper",
                                "evidenceRefs": list(meta_proj["evidence_refs"]),
                                "runtimeId": meta_proj.get("runtime_id"),
                                "runtimeBindingId": meta_proj.get("runtime_binding_id"),
                                "tenantId": prov_record.tenant_id,
                            },
                            routed_strategies=0,
                            evaluate_provisioning=False,
                        )
            except HTTPException:
                raise
            except Exception as exc:
                log.warning("Failed to lookup persona %s from durable provisioning store: dependency %s unavailable", persona_id, "persona_provisioning_store")
                raise _bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Persona durable readback is unavailable",
                    "Authoritative provisioning store is unreachable or degraded",
                    precondition_failed="persona_provisioning_store",
                    suggestion="Inspect persona provisioning persistence health before retrying",
                ) from exc
        if not raw:
            raise _bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )
        overlay = _PERSONA_BFF_OVERLAY.get(persona_id)
        if overlay and str(overlay.get("tenantId") or "") not in {"", caller_tenant}:
            overlay = None
        dto = await asyncio.to_thread(
            lambda: _project_persona_dto(
                raw,
                overlay=overlay,
                routed_strategies=_routed_strategies_for_persona(persona_id),
            )
        )
        containment = read_store.get_persona_containment(persona_id)
        if containment:
            containment_state = str(containment.get("containment_state") or "frozen")
            dto["containment_state"] = containment_state
            dto["containmentState"] = containment_state
            dto["frozen"] = containment_state == "frozen"
            dto["containment"] = containment
        meta = _read_surface_meta(
            "personas", "persona_detail",
            snapshot_at=snapshot_at,
        )
        if containment:
            meta.setdefault("surfaces", {})["containment"] = _dataset_surface_status(
                "containments",
                snapshot_at=snapshot_at,
                has_data=True,
            )
        return {
            "data": dto,
            "meta": meta,
        }


    @router.patch("/bff/personas/{persona_id}")
    async def bff_patch_persona(
        persona_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: patch persona fields through the BFF read store."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        managed_fields = sorted(_PERSONA_PATCH_SERVER_MANAGED_FIELDS.intersection(payload))
        if managed_fields:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Persona lifecycle and ownership fields are server-managed",
                f"Client PATCH cannot mutate: {', '.join(managed_fields)}",
                precondition_failed=managed_fields[0],
                suggestion="Use the governed Persona action or promotion workflow.",
            )
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        raw = read_store.get_persona(persona_id)
        overlay = _PERSONA_BFF_OVERLAY.get(persona_id)
        caller_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        raw_tenant = _persona_record_tenant_id(raw) if raw else ""
        overlay_tenant = str((overlay or {}).get("tenantId") or "")
        if raw and raw_tenant not in {"", caller_tenant}:
            raw = None
            overlay = None
        if overlay and overlay_tenant != caller_tenant:
            overlay = None
        if raw and not raw_tenant and overlay is None:
            # Tenantless legacy catalog rows may remain readable, but mutation is
            # fail-closed until an authoritative tenant owner exists.
            raw = None
        if not raw and not overlay:
            raise _bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )
        cache_key = ":".join(
            ("persona-patch", caller_tenant, identity.operator_id, resolved_key)
        )
        request_hash = _stable_json_hash(
            {
                "route": "PATCH /bff/personas/{persona_id}",
                "tenant_id": caller_tenant,
                "operator_id": identity.operator_id,
                "id": persona_id,
                "payload": payload,
            }
        )
        cached = _strategy_persona_idempotency_check(cache_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = utc_now()
        base = dict(overlay) if overlay else {}
        if not base:
            routed = _routed_strategies_for_persona(persona_id)
            base = _project_persona_dto(raw or {"persona_id": persona_id}, routed_strategies=routed)
        for field in (
            "name", "risk",
            "archetype", "routedStrategies", "successRate",
        ):
            if field in payload:
                base[field] = payload[field]
        if "risk" in payload:
            base["risk"] = _normalize_risk_level(payload["risk"])
        base["updatedAt"] = snapshot_at
        base["id"] = persona_id
        base["tenantId"] = caller_tenant
        existing_metadata = dict(raw.get("metadata") if isinstance(raw, dict) and isinstance(raw.get("metadata"), dict) else {})
        canonical_lifecycle = str(
            (raw or {}).get("lifecycle_state")
            or (raw or {}).get("state")
            or "draft"
        )
        update_metadata: Dict[str, Any] = {
            "success_rate": float(base.get("successRate") or 0.0),
        }
        update_metadata["openclaw_agent_reconcile"] = _openclaw_agent_reconcile_request(
            {
                "id": persona_id,
                "persona_id": persona_id,
                "name": str(base.get("name") or persona_id),
                "mandate": str(base.get("archetype") or existing_metadata.get("archetype") or "generalist"),
                "strategy_family": str(base.get("archetype") or existing_metadata.get("archetype") or "generalist"),
                "lifecycle_state": canonical_lifecycle,
                "metadata": {
                    **existing_metadata,
                    **update_metadata,
                    "owner": str(existing_metadata.get("owner") or identity.operator_id),
                    "archetype": str(base.get("archetype") or "generalist"),
                    "risk_level": str(base.get("risk") or "low"),
                },
            },
            reason="persona_updated",
        )
        persona_record = read_store.update_persona(
            persona_id,
            name=str(base.get("name") or persona_id),
            actor_id=str(existing_metadata.get("owner") or identity.operator_id),
            updated_at=snapshot_at,
            archetype=str(base.get("archetype") or "generalist"),
            # Lifecycle is controller-owned.  Omitting it makes update_persona
            # re-read and preserve the latest canonical value, avoiding a stale
            # overlay racing paper_running/provisioning_failed reconciliation.
            lifecycle_state=None,
            risk_level=str(base.get("risk") or "low"),
            metadata=update_metadata,
        )
        if persona_record is not None:
            routed = _routed_strategies_for_persona(persona_id)
            base = _project_persona_dto(
                persona_record,
                overlay={
                    "routedStrategies": int(base.get("routedStrategies") or routed),
                    "successRate": float(base.get("successRate") or 0.0),
                    "tenantId": caller_tenant,
                },
                routed_strategies=routed,
            )
        _PERSONA_BFF_OVERLAY[persona_id] = deepcopy(base)
        result = {"data": deepcopy(base), "meta": {"snapshot_at": snapshot_at}}
        _STRATEGY_PERSONA_BFF_IDEMPOTENCY[cache_key] = {
            "request_hash": request_hash,
            "result": deepcopy(result),
        }
        return result


    @router.get("/bff/personas/{persona_id}/route-policy")
    async def bff_get_persona_route_policy(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona route policy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        policy = None
        fetcher = getattr(read_store, "get_route_policy_for_persona", None)
        if callable(fetcher):
            policy = fetcher(persona_id)
        if not policy:
            consult_policy = None
            consult_fetcher = getattr(read_store, "get_persona_consult_policy", None)
            if callable(consult_fetcher):
                consult_policy = consult_fetcher(persona_id)
            policy = {
                "personaId": persona_id,
                "version": "v1",
                "rules": [],
                "consult_policy": consult_policy,
            }
        return {
            "data": policy,
            "meta": _read_surface_meta(
                "personas", "persona_route_policy",
                snapshot_at=snapshot_at,
            ),
        }


    @router.get("/bff/personas/{persona_id}/runtime-profile")
    async def bff_get_persona_runtime_profile(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona OpenClaw runtime profile contract."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        persona = read_store.get_persona(persona_id) or {"persona_id": persona_id}
        route_policy = None
        fetcher = getattr(read_store, "get_route_policy_for_persona", None)
        if callable(fetcher):
            route_policy = fetcher(persona_id)
        try:
            profile = build_persona_runtime_profile(
                persona,
                route_policy=route_policy if isinstance(route_policy, dict) else None,
            ).to_dict()
        except ValueError as exc:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid persona runtime profile",
                str(exc),
            ) from exc
        return {
            "data": profile,
            "meta": _read_surface_meta(
                "personas", "persona_runtime_profile",
                snapshot_at=snapshot_at,
            ),
        }


    @router.get("/bff/personas/{persona_id}/activity")
    async def bff_get_persona_activity(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona activity (sessions + recent consultations)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        sessions = read_store.get_sessions_for_persona(persona_id) or []
        consultations: List[Dict[str, Any]] = []
        consult_fetcher = getattr(read_store, "list_consultations_for_persona", None)
        if callable(consult_fetcher):
            consultations = consult_fetcher(persona_id) or []
        activity = {
            "personaId": persona_id,
            "sessions": sessions,
            "consultations": consultations,
        }
        return {
            "data": activity,
            "meta": _read_surface_meta(
                "personas", "persona_activity",
                snapshot_at=snapshot_at, total=len(sessions) + len(consultations),
            ),
        }


    @router.get("/bff/personas/{persona_id}/evaluations")
    async def bff_get_persona_evaluations(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona evaluations (teaching sessions stand in until evaluation runs ship)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        teaching = read_store.get_teaching_sessions_for_persona(persona_id) or []
        return {
            "data": teaching,
            "items": teaching,
            "page_info": {"next_page_token": None, "total": len(teaching)},
            "meta": _read_surface_meta(
                "personas", "persona_evaluations",
                snapshot_at=snapshot_at, total=len(teaching),
            ),
        }


    @router.get("/bff/personas/{persona_id}/memory")
    async def bff_get_persona_memory(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: canonical Memory Plane entries for a persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        memory, source = _retrieve_canonical_persona_memory(persona_id, identity)
        surface_status = "ok" if source["available"] else "degraded"
        return {
            "data": memory,
            "items": memory,
            "page_info": {"next_page_token": None, "total": len(memory)},
            "meta": _read_surface_meta(
                "personas", "persona_memory",
                snapshot_at=snapshot_at, total=len(memory),
            ) | {
                "status": surface_status,
                "memory_source": source,
            },
        }


    @router.get("/bff/personas/{persona_id}/audit")
    async def bff_get_persona_audit(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: audit trail for a persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        events = _list_governance_audit_events() or []
        filtered = _filter_audit_events_by_target(events, persona_id)
        return {
            "data": filtered,
            "items": filtered,
            "page_info": {"next_page_token": None, "total": len(filtered)},
            "meta": _read_surface_meta(
                "governance_audit_events", "persona_audit",
                snapshot_at=snapshot_at, total=len(filtered),
            ),
        }


    @router.get("/bff/personas/{persona_id}/skills")
    async def bff_get_persona_skills(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: skills accessible to a persona, derived from capability snapshot (PER-002)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
        effective_skill_ids = list((snapshot or {}).get("effective_skills") or [])
        all_skills = _merged_skill_records()
        skill_by_id: Dict[str, Dict[str, Any]] = {
            str(s.get("skill_id") or s.get("id") or ""): s
            for s in all_skills
            if s.get("skill_id") or s.get("id")
        }
        items: List[Dict[str, Any]] = []
        for sid in effective_skill_ids:
            sid = str(sid)
            record = skill_by_id.get(sid)
            if record:
                items.append(dict(record))
            else:
                items.append({"skill_id": sid, "id": sid, "name": sid, "status": "active"})
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": _read_surface_meta(
                "capability_snapshots", "persona_skills",
                snapshot_at=snapshot_at, total=len(items),
            ),
        }


    @router.get("/bff/personas/{persona_id}/tools")
    async def bff_get_persona_tools(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: tools accessible to a persona, derived from capability snapshot (PER-002)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
        effective_tool_ids = list((snapshot or {}).get("effective_tools") or [])
        all_tools = _merged_tool_records()
        tool_by_id: Dict[str, Dict[str, Any]] = {
            str(t.get("tool_id") or t.get("id") or ""): t
            for t in all_tools
            if t.get("tool_id") or t.get("id")
        }
        items: List[Dict[str, Any]] = []
        for tid in effective_tool_ids:
            tid = str(tid)
            record = tool_by_id.get(tid)
            if record:
                items.append(dict(record))
            else:
                items.append({"tool_id": tid, "id": tid, "name": tid, "status": "active"})
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": _read_surface_meta(
                "capability_snapshots", "persona_tools",
                snapshot_at=snapshot_at, total=len(items),
            ),
        }


    @router.get("/bff/personas/{persona_id}/capabilities")
    async def bff_get_persona_capabilities_surface(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: capability snapshot surface for a persona (PER-002 read surface)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
        data: Dict[str, Any] = {
            "personaId": persona_id,
            "effectiveSkills": (snapshot or {}).get("effective_skills") or [],
            "effectiveTools": (snapshot or {}).get("effective_tools") or [],
            "effectiveWorkflows": (snapshot or {}).get("effective_workflows") or [],
            "restrictions": (snapshot or {}).get("restrictions") or [],
            "generatedAt": (snapshot or {}).get("generated_at"),
            "sourceRefs": (snapshot or {}).get("source_refs") or [],
            "snapshotId": (snapshot or {}).get("snapshot_id"),
        }
        return {
            "data": data,
            "meta": _read_surface_meta(
                "capability_snapshots", "persona_capabilities",
                snapshot_at=snapshot_at,
            ),
        }

    return router
