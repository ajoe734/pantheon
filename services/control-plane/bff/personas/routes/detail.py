"""Persona detail, capabilities, audit, and profile routes."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from services.control_plane.bff.models import ErrorCode
from ..service import (
    _PERSONA_PATCH_SERVER_MANAGED_FIELDS,
    _bff_me_tenant_payload,
    _composed_surface_status,
    _ensure_persona_exists,
    _filter_audit_events_by_target,
    _get_persona_directory_snapshot,
    _list_governance_audit_events,
    _merged_skill_records,
    _merged_tool_records,
    _persona_provisioning_store,
    _persona_record_for_provisioning,
    _project_persona_dto,
    _project_persona_fleet_item,
    _retrieve_canonical_persona_memory,
    _routed_strategies_for_persona,
    build_persona_runtime_profile,
    deterministic_provisioning_ids,
)
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)


def build_detail_router(ctx: PersonaRouteContext) -> APIRouter:
    router = APIRouter(tags=["personas"], dependencies=[make_context_dependency(ctx)])

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
    def get_persona_detail(persona_id: str, authorization: Optional[str] = Header(default=None)):
        """PS-02: Persona Detail with bindings."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        persona = _service.get_persona(persona_id)
        if not persona:
            _raise_if_read_surface_unavailable(persona_surface, label="Persona")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        bindings = _service.get_bindings_for_persona(persona_id) or []
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
    def get_persona_capabilities(
        persona_id: str, authorization: Optional[str] = Header(default=None),
    ):
        """PS-06: Capability snapshot for a persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        persona = _service.get_persona(persona_id)
        if not persona:
            _raise_if_read_surface_unavailable(persona_surface, label="Persona")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        capability_surface = _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at)
        snapshot = _service.get_capability_snapshot_for_persona(persona_id)
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
    def get_persona_management(
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
        persona = _service.get_persona(persona_id)
        if not persona:
            _raise_if_read_surface_unavailable(_dataset_surface_status("personas", snapshot_at=utc_now()), label="Persona")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )

        snapshot_at = utc_now()
        surfaces = {}

        # PS-02: Persona bindings (bindings where this persona is the owner)
        persona_bindings = _service.get_bindings_for_persona(persona_id)
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
            pool = _service.get_capital_pool(binding.get("capital_pool_id"))
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
        sessions = _service.get_sessions_for_persona(persona_id)
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
        teaching_sessions = _service.get_teaching_sessions_for_persona(persona_id)
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
        allowed_actions = _service.get_persona_allowed_actions(persona_id)
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
                all_runtime_bindings=_service.list_runtime_bindings(),
                all_incidents=_service.list_incidents(),
                all_evolution_decisions=_service.list_evolution_decisions(),
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

        # BFF-WRITE-P0-WIZARD-008 / P0-8 & BFF-PERSONA-OPERATING-CONTEXT-20261107:
        # Resolve deploymentPlans and approvals for this persona via authoritative
        # references (plan_id, persona_id, persona_capital_binding_id, runtime_binding_id,
        # capital_pool_id, artifact_id, registry_id, approval_decision_id) and owner reads.
        persona_meta = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
        auth_readback = (
            persona_meta.get("provisioning_authoritative_readback")
            if isinstance(persona_meta.get("provisioning_authoritative_readback"), dict)
            else {}
        )
        dep_readback = auth_readback.get("deployment") if isinstance(auth_readback.get("deployment"), dict) else {}
        gov_readback = auth_readback.get("governance") if isinstance(auth_readback.get("governance"), dict) else {}
        reg_readback = auth_readback.get("registry") if isinstance(auth_readback.get("registry"), dict) else {}

        persona_binding_ids = {
            str(b.get("id") or b.get("binding_id") or "").strip()
            for b in persona_bindings
            if str(b.get("id") or b.get("binding_id") or "").strip()
        }
        for meta_key in ("persona_capital_binding_id", "binding_id"):
            val = str(persona_meta.get(meta_key) or "").strip()
            if val:
                persona_binding_ids.add(val)

        runtime_binding_ids = {
            str(rb.get("binding_id") or rb.get("runtime_binding_id") or rb.get("id") or "").strip()
            for rb in runtime_bindings_for_persona
            if isinstance(rb, dict)
            and str(rb.get("binding_id") or rb.get("runtime_binding_id") or rb.get("id") or "").strip()
        }
        for meta_key in ("runtime_binding_id", "runtime_id"):
            val = str(persona_meta.get(meta_key) or "").strip()
            if val:
                runtime_binding_ids.add(val)

        persona_capital_pool_ids = {
            str(b.get("capital_pool_id") or "").strip()
            for b in persona_bindings
            if isinstance(b, dict) and str(b.get("capital_pool_id") or "").strip()
        } | {
            str(p.get("pool_id") or p.get("id") or "").strip()
            for p in capital_pools_for_persona
            if isinstance(p, dict) and str(p.get("pool_id") or p.get("id") or "").strip()
        } | {
            str(rb.get("capital_pool_id") or "").strip()
            for rb in runtime_bindings_for_persona
            if isinstance(rb, dict) and str(rb.get("capital_pool_id") or "").strip()
        }
        for meta_key in ("capital_pool_id", "internal_paper_capital_pool_id", "legacy_paper_capital_pool_id"):
            val = str(persona_meta.get(meta_key) or "").strip()
            if val:
                persona_capital_pool_ids.add(val)

        known_plan_ids = {
            str(rb.get("plan_id") or rb.get("deployment_plan_id") or "").strip()
            for rb in runtime_bindings_for_persona
            if isinstance(rb, dict) and str(rb.get("plan_id") or rb.get("deployment_plan_id") or "").strip()
        }
        for candidate_plan_id in (
            persona_meta.get("deployment_plan_id"),
            dep_readback.get("plan_id"),
        ):
            val = str(candidate_plan_id or "").strip()
            if val:
                known_plan_ids.add(val)

        all_candidate_binding_ids = persona_binding_ids | runtime_binding_ids
        expected_tenant = str(
            persona.get("tenant_id")
            or persona_meta.get("tenant_id")
            or ""
        ).strip()
        if not expected_tenant:
            try:
                expected_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None).get("id") or "").strip()
            except Exception:
                expected_tenant = ""

        def _record_tenant_matches(record: Dict[str, Any]) -> bool:
            rec_meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            rec_tenant = str(
                record.get("tenant_id")
                or record.get("tenantId")
                or record.get("owner_tenant")
                or rec_meta.get("tenant_id")
                or rec_meta.get("tenantId")
                or ""
            ).strip()
            return not rec_tenant or not expected_tenant or rec_tenant == expected_tenant

        def _plan_matches_persona(plan: Dict[str, Any]) -> bool:
            if not isinstance(plan, dict) or not _record_tenant_matches(plan):
                return False
            pid = str(plan.get("id") or plan.get("plan_id") or "").strip()
            if pid and pid in known_plan_ids:
                return True
            plan_meta = plan.get("metadata") if isinstance(plan.get("metadata"), dict) else {}
            plan_persona = str(
                plan.get("sponsor_persona_id")
                or plan.get("persona_id")
                or plan_meta.get("persona_id")
                or ""
            ).strip()
            if plan_persona and plan_persona == persona_id:
                return True
            plan_pcb = str(
                plan.get("persona_capital_binding_id")
                or plan_meta.get("persona_capital_binding_id")
                or ""
            ).strip()
            if plan_pcb and plan_pcb in persona_binding_ids:
                return True
            plan_binding_id = str(plan.get("binding_id") or plan.get("runtime_binding_id") or "").strip()
            if plan_binding_id and plan_binding_id in all_candidate_binding_ids:
                return True
            if all_candidate_binding_ids & {
                str(bid).strip() for bid in (plan.get("binding_ids") or []) if str(bid).strip()
            }:
                return True
            plan_pool_id = str(plan.get("capital_pool_id") or plan.get("target_pool_id") or "").strip()
            if plan_pool_id and plan_pool_id in persona_capital_pool_ids:
                return True
            return False

        deployment_plans_for_persona: List[Dict[str, Any]] = []
        approvals_for_persona: List[Dict[str, Any]] = []
        try:
            all_plans = _service.list_deployment_plans()
            seen_plan_ids: set[str] = set()
            for plan in all_plans:
                if _plan_matches_persona(plan):
                    pid = str(plan.get("id") or plan.get("plan_id") or "").strip()
                    if pid:
                        if pid in seen_plan_ids:
                            continue
                        seen_plan_ids.add(pid)
                    deployment_plans_for_persona.append(plan)

            get_plan_fn = getattr(_service, "get_deployment_plan", None)
            if callable(get_plan_fn):
                for pid in sorted(known_plan_ids - seen_plan_ids):
                    fetched_plan = get_plan_fn(pid)
                    if isinstance(fetched_plan, dict) and _plan_matches_persona(fetched_plan):
                        seen_plan_ids.add(pid)
                        deployment_plans_for_persona.append(fetched_plan)

            plan_ids = set(seen_plan_ids) | known_plan_ids
            artifact_and_registry_ids: set[str] = set()
            known_approval_ids: set[str] = set()
            for candidate_target in (
                persona_meta.get("strategy_artifact_id"),
                persona_meta.get("artifact_id"),
                persona_meta.get("strategy_registry_id"),
                persona_meta.get("registry_id"),
                reg_readback.get("artifact_id"),
                reg_readback.get("registry_id"),
                dep_readback.get("artifact_id"),
            ):
                val = str(candidate_target or "").strip()
                if val:
                    artifact_and_registry_ids.add(val)

            for candidate_apv in (
                persona_meta.get("approval_decision_id"),
                persona_meta.get("strategy_artifact_approval_decision_id"),
                gov_readback.get("approval_decision_id"),
                gov_readback.get("decision_id"),
                dep_readback.get("approval_decision_id"),
            ):
                val = str(candidate_apv or "").strip()
                if val:
                    known_approval_ids.add(val)

            for rb in runtime_bindings_for_persona:
                if not isinstance(rb, dict):
                    continue
                for rb_target in (rb.get("artifact_id"), rb.get("registry_id")):
                    val = str(rb_target or "").strip()
                    if val:
                        artifact_and_registry_ids.add(val)
                rb_meta = rb.get("metadata") if isinstance(rb.get("metadata"), dict) else {}
                attestation = (
                    rb_meta.get("authoritative_loader_attestation")
                    if isinstance(rb_meta.get("authoritative_loader_attestation"), dict)
                    else {}
                )
                for rb_apv in (
                    rb.get("approval_decision_id"),
                    rb_meta.get("approval_decision_id"),
                    attestation.get("approval_decision_id"),
                ):
                    val = str(rb_apv or "").strip()
                    if val:
                        known_approval_ids.add(val)
                for rb_att_target in (attestation.get("artifact_id"), attestation.get("registry_id")):
                    val = str(rb_att_target or "").strip()
                    if val:
                        artifact_and_registry_ids.add(val)

            for plan in deployment_plans_for_persona:
                if not isinstance(plan, dict):
                    continue
                for plan_target in (
                    plan.get("artifact_id"),
                    plan.get("strategy_artifact_id"),
                    plan.get("registry_id"),
                ):
                    val = str(plan_target or "").strip()
                    if val:
                        artifact_and_registry_ids.add(val)
                plan_apv = str(plan.get("approval_decision_id") or "").strip()
                if plan_apv:
                    known_approval_ids.add(plan_apv)

            approval_target_ids = plan_ids | artifact_and_registry_ids
            all_approvals = _service.list_approval_decisions()
            seen_approval_ids: set[str] = set()

            def _approval_matches_persona(decision: Dict[str, Any]) -> bool:
                if not isinstance(decision, dict) or not _record_tenant_matches(decision):
                    return False
                did = str(decision.get("decision_id") or decision.get("id") or decision.get("item_id") or "").strip()
                if did and did in known_approval_ids:
                    return True
                target_id = str(decision.get("target_id") or decision.get("plan_id") or "").strip()
                if target_id and target_id in approval_target_ids:
                    return True
                return False

            for decision in all_approvals:
                if _approval_matches_persona(decision):
                    did = str(decision.get("decision_id") or decision.get("id") or decision.get("item_id") or "").strip()
                    if did:
                        if did in seen_approval_ids:
                            continue
                        seen_approval_ids.add(did)
                    approvals_for_persona.append(decision)

            get_apv_fn = getattr(_service, "get_approval_decision", None)
            if callable(get_apv_fn):
                for aid in sorted(known_approval_ids - seen_approval_ids):
                    fetched_apv = get_apv_fn(aid)
                    if isinstance(fetched_apv, dict) and _approval_matches_persona(fetched_apv):
                        seen_approval_ids.add(aid)
                        approvals_for_persona.append(fetched_apv)
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
    def bff_get_persona(
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
            _raise_if_read_surface_unavailable(_dataset_surface_status("personas", snapshot_at=snapshot_at), label="Persona")
            raise _bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )
        dto = _project_persona_dto(
            raw,
            overlay=None,
            routed_strategies=_routed_strategies_for_persona(persona_id),
        )
        containment = _service.get_persona_containment(persona_id)
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
    def bff_patch_persona(
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
        return _service.patch_persona(
            persona_id=persona_id,
            payload=payload,
            identity=identity,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )



    @router.get("/bff/personas/{persona_id}/route-policy")
    def bff_get_persona_route_policy(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona route policy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        policy = _service.get_route_policy_for_persona(persona_id)
        if not policy:
            consult_policy = _service.get_persona_consult_policy(persona_id)
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
    def bff_get_persona_runtime_profile(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona OpenClaw runtime profile contract."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        persona = _service.get_persona(persona_id) or {"persona_id": persona_id}
        route_policy = _service.get_route_policy_for_persona(persona_id)
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
    def bff_get_persona_activity(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona activity (sessions + recent consultations)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        sessions = _service.get_sessions_for_persona(persona_id) or []
        consultations: List[Dict[str, Any]] = _service.list_consultations_for_persona(persona_id) or []
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
    def bff_get_persona_evaluations(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona evaluations (teaching sessions stand in until evaluation runs ship)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        teaching = _service.get_teaching_sessions_for_persona(persona_id) or []
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
    def bff_get_persona_memory(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ):
        """BFF: canonical Memory Plane entries for a persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        memory, source = _retrieve_canonical_persona_memory(persona_id, identity, authorization, (x_tenant_id or '').strip() or None)
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
    def bff_get_persona_audit(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: audit trail for a persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        events = _list_governance_audit_events(include_agora_events=False) or []
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
    def bff_get_persona_skills(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: skills accessible to a persona, derived from capability snapshot (PER-002)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        snapshot = _service.get_capability_snapshot_for_persona(persona_id)
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
    def bff_get_persona_tools(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: tools accessible to a persona, derived from capability snapshot (PER-002)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        snapshot = _service.get_capability_snapshot_for_persona(persona_id)
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
    def bff_get_persona_capabilities_surface(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: capability snapshot surface for a persona (PER-002 read surface)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_persona_exists(persona_id)
        snapshot_at = utc_now()
        snapshot = _service.get_capability_snapshot_for_persona(persona_id)
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
