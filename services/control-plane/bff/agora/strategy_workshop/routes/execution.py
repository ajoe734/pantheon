"""Downstream-execution Workshop routes: research runs, consultations, conclude.

Split out of the former single-file strategy_workshop/router.py factory
(ACG-06-004). Route bodies below are unchanged from the original
implementation; only the surrounding closure scaffolding (this
build_execution_router wrapper binding the shared admission context) is new.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Response

from .._common import _StrategyVersionProjectionError
from ..events import _ws_publish
from ..operations import CanonicalOperationError
from ..store import WorkshopVersionProjectionConflict
from ..schemas import (
    WorkshopConcludeRequest,
    WorkshopConsultationRequest,
    WorkshopResearchRunRequest,
)


def build_execution_router(
    *,
    store: Any,
    canonical: Any,
    utc_now: Callable[[], str],
    bff_error: Callable[..., HTTPException],
    ctx: Any,
) -> APIRouter:
    router = APIRouter(tags=["agora-workshop"])
    _scope = ctx.scope
    _scoped_session = ctx.scoped_session
    _admit_command = ctx.admit_command
    _complete_or_raise = ctx.complete_or_raise
    _require_approval = ctx.require_approval
    _require_command_headers = ctx.require_command_headers
    _read_strategy_version = ctx.read_strategy_version
    _canonical_error = ctx.canonical_error
    _fail_domain_command = ctx.fail_domain_command
    _command_response = ctx.command_response
    _resume_context = ctx.resume_context
    _resolve_resumed_compensation = ctx.resolve_resumed_compensation
    _source_resolution = ctx.source_resolution
    _require_replayable_receipt = ctx.require_replayable_receipt

    @router.post("/bff/agora/workshops/{workshop_id}/research-runs", status_code=202)
    def create_workshop_research_run(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopResearchRunRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from services.control_plane.bff.models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WorkshopResearchRunRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        version_id = body.strategy_version_ref or session.get("selected_version_id")
        link = store.get_version_link(workshop_id, str(version_id or ""))
        if link is None:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "A selected workshop version is required for research",
                "WORKSHOP_VERSION_REQUIRED",
                precondition_failed="strategy_version_ref",
            )
        approval = _require_approval(
            approval_decision_id=body.approval_decision_id,
            workshop_id=workshop_id,
            version_id=str(version_id),
            session=session,
            scope=scope,
        )
        safe_modes = {"stub", "handoff_only", "manual"}
        if (
            body.adapter not in safe_modes
            or body.requested_mode not in safe_modes
            or body.dispatch_mode not in safe_modes
        ):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Workshop research is restricted to non-live handoff modes",
                "RESEARCH_ENVIRONMENT_FORBIDDEN",
                precondition_failed="research_mode",
            )
        serialized_parameters = json.dumps(body.parameters, sort_keys=True).lower()
        if any(token in serialized_parameters for token in ('"live"', '"canary"', '"production"')):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Workshop research cannot target canary, live, or production",
                "RESEARCH_ENVIRONMENT_FORBIDDEN",
                precondition_failed="parameters.environment",
            )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="dispatch_research",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="research_orchestrator",
                response=response,
            )
        registry_id = str(link["strategy_spec_registry_id"])
        resume = _resume_context(
            workshop_id=workshop_id,
            scope=scope,
            operation="dispatch_research",
            request_hash=request_hash,
            claimed_by=command_key,
        )
        # Reusing the recorded downstream idempotency digest keeps the
        # downstream task/run idempotency keys stable across a new-key retry,
        # so an unacknowledged prior create is deduplicated downstream.
        digest = (resume or {}).get("digest") or hashlib.sha256(
            f"{workshop_id}:{command_key}".encode()
        ).hexdigest()[:20]
        dispatch_kwargs: Dict[str, Any] = {}
        if resume is not None and resume["partial_effects"]:
            dispatch_kwargs["resume"] = {
                "research_task_id": resume["partial_effects"].get("research_task_id"),
                "research_run_id": resume["partial_effects"].get("research_run_id"),
            }
        try:
            downstream = canonical.dispatch_research_run(
                **dispatch_kwargs,
                task_payload={
                    "title": f"Strategy Workshop research {workshop_id}",
                    "objective": body.research_context,
                    "source_refs": [
                        {"type": "strategy_workshop", "id": workshop_id},
                        {"type": "workshop_version", "id": str(version_id)},
                        {"type": "strategy_spec_registry", "id": registry_id},
                        {"type": "approval_decision", "id": body.approval_decision_id},
                    ],
                    "constraints": {
                        "tenant_id": scope.tenant_id,
                        "environment_ceiling": "research",
                        "no_live_capital": True,
                    },
                    "actor_id": scope.user_id,
                    "idempotency_key": f"workshop-{digest}-task",
                    "created_at": utc_now(),
                },
                run_payload={
                    "adapter": body.adapter,
                    "requested_mode": body.requested_mode,
                    "dispatch_mode": body.dispatch_mode,
                    "input_refs": [
                        {"type": "strategy_spec", "id": registry_id},
                        {"type": "workshop_version", "id": str(version_id)},
                    ],
                    "parameters": {
                        **body.parameters,
                        "workshop_id": workshop_id,
                        "tenant_id": scope.tenant_id,
                        "approval_decision_id": body.approval_decision_id,
                    },
                    "actor_id": scope.user_id,
                    "idempotency_key": f"workshop-{digest}-run",
                    "requested_at": utc_now(),
                },
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="dispatch_research",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
                resume_digest=digest,
                resume=resume,
            )
        run = dict(downstream["run"])
        run_id = str(run.get("run_id") or run.get("id") or "")
        downstream_status = str(run.get("status") or "").lower()
        if downstream_status == "rejected":
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="dispatch_research",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.PRECONDITION_FAILED,
                message="Research orchestrator rejected the run",
                reason="RESEARCH_RUN_REJECTED",
                precondition_failed="research_dispatch",
            )
        resource = {
            **downstream,
            "downstream_status": downstream_status,
            "downstream_terminal": downstream_status
            in {"completed", "succeeded", "failed", "cancelled", "timed_out"},
        }
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="dispatch_research",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "research_task_id": downstream["task"].get("task_id"),
                "research_run_id": run_id,
                "workshop_version_id": str(version_id),
                "approval_decision_id": approval["approval_decision_id"],
                **(
                    {
                        "resumed_from_command_id": resume["receipt"].get("command_id"),
                        "resumed_from_idempotency_key": resume["receipt"].get(
                            "idempotency_key"
                        ),
                    }
                    if resume is not None
                    else {}
                ),
            },
            event={
                "event_id": f"wsevt-research-{digest}",
                "actor_type": "operator",
                "event_type": "research_dispatched",
                "redacted_summary": "Research run admitted by canonical orchestrator",
                "payload_refs_json": {
                    "research_run_id": run_id,
                    "workshop_version_id": str(version_id),
                },
                "trace_id": request_id,
            },
            downstream_digest=digest,
            resume=resume,
        )
        _resolve_resumed_compensation(
            workshop_id=workshop_id,
            scope=scope,
            operation="dispatch_research",
            resume=resume,
            resolved_by_idempotency_key=command_key,
        )
        _ws_publish(
            workshop_id,
            "research.run.progress",
            {"run_id": run_id, "status": downstream_status},
            utc_now_fn=utc_now,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or session,
            scope=scope,
            canonical_authority="research_orchestrator",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/consultations", status_code=201)
    def create_workshop_consultation(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopConsultationRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from services.control_plane.bff.models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WorkshopConsultationRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        if body.consultation_type not in {"committee", "advisory"}:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "consultation_type must be committee or advisory",
                "CONSULTATION_TYPE_INVALID",
            )
        version_id = str(session.get("selected_version_id") or "")
        link = store.get_version_link(workshop_id, version_id)
        if link is None:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "A selected workshop version is required for consultation",
                "WORKSHOP_VERSION_REQUIRED",
                precondition_failed="selected_version_id",
            )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="open_consultation",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="consultation_service",
                response=response,
            )
        resume = _resume_context(
            workshop_id=workshop_id,
            scope=scope,
            operation="open_consultation",
            request_hash=request_hash,
            claimed_by=command_key,
        )
        # Reusing the recorded digest re-derives the same deterministic
        # consultation request id, so a new-key retry adopts the request a
        # failed attempt may already have created downstream.
        digest = (resume or {}).get("digest") or hashlib.sha256(
            f"{workshop_id}:{command_key}".encode()
        ).hexdigest()[:20]
        consultation_id = (
            str((resume or {}).get("partial_effects", {}).get("consultation_request_id") or "")
            or f"cr-ws-{digest}"
        )
        open_kwargs: Dict[str, Any] = {}
        if resume is not None:
            open_kwargs["resume"] = True
        try:
            consultation = canonical.open_consultation(
                **open_kwargs,
                request_id=consultation_id,
                payload={
                    "request_type": "strategy_review",
                    "requested_by": {
                        "actor_type": "operator",
                        "actor_id": scope.user_id,
                    },
                    "target_type": "strategy_workshop",
                    "target_id": workshop_id,
                    "task": body.subject,
                    "consultation_type": body.consultation_type,
                    "context_refs": [
                        {"type": "workshop_version", "id": version_id},
                        {
                            "type": "strategy_spec_registry",
                            "id": link["strategy_spec_registry_id"],
                        },
                        *[
                            {"type": "external_context", "id": ref}
                            for ref in body.context_refs
                        ],
                    ],
                    "priority": "normal",
                    "metadata": {
                        "tenant_id": scope.tenant_id,
                        "owner_user_id": scope.user_id,
                        "workshop_id": workshop_id,
                        "workshop_version_id": version_id,
                        "idempotency_key": command_key,
                    },
                    "trace_id": request_id,
                },
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="open_consultation",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
                resume_digest=digest,
                resume=resume,
            )
        downstream_status = str(consultation.get("status") or "").lower()
        if downstream_status == "cancelled":
            # A cancelled consultation is not a successful open.  Seal the
            # adopted lineage in the same transaction as this failure so no
            # later retry re-adopts the dead request; a new-key retry then
            # opens a fresh consultation under its own digest.
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="open_consultation",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Consultation request is cancelled downstream",
                reason="CONSULTATION_REQUEST_CANCELLED",
                precondition_failed="consultation_request",
                resolve_source=(
                    _source_resolution(
                        resume,
                        resolution="cancelled",
                        resolved_by=command_key,
                        extra={"consultation_request_id": consultation_id},
                    )
                    if resume is not None
                    else None
                ),
            )
        resource = {
            "consultation": consultation,
            "downstream_status": downstream_status,
            "downstream_terminal": downstream_status
            in {"published", "cancelled", "failed"},
        }
        try:
            receipt = _complete_or_raise(
                workshop_id=workshop_id,
                operation="open_consultation",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                result=resource,
                canonical_refs={
                    "consultation_request_id": consultation_id,
                    "workshop_version_id": version_id,
                    **(
                        {
                            "resumed_from_command_id": resume["receipt"].get(
                                "command_id"
                            ),
                            "resumed_from_idempotency_key": resume["receipt"].get(
                                "idempotency_key"
                            ),
                        }
                        if resume is not None
                        else {}
                    ),
                },
                event={
                    "event_id": f"wsevt-consult-{digest}",
                    "actor_type": "operator",
                    "event_type": "consultation_started",
                    "redacted_summary": "Consultation request opened",
                    "payload_refs_json": {
                        "consultation_request_id": consultation_id,
                        "workshop_version_id": version_id,
                    },
                    "trace_id": request_id,
                },
                downstream_digest=digest,
                resume=resume,
            )
        except HTTPException:
            # Compensation-or-resume with exclusive effect ownership: only a
            # consultation this attempt itself created (resume is None) may
            # be cancelled; on success the failed receipt's compensation is
            # sealed as cancelled so a retry opens a fresh consultation.  An
            # adopted request is shared lineage — cancelling it could destroy
            # a resource the prior receipt chain still references — so the
            # failed receipt keeps its resumable lineage instead (the adopted
            # source was resolved as superseded in the same failure write).
            # If cancellation itself fails, the receipt likewise keeps its
            # resumable lineage and a new-key retry adopts the recorded
            # request instead of opening a duplicate.
            if resume is None:
                cancelled = False
                try:
                    canonical.cancel_consultation(
                        consultation_id,
                        actor_id=scope.user_id,
                        trace_id=request_id,
                    )
                    cancelled = True
                except Exception:
                    pass
                if cancelled and hasattr(store, "resolve_command_compensation"):
                    try:
                        store.resolve_command_compensation(
                            workshop_id=workshop_id,
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                            operation="open_consultation",
                            idempotency_key=command_key,
                            resolution={
                                "resolved_at": utc_now(),
                                "resolution": "cancelled",
                                "consultation_request_id": consultation_id,
                            },
                        )
                    except Exception:
                        pass
            raise
        _resolve_resumed_compensation(
            workshop_id=workshop_id,
            scope=scope,
            operation="open_consultation",
            resume=resume,
            resolved_by_idempotency_key=command_key,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or session,
            scope=scope,
            canonical_authority="consultation_service",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/conclude")
    def conclude_workshop(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopConcludeRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from services.control_plane.bff.models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WorkshopConcludeRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        if session.get("status") != "in_review":
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Workshop must be in_review before conclude",
                "WORKSHOP_NOT_IN_REVIEW",
                precondition_failed="workshop_status",
            )
        version_id = body.final_version_id or session.get("selected_version_id")
        link = store.get_version_link(workshop_id, str(version_id or ""))
        if link is None:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "A valid final workshop version is required",
                "WORKSHOP_VERSION_REQUIRED",
                precondition_failed="final_version_id",
            )
        selected_version_id = str(session.get("selected_version_id") or "")
        if str(version_id) != selected_version_id:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Conclusion requires the currently selected workshop version",
                "WORKSHOP_FINAL_VERSION_NOT_SELECTED",
                precondition_failed="final_version_id",
                details_extra={"selected_version_id": selected_version_id},
            )
        two_person_proof = _require_approval(
            approval_decision_id=body.approval_decision_id,
            workshop_id=workshop_id,
            version_id=str(version_id),
            session=session,
            scope=scope,
        )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="conclude",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="workshop_store+strategy_registry+approval_decision_store",
                response=response,
            )
        registry_id = str(link["strategy_spec_registry_id"])
        try:
            # The terminal transition demands the same authoritative projection
            # proof as version selection: Registry readback must expose a
            # complete StrategySpec document in the caller's scope whose digest
            # still matches the immutable durable link.
            registry_readback, document_sha256, final_strategy_id = (
                _read_strategy_version(
                    registry_id=registry_id,
                    scope=scope,
                    expected_strategy_id=str(link.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
            )
            link = store.ensure_version_link_digest(
                workshop_id=workshop_id,
                workshop_version_id=str(version_id),
                strategy_id=final_strategy_id,
                document_sha256=document_sha256,
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="conclude",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="conclude",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="Final StrategySpec version projection is incomplete or inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        except WorkshopVersionProjectionConflict:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="conclude",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Final workshop version digest no longer matches its immutable link",
                reason="WORKSHOP_VERSION_PROJECTION_CONFLICT",
                precondition_failed="workshop_version_projection",
            )
        concluded_at = utc_now()
        concluded_session = {
            **session,
            "selected_version_id": str(version_id),
            "active_workshop_version_id": str(version_id),
            "active_strategy_spec_registry_id": registry_id,
            "final_workshop_version_id": str(version_id),
            "final_strategy_spec_registry_id": registry_id,
            "status": "concluded",
            "concluded_at": concluded_at,
            "lock_version": receipt.get("admitted_lock_version"),
        }
        no_direct_action = {
            "deployment_triggered": False,
            "order_submitted": False,
            "live_capital_changed": False,
        }
        resource = {
            "workshop": concluded_session,
            "version": {**link, "strategy_spec": registry_readback},
            "approval_decision_id": body.approval_decision_id,
            "two_person_proof": two_person_proof,
            "no_direct_action_proof": no_direct_action,
        }
        digest = hashlib.sha256(f"{workshop_id}:{command_key}".encode()).hexdigest()[:20]
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="conclude",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "workshop_version_id": str(version_id),
                "strategy_spec_registry_id": registry_id,
                "approval_decision_id": body.approval_decision_id,
            },
            session_updates={
                "selected_version_id": str(version_id),
                "active_workshop_version_id": str(version_id),
                "active_strategy_spec_registry_id": registry_id,
                "final_workshop_version_id": str(version_id),
                "final_strategy_spec_registry_id": registry_id,
                "status": "concluded",
                "concluded_at": concluded_at,
            },
            event={
                "event_id": f"wsevt-conclude-{digest}",
                "actor_type": "operator",
                "event_type": "concluded",
                "redacted_summary": "Workshop concluded with approved final version",
                "payload_refs_json": {
                    "final_workshop_version_id": str(version_id),
                    "final_strategy_spec_registry_id": registry_id,
                    "approval_decision_id": body.approval_decision_id,
                },
                "trace_id": request_id,
            },
        )
        _ws_publish(
            workshop_id,
            "workshop.concluded",
            {
                "final_workshop_version_id": str(version_id),
                "final_strategy_spec_registry_id": registry_id,
            },
            utc_now_fn=utc_now,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or concluded_session,
            scope=scope,
            canonical_authority="workshop_store+strategy_registry+approval_decision_store",
            response=response,
        )

    return router
