"""Persona Management canonical domain router.

Part of OPGAP-BE-PERSONA-ROUTER-V2-20260830.
Zero reverse imports of main.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from . import service as _service_mod
from .service import PersonaService

# Bring all symbols from service into module namespace
for _k, _v in list(_service_mod.__dict__.items()):
    if not _k.startswith("__"):
        globals()[_k] = _v

log = logging.getLogger(__name__)


def create_personas_router(
    *,
    service: Optional[PersonaService] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    get_provisioning_store: Optional[Callable[[], Any]] = None,
    extract_identity_fn: Optional[Callable[..., Any]] = None,
    require_read_role_fn: Optional[Callable[..., None]] = None,
    require_operator_role_fn: Optional[Callable[..., None]] = None,
    bff_error_fn: Optional[Callable[..., HTTPException]] = None,
    utc_now_fn: Optional[Callable[[], str]] = None,
    page_slice_fn: Optional[Callable[..., Any]] = None,
    snapshot_meta_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    dataset_surface_status_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    raise_if_read_surface_unavailable_fn: Optional[Callable[..., None]] = None,
    reject_body_idempotency_key_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
    resolve_final_idempotency_key_fn: Optional[Callable[[Optional[str], Optional[str]], str]] = None,
    submit_persona_action_fn: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Build the canonical Persona Management domain router.

    Registers 49 route decorators across 45 unique handlers.
    """
    router = APIRouter(tags=["personas"])

    _service = service or PersonaService(
        get_read_store=get_read_store,
        get_command_store=get_command_store,
        get_provisioning_store=get_provisioning_store,
        utc_now_fn=utc_now_fn,
        bff_error_fn=bff_error_fn,
        snapshot_meta_fn=snapshot_meta_fn,
        dataset_surface_status_fn=dataset_surface_status_fn,
        raise_if_read_surface_unavailable_fn=raise_if_read_surface_unavailable_fn,
    )

    read_store = _service.get_read_store()
    command_store = _service.get_command_store()

    _extract_identity = extract_identity_fn or getattr(_service_mod, "_extract_identity")
    _require_read_role = require_read_role_fn or getattr(_service_mod, "_require_read_role")
    _require_operator_role = require_operator_role_fn or getattr(_service_mod, "_require_operator_role")
    _bff_error = bff_error_fn or getattr(_service_mod, "_bff_error")
    utc_now = utc_now_fn or getattr(_service_mod, "utc_now")
    _page_slice = page_slice_fn or getattr(_service_mod, "_page_slice")
    _snapshot_meta = snapshot_meta_fn or getattr(_service_mod, "_snapshot_meta")
    _dataset_surface_status = dataset_surface_status_fn or getattr(_service_mod, "_dataset_surface_status")
    _raise_if_read_surface_unavailable = raise_if_read_surface_unavailable_fn or getattr(_service_mod, "_raise_if_read_surface_unavailable")
    _reject_body_idempotency_key = reject_body_idempotency_key_fn or getattr(_service_mod, "_reject_body_idempotency_key")
    _resolve_final_idempotency_key = resolve_final_idempotency_key_fn or getattr(_service_mod, "_resolve_final_idempotency_key")


    # --- list_personas ---
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

    # --- get_persona_detail ---
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

    # --- list_persona_sessions ---
    @router.get("/api/v1/personas/{persona_id}/sessions")
    async def list_persona_sessions(
        persona_id: str,
        status: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        """PS-03: Persona Sessions list."""
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

        sessions = read_store.list_sessions_for_persona(persona_id, status=status) or []
        return {
            "data": sessions,
            "meta": _read_surface_meta(
                "sessions",
                "persona_sessions",
                snapshot_at=snapshot_at,
                total=len(sessions),
            ),
        }

    # --- get_session_detail ---
    @router.get("/api/v1/sessions/{session_id}")
    async def get_session_detail(session_id: str, authorization: Optional[str] = Header(default=None)):
        """PS-04: Session detail with capability snapshot."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        session_surface = _dataset_surface_status("sessions", snapshot_at=snapshot_at)
        session = read_store.get_session(session_id)
        if not session:
            _raise_if_read_surface_unavailable(session_surface, label="Session")
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Session not found",
                f"Session {session_id} does not exist",
            )

        snapshot = read_store.get_capability_snapshot(session.get("capability_snapshot_id"))
        if snapshot is None:
            snapshot = read_store.get_capability_snapshot_for_persona(session.get("persona_id"))

        payload = dict(session)
        if snapshot:
            payload["capability_snapshot"] = snapshot

        return {
            "data": payload,
            "meta": _read_surface_meta(
                "sessions",
                "session_detail",
                snapshot_at=snapshot_at,
                surface=session_surface,
            ),
        }

    # --- list_persona_teaching_sessions ---
    @router.get("/api/v1/personas/{persona_id}/teaching")
    async def list_persona_teaching_sessions(
        persona_id: str,
        status: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ):
        """PS-05: Teaching sessions list for a persona."""
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

        sessions = read_store.list_teaching_sessions_for_persona(persona_id, status=status) or []
        return {
            "data": sessions,
            "meta": _read_surface_meta(
                "teaching_sessions",
                "teaching_sessions",
                snapshot_at=snapshot_at,
                total=len(sessions),
            ),
        }

    # --- get_persona_capabilities ---
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

    # --- get_persona_management ---
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

    # --- bff_ppl_alloc_009_paper_eligibility_proof ---
    @router.post("/bff/management/personas/{persona_id}/ppl-alloc-009-paper-eligibility-proof", status_code=202)
    async def bff_ppl_alloc_009_paper_eligibility_proof(
        persona_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Run the exact dev-only paper positive control through telemetry owner."""

        identity = _extract_identity(authorization, mfa_token=x_mfa_token)
        _require_operator_role(identity)
        if not identity.mfa_verified:
            raise _ppl_alloc_009_eligibility_error(
                "Paper eligibility proof requires MFA",
                "The authenticated operator identity has no verified MFA claim.",
                precondition="mfa",
                status_code=403,
            )
        _ppl_alloc_009_paper_environment_guard()
        if not _ppl_alloc_009_dev_proof_enabled():
            raise _ppl_alloc_009_eligibility_error(
                "PPL-ALLOC-009 dev proof is disabled",
                (
                    "PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED must be explicitly "
                    "enabled for the one bounded acceptance run."
                ),
                precondition="dev_proof_feature_flag",
                status_code=403,
            )
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(
            idempotency_key,
            x_idempotency_key,
        )
        if resolved_key != _PPL_ALLOC_009_ELIGIBILITY_IDEMPOTENCY_KEY:
            raise _ppl_alloc_009_eligibility_error(
                "Idempotency key is outside the task scope",
                "Use the exact PPL-ALLOC-009 acceptance retry key.",
                precondition="task_idempotency_key",
            )
        expected_payload = {
            "task_id": _PPL_ALLOC_009_ELIGIBILITY_TASK_ID,
            "run_key": _PPL_ALLOC_009_ELIGIBILITY_RUN_KEY,
            "benchmark_version": _PPL_ALLOC_009_ELIGIBILITY_BENCHMARK_VERSION,
        }
        if payload != expected_payload:
            raise _ppl_alloc_009_eligibility_error(
                "Paper benchmark request is outside the task scope",
                (
                    "The route accepts only the immutable PPL-ALLOC-009 task, run, "
                    "and benchmark identities; clients cannot submit metrics."
                ),
                precondition="paper_benchmark_identity",
            )

        try:
            observed_at = await asyncio.to_thread(
                _ppl_alloc_009_eligibility_observation_store.reserve,
                idempotency_key=resolved_key,
                proposed_at=utc_now(),
            )
        except Exception as exc:
            raise _ppl_alloc_009_eligibility_error(
                "Paper benchmark observation could not be reserved",
                str(exc) or exc.__class__.__name__,
                precondition="paper_benchmark_observation",
                status_code=503,
            ) from exc
        context = await asyncio.to_thread(
            _ppl_alloc_009_paper_eligibility_context,
            persona_id=persona_id,
            identity=identity,
            observed_at=observed_at,
        )
        event, benchmark = _ppl_alloc_009_build_telemetry_event(
            persona_id=persona_id,
            actor_id=identity.operator_id,
            idempotency_key=resolved_key,
            observed_at=observed_at,
            runtime_binding=context["runtime_binding"],
            strategy_id=context["strategy_id"],
        )
        expected_metrics = benchmark["metrics"]
        write_reconciliation = "accepted"
        try:
            owner_receipt = await asyncio.to_thread(
                _post_json,
                _ppl_alloc_009_telemetry_url("/api/telemetry/ingest"),
                event,
            )
        except urllib_error.HTTPError as exc:
            if exc.code != 409:
                raise _ppl_alloc_009_eligibility_error(
                    "Telemetry owner rejected the paper benchmark",
                    f"HTTP {exc.code}",
                    precondition="telemetry_owner_receipt",
                    status_code=502,
                ) from exc
            owner_receipt = {"status": "idempotent_replay", "http_status": 409}
            write_reconciliation = "http_409_readback"
        except (urllib_error.URLError, TimeoutError, OSError) as exc:
            owner_receipt = {
                "status": "write_outcome_uncertain",
                "error_type": exc.__class__.__name__,
            }
            write_reconciliation = "uncertain_write_readback"
        except Exception as exc:
            raise _ppl_alloc_009_eligibility_error(
                "Telemetry owner rejected the paper benchmark",
                str(exc) or exc.__class__.__name__,
                precondition="telemetry_owner_receipt",
                status_code=502,
            ) from exc
        if (
            not isinstance(owner_receipt, dict)
            or owner_receipt.get("status")
            not in {"accepted", "idempotent_replay", "write_outcome_uncertain"}
        ):
            raise _ppl_alloc_009_eligibility_error(
                "Telemetry owner returned an invalid receipt",
                "The ingest response must carry an accepted or reconcilable status.",
                precondition="telemetry_owner_receipt_schema",
                status_code=502,
            )
        try:
            telemetry_readback, readback_attempts = await asyncio.to_thread(
                _ppl_alloc_009_wait_for_telemetry_readback,
                expected_event=event,
            )
        except Exception as exc:
            raise _ppl_alloc_009_eligibility_error(
                "Telemetry owner readback did not prove the paper benchmark",
                str(exc) or exc.__class__.__name__,
                precondition="telemetry_owner_readback",
                status_code=502,
            ) from exc

        refreshed_rows = _pm12_persona_league_rows(q=persona_id, tenant_id=caller_tenant)
        refreshed_matches = [
            _pm12_persona_league_ranking_item(row)
            for row in refreshed_rows
            if str(row.get("persona_id") or row.get("id") or "").strip() == persona_id
        ]
        refreshed = refreshed_matches[0] if len(refreshed_matches) == 1 else {}
        actions = _pm12_recommendation_action_ids(refreshed) if refreshed else []
        if (
            refreshed.get("eligible") is not True
            or "promote_to_canary_candidate" not in actions
        ):
            raise _ppl_alloc_009_eligibility_error(
                "Canonical ranking did not admit the positive control",
                (
                    "Telemetry was accepted, but the authoritative ranking did not "
                    "produce the required promotion-review recommendation."
                ),
                precondition="canonical_promotion_eligibility",
                status_code=502,
            )

        return {
            "data": {
                "task_id": _PPL_ALLOC_009_ELIGIBILITY_TASK_ID,
                "run_key": _PPL_ALLOC_009_ELIGIBILITY_RUN_KEY,
                "benchmark_version": _PPL_ALLOC_009_ELIGIBILITY_BENCHMARK_VERSION,
                "observed_at": observed_at,
                "scenario_digest": benchmark["scenario_digest"],
                "event_id": event["event_id"],
                "trace_id": event["trace_id"],
                "persona_id": persona_id,
                "runtime_id": event["runtime_id"],
                "runtime_binding_id": event["binding_id"],
                "persona_capital_binding_id": event["persona_capital_binding_id"],
                "capital_pool_id": event["capital_pool_id"],
                "paper_session_id": context["paper_session_id"],
                "paper_ledger_id": context["paper_ledger_id"],
                "metrics": expected_metrics,
                "ranking": {
                    "eligible": refreshed["eligible"],
                    "overall_score": refreshed["overall_score"],
                    "components": refreshed["components"],
                    "recommendation_action_ids": actions,
                },
                "owner_receipt": {
                    "service": "telemetry",
                    "status": owner_receipt["status"],
                    "accepted_event_id": event["event_id"],
                    "reconciliation": write_reconciliation,
                    "readback_attempts": readback_attempts,
                    "readback_event_id": telemetry_readback.get("event_id"),
                    "readback_created_at": telemetry_readback.get("created_at"),
                },
                "safety": {
                    "paper_only": True,
                    "real_capital_side_effects": False,
                    "real_order_side_effects": False,
                    "canary_execution_enabled": False,
                    "live_execution_enabled": False,
                },
            },
            "meta": {
                "snapshot_at": utc_now(),
                "source": "telemetry_owner_positive_control_readback",
                "idempotency_key": resolved_key,
            },
        }

    # --- bff_management_persona_intent ---
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

    # --- bff_list_personas ---
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

    # --- bff_reconcile_persona_provisioning ---
    @router.post("/bff/personas/{persona_id}/provisioning/reconcile")
    async def bff_reconcile_persona_provisioning(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """Operator-triggered controller pass; Persona GET/list remain pure reads."""

        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        caller_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        directory = _get_persona_directory_snapshot(caller_tenant)
        raw = directory.records_by_id.get(persona_id) or read_store.get_persona(persona_id)
        if (
            raw is None
            or _persona_record_tenant_id(raw) != caller_tenant
        ):
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona {persona_id} does not exist",
            )
        diagnostics: List[str] = []
        state = await asyncio.to_thread(
            _evaluate_persona_provisioning_status,
            persona_id,
            raw,
            diagnostics=diagnostics,
        )
        dto = _project_persona_dto(
            raw,
            overlay=_PERSONA_BFF_OVERLAY.get(persona_id),
            routed_strategies=_routed_strategies_for_persona(persona_id),
            evaluate_provisioning=False,
        )
        authoritative_meta = _persona_provisioning_authoritative_meta(raw)
        return {
            "data": dto,
            "meta": {
                "snapshot_at": utc_now(),
                "reconciled_by": "persona_provisioning_controller",
                "lifecycle_state": state,
                "status": "degraded" if diagnostics else "ok",
                "degraded_dependencies": sorted(set(diagnostics)),
                "authoritative_readback": authoritative_meta,
            },
        }

    # --- bff_create_persona ---
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

    # --- bff_create_paper_persona_bundle ---
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

    # --- bff_get_persona ---
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

    # --- bff_patch_persona ---
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

    # --- bff_get_persona_route_policy ---
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

    # --- bff_get_persona_runtime_profile ---
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

    # --- bff_get_persona_strategy_matches ---
    @router.get("/api/v1/personas/{persona_id}/strategy-matches")
    @router.get("/bff/personas/{persona_id}/strategy-matches")
    async def bff_get_persona_strategy_matches(
        persona_id: str,
        include_retired: bool = False,
        include_blocked: bool = True,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: explainable Persona strategy discovery matches.

        This is a research-only read surface.  It can recommend research tickets,
        seed-candidate promotion review, or rapid eval requests; it never grants
        deployment, broker, runtime, or order-routing authority.
        """
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _persona_strategy_matches_response(
            persona_id,
            include_retired=include_retired,
            include_blocked=include_blocked,
            page_token=page_token,
            page_size=page_size,
        )

    # --- bff_start_persona_strategy_discovery ---
    @router.post("/api/v1/personas/{persona_id}/strategy-discovery", status_code=202)
    @router.post("/bff/personas/{persona_id}/strategy-discovery", status_code=202)
    async def bff_start_persona_strategy_discovery(
        persona_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: create a transient research-only discovery session view."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash(
            {
                "route": "POST /api/v1/personas/{persona_id}/strategy-discovery",
                "persona_id": persona_id,
                "payload": payload,
            }
        )
        cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        discovery_session_id = f"psd-{hashlib.sha256(resolved_key.encode('utf-8')).hexdigest()[:12]}"
        response = _persona_strategy_matches_response(
            persona_id,
            include_retired=bool(payload.get("include_retired", False)),
            include_blocked=bool(payload.get("include_blocked", True)),
            page_token=None,
            page_size=_strategy_discovery_page_size(payload.get("page_size")),
            discovery_session_id=discovery_session_id,
        )
        response["discovery_session"] = {
            "session_id": discovery_session_id,
            "status": "candidate",
            "research_only": True,
            "execution_route": "none",
        }
        _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {
            "request_hash": request_hash,
            "result": response,
        }
        return response

    # --- bff_persona_strategy_match_action ---
    @router.post("/api/v1/personas/{persona_id}/strategy-matches/{match_id}/actions", status_code=202)
    @router.post("/bff/personas/{persona_id}/strategy-matches/{match_id}/actions", status_code=202)
    async def bff_persona_strategy_match_action(
        persona_id: str,
        match_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: research-only action for a Persona strategy match."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _persona_strategy_match_action_response(
            persona_id=persona_id,
            match_id=match_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    # --- bff_get_persona_activity ---
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

    # --- bff_get_persona_evaluations ---
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

    # --- bff_get_persona_memory ---
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

    # --- bff_get_persona_audit ---
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

    # --- bff_get_persona_skills ---
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

    # --- bff_get_persona_tools ---
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

    # --- bff_get_persona_capabilities_surface ---
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

    # --- bff_management_quarterly_ranking_recommendation_submit ---
    @router.post("/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit", status_code=202)
    async def bff_management_quarterly_ranking_recommendation_submit(
        recommendation_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: submit a PM-12 recommendation into Human Gate review without live mutation."""
        route_review_id = _promotion_review_clean_id(recommendation_id)
        recommendation_id = _promotion_review_revision_recommendation_id(
            route_review_id
        )
        identity = _extract_identity(authorization)
        if not {"operator", "approver", "admin"}.intersection(identity.roles):
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Quarterly ranking recommendation submission requires operator-level role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
                suggestion="Escalate to a user with operator, approver, or admin role",
            )
        _reject_body_idempotency_key(payload)
        _raise_if_promotion_review_direct_mutation_requested(payload)
        for key in ("recommendation_id", "recommendationId"):
            asserted_id = str(payload.get(key) or "").strip()
            if asserted_id and asserted_id != recommendation_id:
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "recommendation id assertion mismatch",
                    f"{key} must match the recommendation id in the route.",
                    precondition_failed="recommendation_id",
                )

        snapshot_at = utc_now()
        requested_ranking_snapshot_id = str(
            payload.get("ranking_snapshot_id") or ""
        ).strip()
        command_payload: Optional[Dict[str, Any]] = None
        if requested_ranking_snapshot_id:
            command_payload = {
                **payload,
                "quarter": (
                    payload.get("quarter")
                    or _promotion_review_quarter_from_id(recommendation_id)
                ),
                "recommendation_id": recommendation_id,
                "ranking_snapshot_id": requested_ranking_snapshot_id,
            }
            # Validate caller assertions against the durable snapshot before
            # resolving the dynamic current alias. Forged IDs and snapshots remain
            # validation failures rather than being masked as a missing current row.
            _validate_quarterly_ranking_recommendation_submit(
                command_payload,
                identity,
            )
        current_review: Optional[Dict[str, Any]] = None
        if not requested_ranking_snapshot_id:
            # A snapshotless request deliberately follows the mutable stable alias.
            # A caller that supplied an admitted snapshot has already been resolved
            # from the durable snapshot store above and must not be rebound to this
            # current-only projection after a lifecycle/session rotation.
            current_review, _, _, _ = _promotion_review_find(
                identity,
                recommendation_id,
                snapshot_at=snapshot_at,
                quarter=str(payload.get("quarter") or "").strip() or None,
                include_historical=False,
            )
            if current_review is None:
                if route_review_id == recommendation_id:
                    raise _bff_error(
                        404,
                        ErrorCode.RESOURCE_NOT_FOUND,
                        "Quarterly ranking recommendation not found",
                        f"Recommendation {recommendation_id} does not exist",
                        precondition_failed="recommendation_id",
                    )
                raise _bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "historical promotion review requires its immutable snapshot",
                    "Refresh the historical review and replay it with ranking_snapshot_id.",
                    precondition_failed="ranking_snapshot_id",
                )
            requested_ranking_snapshot_id = str(
                current_review.get("ranking_snapshot_id") or ""
            ).strip()
            command_payload = {
                **payload,
                "quarter": (
                    payload.get("quarter")
                    or _promotion_review_quarter_from_id(recommendation_id)
                ),
                "recommendation_id": recommendation_id,
                "ranking_snapshot_id": requested_ranking_snapshot_id,
            }
            _validate_quarterly_ranking_recommendation_submit(
                command_payload,
                identity,
            )
        assert command_payload is not None
        review_revision_id = str(
            command_payload.get("promotion_review_id")
            or command_payload.get("review_id")
            or ""
        ).strip()
        if not review_revision_id:
            raise _bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "admitted ranking snapshot has no promotion review revision",
                "The server could not bind the recommendation to its immutable snapshot.",
                precondition_failed="promotion_review_id",
            )
        if (
            route_review_id != recommendation_id
            and route_review_id != review_revision_id
        ):
            raise _bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "promotion review revision is stale",
                "The route revision does not identify the admitted ranking snapshot.",
                precondition_failed="promotion_review_id",
                suggestion="Refresh the current recommendation before submitting.",
            )

        existing_submission = _promotion_review_submission_projection(
            review_revision_id,
            include_source_recommendation=True,
        )
        if existing_submission:
            stored_source = existing_submission.get("source_recommendation")
            if not isinstance(stored_source, dict):
                raise _bff_error(
                    409,
                    ErrorCode.PRECONDITION_FAILED,
                    "submitted recommendation has no immutable source snapshot",
                    "The legacy submission is audit-readable but cannot be replayed as a snapshot-bound revision.",
                    precondition_failed="source_recommendation",
                    suggestion="Submit the current governed recommendation revision.",
                )
            stored_source = json.loads(json.dumps(stored_source))
            # Evidence visibility is request-scoped. Never replay stored evidence
            # bodies across identities or roles.
            stored_source["evidence_refs"] = []
            stored_source["evidence_ref_ids"] = []
            already = _promotion_review_item_from_recommendation(stored_source)
            replay_snapshot_id = str(
                existing_submission.get("ranking_snapshot_id")
                or already.get("ranking_snapshot_id")
                or ""
            ).strip()
            return JSONResponse(
                status_code=200,
                content=jsonable_encoder(
                    {
                        "data": {
                            "command_id": existing_submission.get("command_id"),
                            "review_id": already["review_id"],
                            "promotion_review_id": already["promotion_review_id"],
                            "recommendation_id": already["recommendation_id"],
                            "persona_id": already.get("persona_id"),
                            "action_id": already.get("action_id"),
                            "ranking_snapshot_id": replay_snapshot_id,
                            "status": already.get("status"),
                            "submitted": True,
                            "human_inbox_id": already.get("human_inbox_id"),
                            "requires_human_gate_decision": True,
                            "live_capital_mutation": False,
                            "review": already,
                            "links": already.get("links") or {},
                        },
                        "meta": {
                            **_snapshot_meta(snapshot_at),
                            "ranking_snapshot_id": replay_snapshot_id,
                            "idempotency": {
                                "replayed": True,
                                "source": "existing_submission",
                            },
                            "live_capital_mutation": False,
                            "direct_live_capital_mutation": False,
                            "requires_human_gate_decision": True,
                            "governance_policy": "promotion_governance_human_gate_no_direct_live_capital",
                        },
                    }
                ),
            )
        if route_review_id != recommendation_id:
            if current_review is None:
                current_review, _, _, _ = _promotion_review_find(
                    identity,
                    recommendation_id,
                    snapshot_at=snapshot_at,
                    quarter=str(payload.get("quarter") or "").strip() or None,
                    include_historical=False,
                )
            current_revision_id = str(
                (current_review or {}).get("promotion_review_id")
                or (current_review or {}).get("review_id")
                or ""
            ).strip()
            if route_review_id != current_revision_id:
                raise _bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "historical promotion review cannot create a new submission",
                    "Only the current admitted recommendation revision may create a Human Gate submission.",
                    precondition_failed="promotion_review_id",
                    suggestion="Refresh the current recommendation before submitting.",
                )

        source_recommendation = command_payload.get("source_recommendation")
        if not isinstance(source_recommendation, dict):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "admitted ranking snapshot has no recommendation",
                "The durable snapshot could not materialize the requested recommendation.",
                precondition_failed="recommendation_id",
            )
        review = _promotion_review_item_from_recommendation(source_recommendation)
        client_idempotency_key = _resolve_final_idempotency_key(
            idempotency_key,
            x_idempotency_key,
        )
        scoped_idempotency_key = _promotion_review_scoped_idempotency_key(
            client_idempotency_key,
            None,
            review["review_id"],
        )
        command_response = _sem_command_response(
            command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
            target_type=ObjectType.RANKING,
            target_id=review["review_id"],
            payload=command_payload,
            identity=identity,
            idempotency_key=scoped_idempotency_key,
            trusted_evidence_producer=_HUMAN_INBOX_PROMOTION_PRODUCER,
        )
        return _promotion_review_submit_response(
            command_response,
            review=review,
            client_idempotency_key=client_idempotency_key,
        )

    # --- bff_management_promotion_reviews ---
    @router.get("/bff/management/promotion-reviews")
    async def bff_management_promotion_reviews(
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        action_id: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: promotion review queue derived from PM-12 recommendations."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        reviews, quarter_window, redacted_count, evidence_dataset_available = _promotion_review_items(
            identity,
            snapshot_at=snapshot_at,
            quarter=quarter,
            state=state,
            archetype=archetype,
            q=q,
        )
        if action_id:
            clean_action = str(action_id or "").strip()
            if clean_action not in _PROMOTION_REVIEW_ACTION_IDS:
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "action_id is not a promotion review action",
                    f"action_id must be one of {sorted(_PROMOTION_REVIEW_ACTION_IDS)}",
                    precondition_failed="action_id",
                )
            reviews = [item for item in reviews if item.get("action_id") == clean_action]
        if status:
            requested_statuses = {value.strip() for value in str(status).split(",") if value.strip()}
            reviews = [item for item in reviews if str(item.get("status") or "") in requested_statuses]

        total = len(reviews)
        page_items, next_page_token = _page_slice(reviews, page_token, page_size)
        surfaces = _promotion_review_surfaces(
            snapshot_at=snapshot_at,
            evidence_dataset_available=evidence_dataset_available,
        )
        summary = {
            "quarter": quarter_window["quarter"],
            "review_count": total,
            "returned_count": len(page_items),
            "pending_count": len([item for item in reviews if item.get("decision_status") == "pending"]),
            "decision_accepted_count": len([item for item in reviews if item.get("decision_status") == "accepted"]),
            "live_capital_mutation_count": 0,
            "requires_human_gate_decision": True,
            "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
            "policy": "promotion_governance_human_gate_no_direct_live_capital",
        }
        return {
            "data": {
                "id": f"promotion-reviews-{quarter_window['quarter'].lower()}",
                "quarter": quarter_window["quarter"],
                "quarter_window": quarter_window,
                "items": page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                **_snapshot_meta(snapshot_at),
                "surfaces": surfaces,
                "composition_sources": [
                    "GET /bff/management/quarterly-ranking/recommendations",
                    "GET /bff/management/human-inbox",
                    "GET /api/v1/operator/governance/approval-queue",
                ],
                "redacted_evidence_count": redacted_count,
                "requires_human_gate_decision": True,
                "live_capital_mutation": False,
                "direct_live_capital_mutation": False,
                "allowed_decisions": sorted(_PROMOTION_REVIEW_DECISIONS),
                "policy": "promotion_governance_human_gate_no_direct_live_capital",
            },
        }

    # --- bff_management_promotion_review_detail ---
    @router.get("/bff/management/promotion-reviews/{review_id}")
    async def bff_management_promotion_review_detail(
        review_id: str,
        quarter: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: promotion review detail by review id."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)

        snapshot_at = utc_now()
        review, quarter_window, redacted_count, evidence_dataset_available = _promotion_review_find(
            identity,
            review_id,
            snapshot_at=snapshot_at,
            quarter=quarter,
        )
        if review is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Promotion review not found",
                f"Promotion review {review_id} does not exist",
                precondition_failed="review_id",
            )
        return {
            "data": review,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "quarter": quarter_window["quarter"],
                "surfaces": _promotion_review_surfaces(
                    snapshot_at=snapshot_at,
                    evidence_dataset_available=evidence_dataset_available,
                ),
                "redacted_evidence_count": redacted_count,
                "requires_human_gate_decision": True,
                "live_capital_mutation": False,
                "direct_live_capital_mutation": False,
                "policy": "promotion_governance_human_gate_no_direct_live_capital",
            },
        }

    # --- bff_management_promotion_review_decision ---
    @router.post("/bff/management/promotion-reviews/{review_id}/decisions", status_code=202)
    async def bff_management_promotion_review_decision(
        review_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: accept a human-gated promotion review decision without live mutation."""
        identity = _extract_identity(authorization)
        if not {"approver", "admin"}.intersection(identity.roles):
            raise _bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Promotion review decision requires 'approver' or 'admin' role",
                "Operator does not hold the required role",
                precondition_failed="role_check",
                suggestion="Escalate to a user with approver or admin role",
            )
        _reject_body_idempotency_key(payload)
        _raise_if_promotion_review_direct_mutation_requested(payload)

        raw_decision = str(payload.get("decision") or "").strip().lower()
        if raw_decision not in _PROMOTION_REVIEW_DECISIONS:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "decision is invalid",
                f"decision must be one of {sorted(_PROMOTION_REVIEW_DECISIONS)}",
                precondition_failed="decision",
            )
        rationale = _promotion_review_rationale(payload)
        if raw_decision == "reject" and not rationale:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "reject decision requires a non-empty rationale",
                "rationale must be a non-empty string when decision=reject",
                precondition_failed="rationale",
            )

        snapshot_at = utc_now()
        clean_review_id = _promotion_review_clean_id(review_id)
        exact_revision_requested = (
            clean_review_id
            != _promotion_review_revision_recommendation_id(clean_review_id)
        )
        review, _quarter_window, _redacted_count, _evidence_dataset_available = _promotion_review_find(
            identity,
            review_id,
            snapshot_at=snapshot_at,
            quarter=str(payload.get("quarter") or "").strip() or None,
            # Stable aliases remain current-only. An exact immutable revision may
            # still receive its one pending decision after a newer ranking snapshot
            # becomes current; the revision id keeps that authority isolated.
            include_historical=exact_revision_requested,
        )
        if review is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Promotion review not found",
                f"Promotion review {review_id} does not exist",
                precondition_failed="review_id",
            )
        if exact_revision_requested and str(
            review.get("decision_status") or "pending"
        ).strip().lower() != "pending":
            current_review, _, _, _ = _promotion_review_find(
                identity,
                _promotion_review_revision_recommendation_id(clean_review_id),
                snapshot_at=snapshot_at,
                quarter=str(payload.get("quarter") or "").strip() or None,
                include_historical=False,
            )
            current_revision_id = str(
                (current_review or {}).get("promotion_review_id")
                or (current_review or {}).get("review_id")
                or ""
            ).strip()
            if clean_review_id != current_revision_id:
                # Resolved historical revisions remain read-only; in particular an
                # old approval must never be reused as authority for a newer
                # revision. The current exact revision still reaches the durable
                # idempotency layer so its original decision receipt can replay.
                raise _bff_error(
                    404,
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "Promotion review not found",
                    f"Promotion review {review_id} does not exist",
                    precondition_failed="review_id",
                )
        if not bool(review.get("submitted")):
            raise _bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Promotion review has not been submitted",
                "Submit the quarterly ranking recommendation before recording a Human Gate decision.",
                precondition_failed="recommendation_submission",
                suggestion="POST the recommendation submit route and then retry the decision.",
                details_extra={
                    "recommendationId": review.get("recommendation_id"),
                    "submitHref": (review.get("links") or {}).get("submit"),
                },
            )

        command_type = (
            CommandType.HUMAN_GATE_REJECT
            if raw_decision == "reject"
            else CommandType.HUMAN_GATE_APPROVE
        )
        command_payload = _promotion_review_decision_payload(
            payload=payload,
            review=review,
            decision=raw_decision,
            rationale=rationale,
            identity=identity,
        )
        client_idempotency_key = _resolve_final_idempotency_key(
            idempotency_key,
            x_idempotency_key,
        )
        scoped_idempotency_key = _promotion_review_scoped_idempotency_key(
            client_idempotency_key,
            None,
            review["review_id"],
        )
        command_response = _sem_command_response(
            command_type=command_type,
            target_type=ObjectType.HUMAN_GATE_ITEM,
            target_id=_promotion_review_target_id(review["review_id"]),
            payload=command_payload,
            identity=identity,
            idempotency_key=scoped_idempotency_key,
        )
        return _promotion_review_decision_response(
            command_response,
            review=review,
            decision=raw_decision,
            command_payload=command_payload,
            client_idempotency_key=client_idempotency_key,
        )

    # --- bff_management_persona_league ---
    @router.get("/bff/management/persona-league")
    async def bff_management_persona_league(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: PM-12 persona-league table composed from persona-side read surfaces."""
        state = _resolve_param(state)
        archetype = _resolve_param(archetype)
        q = _resolve_param(q)
        page_token = _resolve_param(page_token)
        page_size = _resolve_param(page_size)
        authorization = _resolve_param(authorization)

        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        all_rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranking_basis, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            [_pm12_persona_league_ranking_item(row) for row in all_rows],
            surface="rolling",
            period="short_cycle",
        )
        ranking_by_persona = {
            str(item.get("persona_id") or ""): item
            for item in ranking_basis
            if str(item.get("persona_id") or "")
        }
        rows = _pm12_filter_persona_items(
            [
            {
                **row,
                **{
                    field: ranking_by_persona.get(str(row.get("persona_id") or ""), {}).get(field)
                    for field in (
                        "eligible",
                        "exclusion_reason",
                        "exclusion_reasons",
                        "exclusion_codes",
                        "evidence_coverage",
                        "evidence_refs",
                        "source_confidence",
                        "ranking_snapshot_id",
                    )
                },
            }
            for row in all_rows
            ],
            state=state,
            archetype=archetype,
            q=q,
        )
        total = len(rows)
        page_items, next_page_token = _page_slice(rows, page_token, page_size)
        summary = {
            "persona_count": total,
            "returned_count": len(page_items),
            "ranking_snapshot_id": ranking_snapshot_id,
        }
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        surfaces = {
            "persona_league": _composed_surface_status(snapshot_at=snapshot_at),
            "personas": persona_surface,
            "route_policies": _composed_surface_status(snapshot_at=snapshot_at),
            "capability_snapshots": _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at),
            "persona_bindings": _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at),
            "persona_sessions": _dataset_surface_status("sessions", snapshot_at=snapshot_at),
            "teaching_sessions": _dataset_surface_status("teaching_sessions", snapshot_at=snapshot_at),
            "persona_memory": _composed_surface_status(snapshot_at=snapshot_at),
            "persona_health": dict(persona_surface),
        }
        return {
            "data": {
                "id": "management-persona-league",
                "ranking_snapshot_id": ranking_snapshot_id,
                "items": page_items,
                "summary": summary,
            },
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": {
                "snapshot_at": snapshot_at,
                "ranking_snapshot_id": ranking_snapshot_id,
                "total": total,
                "surfaces": surfaces,
                "composition_sources": [
                    "GET /bff/personas",
                    "GET /bff/personas/{id}/route-policy",
                    "GET /bff/personas/{id}/capabilities",
                    "GET /bff/personas/{id}/activity",
                    "GET /bff/personas/{id}/evaluations",
                    "GET /bff/personas/{id}/memory",
                    "GET /bff/v5/execution/persona-health",
                ],
            },
        }

    # --- bff_management_persona_league_rankings ---
    @router.get("/bff/management/persona-league/rankings")
    async def bff_management_persona_league_rankings(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        criteria: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 persona-league ranking blocks computed from league rows."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)

        # Pre-enrich and filter the base league rows represented as ranking items
        base_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            [_pm12_persona_league_ranking_item(row) for row in rows],
            surface="rolling",
            period="short_cycle",
        )
        enriched_items = _pm12_filter_persona_items(
            base_items,
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_items = _filter_by_common_identifiers(
            enriched_items,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )

        blocks = _pm12_persona_league_rankings(
            rows,
            criteria=criteria,
            limit=limit,
            base_items=filtered_items,
        )
        for block in blocks:
            block["ranking_snapshot_id"] = ranking_snapshot_id
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        rankings_surface = _aggregate_group_surface(
            "persona_league_rankings",
            list(source_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Persona league rankings aggregate unavailable.",
            degraded_message="Persona league rankings are degraded because one or more source surfaces are degraded.",
        )
        top_item = (blocks[0].get("items") or [None])[0] if blocks else None
        summary = {
            "persona_count": len(filtered_items),
            "criteria": [block["criteria"] for block in blocks],
            "top_persona_id": (top_item or {}).get("persona_id") if isinstance(top_item, dict) else None,
            "ranking_snapshot_id": ranking_snapshot_id,
        }
        return {
            "data": {
                "id": "management-persona-league-rankings",
                "ranking_snapshot_id": ranking_snapshot_id,
                "items": blocks,
                "summary": summary,
            },
            "page_info": {"next_page_token": None, "total": len(blocks), "page_size": len(blocks)},
            "meta": {
                "snapshot_at": snapshot_at,
                "ranking_snapshot_id": ranking_snapshot_id,
                "surfaces": {
                    name: _performance_ranking_source_surface(surface, snapshot_at=snapshot_at)
                    for name, surface in {
                        "persona_league_rankings": rankings_surface,
                        **source_surfaces,
                    }.items()
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/tiers",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
            },
        }

    # --- bff_management_persona_league_movers ---
    @router.get("/bff/management/persona-league/movers")
    async def bff_management_persona_league_movers(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        direction: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: PM-12 persona-league movement list computed from league rows."""
        state = _resolve_param(state)
        archetype = _resolve_param(archetype)
        q = _resolve_param(q)
        direction = _resolve_param(direction)
        limit = _resolve_param(limit)
        authorization = _resolve_param(authorization)

        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        normalized_direction = _pm12_normalize_mover_direction(direction)
        rows = _pm12_persona_league_rows(state=state, archetype=archetype, q=q, tenant_id=caller_tenant_id)
        movers, summary = _pm12_persona_league_mover_items(
            rows,
            direction=normalized_direction,
            limit=limit,
        )
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        history_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=False,
            missing_message="Historical persona league baseline is unavailable; movers are current-snapshot entries.",
        )
        movers_surface = _aggregate_group_surface(
            "persona_league_movers",
            [*source_surfaces.values(), history_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Persona league movers aggregate unavailable.",
            degraded_message="Persona league movers are degraded because one or more source surfaces are degraded.",
        )
        data = {
            "id": "management-persona-league-movers",
            "items": movers,
            "summary": summary,
            "policy": "read_only_governance_advisory",
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": None,
                "total": summary["mover_count"],
                "page_size": len(movers),
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_league_movers": movers_surface,
                    "persona_league_history": history_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
                "policy": "read_only_governance_advisory",
                "baseline_status": "unavailable",
            },
        }

    # --- bff_management_persona_league_tiers ---
    @router.get("/bff/management/persona-league/tiers")
    async def bff_management_persona_league_tiers(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 persona-league tier definitions and current season assignment."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        rows = _pm12_persona_league_rows(state=state, archetype=archetype, q=q, tenant_id=caller_tenant_id)

        base_items = [_pm12_persona_league_ranking_item(row) for row in rows]
        filtered_items = _filter_by_common_identifiers(
            base_items,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )

        tiers, assignments, summary = _pm12_persona_league_tier_payload(
            rows,
            ranking_items=filtered_items,
        )
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        tiers_surface = _aggregate_group_surface(
            "persona_league_tiers",
            list(source_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Persona league tiers aggregate unavailable.",
            degraded_message="Persona league tiers are degraded because one or more source surfaces are degraded.",
        )
        return {
            "data": {
                "id": "management-persona-league-tiers",
                "items": tiers,
                "summary": summary,
                "related": {
                    "assignments": assignments,
                },
                "policy": "read_only_governance_advisory",
            },
            "page_info": {"next_page_token": None, "total": len(tiers), "page_size": len(tiers)},
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_league_tiers": tiers_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
                "policy": "read_only_governance_advisory",
            },
        }

    # --- bff_management_persona_league_heatmap ---
    @router.get("/bff/management/persona-league/heatmap")
    async def bff_management_persona_league_heatmap(
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        bucket: str = Query(default="day"),
        bucket_count: int = Query(default=7, ge=1, le=90),
        limit: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: persona x time-bucket league heatmap using the PM-12 composite score."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        rows = _pm12_persona_league_rows(state=state, archetype=archetype, q=q, tenant_id=caller_tenant_id)[:limit]
        bucket_key, buckets = _pm12_heatmap_buckets(
            snapshot_at,
            bucket=bucket,
            bucket_count=bucket_count,
        )
        heatmap_rows, _, summary = _pm12_persona_league_heatmap_rows(rows, buckets)
        summary = {
            **summary,
            "bucket": bucket_key,
            "returned_persona_count": len(heatmap_rows),
        }
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        heatmap_surface = _aggregate_group_surface(
            "persona_league_heatmap",
            list(source_surfaces.values()),
            snapshot_at=snapshot_at,
            unavailable_message="Persona league heatmap aggregate unavailable.",
            degraded_message="Persona league heatmap is degraded because one or more source surfaces are degraded.",
        )
        data = {
            "id": "persona-league-heatmap",
            "heatmap_id": "persona-league-heatmap",
            "bucket": bucket_key,
            "items": heatmap_rows,
            "buckets": buckets,
            "summary": summary,
            "formula_version": _PM12_LEAGUE_FORMULA_VERSION,
            "basis": "persona_x_time_bucket_composite_score",
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": None,
                "total": len(heatmap_rows),
                "page_size": len(heatmap_rows),
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "persona_league_heatmap": heatmap_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/personas",
                    "GET /bff/v5/execution/persona-health",
                ],
                "policy": "read_only_governance_advisory",
            },
        }

    # --- bff_management_quarterly_ranking_formula ---
    @router.get("/bff/management/quarterly-ranking/formula")
    async def bff_management_quarterly_ranking_formula(
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: PM-12 quarterly ranking formula weights, version, and governance trace."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = utc_now()
        formula = _pm12_quarter_formula_payload()
        evidence_refs = _pm12_quarter_formula_governance_evidence_refs()
        version_history = list(formula.get("version_history") or [])
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=bool(evidence_refs),
            missing_message="Quarterly ranking formula governance evidence is unavailable.",
        )
        weights = formula.get("weights") if isinstance(formula.get("weights"), dict) else {}
        summary = {
            "formula_id": formula["formula_id"],
            "formula_version": formula["formula_version"],
            "component_count": len(formula.get("components") or []),
            "weight_total": round(sum(_management_number(value) or 0.0 for value in weights.values()), 6),
            "evidence_ref_count": len(evidence_refs),
            "basis": formula["basis"],
            "policy": formula["policy"],
        }
        return {
            "data": formula,
            "formula": formula,
            "version_history": version_history,
            "evidence_refs": evidence_refs,
            "summary": summary,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "surfaces": {
                    "quarterly_ranking_formula": formula_surface,
                    "formula": formula_surface,
                    "governance_evidence": evidence_surface,
                },
                "composition_sources": [
                    "GET /bff/management/persona-league/rankings",
                    "GET /api/v1/knowledge/evidence",
                    _PM12_QUARTERLY_FORMULA_DOC_REF,
                ],
                "policy": formula["policy"],
                "version_policy": "formula_version_changes_require_governance_evidence",
            },
        }

    # --- bff_management_quarterly_ranking ---
    @router.get("/bff/management/quarterly-ranking")
    async def bff_management_quarterly_ranking(
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 quarterly persona ranking composed from league rows and evidence."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        quarter_window = _pm12_quarter_window(quarter, snapshot_at)
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
        (
            public_evidence_refs,
            canonical_evidence_refs,
            redacted_count,
            evidence_dataset_available,
        ) = _pm12_public_quarter_evidence_refs(
            identity,
            quarter_window,
        )
        ranked_items = _pm12_attach_ranking_evidence(
            ranked_items,
            public_evidence_refs,
            canonical_evidence_refs=canonical_evidence_refs,
        )
        ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            ranked_items,
            surface="quarterly",
            period=quarter_window["quarter"],
        )

        # Apply common filters after the immutable full-universe snapshot is built.
        enriched_items = _pm12_filter_persona_items(
            ranked_items,
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_items = _filter_by_common_identifiers(
            enriched_items,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )
        total = len(filtered_items)
        page_items, next_page_token = _page_slice(filtered_items, page_token, page_size)

        formula = _pm12_quarter_formula_payload()
        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _dataset_surface_status(
            "evidence_refs",
            snapshot_at=snapshot_at,
            has_data=evidence_dataset_available,
            missing_message="Evidence reference read surface is unavailable.",
        )
        quarterly_surface = _aggregate_group_surface(
            "quarterly_ranking",
            [*source_surfaces.values(), formula_surface, evidence_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking aggregate unavailable.",
            degraded_message="Quarterly ranking is degraded because one or more source surfaces are degraded.",
        )
        quarterly_surfaces = {
            name: _performance_ranking_source_surface(surface, snapshot_at=snapshot_at)
            for name, surface in {
                "quarterly_ranking": quarterly_surface,
                "formula": formula_surface,
                "evidence_refs": evidence_surface,
                "knowledge_evidence": evidence_surface,
                **source_surfaces,
            }.items()
        }
        top_item = filtered_items[0] if filtered_items else None
        summary = {
            "quarter": quarter_window["quarter"],
            "formula_version": formula["formula_version"],
            "persona_count": total,
            "ranking_universe_count": len(rows),
            "ranked_count": total,
            "returned_count": len(page_items),
            "top_persona_id": (top_item or {}).get("persona_id") if isinstance(top_item, dict) else None,
            "evidence_ref_count": len(public_evidence_refs),
            "redacted_evidence_count": redacted_count,
            "basis": formula["basis"],
            "ranking_snapshot_id": ranking_snapshot_id,
        }
        data = {
            "id": f"pm12-quarterly-ranking-{quarter_window['quarter'].lower()}",
            "ranking_snapshot_id": ranking_snapshot_id,
            "quarter": quarter_window["quarter"],
            "quarter_window": quarter_window,
            "formula": formula,
            "items": page_items,
            "evidence_refs": public_evidence_refs,
            "summary": summary,
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                **_snapshot_meta(snapshot_at),
                "ranking_snapshot_id": ranking_snapshot_id,
                "surfaces": quarterly_surfaces,
                "composition_sources": [
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /api/v1/knowledge/evidence",
                ],
                "policy": "read_only_governance_advisory",
                "redacted_evidence_count": redacted_count,
            },
        }

    # --- bff_management_quarterly_ranking_drilldown ---
    @router.get("/bff/management/quarterly-ranking/drilldown")
    async def bff_management_quarterly_ranking_drilldown(
        response: Response,
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona_id_snake: Optional[str] = Query(default=None, alias="persona_id"),
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        authorization: Optional[str] = Header(default=None),
        x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
        # Common filters:
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 single-persona contribution breakdown for quarterly ranking."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        correlation_id = str(x_correlation_id or "").strip() or f"pm12-drilldown-{uuid.uuid4().hex}"
        response.headers["X-Correlation-Id"] = correlation_id

        resolved_persona_id = str(persona_id or persona_id_snake or "").strip()
        if not resolved_persona_id:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "personaId is required",
                "Quarterly ranking drilldown requires personaId or persona_id.",
                precondition_failed="personaId",
                correlation_id=correlation_id,
            )

        snapshot_at = utc_now()
        quarter_window = _pm12_quarter_window(quarter, snapshot_at)
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
        (
            public_evidence_refs,
            canonical_evidence_refs,
            redacted_count,
            evidence_dataset_available,
        ) = _pm12_public_quarter_evidence_refs(
            identity,
            quarter_window,
        )
        ranked_items = _pm12_attach_ranking_evidence(
            ranked_items,
            public_evidence_refs,
            canonical_evidence_refs=canonical_evidence_refs,
        )
        ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            ranked_items,
            surface="quarterly",
            period=quarter_window["quarter"],
        )
        ranking_item = _pm12_quarterly_find_persona_item(ranked_items, resolved_persona_id)
        if ranking_item is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Quarterly ranking persona not found",
                f"Persona {resolved_persona_id} is not present in the requested quarterly ranking.",
                precondition_failed="personaId",
                correlation_id=correlation_id,
            )

        legacy_filtered_results = _pm12_filter_persona_items(
            [ranking_item],
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_results = _filter_by_common_identifiers(
            legacy_filtered_results,
            persona_id=resolved_persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )
        if not filtered_results:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Quarterly ranking persona not found matching filter criteria",
                f"Persona {resolved_persona_id} does not match the requested filter criteria.",
                precondition_failed="personaId",
                correlation_id=correlation_id,
            )

        ranking_item = filtered_results[0]

        row = _pm12_quarterly_find_persona_row(rows, resolved_persona_id)
        item_evidence_refs = list(ranking_item.get("evidence_refs") or [])
        drilldown = _pm12_quarterly_drilldown_payload(
            item=ranking_item,
            row=row,
            quarter_window=quarter_window,
            ranked_count=len(ranked_items),
            evidence_refs=item_evidence_refs,
        )

        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _dataset_surface_status(
            "evidence_refs",
            snapshot_at=snapshot_at,
            has_data=evidence_dataset_available,
            missing_message="Evidence reference read surface is unavailable.",
        )
        quarterly_surface = _aggregate_group_surface(
            "quarterly_ranking",
            [*source_surfaces.values(), formula_surface, evidence_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking aggregate unavailable.",
            degraded_message="Quarterly ranking is degraded because one or more source surfaces are degraded.",
        )
        drilldown_surface = _aggregate_group_surface(
            "quarterly_ranking_drilldown",
            [quarterly_surface, formula_surface, evidence_surface, *source_surfaces.values()],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking drilldown aggregate unavailable.",
            degraded_message="Quarterly ranking drilldown is degraded because one or more source surfaces are degraded.",
        )
        summary = dict(drilldown["summary"])
        summary["redacted_evidence_count"] = redacted_count

        return {
            "data": drilldown,
            "item": ranking_item,
            "ranking_item": ranking_item,
            "contributions": drilldown["contributions"],
            "contribution_breakdown": drilldown["contribution_breakdown"],
            "source_breakdown": drilldown["source_breakdown"],
            "formula": drilldown["formula"],
            "quarter_window": quarter_window,
            "evidence_refs": item_evidence_refs,
            "summary": summary,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "ranking_snapshot_id": ranking_snapshot_id,
                "correlation_id": correlation_id,
                "surfaces": {
                    "quarterly_ranking_drilldown": drilldown_surface,
                    "quarterly_ranking": quarterly_surface,
                    "formula": formula_surface,
                    "evidence_refs": evidence_surface,
                    "knowledge_evidence": evidence_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/quarterly-ranking",
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /api/v1/knowledge/evidence",
                ],
                "policy": "read_only_governance_advisory",
                "redacted_evidence_count": redacted_count,
            },
        }

    # --- bff_management_quarterly_ranking_recommendations ---
    @router.get("/bff/management/quarterly-ranking/recommendations")
    async def bff_management_quarterly_ranking_recommendations(
        quarter: Optional[str] = Query(default=None),
        state: Optional[str] = None,
        archetype: Optional[str] = None,
        q: str = Query(default=""),
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        period: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 quarterly governance recommendations without live mutations."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        caller_tenant_id = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
        snapshot_at = utc_now()
        quarter_window = _pm12_quarter_window(quarter, snapshot_at)
        rows = _pm12_persona_league_rows(tenant_id=caller_tenant_id)
        ranked_items = _pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)
        (
            public_evidence_refs,
            canonical_evidence_refs,
            redacted_count,
            evidence_dataset_available,
        ) = _pm12_public_quarter_evidence_refs(
            identity,
            quarter_window,
        )
        ranked_items = _pm12_attach_ranking_evidence(
            ranked_items,
            public_evidence_refs,
            canonical_evidence_refs=canonical_evidence_refs,
        )
        ranked_items, ranking_snapshot_id = _pm12_attach_ranking_snapshot(
            ranked_items,
            surface="quarterly",
            period=quarter_window["quarter"],
        )
        recommendations = _pm12_quarterly_recommendations(
            ranked_items,
            quarter_window=quarter_window,
            evidence_refs=public_evidence_refs,
        )

        enriched_recs = _pm12_filter_persona_items(
            recommendations,
            state=state,
            archetype=archetype,
            q=q,
        )
        filtered_recs = _filter_by_common_identifiers(
            enriched_recs,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, period=period, as_of=as_of
        )
        total = len(filtered_recs)
        page_items, next_page_token = _page_slice(filtered_recs, page_token, page_size)

        formula = _pm12_quarter_formula_payload()
        action_counts = {
            action_id: len([item for item in filtered_recs if item.get("action_id") == action_id])
            for action_id in _PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER
        }
        filtered_persona_ids = {
            str(item.get("persona_id") or "")
            for item in filtered_recs
            if str(item.get("persona_id") or "")
        }
        top_item = next(
            (
                item
                for item in ranked_items
                if str(item.get("persona_id") or "") in filtered_persona_ids
            ),
            None,
        )
        summary = {
            "quarter": quarter_window["quarter"],
            "formula_version": formula["formula_version"],
            "persona_count": len(rows),
            "ranked_count": len(ranked_items),
            "recommendation_count": total,
            "returned_count": len(page_items),
            "top_persona_id": (top_item or {}).get("persona_id") if isinstance(top_item, dict) else None,
            "human_gate_decision_count": total,
            "live_capital_mutation_count": 0,
            "evidence_ref_count": len(public_evidence_refs),
            "redacted_evidence_count": redacted_count,
            "by_action": action_counts,
            "allowed_actions": list(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER),
            "basis": formula["basis"],
            "policy": "read_only_governance_advisory",
            "ranking_snapshot_id": ranking_snapshot_id,
        }

        source_surfaces = _pm12_persona_league_source_surfaces(snapshot_at)
        formula_surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
        evidence_surface = _dataset_surface_status(
            "evidence_refs",
            snapshot_at=snapshot_at,
            has_data=evidence_dataset_available,
            missing_message="Evidence reference read surface is unavailable.",
        )
        approval_queue_surface = _dataset_surface_status("approval_queue_items", snapshot_at=snapshot_at)
        human_gate_surface = _dataset_surface_status("approval_decisions", snapshot_at=snapshot_at)
        human_inbox_surface = _composed_surface_status(
            snapshot_at=snapshot_at,
            available=(
                approval_queue_surface.get("status") != "unavailable"
                or human_gate_surface.get("status") != "unavailable"
            ),
            missing_message="Human Inbox and HumanGateDecision read surfaces are unavailable.",
        )
        quarterly_surface = _aggregate_group_surface(
            "quarterly_ranking",
            [*source_surfaces.values(), formula_surface, evidence_surface],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking aggregate unavailable.",
            degraded_message="Quarterly ranking is degraded because one or more source surfaces are degraded.",
        )
        recommendations_surface = _aggregate_group_surface(
            "quarterly_ranking_recommendations",
            [
                quarterly_surface,
                formula_surface,
                evidence_surface,
                approval_queue_surface,
                human_gate_surface,
                human_inbox_surface,
            ],
            snapshot_at=snapshot_at,
            unavailable_message="Quarterly ranking recommendations aggregate unavailable.",
            degraded_message="Quarterly ranking recommendations are degraded because one or more governance source surfaces are degraded.",
        )
        governance_destinations = ["human_inbox", "governance_queue", "human_gate_decision"]
        data = {
            "id": f"pm12-quarterly-ranking-recommendations-{quarter_window['quarter'].lower()}",
            "ranking_snapshot_id": ranking_snapshot_id,
            "quarter": quarter_window["quarter"],
            "quarter_window": quarter_window,
            "formula": formula,
            "items": page_items,
            "evidence_refs": public_evidence_refs,
            "summary": summary,
            "policy": "read_only_governance_advisory",
            "governance_destinations": governance_destinations,
            "allowed_actions": list(_PM12_QUARTERLY_RECOMMENDATION_ACTION_ORDER),
        }
        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": {
                **_snapshot_meta(snapshot_at),
                "ranking_snapshot_id": ranking_snapshot_id,
                "surfaces": {
                    "quarterly_ranking_recommendations": recommendations_surface,
                    "quarterly_ranking": quarterly_surface,
                    "formula": formula_surface,
                    "evidence_refs": evidence_surface,
                    "knowledge_evidence": evidence_surface,
                    "human_inbox": human_inbox_surface,
                    "governance_queue": approval_queue_surface,
                    "human_gate_decision": human_gate_surface,
                    **source_surfaces,
                },
                "composition_sources": [
                    "GET /bff/management/quarterly-ranking",
                    "GET /bff/management/persona-league",
                    "GET /bff/management/persona-league/rankings",
                    "GET /bff/management/persona-league/tiers",
                    "GET /api/v1/knowledge/evidence",
                    "GET /bff/management/human-inbox",
                    "GET /api/v1/operator/governance/approval-queue",
                ],
                "policy": "read_only_governance_advisory",
                "governance_destinations": governance_destinations,
                "redacted_evidence_count": redacted_count,
                "live_capital_mutation": False,
            },
        }

    # --- bff_persona_action ---
    @router.post("/bff/personas/{persona_id}/actions/{action_id}", status_code=202)
    async def bff_persona_action(
        persona_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: persona action — routes through command/precondition machinery.

        AdvanceLifecycle is registered (P0-1) and returns 202. All other action_ids
        still return 410 until individually registered.
        """
        # P0-1: AdvanceLifecycle registered and active
        if action_id == "AdvanceLifecycle":
            identity = _extract_identity(authorization)
            _require_operator_role(identity)
            _reject_body_idempotency_key(payload)
            resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)

            target_state = str(payload.get("target_state") or "").strip()
            if target_state not in _ADVANCE_LIFECYCLE_VALID_TARGETS:
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "target_state is required and must be paper_owner, live_owner, or retired",
                    f"Got {target_state!r}; allowed: {sorted(_ADVANCE_LIFECYCLE_VALID_TARGETS)}",
                    precondition_failed="target_state",
                )

            if target_state == "live_owner" and not _ADVANCE_LIFECYCLE_LIVE_ROLES.intersection(identity.roles):
                raise _bff_error(
                    403,
                    ErrorCode.FORBIDDEN,
                    "Advancing to live_owner requires approver or admin role",
                    "Operator does not hold live_owner_approver role",
                    precondition_failed="role_check",
                )

            confirm_token = str(payload.get("confirm_token") or "").strip()
            if not confirm_token:
                raise _bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "confirm_token is required for AdvanceLifecycle",
                    "Provide a valid confirm_token in the request body",
                    precondition_failed="confirm_token",
                )

            _ensure_persona_exists(persona_id)

            enriched_payload = {**payload, "persona_id": persona_id}
            return _strategy_persona_action_command(
                entity_type=ObjectType.PERSONA,
                entity_id=persona_id,
                action_id=action_id,
                resolved_key=resolved_key,
                identity=identity,
                payload=enriched_payload,
                command_type=CommandType.ADVANCE_LIFECYCLE,
            )

        return _deprecated_bff_path_response(
            route="/bff/personas/{persona_id}/actions/{action_id}",
            replacement="/bff/actions/persona/{persona_id}/{action_id}",
        )
        identity = _extract_identity(authorization)  # noqa: F841 — unreachable; preserved for future de-deprecation
        _require_read_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        _ensure_persona_exists(persona_id)
        return _strategy_persona_action_command(
            entity_type=ObjectType.PERSONA,
            entity_id=persona_id,
            action_id=action_id,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.PERSONA_ACTION,
        )

    # --- bff_persona_test_prompt ---
    @router.post("/bff/personas/{persona_id}/test-prompt", status_code=202)
    async def bff_persona_test_prompt(
        persona_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: send a test prompt to a persona; returns a stub trial handle."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _reject_body_idempotency_key(payload)
        resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        _ensure_persona_exists(persona_id)
        request_hash = _stable_json_hash(
            {"route": "POST /bff/personas/{id}/test-prompt", "id": persona_id, "payload": payload}
        )
        cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise _bff_error(
                422, ErrorCode.VALIDATION_FAILED, "prompt is required",
                "Persona test-prompt requires a non-empty prompt",
                precondition_failed="prompt",
            )
        snapshot_at = utc_now()
        trial_id = f"prompt-{persona_id}-{uuid.uuid4().hex[:8]}"
        result = {
            "data": {
                "trial_id": trial_id,
                "persona_id": persona_id,
                "status": "queued",
                "queued_at": snapshot_at,
                "prompt": prompt,
                "params": payload.get("params") or {},
                "requested_by": identity.operator_id,
            },
            "meta": {"snapshot_at": snapshot_at},
        }
        _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    # --- bff_persona_league ---
    @router.get("/bff/persona-league")
    async def bff_persona_league(
        market_scope: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _persona_league_payload(
            snapshot_at=utc_now(),
            market_scope=market_scope,
            status=status,
            page_token=page_token,
            page_size=page_size,
        )

    # --- bff_persona_league_detail ---
    @router.get("/bff/persona-league/{persona_id}")
    @router.get("/bff/management/persona-league/{persona_id}")
    async def bff_persona_league_detail(
        persona_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = utc_now()
        entry = read_store.get_persona_league_entry(persona_id)
        if not entry:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona league entry not found",
                f"Persona league entry {persona_id} does not exist",
            )
        return {
            "data": entry,
            "meta": _read_surface_meta(
                "persona_league",
                "persona_league_detail",
                snapshot_at=snapshot_at,
            ),
        }

    # --- bff_management_persona_fleet ---
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


# Canonical default router instance
router = create_personas_router()
