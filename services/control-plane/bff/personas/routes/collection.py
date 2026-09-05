"""Persona collection and fleet routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, Query

from services.control_plane.bff.models import ErrorCode
from ..service import (
    PersonaProvisioningCoordinationError,
    ProvisioningConflict,
    ProvisioningRecord,
    _PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
    _bff_me_tenant_payload,
    _coordinate_persona_create,
    _dry_run_success_response,
    _get_persona_directory_snapshot,
    _management_prune_camel_aliases,
    _normalize_persona_create_name,
    _normalize_risk_level,
    _persona_create_canonical_payload,
    _persona_create_identity,
    _persona_create_required_data_sources,
    _persona_create_response,
    _persona_create_validate_paper_only,
    _persona_fleet_slim_list_payload,
    _persona_intent_all_items,
    _persona_intent_filter_items,
    _persona_intent_summary,
    _persona_intent_surfaces,
    _persona_provisioning_metadata,
    _persona_record_archetype,
    _persona_record_projected_state,
    _project_persona_dto,
    _project_persona_list_records,
    _request_dry_run_requested,
    _stable_json_hash,
    deterministic_provisioning_ids,
)
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)


def build_collection_router(ctx: PersonaRouteContext) -> APIRouter:
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

    @router.get("/api/v1/personas")
    async def list_personas(
        lifecycle_state: Optional[str] = None,
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        """PS-01: Persona List with optional filters."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        personas = read_store.list_personas(
            lifecycle_state=lifecycle_state,
            mandate=mandate,
            strategy_family=strategy_family,
        )
        snapshot_at = utc_now()
        return {
            "data": personas,
            "meta": _read_surface_meta(
                "personas",
                "persona_list",
                snapshot_at=snapshot_at,
                total=len(personas),
            ),
        }


    @router.get("/bff/management/persona-intent")
    async def bff_management_persona_intent(
        source_type: Optional[str] = None,
        persona_id: Optional[str] = None,
        status: Optional[str] = None,
        intent: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: compose redacted Persona Intent trace, trainer, and Agora summaries."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])

        snapshot_at = utc_now()
        items, _persona_sessions, _trainer_sessions, _agora_sessions = _persona_intent_all_items(caller_tenant_id)
        filtered = _persona_intent_filter_items(
            items,
            source_type=source_type,
            persona_id=persona_id,
            status=status,
            intent=intent,
        )
        total = len(filtered)
        page_items, next_page_token = _page_slice(filtered, page_token, page_size)
        summary = _management_prune_camel_aliases(_persona_intent_summary(filtered, len(page_items)))
        canonical_page_items = _management_prune_camel_aliases(page_items)
        meta = _snapshot_meta(snapshot_at)
        meta["surfaces"] = _persona_intent_surfaces(snapshot_at=snapshot_at)
        meta["composition_sources"] = [
            "persona_traces",
            "teaching_sessions",
            "agora_sessions",
        ]
        meta["redacted_item_count"] = summary["redacted_item_count"]
        return {
            "data": {
                "id": "management_persona_intent",
                "items": canonical_page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": meta,
        }


    @router.get("/bff/personas")
    async def bff_list_personas(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona list (execute-plans Persona DTO compatibility)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = utc_now()
        tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        directory = _get_persona_directory_snapshot(tenant_id, snapshot_at=snapshot_at)
        raw_personas = list(directory.records_by_id.values())
        if state:
            raw_personas = [
                raw for raw in raw_personas if _persona_record_projected_state(raw) == state
            ]
        if archetype:
            raw_personas = [
                raw for raw in raw_personas if _persona_record_archetype(raw) == archetype
            ]
        canonical_total = len(directory.records_by_id)
        filtered_total = len(raw_personas)
        catalog_default_total = len(directory.catalog_defaults_by_id)
        page_raw, next_page_token = _page_slice(raw_personas, page_token, page_size)
        page_items = await asyncio.to_thread(_project_persona_list_records, page_raw)
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {
                "next_page_token": next_page_token,
                "total": filtered_total,
                "canonical_total": canonical_total,
                "filtered_total": filtered_total,
                "catalog_default_total": catalog_default_total,
            },
            "meta": _read_surface_meta(
                "personas", "persona_list",
                snapshot_at=snapshot_at, total=filtered_total,
            ),
        }


    @router.post("/bff/personas", status_code=201)
    async def bff_create_persona(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Create a real dynamic paper Persona through canonical owner services."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)

        name = str(payload.get("name") or "").strip()
        if not name:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "name is required",
                "Persona name must be a non-empty string",
                precondition_failed="name",
            )
        _persona_create_validate_paper_only(payload)

        requested_owner = str(payload.get("owner") or "").strip()
        if requested_owner and requested_owner != identity.operator_id:
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Persona owner must match the authenticated operator",
                "Client-supplied owner assertions cannot impersonate another operator",
                precondition_failed="owner",
            )
        tenant = _bff_me_tenant_payload(
            identity,
            requested_tenant=str(payload.get("tenantId") or payload.get("tenant_id") or "").strip()
            or None,
        )
        tenant_id = str(tenant["id"])
        normalized_name = _normalize_persona_create_name(name)
        canonical_payload = _persona_create_canonical_payload(
            payload,
            name=name,
            tenant_id=tenant_id,
            requested_by=identity.operator_id,
        )
        request_hash = _stable_json_hash(
            {
                "route": "POST /bff/personas",
                "tenant_id": tenant_id,
                "payload": canonical_payload,
            }
        )
        snapshot_at = utc_now()
        record = ProvisioningRecord(
            tenant_id=tenant_id,
            idempotency_key=resolved_key,
            request_hash=request_hash,
            normalized_name=normalized_name,
            persona_id=_persona_create_identity(tenant_id, normalized_name),
            request_payload=canonical_payload,
            created_at=snapshot_at,
            updated_at=snapshot_at,
        )

        if _request_dry_run_requested():
            ids = deterministic_provisioning_ids(record)
            archetype = str(canonical_payload.get("archetype") or "generalist")
            risk = _normalize_risk_level(canonical_payload.get("risk") or "low")
            mandate = str(canonical_payload.get("mandate") or "").strip() or None
            strategy_family = str(
                canonical_payload.get("strategy_family")
                or canonical_payload.get("strategyFamily")
                or ""
            ).strip() or None
            raw_traits = canonical_payload.get("traits")
            traits = dict(raw_traits) if isinstance(raw_traits, dict) else None
            metadata = _persona_provisioning_metadata(
                record,
                ids=ids,
                payload=canonical_payload,
                owner=identity.operator_id,
                archetype=archetype,
                risk=risk,
                mandate=mandate,
                strategy_family=strategy_family,
                traits=traits,
                lifecycle_state="provisioning",
            )
            preview_persona = {
                "id": record.persona_id,
                "persona_id": record.persona_id,
                "name": name,
                "mandate": mandate or archetype,
                "strategy_family": strategy_family or archetype,
                "lifecycle_state": "provisioning",
                "created_at": snapshot_at,
                "updated_at": snapshot_at,
                "created_by": identity.operator_id,
                "required_data_sources": _persona_create_required_data_sources(canonical_payload),
                "metadata": {
                    **metadata,
                    "owner": identity.operator_id,
                    "archetype": archetype,
                    "risk_level": risk,
                },
            }
            preview = _project_persona_dto(
                preview_persona,
                overlay={
                    "capitalMode": "paper",
                    "paperLedgerId": metadata["paper_ledger_id"],
                    "paperLedger": metadata["paper_ledger"],
                    "legacyPaperCapitalPoolId": ids.capital_pool_id,
                    "deploymentPlanId": ids.deployment_plan_id,
                    "deploymentStage": "paper",
                },
                routed_strategies=0,
                evaluate_provisioning=False,
            )
            return _dry_run_success_response(
                preview,
                status_code=201,
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="persona.create",
                extra_meta={
                    "create_flow": "durable_owner_coordinated_provisioning_preview",
                    "mutations_performed": False,
                    "preview_ids": ids.to_dict(),
                    "first_evaluation_workflow_id": _PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                },
            )

        try:
            active, persona, metadata, ooda_packet = await asyncio.to_thread(
                _coordinate_persona_create,
                record,
                payload=canonical_payload,
                owner=identity.operator_id,
            )
        except ProvisioningConflict as exc:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Persona create conflicts with an existing durable reservation",
                "Idempotency key or Persona name conflicts with an existing reservation",
                precondition_failed="idempotency_or_tenant_name",
                suggestion="Replay the original request unchanged or choose a different Persona name",
            ) from exc
        except PersonaProvisioningCoordinationError as exc:
            raise _bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Persona provisioning coordinator is unavailable",
                "Persona provisioning coordinator lease is held by another worker or unavailable",
                precondition_failed="provisioning_coordinator",
                suggestion="Retry the same Idempotency-Key after the active coordinator lease expires",
            ) from exc
        except Exception as exc:
            log.exception("Unexpected error during persona provisioning coordination: %s", exc)
            raise _bff_error(
                502,
                ErrorCode.UPSTREAM_ERROR,
                "Persona provisioning failed due to an upstream dependency or persistence error",
                "Downstream owner service or persistence coordinator returned an error",
                precondition_failed="provisioning_coordination",
                suggestion="Inspect the Persona provisioning logs before retrying with the same Idempotency-Key",
                details_extra={
                    "personaId": record.persona_id,
                    "provisioningState": "failed",
                },
            ) from exc

        response = _persona_create_response(
            active,
            persona=persona,
            metadata=metadata,
            payload=canonical_payload,
            snapshot_at=snapshot_at,
            ooda_packet=ooda_packet,
        )
        if active.state in {"failed", "compensated"}:
            failed_step = str((active.error or {}).get("failed_step") or "provisioning")
            raise _bff_error(
                502,
                ErrorCode.UPSTREAM_ERROR,
                "Persona provisioning failed",
                "Downstream owner rejected persona provisioning step",
                precondition_failed=failed_step,
                suggestion="Inspect the persisted Persona provisioning receipt before a governed retry",
                details_extra={
                    "personaId": active.persona_id,
                    "provisioningState": active.state,
                    "provisioningStep": active.current_step,
                    "compensation": active.compensation,
                },
            )
        return response


    @router.post("/bff/management/personas/create-paper-bundle", status_code=201)
    async def bff_create_paper_persona_bundle(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Create a restart-safe paper Persona bundle through the shared coordinator."""
        return await bff_create_persona(
            payload=payload,
            authorization=authorization,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )


    @router.get("/bff/management/persona-fleet")
    async def bff_management_persona_fleet(
        state: Optional[str] = None,
        health: Optional[str] = None,
        deployment_stage: Optional[str] = None,
        market_scope: Optional[str] = None,
        q: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        tenant_payload = _bff_me_tenant_payload(identity, requested_tenant=None)
        tenant_id = str(tenant_payload.get("id") or "tenant-default")
        return _persona_fleet_slim_list_payload(
            tenant_id=tenant_id,
            snapshot_at=utc_now(),
            state=state,
            health=health,
            deployment_stage=deployment_stage,
            market_scope=market_scope,
            q=q,
            page_token=page_token,
            page_size=page_size,
        )

    return router
