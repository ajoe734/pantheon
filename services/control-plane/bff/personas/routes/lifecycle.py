"""Persona lifecycle, sessions, actions, and strategy discovery routes."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from urllib import error as urllib_error
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query

from services.control_plane.bff.models import CommandType, ErrorCode, ObjectType
from ..service import (
    _ADVANCE_LIFECYCLE_LIVE_ROLES,
    _ADVANCE_LIFECYCLE_VALID_TARGETS,
    _PPL_ALLOC_009_ELIGIBILITY_BENCHMARK_VERSION,
    _PPL_ALLOC_009_ELIGIBILITY_IDEMPOTENCY_KEY,
    _PPL_ALLOC_009_ELIGIBILITY_RUN_KEY,
    _PPL_ALLOC_009_ELIGIBILITY_TASK_ID,
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY,
    _bff_me_tenant_payload,
    _deprecated_bff_path_response,
    _ensure_persona_exists,
    _persona_strategy_match_action_response,
    _persona_strategy_matches_response,
    _pm12_persona_league_ranking_item,
    _pm12_persona_league_rows,
    _pm12_recommendation_action_ids,
    _post_json,
    _ppl_alloc_009_build_telemetry_event,
    _ppl_alloc_009_dev_proof_enabled,
    _ppl_alloc_009_eligibility_error,
    _ppl_alloc_009_eligibility_observation_store,
    _ppl_alloc_009_paper_eligibility_context,
    _ppl_alloc_009_paper_environment_guard,
    _ppl_alloc_009_telemetry_url,
    _ppl_alloc_009_wait_for_telemetry_readback,
    _stable_json_hash,
    _strategy_discovery_page_size,
    _strategy_persona_action_command,
    _strategy_persona_idempotency_check,
)
from .common import PersonaRouteContext, make_context_dependency

log = logging.getLogger(__name__)


def build_lifecycle_router(ctx: PersonaRouteContext) -> APIRouter:
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
    _submit_persona_action = ctx.submit_persona_action

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
        caller_tenant = str(_bff_me_tenant_payload(identity, requested_tenant=None)["id"])
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

    return router
