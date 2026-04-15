"""
BP5-SVC-006: deployable capital service boundary for CapitalPool and PersonaCapitalBinding.

This service wraps the canonical capital governance objects with:
- governed write-authority enforcement
- append-only audit logging
- binding admissibility read paths for runtime-manager and persona surfaces
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from capital_pool import CapitalPool, CapitalPoolError, CapitalPoolStore  # type: ignore
from persona_capital_binding import (  # type: ignore
    DeploymentScope,
    PersonaCapitalBinding,
    PersonaCapitalBindingError,
    PersonaCapitalBindingStore,
)

try:
    from .audit_log import append_audit_event
    from .models import (
        ActivateBindingRequest,
        BindingAdmissibilityResponse,
        CapitalPoolBody,
        CreateBindingRequest,
        CreateCapitalPoolRequest,
        PersonaCapitalBindingBody,
        UpdateBindingStatusRequest,
        UpdateCapitalPoolStatusRequest,
        WriteAuthorityResponse,
    )
    from .write_authority import is_authorized, matrix_as_list
except ImportError:
    from audit_log import append_audit_event  # type: ignore
    from models import (  # type: ignore
        ActivateBindingRequest,
        BindingAdmissibilityResponse,
        CapitalPoolBody,
        CreateBindingRequest,
        CreateCapitalPoolRequest,
        PersonaCapitalBindingBody,
        UpdateBindingStatusRequest,
        UpdateCapitalPoolStatusRequest,
        WriteAuthorityResponse,
    )
    from write_authority import is_authorized, matrix_as_list  # type: ignore

log = logging.getLogger(__name__)


def _resolve_data_dir() -> Path:
    base = (
        os.getenv("CAPITAL_DATA_DIR")
        or os.getenv("PANTHEON_GOVERNANCE_DATA_DIR")
        or "/tmp/pantheon/governance"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = _resolve_data_dir()
POOL_STORE_PATH = DATA_DIR / "capital_pools.json"
BINDING_STORE_PATH = DATA_DIR / "persona_capital_bindings.json"
AUDIT_LOG_PATH = DATA_DIR / "capital_audit.jsonl"


class CapitalServiceError(ValueError):
    """Raised for cross-object service boundary errors."""


pool_store = CapitalPoolStore(path=POOL_STORE_PATH)
binding_store = PersonaCapitalBindingStore(path=BINDING_STORE_PATH)


class CapitalBoundaryService:
    def __init__(
        self,
        *,
        pool_store: CapitalPoolStore,
        binding_store: PersonaCapitalBindingStore,
        audit_log_path: Path,
    ) -> None:
        self.pool_store = pool_store
        self.binding_store = binding_store
        self.audit_log_path = audit_log_path

    def create_pool(self, body: CreateCapitalPoolRequest) -> CapitalPool:
        self._authorize("CapitalPool", "create", body.actor_role)
        pool_id = body.pool_id or f"pool-{uuid.uuid4().hex[:12]}"
        if self.pool_store.get(pool_id) is not None:
            raise CapitalServiceError(f"CapitalPool '{pool_id}' already exists")
        pool = CapitalPool(
            pool_id=pool_id,
            name=body.name,
            owner_id=body.owner_id,
            owner_type=body.owner_type,
            status=body.status,
            created_at=self._utc_now(),
            description=body.description,
            currency=body.currency,
            budget=body.budget,
            risk_policy_ref=body.risk_policy_ref,
            single_runtime_enforced=body.single_runtime_enforced,
            metadata=body.metadata,
        )
        created = self.pool_store.create(pool)
        self._emit(
            event_type="capital_pool_created",
            resource_type="CapitalPool",
            resource_id=created.pool_id,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            detail={"status": created.status, "single_runtime_enforced": created.single_runtime_enforced},
        )
        return created

    def list_pools(
        self,
        *,
        owner_id: str | None = None,
        status: str | None = None,
    ) -> list[CapitalPool]:
        return self.pool_store.list(owner_id=owner_id, status=status)

    def get_pool(self, pool_id: str) -> CapitalPool:
        return self.pool_store.require(pool_id)

    def update_pool_status(self, pool_id: str, body: UpdateCapitalPoolStatusRequest) -> CapitalPool:
        self._authorize("CapitalPool", "update_status", body.actor_role)
        updated = self.pool_store.update_status(pool_id, body.status)
        self._emit(
            event_type="capital_pool_status_updated",
            resource_type="CapitalPool",
            resource_id=updated.pool_id,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            detail={"status": updated.status},
        )
        return updated

    def create_binding(self, body: CreateBindingRequest) -> PersonaCapitalBinding:
        self._authorize("PersonaCapitalBinding", "create", body.actor_role)
        pool = self.pool_store.require(body.capital_pool_id)
        if pool.status == "archived":
            raise CapitalServiceError(
                f"CapitalPool '{pool.pool_id}' is archived and cannot accept new bindings"
            )
        binding_id = body.binding_id or f"binding-{uuid.uuid4().hex[:12]}"
        if self.binding_store.get(binding_id) is not None:
            raise CapitalServiceError(f"PersonaCapitalBinding '{binding_id}' already exists")
        binding = PersonaCapitalBinding(
            binding_id=binding_id,
            persona_id=body.persona_id,
            capital_pool_id=body.capital_pool_id,
            role=body.role,
            allowed_deployment_scope=body.allowed_deployment_scope,
            status="pending",
            created_at=self._utc_now(),
            mandate=body.mandate,
            budget=body.budget,
            effective_from=body.effective_from,
            effective_to=body.effective_to,
            created_by=body.created_by or body.actor_id,
            metadata=body.metadata,
        )
        created = self.binding_store.create(binding)
        self._emit(
            event_type="persona_capital_binding_created",
            resource_type="PersonaCapitalBinding",
            resource_id=created.binding_id,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            detail={
                "capital_pool_id": created.capital_pool_id,
                "persona_id": created.persona_id,
                "role": created.role,
                "allowed_deployment_scope": created.allowed_deployment_scope,
            },
        )
        return created

    def list_bindings(
        self,
        *,
        persona_id: str | None = None,
        capital_pool_id: str | None = None,
        status: str | None = None,
        role: str | None = None,
    ) -> list[PersonaCapitalBinding]:
        return self.binding_store.list(
            persona_id=persona_id,
            capital_pool_id=capital_pool_id,
            status=status,
            role=role,
        )

    def get_binding(self, binding_id: str) -> PersonaCapitalBinding:
        return self.binding_store.require(binding_id)

    def activate_binding(self, binding_id: str, body: ActivateBindingRequest) -> PersonaCapitalBinding:
        self._authorize("PersonaCapitalBinding", "activate", body.actor_role)
        binding = self.binding_store.require(binding_id)
        pool = self.pool_store.require(binding.capital_pool_id)
        if pool.status != "active":
            raise CapitalServiceError(
                f"CapitalPool '{pool.pool_id}' must be active before bindings can be activated"
            )
        updated = self.binding_store.activate(binding_id, body.approval_decision_id)
        self._emit(
            event_type="persona_capital_binding_activated",
            resource_type="PersonaCapitalBinding",
            resource_id=updated.binding_id,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            detail={"approval_decision_id": body.approval_decision_id},
        )
        return updated

    def update_binding_status(
        self,
        binding_id: str,
        body: UpdateBindingStatusRequest,
    ) -> PersonaCapitalBinding:
        self._authorize("PersonaCapitalBinding", "update_status", body.actor_role)
        if body.status == "active":
            raise CapitalServiceError("Use POST /api/bindings/{binding_id}/activate to activate bindings")
        updated = self.binding_store.update_status(binding_id, body.status)
        self._emit(
            event_type="persona_capital_binding_status_updated",
            resource_type="PersonaCapitalBinding",
            resource_id=updated.binding_id,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            detail={"status": updated.status},
        )
        return updated

    def current_live_owner(self, pool_id: str) -> PersonaCapitalBinding | None:
        self.pool_store.require(pool_id)
        return self.binding_store.live_owner_for_pool(pool_id)

    def binding_admissibility(
        self,
        *,
        persona_id: str,
        capital_pool_id: str,
        target_stage: str,
    ) -> BindingAdmissibilityResponse:
        pool = self.pool_store.require(capital_pool_id)
        live_owner = self.binding_store.live_owner_for_pool(capital_pool_id)
        if pool.status != "active":
            return BindingAdmissibilityResponse(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                target_stage=target_stage,
                permitted=False,
                pool_status=pool.status,
                single_runtime_enforced=pool.single_runtime_enforced,
                active_live_owner_binding_id=live_owner.binding_id if live_owner else None,
                reason=f"CapitalPool '{capital_pool_id}' is {pool.status}",
            )

        candidates = self.binding_store.list(
            persona_id=persona_id,
            capital_pool_id=capital_pool_id,
            status="active",
        )
        permitted = [binding for binding in candidates if binding.permits_deployment_to(target_stage)]
        if not permitted:
            return BindingAdmissibilityResponse(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                target_stage=target_stage,
                permitted=False,
                pool_status=pool.status,
                single_runtime_enforced=pool.single_runtime_enforced,
                active_live_owner_binding_id=live_owner.binding_id if live_owner else None,
                reason="No active binding permits the requested target stage",
            )

        chosen = max(
            permitted,
            key=lambda binding: DeploymentScope(binding.allowed_deployment_scope).level,
        )
        return BindingAdmissibilityResponse(
            persona_id=persona_id,
            capital_pool_id=capital_pool_id,
            target_stage=target_stage,
            permitted=True,
            pool_status=pool.status,
            single_runtime_enforced=pool.single_runtime_enforced,
            binding_id=chosen.binding_id,
            binding_role=chosen.role,
            binding_status=chosen.status,
            allowed_deployment_scope=chosen.allowed_deployment_scope,
            active_live_owner_binding_id=live_owner.binding_id if live_owner else None,
        )

    def audit_events(
        self,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> list[Dict[str, Any]]:
        if not self.audit_log_path.exists():
            return []
        events: list[Dict[str, Any]] = []
        for line in self.audit_log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if resource_type and event.get("resource_type") != resource_type:
                continue
            if resource_id and event.get("resource_id") != resource_id:
                continue
            events.append(event)
        return events

    def _authorize(self, resource_type: str, operation: str, actor_role: str) -> None:
        if not is_authorized(resource_type, operation, actor_role):
            raise PermissionError(
                f"actor_role '{actor_role}' is not authorized for {resource_type}.{operation}"
            )

    def _emit(
        self,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str,
        actor_id: str,
        actor_role: str,
        detail: Dict[str, Any],
    ) -> None:
        append_audit_event(
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            detail=detail,
            audit_log_path=str(self.audit_log_path),
        )

    @staticmethod
    def _utc_now() -> str:
        from datetime import datetime, timezone

        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )


app = FastAPI(
    title="Pantheon Capital Service",
    version="0.1.0",
    description=(
        "Canonical capital-pool and persona-binding service boundary. "
        "Write flows go through this service; downstream runtime and persona "
        "consumers use its governed read paths."
    ),
)


def get_capital_service() -> CapitalBoundaryService:
    return CapitalBoundaryService(
        pool_store=pool_store,
        binding_store=binding_store,
        audit_log_path=AUDIT_LOG_PATH,
    )


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    message = str(exc)
    if "not found" in message.lower():
        raise HTTPException(status_code=404, detail=message)
    raise HTTPException(status_code=400, detail=message)


def _pool_body(pool: CapitalPool) -> CapitalPoolBody:
    return CapitalPoolBody(**pool.to_dict())


def _binding_body(binding: PersonaCapitalBinding) -> PersonaCapitalBindingBody:
    return PersonaCapitalBindingBody(**binding.to_dict())


@app.post("/api/capital-pools", response_model=CapitalPoolBody, status_code=201)
def create_capital_pool(body: CreateCapitalPoolRequest) -> CapitalPoolBody:
    service = get_capital_service()
    try:
        return _pool_body(service.create_pool(body))
    except (CapitalServiceError, CapitalPoolError, PermissionError) as exc:
        _raise_http_error(exc)


@app.get("/api/capital-pools", response_model=List[CapitalPoolBody])
def list_capital_pools(
    owner_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[CapitalPoolBody]:
    pools = get_capital_service().list_pools(owner_id=owner_id, status=status)
    return [_pool_body(pool) for pool in pools]


@app.get("/api/capital-pools/{pool_id}", response_model=CapitalPoolBody)
def get_capital_pool(pool_id: str) -> CapitalPoolBody:
    try:
        return _pool_body(get_capital_service().get_pool(pool_id))
    except (CapitalServiceError, CapitalPoolError) as exc:
        _raise_http_error(exc)


@app.patch("/api/capital-pools/{pool_id}/status", response_model=CapitalPoolBody)
def update_capital_pool_status(
    pool_id: str,
    body: UpdateCapitalPoolStatusRequest,
) -> CapitalPoolBody:
    service = get_capital_service()
    try:
        return _pool_body(service.update_pool_status(pool_id, body))
    except (CapitalServiceError, CapitalPoolError, PermissionError) as exc:
        _raise_http_error(exc)


@app.get(
    "/api/capital-pools/{pool_id}/live-owner",
    response_model=Optional[PersonaCapitalBindingBody],
)
def get_live_owner(pool_id: str) -> Optional[PersonaCapitalBindingBody]:
    try:
        binding = get_capital_service().current_live_owner(pool_id)
    except (CapitalServiceError, CapitalPoolError) as exc:
        _raise_http_error(exc)
    return _binding_body(binding) if binding else None


@app.get(
    "/api/bindings/admissibility",
    response_model=BindingAdmissibilityResponse,
)
def binding_admissibility(
    persona_id: str = Query(...),
    capital_pool_id: str = Query(...),
    target_stage: str = Query(...),
) -> BindingAdmissibilityResponse:
    try:
        return get_capital_service().binding_admissibility(
            persona_id=persona_id,
            capital_pool_id=capital_pool_id,
            target_stage=target_stage,
        )
    except (CapitalServiceError, CapitalPoolError, PersonaCapitalBindingError) as exc:
        _raise_http_error(exc)


@app.post("/api/bindings", response_model=PersonaCapitalBindingBody, status_code=201)
def create_binding(body: CreateBindingRequest) -> PersonaCapitalBindingBody:
    service = get_capital_service()
    try:
        return _binding_body(service.create_binding(body))
    except (
        CapitalServiceError,
        CapitalPoolError,
        PersonaCapitalBindingError,
        PermissionError,
    ) as exc:
        _raise_http_error(exc)


@app.get("/api/bindings", response_model=List[PersonaCapitalBindingBody])
def list_bindings(
    persona_id: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    status: Optional[str] = None,
    role: Optional[str] = None,
) -> List[PersonaCapitalBindingBody]:
    bindings = get_capital_service().list_bindings(
        persona_id=persona_id,
        capital_pool_id=capital_pool_id,
        status=status,
        role=role,
    )
    return [_binding_body(binding) for binding in bindings]


@app.get("/api/bindings/{binding_id}", response_model=PersonaCapitalBindingBody)
def get_binding(binding_id: str) -> PersonaCapitalBindingBody:
    try:
        return _binding_body(get_capital_service().get_binding(binding_id))
    except (CapitalServiceError, PersonaCapitalBindingError) as exc:
        _raise_http_error(exc)


@app.post("/api/bindings/{binding_id}/activate", response_model=PersonaCapitalBindingBody)
def activate_binding(binding_id: str, body: ActivateBindingRequest) -> PersonaCapitalBindingBody:
    service = get_capital_service()
    try:
        return _binding_body(service.activate_binding(binding_id, body))
    except (
        CapitalServiceError,
        CapitalPoolError,
        PersonaCapitalBindingError,
        PermissionError,
    ) as exc:
        _raise_http_error(exc)


@app.patch("/api/bindings/{binding_id}/status", response_model=PersonaCapitalBindingBody)
def update_binding_status(
    binding_id: str,
    body: UpdateBindingStatusRequest,
) -> PersonaCapitalBindingBody:
    service = get_capital_service()
    try:
        return _binding_body(service.update_binding_status(binding_id, body))
    except (
        CapitalServiceError,
        PersonaCapitalBindingError,
        PermissionError,
    ) as exc:
        _raise_http_error(exc)


@app.get("/api/capital/write-authority", response_model=WriteAuthorityResponse)
def write_authority() -> WriteAuthorityResponse:
    return WriteAuthorityResponse(
        matrix=matrix_as_list(),
        description=(
            "CapitalPool writes require capital.admin. PersonaCapitalBinding "
            "writes require persona.admin. BFF and runtime consumers stay on read paths."
        ),
    )


@app.get("/api/capital/audit")
def audit(
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return get_capital_service().audit_events(resource_type=resource_type, resource_id=resource_id)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "pantheon-capital"}

