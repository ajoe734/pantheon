"""Evolution Program owner API (U8A / U8B).

Per docs/operations/bff-upstream-v2-20260911/decisions/evolution-lifecycle.md §3/§4:
``/api/evolution/programs`` — create / list / get / metadata-PATCH(name only),
and ``/api/evolution/programs/{program_id}/actions/{action_id}`` for real lifecycle
controls, state transitions, generation freeze, candidate promotions, and receipts.

This module is a bounded addition inside the existing Evolution service, not
a new microservice. It is mounted by ``services/evolution/main.py`` under the
same ``authenticate_tenant`` middleware already enforced for every
``/api/evolution`` path, so tenant identity here is always the
already-authenticated request tenant — callers inject that resolution via
``current_tenant``/``authorize_request_tenant`` rather than trusting a raw
request body field.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.evolution.models import ProgramActionRequest
from services.evolution.program_service import (
    ProgramConflictError,
    ProgramDivergentReplayError,
    ProgramNotFoundError,
    ProgramService,
    ProgramValidationError,
)

CurrentTenant = Callable[[], str]
AuthorizeRequestTenant = Callable[[Optional[str]], str]


class ProgramCreateRequest(BaseModel):
    """Only ``name`` and ``actor_id`` are accepted. Any other field
    (including ``status``, which the contract forbids clients from setting)
    is rejected with 422 by ``extra="forbid"`` rather than silently
    dropped. ``tenant_id`` may be supplied only to be revalidated against
    the authenticated request tenant — it can never override it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    actor_id: str
    tenant_id: Optional[str] = None


class ProgramPatchRequest(BaseModel):
    """Metadata PATCH allowlist is ``name`` only (contract §3). Any other
    field — status, state, params, tenant/actor/IDs, revision/timestamps,
    generation/population/fitness/progress, membership, approval refs,
    constraints, formulas, mutation rules, budget, deployment fields — is
    rejected with 422 via ``extra="forbid"``, never silently dropped.
    ``expected_revision`` is the explicit CAS precondition, not a patchable
    metadata field."""

    model_config = ConfigDict(extra="forbid")

    name: str
    expected_revision: int
    actor_id: str
    tenant_id: Optional[str] = None


def _idempotency_key(
    idempotency_key: Optional[str], x_idempotency_key: Optional[str]
) -> Optional[str]:
    resolved = (idempotency_key or x_idempotency_key or "").strip()
    return resolved or None


def create_program_router(
    *,
    service: ProgramService,
    current_tenant: CurrentTenant,
    authorize_request_tenant: AuthorizeRequestTenant,
) -> APIRouter:
    router = APIRouter()

    def _resolve_tenant(requested: Optional[str]) -> str:
        try:
            return authorize_request_tenant(requested)
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _unavailable(exc: Exception) -> HTTPException:
        return HTTPException(
            status_code=503,
            detail=f"Evolution program store is unavailable: {exc}",
        )

    @router.post("/api/evolution/programs", status_code=201)
    def create_program(
        body: ProgramCreateRequest,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        tenant_id = _resolve_tenant(body.tenant_id)
        key = _idempotency_key(idempotency_key, x_idempotency_key)
        try:
            program, _replayed = service.create_program(
                tenant_id=tenant_id,
                actor_id=body.actor_id,
                name=body.name,
                idempotency_key=key,
            )
        except ProgramValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProgramDivergentReplayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProgramConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc
        return program

    @router.get("/api/evolution/programs")
    def list_programs() -> Dict[str, Any]:
        tenant_id = current_tenant()
        try:
            programs = service.list_programs(tenant_id=tenant_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc
        return {"items": programs}

    @router.get("/api/evolution/programs/{program_id}")
    def get_program(program_id: str) -> Dict[str, Any]:
        tenant_id = current_tenant()
        try:
            program = service.get_program(tenant_id=tenant_id, program_id=program_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc
        if program is None:
            raise HTTPException(status_code=404, detail=f"Evolution program not found: {program_id}")
        return program

    @router.patch("/api/evolution/programs/{program_id}")
    def patch_program(
        program_id: str,
        body: ProgramPatchRequest,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        tenant_id = _resolve_tenant(body.tenant_id)
        key = _idempotency_key(idempotency_key, x_idempotency_key)
        try:
            program, _replayed = service.patch_program_name(
                tenant_id=tenant_id,
                actor_id=body.actor_id,
                program_id=program_id,
                name=body.name,
                expected_revision=body.expected_revision,
                idempotency_key=key,
            )
        except ProgramValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProgramNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProgramDivergentReplayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProgramConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc
        return program

    @router.post("/api/evolution/programs/{program_id}/actions/{action_id}", status_code=200)
    def execute_program_action(
        program_id: str,
        action_id: str,
        body: Optional[ProgramActionRequest] = None,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        req_body = body or ProgramActionRequest(actor_id="operator")
        tenant_id = _resolve_tenant(req_body.tenant_id)
        key = _idempotency_key(idempotency_key, x_idempotency_key)
        payload = req_body.model_dump(exclude_unset=True)
        try:
            result, _replayed = service.execute_action(
                tenant_id=tenant_id,
                actor_id=req_body.actor_id,
                actor_role=req_body.actor_role,
                program_id=program_id,
                action_id=action_id,
                expected_revision=req_body.expected_revision,
                idempotency_key=key,
                payload=payload,
            )
        except ProgramValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProgramNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProgramDivergentReplayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProgramConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc) from exc
        return result

    return router
