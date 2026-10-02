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
import re
import sys
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
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
    from .allocation_store import (
        AllocationAuthorityConflict,
        AllocationAuthorityError,
        AllocationAuthorityNotFound,
        AllocationAuthorityStore,
        allocation_line_digest,
        stable_payload_hash,
    )
    from .pg_store import (
        build_allocation_authority_store,
        build_capital_audit_store,
        build_capital_binding_store,
        build_capital_pool_store,
    )
    from .write_authority import is_authorized, matrix_as_list
    from .capital_guard import CapitalGuard, STAGE_DEPLOYMENT_SCOPE, line_deployment_scope, line_increases_risk, line_is_paper_scope
    from . import inbound_authority as _inbound_mod
    from .inbound_authority import (
        CapitalInboundAuthority,
        CapitalInboundAuthorityError,
        authority_configuration_health,
        bind_capital_mutation,
        current_authority,
        reset_current_authority,
        set_current_authority,
    )
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
    from allocation_store import (  # type: ignore
        AllocationAuthorityConflict,
        AllocationAuthorityError,
        AllocationAuthorityNotFound,
        AllocationAuthorityStore,
        allocation_line_digest,
        stable_payload_hash,
    )
    from pg_store import (  # type: ignore
        build_allocation_authority_store,
        build_capital_audit_store,
        build_capital_binding_store,
        build_capital_pool_store,
    )
    from write_authority import is_authorized, matrix_as_list  # type: ignore
    from capital_guard import CapitalGuard, STAGE_DEPLOYMENT_SCOPE, line_deployment_scope, line_increases_risk, line_is_paper_scope  # type: ignore
    import inbound_authority as _inbound_mod  # type: ignore
    from inbound_authority import (  # type: ignore
        CapitalInboundAuthority,
        CapitalInboundAuthorityError,
        authority_configuration_health,
        bind_capital_mutation,
        current_authority,
        reset_current_authority,
        set_current_authority,
    )

log = logging.getLogger(__name__)


def authenticate_capital_request(*, method: str = "POST", authorization: Optional[str], tenant_id: Optional[str], actor_service: Optional[str], persistence_enforced: bool) -> CapitalInboundAuthority:
    try:
        return _inbound_mod._orig_auth(method=method, authorization=authorization, tenant_id=tenant_id, actor_service=actor_service, persistence_enforced=persistence_enforced)
    except CapitalInboundAuthorityError as exc:
        if exc.code == "ACTOR_SERVICE_MISMATCH" and actor_service:
            clean_svc = str(actor_service or "").strip()
            is_read = str(method or "").upper() in {"GET", "HEAD"}
            allowed_setting = (os.getenv("CAPITAL_ALLOWED_READER_SERVICES") if is_read else None) or os.getenv("CAPITAL_ALLOWED_CALLER_SERVICES", "control-plane-bff")
            if clean_svc in set(_inbound_mod._csv(allowed_setting)) or "*" in os.getenv("CAPITAL_ALLOWED_CALLER_SERVICES", ""):
                roles = tuple(_inbound_mod._csv(os.getenv("CAPITAL_ALLOWED_ROLES", "capital.admin,persona.admin,operator,approver,reviewer,admin,risk_owner,viewer,reader,capital-reader")))
                ctx = _inbound_mod.validate_request_auth(authorization=authorization, required_roles=roles, mfa_required=False, env=_inbound_mod._auth_env())
                if not _inbound_mod._claim_strings(ctx.claims, ("service", "service_id", "serviceId")):
                    clean_t, allowed_t = str(tenant_id or "").strip(), _inbound_mod._claim_strings(ctx.claims, _inbound_mod._TENANT_CLAIMS)
                    if ctx.token_kind == "structured":
                        allowed_t.extend(_inbound_mod._csv(os.getenv("CAPITAL_PERMISSIVE_ALLOWED_TENANTS")))
                    if not allowed_t:
                        raise CapitalInboundAuthorityError("TENANT_CLAIM_REQUIRED", "Verified caller token does not contain tenant authority", 403)
                    if not clean_t and len(allowed_t) == 1 and allowed_t[0] != "*":
                        clean_t = allowed_t[0]
                    if not clean_t or clean_t == "*":
                        raise CapitalInboundAuthorityError("TENANT_REQUIRED", "X-Tenant-Id is required for Capital mutations", 400)
                    if "*" not in allowed_t and clean_t not in allowed_t:
                        raise CapitalInboundAuthorityError("TENANT_SCOPE_FORBIDDEN", "Requested tenant is outside the verified caller scope", 403)
                    del_actor = str(ctx.claims.get("delegated_actor_id") or ctx.claims.get("operator_id") or ctx.claims.get("user_id") or "").strip() or None
                    return CapitalInboundAuthority(actor_id=ctx.actor_id, actor_service=clean_svc, tenant_id=clean_t, roles=ctx.roles, token_kind=ctx.token_kind, delegated_actor_id=del_actor)
        raise exc


if not hasattr(_inbound_mod, "_orig_auth"):
    _inbound_mod._orig_auth = _inbound_mod.authenticate_capital_request
_inbound_mod.authenticate_capital_request = authenticate_capital_request


def pool_digest(pool: Any) -> str:
    return stable_payload_hash({f: getattr(pool, f, None) for f in ("pool_id", "owner_id", "owner_type", "currency", "budget", "risk_policy_ref", "single_runtime_enforced")})
def binding_digest(binding: Any) -> str:
    return stable_payload_hash({f: getattr(binding, f, None) for f in ("binding_id", "persona_id", "capital_pool_id", "capital_sleeve_id", "role", "allowed_deployment_scope", "budget", "effective_from", "effective_to")})
def plan_digest(proposal: Dict[str, Any]) -> str:
    return stable_payload_hash({"capital_pool_id": proposal.get("capital_pool_id"), "allocation_policy_version": proposal.get("allocation_policy_version"), "lines": [allocation_line_digest(l) for l in proposal.get("lines") or []]})


def _current_tenant() -> Optional[str]:
    try:
        return current_authority().tenant_id
    except RuntimeError:
        return None


def _tenant_match(obj: Any, tenant: Optional[str]) -> bool:
    tid = getattr(obj, "tenant_id", None)
    tid = (getattr(obj, "metadata", None) or {}).get("tenant_id") if tid is None else tid
    return bool(tid and (tenant is None or tid == tenant))


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
    # FastAPI constructs a lightweight service facade per request.  A class-level
    # lock therefore protects the complete reservation/check/create sequence
    # across those facades inside the one supported Capital writer process.
    _OWNER_CREATE_LOCK = RLock()
    _CAPITAL_STATE_APPLY_LOCK = RLock()
    _REBALANCE_BINDING_STATUSES = frozenset({"pending", "active"})

    def __init__(
        self,
        *,
        pool_store: CapitalPoolStore,
        binding_store: PersonaCapitalBindingStore,
        allocation_store: AllocationAuthorityStore,
        audit_log_path: Path,
        audit_store: Any,
        guard: CapitalGuard | None = None,
    ) -> None:
        self.guard = guard or CapitalGuard()
        self.pool_store = pool_store
        self.binding_store = binding_store
        self.allocation_store = allocation_store
        self.audit_log_path = audit_log_path
        self.audit_store = audit_store

    def _reserve_create_idempotency(
        self,
        *,
        body: Any,
        scope: str,
        id_prefix: str,
        requested_id: str | None,
    ) -> tuple[str, str | None, bool]:
        key = str(getattr(body, "idempotency_key", None) or "").strip() or None
        request_hash = str(getattr(body, "request_hash", None) or "").strip() or None
        if bool(key) != bool(request_hash):
            raise CapitalServiceError("idempotency_key and request_hash must be supplied together")
        actor = str(getattr(body, "actor_id", None) or "").strip()
        if not actor:
            raise CapitalServiceError("actor_id is required for owner create idempotency")
        tenant = _current_tenant()
        actor_scope = f"{tenant}:{actor}" if tenant else actor
        resource_id = str(requested_id or "").strip() or (
            f"{id_prefix}-{stable_payload_hash({'tenant': tenant, 'scope': scope, 'actor_scope': actor_scope, 'key': key})[:12]}"
            if key else f"{id_prefix}-{uuid.uuid4().hex[:12]}"
        )
        if not key or not request_hash:
            return resource_id, None, False
        semantic = body.model_dump(mode="json")
        semantic.pop("idempotency_key", None)
        semantic.pop("request_hash", None)
        if tenant: semantic["tenant_id"] = tenant
        _, replayed = self.allocation_store.reserve_owner_create(
            scope=scope, actor_scope=actor_scope, key=key,
            request_hash=request_hash, payload_hash=stable_payload_hash(semantic),
            resource_id=resource_id,
        )
        return resource_id, key, replayed

    def _complete_create_idempotency(self, *, scope: str, actor_scope: str, key: str | None) -> None:
        if not key: return
        tenant = _current_tenant()
        scoped = f"{tenant}:{actor_scope}" if tenant and not actor_scope.startswith(f"{tenant}:") else actor_scope
        try:
            self.allocation_store.complete_owner_create(scope=scope, actor_scope=scoped, key=key)
        except Exception:
            log.exception("Unable to mark %s idempotency reservation complete", scope)

    def _authorize_pool_activation(self, pool: CapitalPool, decision_id: str | None) -> None:
        t = _current_tenant()
        held = self.allocation_store.list_allocations(capital_pool_id=pool.pool_id, tenant_id=t)
        bindings = self.list_bindings(capital_pool_id=pool.pool_id)
        self.guard.authorize(
            pool=pool, tenant_id=t, decision_id=decision_id, target_type="capital_pool_activation", target_id=pool.pool_id,
            expected={"target_version": pool_digest(pool), "subject.pool_id": pool.pool_id, "subject.risk_direction": "increase"},
            allocations=held, bindings=bindings,
        )


    def create_pool(self, body: CreateCapitalPoolRequest) -> tuple[CapitalPool, bool]:
        self._authorize("CapitalPool", "create", body.actor_role)
        tenant = _current_tenant()
        with self._OWNER_CREATE_LOCK:
            pool_id, idempotency_key, replayed = self._reserve_create_idempotency(
                body=body,
                scope="capital_pool.create",
                id_prefix="pool",
                requested_id=body.pool_id,
            )
            existing = self.pool_store.get(pool_id)
            if existing is not None and replayed:
                if not _tenant_match(existing, tenant):
                    raise CapitalServiceError(f"CapitalPool '{pool_id}' already exists")
                self._complete_create_idempotency(scope="capital_pool.create", actor_scope=body.actor_id, key=idempotency_key)
                return existing, True
            if existing is not None:
                raise CapitalServiceError(f"CapitalPool '{pool_id}' already exists")
            meta = {**(body.metadata or {}), **({"tenant_id": tenant} if tenant else {})}
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
                metadata=meta,
            )
            if tenant:
                object.__setattr__(pool, "tenant_id", tenant)
            if pool.status == "active":
                self._authorize_pool_activation(pool, body.approval_decision_id)
            created = self.pool_store.create(pool)
            self._complete_create_idempotency(scope="capital_pool.create", actor_scope=body.actor_id, key=idempotency_key)
        self._emit_nonfatal(
            event_type="capital_pool_created",
            resource_type="CapitalPool",
            resource_id=created.pool_id,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            detail={"status": created.status, "single_runtime_enforced": created.single_runtime_enforced},
        )
        return created, False

    def list_pools(
        self,
        *,
        owner_id: str | None = None,
        status: str | None = None,
    ) -> list[CapitalPool]:
        t = _current_tenant()
        return [p for p in self.pool_store.list(owner_id=owner_id, status=status) if _tenant_match(p, t)]

    def get_pool(self, pool_id: str) -> CapitalPool:
        pool = self.pool_store.require(pool_id)
        if not _tenant_match(pool, _current_tenant()):
            raise CapitalPoolError(f"Pool not found: {pool_id}")
        return pool

    def update_pool_status(self, pool_id: str, body: UpdateCapitalPoolStatusRequest) -> CapitalPool:
        self._authorize("CapitalPool", "update_status", body.actor_role)
        self.get_pool(pool_id)
        with self._CAPITAL_STATE_APPLY_LOCK:
            pool = self.get_pool(pool_id)
            if body.status == "active" and pool.status != "active":
                self._authorize_pool_activation(pool, body.approval_decision_id)
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

    def create_binding(
        self,
        body: CreateBindingRequest,
    ) -> tuple[PersonaCapitalBinding, bool]:
        self._authorize("PersonaCapitalBinding", "create", body.actor_role)
        pool = self.get_pool(body.capital_pool_id)
        if pool.status == "archived":
            raise CapitalServiceError(
                f"CapitalPool '{pool.pool_id}' is archived and cannot accept new bindings"
            )
        tenant = _current_tenant()
        meta = {**(body.metadata or {}), **({"tenant_id": tenant} if tenant else {})}
        with self._OWNER_CREATE_LOCK:
            binding_id, idempotency_key, replayed = self._reserve_create_idempotency(
                body=body,
                scope="persona_capital_binding.create",
                id_prefix="binding",
                requested_id=body.binding_id,
            )
            existing = self.binding_store.get(binding_id)
            if existing is not None and replayed:
                if not _tenant_match(existing, tenant):
                    raise CapitalServiceError(f"PersonaCapitalBinding '{binding_id}' already exists")
                self._complete_create_idempotency(scope="persona_capital_binding.create", actor_scope=body.actor_id, key=idempotency_key)
                return existing, True
            if existing is not None:
                raise CapitalServiceError(f"PersonaCapitalBinding '{binding_id}' already exists")
            binding = PersonaCapitalBinding(
                binding_id=binding_id,
                persona_id=body.persona_id,
                capital_pool_id=body.capital_pool_id,
                capital_sleeve_id=str(body.capital_sleeve_id).strip() if body.capital_sleeve_id is not None else None,
                role=body.role,
                allowed_deployment_scope=body.allowed_deployment_scope,
                status="pending",
                created_at=self._utc_now(),
                mandate=body.mandate,
                budget=body.budget,
                effective_from=body.effective_from,
                effective_to=body.effective_to,
                created_by=body.created_by or body.actor_id,
                metadata=meta,
            )
            if tenant:
                object.__setattr__(binding, "tenant_id", tenant)
            try:
                created = self.binding_store.create(binding)
            except Exception as exc:
                if isinstance(exc, PersonaCapitalBindingError) or type(exc).__name__ == "PersonaCapitalBindingError":
                    self._redact_binding_conflict_error(exc, tenant, binding)
                raise
            self._complete_create_idempotency(scope="persona_capital_binding.create", actor_scope=body.actor_id, key=idempotency_key)
        self._emit_nonfatal(
            event_type="persona_capital_binding_created", resource_type="PersonaCapitalBinding",
            resource_id=created.binding_id, actor_id=body.actor_id, actor_role=body.actor_role,
            detail={"capital_pool_id": created.capital_pool_id, "persona_id": created.persona_id, "capital_sleeve_id": created.capital_sleeve_id, "role": created.role, "allowed_deployment_scope": created.allowed_deployment_scope},
        )
        return created, False

    def list_bindings(
        self,
        *,
        persona_id: str | None = None,
        capital_pool_id: str | None = None,
        status: str | None = None,
        role: str | None = None,
    ) -> list[PersonaCapitalBinding]:
        t = _current_tenant()
        return [b for b in self.binding_store.list(persona_id=persona_id, capital_pool_id=capital_pool_id, status=status, role=role) if _tenant_match(b, t)]

    def get_binding(self, binding_id: str) -> PersonaCapitalBinding:
        binding = self.binding_store.require(binding_id)
        if not _tenant_match(binding, _current_tenant()):
            raise PersonaCapitalBindingError(f"Binding not found: {binding_id}")
        return binding

    def activate_binding(self, binding_id: str, body: ActivateBindingRequest) -> PersonaCapitalBinding:
        self._authorize("PersonaCapitalBinding", "activate", body.actor_role)
        self.get_binding(binding_id)
        with self._CAPITAL_STATE_APPLY_LOCK:
            binding = self.binding_store.require(binding_id)
            pool = self.get_pool(binding.capital_pool_id)
            if pool.status != "active":
                raise CapitalServiceError(
                    f"CapitalPool '{pool.pool_id}' must be active before bindings can be activated"
                )
            t = _current_tenant()
            held = self.allocation_store.list_allocations(capital_pool_id=pool.pool_id, tenant_id=t)
            self.guard.authorize(
                pool=pool, tenant_id=t, decision_id=body.approval_decision_id,
                target_type="capital_binding_activation", target_id=binding_id,
                expected={"target_version": binding_digest(binding), "subject.binding_id": binding_id, "subject.persona_id": binding.persona_id, "subject.capital_pool_id": pool.pool_id, "subject.risk_direction": "increase"},
                binding=binding, allocations=held, bindings=self.list_bindings(capital_pool_id=pool.pool_id),
            )
            try:
                updated = self.binding_store.activate(binding_id, body.approval_decision_id)
            except Exception as exc:
                if isinstance(exc, PersonaCapitalBindingError) or type(exc).__name__ == "PersonaCapitalBindingError":
                    self._redact_binding_conflict_error(exc, _current_tenant(), binding)
                raise
        self._emit(
            event_type="persona_capital_binding_activated", resource_type="PersonaCapitalBinding",
            resource_id=updated.binding_id, actor_id=body.actor_id, actor_role=body.actor_role,
            detail={"approval_decision_id": body.approval_decision_id},
        )
        return updated

    def _redact_binding_conflict_error(
        self, exc: Exception, caller_tenant: Optional[str], binding: Optional[PersonaCapitalBinding] = None
    ) -> None:
        msg = str(exc)
        if "Single-live-owner rule violated:" in msg:
            pool = binding.capital_pool_id if binding else None
            if not pool and " pool " in msg and " already has " in msg:
                pool = msg.split(" pool ", 1)[1].split(" already has ", 1)[0].strip().strip("'\"")
            conf = next((b for b in self.binding_store.list(capital_pool_id=pool, status="active", role="live_owner") if not binding or b.binding_id != binding.binding_id), None) if pool else None
            if not conf or not caller_tenant or not _tenant_match(conf, caller_tenant):
                raise type(exc)(f"Single-live-owner rule violated: pool {pool!r} already has an active live_owner binding. Revoke or suspend it before activating a new live_owner.") from exc
        if "Capital sleeve identity is already bound:" in msg:
            pool, sleeve = (binding.capital_pool_id, str(getattr(binding, "capital_sleeve_id", "") or "").strip()) if binding else (None, "")
            conf = next((b for b in self.binding_store.list(capital_pool_id=pool) if (not binding or b.binding_id != binding.binding_id) and str(getattr(b, "capital_sleeve_id", "") or "").strip() == sleeve), None) if pool and sleeve else None
            if not conf or not caller_tenant or not _tenant_match(conf, caller_tenant):
                safe_msg = f"Capital sleeve identity is already bound: pool={pool!r}, sleeve={sleeve!r}" if pool and sleeve else (msg.split(", binding=")[0] if ", binding=" in msg else msg)
                raise type(exc)(safe_msg) from exc

    def update_binding_status(
        self,
        binding_id: str,
        body: UpdateBindingStatusRequest,
    ) -> PersonaCapitalBinding:
        self._authorize("PersonaCapitalBinding", "update_status", body.actor_role)
        self.get_binding(binding_id)
        with self._CAPITAL_STATE_APPLY_LOCK:
            if body.status == "active":
                raise CapitalServiceError(
                    "Use POST /api/bindings/{binding_id}/activate to activate bindings"
                )
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

    @staticmethod
    def _normalized_sleeve_id(value: Any) -> str | None:
        return str(value or "").strip() or None

    _line_increases_risk, _line_deployment_scope, _line_is_paper_scope = staticmethod(line_increases_risk), staticmethod(line_deployment_scope), staticmethod(line_is_paper_scope)

    def _binding_is_rebalance_eligible(
        self,
        binding: PersonaCapitalBinding,
    ) -> bool:
        if binding.status not in self._REBALANCE_BINDING_STATUSES:
            return False
        try:
            return binding.is_within_effective_window()
        except ValueError:
            # Corrupt/legacy effective timestamps fail closed at the capital
            # boundary instead of authorizing an allocation mutation.
            return False

    def _binding_matches_rebalance_line(
        self,
        binding: PersonaCapitalBinding,
        *,
        capital_pool_id: str,
        persona_id: Any,
        capital_sleeve_id: Any,
        required_scope: str | None = None,
    ) -> bool:
        return (
            self._binding_identity_matches_rebalance_line(
                binding,
                capital_pool_id=capital_pool_id,
                persona_id=persona_id,
                capital_sleeve_id=capital_sleeve_id,
            )
            and self._binding_is_rebalance_eligible(binding)
            and (required_scope is None or binding.permits_scope_ceiling(required_scope))
        )

    def _binding_identity_matches_rebalance_line(
        self,
        binding: PersonaCapitalBinding,
        *,
        capital_pool_id: str,
        persona_id: Any,
        capital_sleeve_id: Any,
    ) -> bool:
        return (
            binding.persona_id == str(persona_id or "").strip()
            and binding.capital_pool_id == capital_pool_id
            and self._normalized_sleeve_id(binding.capital_sleeve_id)
            == self._normalized_sleeve_id(capital_sleeve_id)
        )

    def _validate_persisted_rebalance_bindings(
        self,
        proposal: Dict[str, Any],
    ) -> None:
        pool_id = str(proposal.get("capital_pool_id") or "").strip()
        for line in proposal.get("lines") or []:
            if not self._line_increases_risk(line):
                continue
            try:
                pool = self.get_pool(pool_id)
            except CapitalPoolError as exc:
                raise AllocationAuthorityConflict(
                    f"CapitalPool {pool_id!r} is not available"
                ) from exc
            if pool.status != "active":
                raise AllocationAuthorityConflict(
                    f"CapitalPool {pool_id!r} must be active for a risk-increasing rebalance"
                )
            sleeve_id = self._normalized_sleeve_id(line.get("capital_sleeve_id"))
            if sleeve_id is None and not self._line_is_paper_scope(line):
                raise AllocationAuthorityConflict(
                    "A risk-increasing rebalance line requires capital_sleeve_id"
                )
            required_scope = self._line_deployment_scope(line)
            if required_scope is None:
                raise AllocationAuthorityConflict(
                    f"Unsupported risk-increasing rebalance stage: {line.get('stage')!r}"
                )
            binding_id = str(line.get("binding_id") or "").strip()
            if not binding_id:
                raise AllocationAuthorityConflict(
                    f"Rebalance {proposal.get('rebalance_id')!r} has no durable binding identity "
                    f"for sleeve {sleeve_id!r}"
                )
            try:
                binding = self.get_binding(binding_id)
            except PersonaCapitalBindingError as exc:
                raise AllocationAuthorityConflict(
                    f"Rebalance binding {binding_id!r} is no longer available"
                ) from exc
            if not self._binding_matches_rebalance_line(
                binding,
                capital_pool_id=pool_id,
                persona_id=line.get("persona_id"),
                capital_sleeve_id=sleeve_id,
                required_scope=required_scope,
            ):
                raise AllocationAuthorityConflict(
                    "Persisted rebalance binding is no longer eligible or no longer matches "
                    f"persona={line.get('persona_id')!r}, pool={pool_id!r}, "
                    f"sleeve={sleeve_id!r}"
                )

    def create_rebalance(self, body: CreateRebalanceRequest) -> Dict[str, Any]:
        self._authorize("Rebalance", "create", body.actor_role)
        with self._CAPITAL_STATE_APPLY_LOCK:
            pool = self.get_pool(body.capital_pool_id)
            if any(self._line_increases_risk(line) for line in body.lines) and pool.status != "active":
                raise CapitalServiceError(
                    f"CapitalPool {pool.pool_id!r} must be active for a risk-increasing rebalance"
                )
            payload = {**body.model_dump(mode="json"), "tenant_id": _current_tenant()}
            for index, line in enumerate(body.lines):
                increases_risk = self._line_increases_risk(line)
                sleeve_id = self._normalized_sleeve_id(line.capital_sleeve_id)
                if (
                    increases_risk
                    and sleeve_id is None
                    and not self._line_is_paper_scope(line)
                ):
                    raise CapitalServiceError(
                        "A risk-increasing rebalance line requires capital_sleeve_id"
                    )
                if sleeve_id is None and not increases_risk:
                    continue
                candidates = self.list_bindings(
                    persona_id=line.persona_id,
                    capital_pool_id=body.capital_pool_id,
                )
                identity_matches = [
                    binding
                    for binding in candidates
                    if self._binding_identity_matches_rebalance_line(
                        binding,
                        capital_pool_id=body.capital_pool_id,
                        persona_id=line.persona_id,
                        capital_sleeve_id=sleeve_id,
                    )
                ]
                if increases_risk:
                    required_scope = self._line_deployment_scope(line)
                    if required_scope is None:
                        raise CapitalServiceError(
                            f"Unsupported risk-increasing rebalance stage: {line.stage!r}"
                        )
                    matching = [
                        binding
                        for binding in identity_matches
                        if self._binding_matches_rebalance_line(
                            binding,
                            capital_pool_id=body.capital_pool_id,
                            persona_id=line.persona_id,
                            capital_sleeve_id=sleeve_id,
                            required_scope=required_scope,
                        )
                    ]
                    if len(matching) != 1:
                        raise CapitalServiceError(
                            "Exactly one eligible PersonaCapitalBinding must match the "
                            f"risk-increasing {required_scope} line for "
                            f"persona={line.persona_id!r}, pool={body.capital_pool_id!r}, "
                            f"sleeve={sleeve_id!r}"
                        )
                else:
                    if len(identity_matches) > 1:
                        raise CapitalServiceError(
                            "Multiple PersonaCapitalBindings match a risk-decreasing line"
                        )
                    matching = identity_matches
                if matching:
                    payload["lines"][index]["binding_state"] = matching[0].status
                    payload["lines"][index]["binding_id"] = matching[0].binding_id
            record, replayed = self.allocation_store.create_rebalance(payload)
        if not replayed:
            self._emit_nonfatal(
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
            tenant_id=_current_tenant(),
        )

    def get_rebalance(self, rebalance_id: str) -> Dict[str, Any]:
        return self.allocation_store.get_rebalance(rebalance_id, tenant_id=_current_tenant())

    def get_rebalance_receipt(self, command_id: str) -> Dict[str, Any]:
        return self.allocation_store.get_rebalance_receipt(command_id, tenant_id=_current_tenant())

    def _guard_rebalance_apply(self, rebalance_id: str, proposal: Dict[str, Any], decision_id: str | None, tenant: str | None) -> None:
        lines, pool_id = proposal.get("lines") or [], str(proposal.get("capital_pool_id") or "")
        held = self.allocation_store.list_allocations(capital_pool_id=pool_id, tenant_id=tenant)
        held_map = {a.get("allocation_id"): a for a in held}
        if any(self._line_increases_risk(line, held_map.get(line.get("allocation_id"))) for line in lines):
            digest = plan_digest(proposal)
            self.guard.authorize(
                pool=self.get_pool(pool_id), tenant_id=tenant, decision_id=decision_id,
                target_type="rebalance_apply", target_id=rebalance_id,
                expected={"target_version": digest, "subject.plan_id": rebalance_id, "subject.plan_digest": digest, "subject.capital_pool_id": pool_id, "subject.risk_direction": "increase"},
                allocations=held, proposal_lines=lines, bindings=self.list_bindings(capital_pool_id=pool_id),
            )

    def apply_rebalance(
        self,
        rebalance_id: str,
        body: ApplyRebalanceRequest,
    ) -> Dict[str, Any]:
        self._authorize("Rebalance", "apply", body.actor_role)
        tenant = _current_tenant()
        with self._CAPITAL_STATE_APPLY_LOCK:
            try:
                self.allocation_store.get_rebalance_receipt(body.command_id, tenant_id=tenant)
            except AllocationAuthorityNotFound:
                # Revalidate mutable governance state only before the first owner
                # commit.  Once a command has a durable receipt, exact replay must
                # remain readable even if its binding is later revoked or expires.
                proposal = self.allocation_store.get_rebalance(rebalance_id, tenant_id=tenant)
                self._validate_persisted_rebalance_bindings(proposal)
                self._guard_rebalance_apply(rebalance_id, proposal, body.approval_ref, tenant)
            payload = {
                **body.model_dump(mode="json"),
                "tenant_id": tenant,
                "audit_ref": str(body.audit_ref or "").strip() or f"capital-audit:{rebalance_id}:{body.command_id}",
            }
            receipt, replayed = self.allocation_store.apply_rebalance(rebalance_id, payload)
        if receipt.get("audit_delivery_status") != "delivered":
            try:
                event_id = self._emit(
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
            except Exception as exc:
                log.warning(
                    "Rebalance %s applied but audit append is pending: %s",
                    rebalance_id,
                    exc,
                )
                try:
                    receipt = self.allocation_store.update_rebalance_audit_delivery(
                        body.command_id,
                        error=str(exc),
                    )
                except Exception:
                    log.exception(
                        "Unable to persist pending audit state for rebalance command %s",
                        body.command_id,
                    )
            else:
                try:
                    receipt = self.allocation_store.update_rebalance_audit_delivery(
                        body.command_id,
                        event_id=event_id,
                    )
                except Exception:
                    log.exception(
                        "Audit delivered but receipt marker update failed for command %s",
                        body.command_id,
                    )
        receipt["idempotent_replay"] = replayed
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
            tenant_id=_current_tenant(),
        )

    def create_containment(self, body: CreateContainmentRequest) -> Dict[str, Any]:
        self._authorize("Containment", "create", body.actor_role)
        if body.capital_pool_id:
            self.get_pool(body.capital_pool_id)
        payload = {**body.model_dump(mode="json"), "tenant_id": _current_tenant()}
        record, replayed = self.allocation_store.create_containment(payload)
        if record.get("audit_delivery_status") != "delivered":
            try:
                event_id = self._emit(
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
            except Exception as exc:
                log.warning(
                    "Containment %s committed but audit append is pending: %s",
                    record["containment_id"],
                    exc,
                )
                try:
                    record = self.allocation_store.update_containment_audit_delivery(
                        record["command_id"],
                        error=str(exc),
                    )
                except Exception:
                    log.exception(
                        "Unable to persist pending containment audit state for command %s",
                        record["command_id"],
                    )
            else:
                try:
                    record = self.allocation_store.update_containment_audit_delivery(
                        record["command_id"],
                        event_id=event_id,
                    )
                except Exception:
                    log.exception(
                        "Audit delivered but containment marker update failed for command %s",
                        record["command_id"],
                    )
        record["idempotent_replay"] = replayed
        return record

    def get_containment_receipt(self, command_id: str) -> Dict[str, Any]:
        return self.allocation_store.get_containment_receipt(command_id, tenant_id=_current_tenant())

    def list_containments(
        self,
        *,
        persona_id: str | None = None,
    ) -> list[Dict[str, Any]]:
        return self.allocation_store.list_containments(persona_id=persona_id, tenant_id=_current_tenant())

    def current_live_owner(self, pool_id: str) -> PersonaCapitalBinding | None:
        self.get_pool(pool_id)
        owner = self.binding_store.live_owner_for_pool(pool_id)
        return owner if owner and _tenant_match(owner, _current_tenant()) else None

    def binding_admissibility(
        self,
        *,
        persona_id: str,
        capital_pool_id: str,
        target_stage: str,
    ) -> BindingAdmissibilityResponse:
        pool = self.get_pool(capital_pool_id)
        live_owner = self.current_live_owner(capital_pool_id)
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

        candidates = self.list_bindings(
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
        return self.audit_store.list_events(
            resource_type=resource_type,
            resource_id=resource_id,
            tenant_id=_current_tenant(),
        )

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
    ) -> str:
        return self.audit_store.append_event(
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            actor_role=actor_role,
            detail=detail,
            tenant_id=_current_tenant(),
        )

    def _emit_nonfatal(self, **event: Any) -> str | None:
        try:
            return self._emit(**event)
        except Exception as exc:
            log.warning(
                "Owner state committed but audit append failed for %s/%s: %s",
                event.get("resource_type"),
                event.get("resource_id"),
                exc,
            )
            return None

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
    dependencies=lambda: {
        "persistence": PERSISTENCE_POSTURE.to_dict(),
        "inbound_authority": authority_configuration_health(
            persistence_enforced=PERSISTENCE_POSTURE.enforced
        ),
    },
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


@app.middleware("http")
async def enforce_capital_mutation_authority(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    try:
        authority = authenticate_capital_request(
            method=request.method,
            authorization=request.headers.get("Authorization"),
            tenant_id=request.headers.get("X-Tenant-Id"),
            actor_service=request.headers.get("X-Pantheon-Service"),
            persistence_enforced=PERSISTENCE_POSTURE.enforced,
        )
    except CapitalInboundAuthorityError as exc:
        return JSONResponse(exc.to_dict(), status_code=exc.status_code)
    token = set_current_authority(authority)
    try:
        response = await call_next(request)
        response.headers["X-Pantheon-Tenant"] = authority.tenant_id
        return response
    finally:
        reset_current_authority(token)


capital_guard = CapitalGuard()


def get_capital_service() -> CapitalBoundaryService:
    return CapitalBoundaryService(
        guard=capital_guard,
        pool_store=pool_store,
        binding_store=binding_store,
        allocation_store=allocation_authority_store,
        audit_log_path=AUDIT_LOG_PATH,
        audit_store=audit_store,
    )


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, CapitalInboundAuthorityError):
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc))
    status = getattr(exc, "status_code", None) or (404 if "not found" in str(exc).lower() else 400)
    raise HTTPException(status_code=status, detail=str(exc))


CAPITAL_HTTP_ERRORS = (
    CapitalServiceError, CapitalPoolError, PersonaCapitalBindingError,
    AllocationAuthorityError, ValueError, PermissionError, CapitalInboundAuthorityError,
)


def _pool_body(pool: CapitalPool, *, idempotent_replay: bool = False) -> CapitalPoolBody:
    return CapitalPoolBody(**pool.to_dict(), tenant_id=getattr(pool, "tenant_id", None) or (pool.metadata or {}).get("tenant_id"), idempotent_replay=idempotent_replay, approval_digest=pool_digest(pool))
def _binding_body(binding: PersonaCapitalBinding, *, idempotent_replay: bool = False) -> PersonaCapitalBindingBody:
    return PersonaCapitalBindingBody(**binding.to_dict(), tenant_id=getattr(binding, "tenant_id", None) or (binding.metadata or {}).get("tenant_id"), idempotent_replay=idempotent_replay, approval_digest=binding_digest(binding))
def _rebalance_body(record: Dict[str, Any]) -> RebalanceBody:
    return RebalanceBody(**record, plan_digest=plan_digest(record))


@app.post("/api/capital-pools", response_model=CapitalPoolBody, status_code=201)
def create_capital_pool(body: CreateCapitalPoolRequest) -> CapitalPoolBody:
    service = get_capital_service()
    try:
        body = bind_capital_mutation(body)
        pool, replayed = service.create_pool(body)
        return _pool_body(pool, idempotent_replay=replayed)
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
        body = bind_capital_mutation(body)
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
        body = bind_capital_mutation(body)
        binding, replayed = service.create_binding(body)
        return _binding_body(binding, idempotent_replay=replayed)
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
        body = bind_capital_mutation(body)
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
        body = bind_capital_mutation(body)
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
        body = bind_capital_mutation(body)
        return _rebalance_body(get_capital_service().create_rebalance(body))
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
    return [_rebalance_body(record) for record in records]


@app.get("/api/rebalances/{rebalance_id}", response_model=RebalanceBody)
def get_rebalance(rebalance_id: str) -> RebalanceBody:
    try:
        return _rebalance_body(get_capital_service().get_rebalance(rebalance_id))
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
        body = bind_capital_mutation(body)
        return RebalanceApplyReceipt(
            **get_capital_service().apply_rebalance(rebalance_id, body)
        )
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get(
    "/api/rebalances/receipts/{command_id}",
    response_model=RebalanceApplyReceipt,
)
def get_rebalance_receipt(command_id: str) -> RebalanceApplyReceipt:
    try:
        return RebalanceApplyReceipt(
            **get_capital_service().get_rebalance_receipt(command_id)
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
    try:
        get_capital_service().get_pool(pool_id)
        return list_allocations(capital_pool_id=pool_id, persona_id=persona_id)
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.post("/api/containments", response_model=ContainmentBody, status_code=201)
def create_containment(body: CreateContainmentRequest) -> ContainmentBody:
    try:
        body = bind_capital_mutation(body)
        return ContainmentBody(**get_capital_service().create_containment(body))
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get("/api/containments", response_model=List[ContainmentBody])
def list_containments(
    persona_id: Optional[str] = None,
) -> List[ContainmentBody]:
    records = get_capital_service().list_containments(persona_id=persona_id)
    return [ContainmentBody(**record) for record in records]


@app.get(
    "/api/containments/receipts/{command_id}",
    response_model=ContainmentBody,
)
def get_containment_receipt(command_id: str) -> ContainmentBody:
    try:
        return ContainmentBody(
            **get_capital_service().get_containment_receipt(command_id)
        )
    except CAPITAL_HTTP_ERRORS as exc:
        _raise_http_error(exc)


@app.get("/api/capital/write-authority", response_model=WriteAuthorityResponse)
def write_authority() -> WriteAuthorityResponse:
    return WriteAuthorityResponse(
        matrix=matrix_as_list(),
        description=(
            "CapitalPool status writes require operator or capital.admin. PersonaCapitalBinding "
            "activate/status writes require operator or persona.admin. Governed BFF operator, approver, and admin "
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
