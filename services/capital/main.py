"""
BP5-SVC-006: deployable capital service boundary for CapitalPool and PersonaCapitalBinding.

This service wraps the canonical capital governance objects with:
- governed write-authority enforcement
- append-only audit logging
- binding admissibility read paths for runtime-manager and persona surfaces
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture

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
    from .models import (
        ActivateBindingRequest,
        AllocationBody,
        AllocationListResponse,
        ApplyRebalanceRequest,
        BindingAdmissibilityResponse,
        CapitalPoolBody,
        ContainmentBody,
        CreateContainmentRequest,
        CreateBindingRequest,
        CreateCapitalPoolRequest,
        CreateRebalanceRequest,
        PersonaCapitalBindingBody,
        RebalanceApplyReceipt,
        RebalanceBody,
        UpdateBindingStatusRequest,
        UpdateCapitalPoolStatusRequest,
        WriteAuthorityResponse,
    )
    from .allocation_store import AllocationAuthorityError, AllocationAuthorityStore
    from .pg_store import (
        build_allocation_authority_store,
        build_capital_audit_store,
        build_capital_binding_store,
        build_capital_pool_store,
    )
    from .write_authority import is_authorized, matrix_as_list
except ImportError:
    from models import (  # type: ignore
        ActivateBindingRequest,
        AllocationBody,
        AllocationListResponse,
        ApplyRebalanceRequest,
        BindingAdmissibilityResponse,
        CapitalPoolBody,
        ContainmentBody,
        CreateContainmentRequest,
        CreateBindingRequest,
        CreateCapitalPoolRequest,
        CreateRebalanceRequest,
        PersonaCapitalBindingBody,
        RebalanceApplyReceipt,
        RebalanceBody,
        UpdateBindingStatusRequest,
        UpdateCapitalPoolStatusRequest,
        WriteAuthorityResponse,
    )
    from allocation_store import AllocationAuthorityError, AllocationAuthorityStore  # type: ignore
    from pg_store import (  # type: ignore
        build_allocation_authority_store,
        build_capital_audit_store,
        build_capital_binding_store,
        build_capital_pool_store,
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
ALLOCATION_AUTHORITY_PATH = DATA_DIR / "capital_allocation_authority.json"
STORE_BACKEND = os.getenv("CAPITAL_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("capital")


class CapitalServiceError(ValueError):
    """Raised for cross-object service boundary errors."""


pool_store = build_capital_pool_store(POOL_STORE_PATH)
binding_store = build_capital_binding_store(BINDING_STORE_PATH)
audit_store = build_capital_audit_store(AUDIT_LOG_PATH)
allocation_authority_store = build_allocation_authority_store(ALLOCATION_AUTHORITY_PATH)


class CapitalBoundaryService:
    def __init__(
        self,
        *,
        pool_store: CapitalPoolStore,
        binding_store: PersonaCapitalBindingStore,
        allocation_store: AllocationAuthorityStore,
        audit_log_path: Path,
        audit_store: Any,
    ) -> None:
        self.pool_store = pool_store
        self.binding_store = binding_store
        self.allocation_store = allocation_store
        self.audit_log_path = audit_log_path
        self.audit_store = audit_store

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

    def create_rebalance(self, body: CreateRebalanceRequest) -> Dict[str, Any]:
        self._authorize("Rebalance", "create", body.actor_role)
        self.pool_store.require(body.capital_pool_id)
        record, replayed = self.allocation_store.create_rebalance(
            body.model_dump(mode="json")
        )
        if not replayed:
            self._emit(
                event_type="rebalance_proposal_created",
                resource_type="Rebalance",
                resource_id=record["rebalance_id"],
                actor_id=body.actor_id,
                actor_role=body.actor_role,
                detail={
                    "capital_pool_id": body.capital_pool_id,
                    "request_hash": body.request_hash,
                    "line_count": len(record.get("lines") or []),
                },
            )
        return record

    def list_rebalances(
        self,
        *,
        capital_pool_id: str | None = None,
        status: str | None = None,
    ) -> list[Dict[str, Any]]:
        return self.allocation_store.list_rebalances(
            capital_pool_id=capital_pool_id,
            status=status,
        )

    def get_rebalance(self, rebalance_id: str) -> Dict[str, Any]:
        return self.allocation_store.get_rebalance(rebalance_id)

    def apply_rebalance(
        self,
        rebalance_id: str,
        body: ApplyRebalanceRequest,
    ) -> Dict[str, Any]:
        self._authorize("Rebalance", "apply", body.actor_role)
        payload = body.model_dump(mode="json")
        payload["audit_ref"] = (
            str(payload.get("audit_ref") or "").strip()
            or f"capital-audit:{rebalance_id}:{body.command_id}"
        )
        receipt, replayed = self.allocation_store.apply_rebalance(rebalance_id, payload)
        if not replayed:
            self._emit(
                event_type="rebalance_applied",
                resource_type="Rebalance",
                resource_id=rebalance_id,
                actor_id=body.actor_id,
                actor_role=body.actor_role,
                detail={
                    "capital_pool_id": receipt["capital_pool_id"],
                    "command_id": body.command_id,
                    "approval_ref": receipt.get("approval_ref"),
                    "receipt_ref": receipt["receipt_ref"],
                    "audit_ref": receipt["audit_ref"],
                    "authoritative_capital_readback": True,
                },
            )
        return receipt

    def list_allocations(
        self,
        *,
        capital_pool_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[Dict[str, Any]]:
        return self.allocation_store.list_allocations(
            capital_pool_id=capital_pool_id,
            persona_id=persona_id,
        )

    def create_containment(self, body: CreateContainmentRequest) -> Dict[str, Any]:
        self._authorize("Containment", "create", body.actor_role)
        payload = body.model_dump(mode="json")
        record, replayed = self.allocation_store.create_containment(payload)
        if not replayed:
            self._emit(
                event_type="capital_containment_executed",
                resource_type="Containment",
                resource_id=record["containment_id"],
                actor_id=body.actor_id,
                actor_role=body.actor_role,
                detail={
                    "persona_id": body.persona_id,
                    "capital_pool_id": body.capital_pool_id,
                    "action": body.action,
                    "state": record["state"],
                    "receipt_ref": record["receipt_ref"],
                    "audit_ref": record["audit_ref"],
                },
            )
        return record

    def list_containments(
        self,
        *,
        persona_id: str | None = None,
    ) -> list[Dict[str, Any]]:
        return self.allocation_store.list_containments(persona_id=persona_id)

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
        return self.audit_store.list_events(resource_type=resource_type, resource_id=resource_id)

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
        self.audit_store.append_event(
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            detail=detail,
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
register_fastapi_health_routes(
    app,
    "pantheon-capital",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {
        "capital_pool_count": len(get_capital_service().list_pools()),
        "binding_count": len(get_capital_service().list_bindings()),
        "allocation_count": len(get_capital_service().list_allocations()),
        "rebalance_count": len(get_capital_service().list_rebalances()),
    },
    details=lambda: {
        "data_dir": str(DATA_DIR),
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


def get_capital_service() -> CapitalBoundaryService:
    return CapitalBoundaryService(
        pool_store=pool_store,
        binding_store=binding_store,
        allocation_store=allocation_authority_store,
        audit_log_path=AUDIT_LOG_PATH,
        audit_store=audit_store,
    )


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    explicit_status = getattr(exc, "status_code", None)
    if isinstance(explicit_status, int):
        raise HTTPException(status_code=explicit_status, detail=str(exc))
    message = str(exc)
    if "not found" in message.lower():
        raise HTTPException(status_code=404, detail=message)
    raise HTTPException(status_code=400, detail=message)


CAPITAL_HTTP_ERRORS = (
    CapitalServiceError,
    CapitalPoolError,
    PersonaCapitalBindingError,
    AllocationAuthorityError,
    ValueError,
    PermissionError,
)


def _pool_body(pool: CapitalPool) -> CapitalPoolBody:
    return CapitalPoolBody(**pool.to_dict())


def _binding_body(binding: PersonaCapitalBinding) -> PersonaCapitalBindingBody:
    return PersonaCapitalBindingBody(**binding.to_dict())


@app.post("/api/capital-pools", response_model=CapitalPoolBody, status_code=201)
def create_capital_pool(body: CreateCapitalPoolRequest) -> CapitalPoolBody:
    service = get_capital_service()
    try:
        return _pool_body(service.create_pool(body))
    except CAPITAL_HTTP_ERRORS as exc:
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
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.patch("/api/capital-pools/{pool_id}/status", response_model=CapitalPoolBody)
def update_capital_pool_status(
    pool_id: str,
    body: UpdateCapitalPoolStatusRequest,
) -> CapitalPoolBody:
    service = get_capital_service()
    try:
        return _pool_body(service.update_pool_status(pool_id, body))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get(
    "/api/capital-pools/{pool_id}/live-owner",
    response_model=Optional[PersonaCapitalBindingBody],
)
def get_live_owner(pool_id: str) -> Optional[PersonaCapitalBindingBody]:
    try:
        binding = get_capital_service().current_live_owner(pool_id)
    except CAPITAL_HTTP_ERRORS as exc:
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
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.post("/api/bindings", response_model=PersonaCapitalBindingBody, status_code=201)
def create_binding(body: CreateBindingRequest) -> PersonaCapitalBindingBody:
    service = get_capital_service()
    try:
        return _binding_body(service.create_binding(body))
    except CAPITAL_HTTP_ERRORS as exc:
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
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.post("/api/bindings/{binding_id}/activate", response_model=PersonaCapitalBindingBody)
def activate_binding(binding_id: str, body: ActivateBindingRequest) -> PersonaCapitalBindingBody:
    service = get_capital_service()
    try:
        return _binding_body(service.activate_binding(binding_id, body))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.patch("/api/bindings/{binding_id}/status", response_model=PersonaCapitalBindingBody)
def update_binding_status(
    binding_id: str,
    body: UpdateBindingStatusRequest,
) -> PersonaCapitalBindingBody:
    service = get_capital_service()
    try:
        return _binding_body(service.update_binding_status(binding_id, body))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


def _allocation_list_response(records: List[Dict[str, Any]]) -> AllocationListResponse:
    return AllocationListResponse(
        items=[AllocationBody(**record) for record in records],
        count=len(records),
        snapshot_at=CapitalBoundaryService._utc_now(),
        source="capital_service",
        authoritative_capital_readback=True,
    )


@app.post("/api/rebalances", response_model=RebalanceBody, status_code=201)
def create_rebalance(body: CreateRebalanceRequest) -> RebalanceBody:
    try:
        return RebalanceBody(**get_capital_service().create_rebalance(body))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get("/api/rebalances", response_model=List[RebalanceBody])
def list_rebalances(
    capital_pool_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[RebalanceBody]:
    records = get_capital_service().list_rebalances(
        capital_pool_id=capital_pool_id,
        status=status,
    )
    return [RebalanceBody(**record) for record in records]


@app.get("/api/rebalances/{rebalance_id}", response_model=RebalanceBody)
def get_rebalance(rebalance_id: str) -> RebalanceBody:
    try:
        return RebalanceBody(**get_capital_service().get_rebalance(rebalance_id))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.post(
    "/api/rebalances/{rebalance_id}/apply",
    response_model=RebalanceApplyReceipt,
)
def apply_rebalance(
    rebalance_id: str,
    body: ApplyRebalanceRequest,
) -> RebalanceApplyReceipt:
    try:
        return RebalanceApplyReceipt(
            **get_capital_service().apply_rebalance(rebalance_id, body)
        )
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get("/api/allocations", response_model=AllocationListResponse)
def list_allocations(
    capital_pool_id: Optional[str] = None,
    persona_id: Optional[str] = None,
) -> AllocationListResponse:
    records = get_capital_service().list_allocations(
        capital_pool_id=capital_pool_id,
        persona_id=persona_id,
    )
    return _allocation_list_response(records)


@app.get(
    "/api/capital-pools/{pool_id}/allocations",
    response_model=AllocationListResponse,
)
def list_pool_allocations(
    pool_id: str,
    persona_id: Optional[str] = None,
) -> AllocationListResponse:
    service = get_capital_service()
    try:
        service.get_pool(pool_id)
        records = service.list_allocations(
            capital_pool_id=pool_id,
            persona_id=persona_id,
        )
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)
    return _allocation_list_response(records)


@app.post("/api/containments", response_model=ContainmentBody, status_code=201)
def create_containment(body: CreateContainmentRequest) -> ContainmentBody:
    try:
        return ContainmentBody(**get_capital_service().create_containment(body))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get("/api/containments", response_model=List[ContainmentBody])
def list_containments(
    persona_id: Optional[str] = None,
) -> List[ContainmentBody]:
    records = get_capital_service().list_containments(persona_id=persona_id)
    return [ContainmentBody(**record) for record in records]


@app.get("/api/capital/write-authority", response_model=WriteAuthorityResponse)
def write_authority() -> WriteAuthorityResponse:
    return WriteAuthorityResponse(
        matrix=matrix_as_list(),
        description=(
            "CapitalPool writes require capital.admin. PersonaCapitalBinding "
            "writes require persona.admin. Governed BFF operator, approver, and admin "
            "calls may create/apply rebalances and execute risk-decreasing containment."
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
