"""Strict v1.9 candidate lifecycle, validation and approval-link routes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Response

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from .models import (
    CandidateDecisionCommand,
    CandidateFromMeasureCommand,
    CandidateValidationCommand,
    FormalApprovalLinkCommand,
)
from .service import CandidateDecisionService
from .store import CandidateDecisionConflict


def create_candidate_decision_router(
    *,
    service: CandidateDecisionService,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    router = APIRouter(tags=["agora-candidate-decisions"])

    def scope(auth: Optional[str], tenant: Optional[str], *, write: bool = False) -> Any:
        identity = extract_identity(auth)
        require_read_role(identity)
        if write:
            require_write_role(identity)
        try:
            resolved = resolve_agora_user_scope(
                identity, utc_now=utc_now, requested_tenant_id=tenant
            )
        except AgoraScopeResolutionError as exc:
            from services.control_plane.bff.models import ErrorCode
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(exc.status_code, code, exc.message, exc.reason) from exc
        if "agora.workshop.v1" not in resolved.granted_capabilities:
            from services.control_plane.bff.models import ErrorCode
            raise bff_error(
                403, ErrorCode.FORBIDDEN,
                "Agora candidate decision capability denied", "capability_missing",
            )
        return resolved

    def require_idempotency(value: Optional[str]) -> str:
        if not value or not value.strip():
            raise HTTPException(400, detail="Idempotency-Key header is required")
        return value.strip()

    def require_etag(value: Optional[str]) -> str:
        if not value or not value.strip():
            raise HTTPException(428, detail="If-Match header is required")
        return value.strip()

    def envelope(readback: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": readback,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.persona.candidate.v1.9",
                "spec_version": "1.9",
            },
        }

    def readback(proposal_id: str, resolved: Any, response: Response) -> dict[str, Any]:
        try:
            value = service.readback(
                proposal_id=proposal_id,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        response.headers["ETag"] = value["etag"]
        return envelope(value)

    @router.post(
        "/bff/agora/interactions/{interaction_id}/recommended-measures/{measure_id}/candidates",
        status_code=201,
    )
    def create_candidate(
        interaction_id: str,
        measure_id: str,
        body: CandidateFromMeasureCommand,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        if body.interaction_id != interaction_id or body.measure_id != measure_id:
            raise HTTPException(409, detail="candidate path and persisted measure binding mismatch")
        try:
            result = service.create_from_measure(
                command=body,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
                proposer_id=resolved.operator_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                idempotency_key=require_idempotency(idempotency_key),
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if result.replayed:
            response.status_code = 200
        return readback(result.resource["proposal_id"], resolved, response)

    @router.get("/bff/agora/proposals/{proposal_id}/candidate")
    @router.get("/bff/agora/proposals/{proposal_id}/candidate-decisions")
    def get_candidate(
        proposal_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        return readback(proposal_id, scope(authorization, x_tenant_id), response)

    @router.post("/bff/agora/proposals/{proposal_id}/candidate-decisions")
    def decide_candidate(
        proposal_id: str,
        body: CandidateDecisionCommand,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        try:
            service.decide(
                proposal_id=proposal_id,
                command=body,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
                actor_id=resolved.operator_id,
                expected_etag=require_etag(if_match),
                idempotency_key=require_idempotency(idempotency_key),
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return readback(proposal_id, resolved, response)

    @router.post("/bff/agora/proposals/{proposal_id}/validations")
    def validate_candidate(
        proposal_id: str,
        body: CandidateValidationCommand,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        try:
            service.run_authoritative_validation(
                proposal_id=proposal_id,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
                expected_revision=body.expected_revision,
                expected_proposal_digest=body.expected_proposal_digest,
                expected_etag=require_etag(if_match),
                idempotency_key=require_idempotency(idempotency_key),
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return readback(proposal_id, resolved, response)

    @router.get("/bff/agora/proposals/{proposal_id}/validations/{validation_receipt_id}")
    def get_validation(
        proposal_id: str,
        validation_receipt_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        detail = service.readback(
            proposal_id=proposal_id,
            tenant_id=resolved.tenant_id,
            owner_user_id=resolved.user_id,
        )
        receipt = next((
            item for item in detail["validation_receipts"]
            if item.get("validation_receipt_id") == validation_receipt_id
        ), None)
        if receipt is None:
            raise HTTPException(404, detail="validation receipt not found")
        return envelope(receipt)

    @router.get("/bff/agora/proposals/{proposal_id}/review-readiness")
    def review_readiness(
        proposal_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        detail = readback(proposal_id, scope(authorization, x_tenant_id), response)
        return {
            **detail,
            "data": {
                "proposal_id": proposal_id,
                "readiness": detail["data"]["readiness"],
                "etag": detail["data"]["etag"],
                "execution_authority": "none",
            },
        }

    @router.post(
        "/bff/agora/proposals/{proposal_id}/formal-approvals/{approval_decision_id}:link"
    )
    def link_formal_approval(
        proposal_id: str,
        approval_decision_id: str,
        body: FormalApprovalLinkCommand,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id, write=True)
        try:
            service.link_formal_approval(
                proposal_id=proposal_id,
                approval_decision_id=approval_decision_id,
                tenant_id=resolved.tenant_id,
                owner_user_id=resolved.user_id,
                expected_revision=body.expected_revision,
                expected_proposal_digest=body.expected_proposal_digest,
                expected_etag=require_etag(if_match),
                idempotency_key=require_idempotency(idempotency_key),
            )
        except CandidateDecisionConflict as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return readback(proposal_id, resolved, response)

    @router.get(
        "/bff/agora/proposals/{proposal_id}/formal-approvals/{approval_decision_id}"
    )
    def get_formal_approval(
        proposal_id: str,
        approval_decision_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        resolved = scope(authorization, x_tenant_id)
        detail = service.readback(
            proposal_id=proposal_id,
            tenant_id=resolved.tenant_id,
            owner_user_id=resolved.user_id,
        )
        receipt = next((
            item for item in detail["formal_approval_receipts"]
            if item.get("approval_decision_id") == approval_decision_id
        ), None)
        if receipt is None:
            raise HTTPException(404, detail="formal approval receipt not found")
        return envelope(receipt)

    return router
