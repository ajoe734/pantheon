"""Thin, authenticated human-evidence routes. No runtime mutation authority."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from services.agora.service import AgoraEvidenceWriteService
from services.agora.store import AgoraEvidenceStore
from services.agora.write_authority import AgoraWriteForbiddenError
from ..dataset_extraction.extractor import IdempotencyConflictError, PrivacyConsentError
from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

log = logging.getLogger(__name__)


class HumanEvidenceRequest(BaseModel):
    model_config = {"extra": "forbid"}
    evidence_id: str = Field(min_length=1, max_length=200)
    persona_id: str = Field(min_length=1, max_length=200)
    subject_ref: str = Field(min_length=1, max_length=1000)
    content: Dict[str, Any] = Field(min_length=1)
    source_refs: List[str] = Field(default_factory=list, max_length=100)
    captured_at: AwareDatetime
    consent_granted: bool
    learning_eligible: bool = True
    retention_days: int = Field(default=30, ge=1, le=365)
    environment_ceiling: Literal["paper"] = "paper"

    @field_validator("evidence_id", "persona_id", "subject_ref")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reference must not be blank")
        return value.strip()

    @field_validator("content")
    @classmethod
    def reserved_metadata(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if "agora_human_evidence" in value:
            raise ValueError("Agora evidence metadata is owner assigned")
        return value


def create_human_evidence_router(
    *, dataset_store: Any, extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None], require_write_role: Callable[..., None],
    utc_now: Callable[[], str], allow_ephemeral: bool = False,
) -> APIRouter:
    router = APIRouter(tags=["agora-human-evidence"])
    owner = AgoraEvidenceWriteService(AgoraEvidenceStore(dataset_store))

    def scope_for(auth: Any, tenant: Any, *, write: bool = False) -> Any:
        identity = extract_identity(auth)
        (require_write_role if write else require_read_role)(identity)
        try:
            return resolve_agora_user_scope(identity, utc_now=utc_now, requested_tenant_id=tenant)
        except AgoraScopeResolutionError as exc:
            raise HTTPException(exc.status_code, detail={"status": "unavailable", "reason": exc.reason}) from exc

    def unavailable(reason: str, status_code: int = 503) -> HTTPException:
        return HTTPException(status_code, detail={"status": "unavailable", "reason": reason})

    def require_owner() -> None:
        if getattr(dataset_store, "backend", None) != "postgres" and not allow_ephemeral:
            raise unavailable("agora_evidence_durable_owner_unconfigured")

    def register_write(path: str, evidence_type: str) -> None:
        def submit(
            body: HumanEvidenceRequest,
            authorization: Optional[str] = Header(default=None),
            idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
            x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        ) -> Dict[str, Any]:
            scope = scope_for(authorization, x_tenant_id, write=True)
            require_owner()
            if not (idempotency_key or "").strip():
                raise HTTPException(400, detail={"reason": "missing_idempotency_key"})
            try:
                entry, created = owner.submit(
                    evidence_type, body, scope=scope, idempotency_key=idempotency_key.strip(), admitted_at=utc_now(),
                )
            except AgoraWriteForbiddenError as exc:
                raise HTTPException(403, detail=exc.to_dict()) from exc
            except IdempotencyConflictError as exc:
                raise HTTPException(409, detail={"reason": "agora_evidence_idempotency_conflict"}) from exc
            except PrivacyConsentError as exc:
                raise HTTPException(400, detail={"reason": "agora_evidence_consent_required"}) from exc
            except Exception:
                log.exception("Agora human evidence admission failed")
                raise unavailable("agora_evidence_owner_unreachable")
            return {"status": "created" if created else "exists", "idempotent": not created, "data": owner.public_record(entry)}

        router.add_api_route(path, submit, methods=["POST"], status_code=201, name=f"submit_agora_{evidence_type}")

    for path, kind in (
        ("signal-feedback", "signal_feedback"), ("notes", "note"),
        ("insights", "insight"), ("training-examples", "training_example"),
    ):
        register_write(f"/bff/agora/{path}", kind)

    @router.get("/bff/agora/human-evidence/{evidence_id}")
    def get_evidence(
        evidence_id: str, authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = scope_for(authorization, x_tenant_id)
        require_owner()
        try:
            item = owner.get(evidence_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
        except Exception:
            log.exception("Agora human evidence read failed")
            raise unavailable("agora_evidence_owner_unreachable")
        if item is None:
            raise unavailable("agora_human_evidence_missing", 404)
        return {"status": "ok", "data": item}

    @router.get("/bff/agora/personas/{persona_id}/human-evidence")
    def observe_evidence(
        persona_id: str, since: Optional[AwareDatetime] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = scope_for(authorization, x_tenant_id)
        require_owner()
        try:
            items = owner.observe(tenant_id=scope.tenant_id, user_id=scope.user_id, persona_id=persona_id, since=since)
        except Exception:
            log.exception("Agora Observe evidence read failed")
            raise unavailable("agora_evidence_owner_unreachable")
        return {"status": "ok" if items else "unavailable", "reason": None if items else "no_new_human_evidence",
                "items": items, "total": len(items), "environment_ceiling": "paper", "execution_authority": "none"}

    return router
