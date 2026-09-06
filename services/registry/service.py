"""
BP5-SVC-002: FastAPI registry service exposing the split artifact_state / deployment_stage API.

Endpoints map to §8 operations in services/registry/contract.md:
- POST   /api/registry/entries                          → register()
- GET    /api/registry/entries/{registry_id}            → get()
- GET    /api/registry/strategies/{strategy_id}/entries → list_by_strategy()
- POST   /api/registry/entries/{registry_id}/advance    → advance_artifact_state()
- GET    /api/registry/strategies/{strategy_id}/latest-approved → resolve_latest_approved()
- GET    /api/registry/strategies/{strategy_id}/deployment-view → resolve_deployment_view()

Internal (deployment service calls):
- PUT    /api/registry/entries/{registry_id}/deployment-summary → update_deployment_summary()
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth

from .models import (
    ArtifactType,
    ArtifactState,
    BUILTIN_TENANT,
    DeploymentStage,
    DeploymentView,
    Lineage,
    RegistryEntryCreate,
    RegistryEntryView,
    StorageBackend,
    StorageRef,
)
from .split_api import RegistryConflictError, RegistryError, RegistryNotFoundError, RegistryService
from .storage import get_store
from .strategy_artifact import (
    build_strategy_artifact_registry_payload,
    ensure_builtin_strategy_artifacts,
    mutate_strategy_artifact,
    strategy_artifact_checksum,
    validate_strategy_artifact,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def registry_lifespan(_: FastAPI):
    """Fail service startup if a checked-in built-in cannot be registered."""
    get_registry_service()
    yield


app = FastAPI(
    title="Pantheon Registry Service",
    description="Artifact-state and deployment-stage split API per BP5-SVC-002",
    version="0.1.0",
    lifespan=registry_lifespan,
)


def get_registry_service() -> RegistryService:
    """Build the service and idempotently expose checked-in built-in artifacts."""
    registry_service = RegistryService(get_store())
    ensure_builtin_strategy_artifacts(registry_service)
    return registry_service


# -- Request/Response wrappers --------------------------------------------

class AdvanceRequest(BaseModel):
    target_state: ArtifactState
    approver: Optional[str] = None
    approval_decision_id: Optional[str] = None


class DeploymentSummaryUpdate(BaseModel):
    current_stage: DeploymentStage
    deployment_plan_id: Optional[str] = None
    runtime_binding_id: Optional[str] = None


class MetadataUpdateRequest(BaseModel):
    """Allowed metadata update with CAS — architecture-resumption-sa-sd.md §3.2.

    ``expected_metadata`` must equal the entry's current durable metadata
    (``None`` means the caller expects no metadata set yet); this is the
    caller's base snapshot binding, not a value fetched fresh at write time.
    ``command_key`` makes an identical retried request an idempotent replay.
    """
    expected_metadata: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    command_key: Optional[str] = None


class RegistryEntryOrDraftRequest(BaseModel):
    """POST /api/registry/entries body — either a name-only draft or a full
    typed registration.

    architecture-resumption-sa-sd.md §3.2 requires "name-only draft creation
    with stable strategy identity" as a distinct capability from full typed
    registration (reviewer finding 3): a bare ``{"name": "..."}`` body was
    previously rejected with a 422 because the route bound directly to the
    fully-typed ``RegistryEntryCreate`` dataclass, which requires
    artifact_type/strategy_id/version. Supplying ``name`` XOR the full typed
    fields selects which of the two draft-kinds this request is; mixing them
    is rejected explicitly rather than silently picking one.
    """
    name: Optional[str] = None
    artifact_type: Optional[ArtifactType] = None
    strategy_id: Optional[str] = None
    version: Optional[str] = None
    artifact_state: ArtifactState = ArtifactState.DRAFT
    lineage: Optional[dict[str, Any]] = None
    storage_ref: Optional[dict[str, Any]] = None
    checksum: Optional[str] = None
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    rollback_target: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class StrategySpecRegisterRequest(BaseModel):
    strategy_id: str
    version: str
    artifact_state: ArtifactState = ArtifactState.DRAFT
    registry_id: Optional[str] = None
    source_seed_id: Optional[str] = None
    lineage: Optional[dict[str, Any]] = None
    storage_ref: Optional[dict[str, Any]] = None
    checksum: Optional[str] = None
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    rollback_target: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    strategy_spec: Optional[dict[str, Any]] = None


class StrategyArtifactRegisterRequest(BaseModel):
    registry_id: Optional[str] = None
    artifact_state: ArtifactState = ArtifactState.CANDIDATE
    strategy_artifact: dict[str, Any]
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    rollback_target: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class StrategyArtifactMutationRequest(BaseModel):
    new_artifact_id: str
    new_version: str
    parameter_updates: dict[str, Any]
    source_run_ids: list[str]


# -- Error handling -------------------------------------------------------

@app.exception_handler(RegistryNotFoundError)
async def registry_not_found_handler(request: Request, exc: RegistryNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RegistryConflictError)
async def registry_conflict_handler(request: Request, exc: RegistryConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RegistryError)
async def registry_error_handler(request: Request, exc: RegistryError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


_REGISTRY_WRITE_ROLES = ("operator", "registry-writer", "admin")
_REGISTRY_READ_ROLES = _REGISTRY_WRITE_ROLES + ("registry-reader",)


def _registry_auth_env() -> dict[str, str]:
    """Map REGISTRY_* / fallback RUNTIME_* env vars onto validate_request_auth's
    expected PANTHEON_RUNTIME_* keys, mirroring services/governance/main.py's
    ``_governance_auth_env`` so each service can configure its own issuer/
    audience/secret without a second JWT engine."""
    return {
        "PANTHEON_RUNTIME_AUTH_MODE": os.getenv("PANTHEON_REGISTRY_AUTH_MODE") or os.getenv("PANTHEON_RUNTIME_AUTH_MODE") or "permissive",
        "PANTHEON_RUNTIME_JWT_SECRET": os.getenv("PANTHEON_REGISTRY_JWT_SECRET") or os.getenv("PANTHEON_RUNTIME_JWT_SECRET", ""),
        "PANTHEON_RUNTIME_JWT_ISSUER": os.getenv("PANTHEON_REGISTRY_JWT_ISSUER") or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER", ""),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": os.getenv("PANTHEON_REGISTRY_JWT_AUDIENCE") or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE", ""),
        "PANTHEON_RUNTIME_DEFAULT_ROLE": os.getenv("PANTHEON_REGISTRY_DEFAULT_ROLE") or os.getenv("PANTHEON_RUNTIME_DEFAULT_ROLE", "operator"),
        "PANTHEON_RUNTIME_MFA_REQUIRED": os.getenv("PANTHEON_REGISTRY_MFA_REQUIRED") or os.getenv("PANTHEON_RUNTIME_MFA_REQUIRED", "false"),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": os.getenv("PANTHEON_REGISTRY_ROLE_CLAIMS") or os.getenv("PANTHEON_RUNTIME_ROLE_CLAIMS", ""),
        "PANTHEON_RUNTIME_MFA_CLAIMS": os.getenv("PANTHEON_REGISTRY_MFA_CLAIMS") or os.getenv("PANTHEON_RUNTIME_MFA_CLAIMS", ""),
        "PANTHEON_RUNTIME_MFA_VALUES": os.getenv("PANTHEON_REGISTRY_MFA_VALUES") or os.getenv("PANTHEON_RUNTIME_MFA_VALUES", ""),
    }


_REQUIRED_JWT_CLAIMS = ("sub", "exp")
_TENANT_CLAIM_NAMES = ("tenant", "tenant_id")


def _require_verified_identity(ctx: AuthContext) -> None:
    """Enforce the full verified-identity claim set for a JWT caller.

    A signature-valid JWT that omits subject/tenant/role/expiry claims is
    still an incomplete identity — signature validity alone is not identity
    completeness (architecture-resumption-sa-sd.md §3.3). Permissive-mode
    structured legacy tokens (``actor_id:role1,role2`` — the test-double form
    used across this service's unit tests) are exempt from this stricter
    claim-completeness check; only a real JWT is held to it, since a
    structured token has no claims to omit in the first place.
    """
    if ctx.token_kind != "jwt":
        return
    missing = [claim for claim in _REQUIRED_JWT_CLAIMS if not ctx.claims.get(claim)]
    if not any(ctx.claims.get(name) for name in _TENANT_CLAIM_NAMES):
        missing.append("tenant")
    # Checked directly against the raw claims (not the resolved ctx.roles),
    # because validate_request_auth assigns PANTHEON_RUNTIME_DEFAULT_ROLE when
    # no role claim is present at all — that default-role fallback would
    # otherwise mask a JWT that never carried a role claim in the first place.
    if not (ctx.claims.get("roles") or ctx.claims.get("role")):
        missing.append("role")
    if missing:
        raise HTTPException(
            status_code=403,
            detail=(
                "Verified JWT is missing required identity claims: "
                f"{sorted(set(missing))}"
            ),
        )


def _require_production_auth_configuration(env: dict[str, str]) -> None:
    """Fail closed (500 config error) rather than serve requests permissively
    once the durable Postgres backend is selected.

    Reviewer finding 2: the auth env defaults (``permissive`` mode, empty
    issuer/audience) exist so unit tests can run without any configuration —
    but that same silent default let a live deployment pointed at a real
    Postgres backend accept an unsigned structured Bearer token, and let a
    strict-mode deployment that forgot to configure an expected issuer/
    audience accept a signed token asserting *any* issuer/audience. Once the
    in-memory test double is not what's selected, auth must be explicitly
    strict with both an expected issuer and audience configured — no
    implicit "trust whatever mode the environment happened to leave unset".
    """
    try:
        from .pg_store import _registry_backend
    except ImportError:  # pragma: no cover - defensive; pg_store always importable here
        return
    if _registry_backend() != "postgres":
        return
    if env.get("PANTHEON_RUNTIME_AUTH_MODE", "").strip().lower() != "strict":
        raise HTTPException(
            status_code=500,
            detail=(
                "Registry backend is postgres; PANTHEON_REGISTRY_AUTH_MODE=strict is "
                "required before serving requests (no permissive-mode fallback against a "
                "durable production backend)."
            ),
        )
    if not env.get("PANTHEON_RUNTIME_JWT_ISSUER", "").strip() or not env.get(
        "PANTHEON_RUNTIME_JWT_AUDIENCE", ""
    ).strip():
        raise HTTPException(
            status_code=500,
            detail=(
                "Registry backend is postgres; PANTHEON_REGISTRY_JWT_ISSUER and "
                "PANTHEON_REGISTRY_JWT_AUDIENCE must both be configured — strict mode "
                "without an expected issuer/audience would accept a signed token "
                "asserting any issuer/audience."
            ),
        )


def _authenticate_registry(
    authorization: Optional[str], *, required_roles: tuple[str, ...],
) -> AuthContext:
    """Shared verified-caller auth path for both read and write Registry routes.

    Always requires a well-formed Bearer token (an absent/malformed
    Authorization header is rejected regardless of mode) — reviewer finding
    1: reads were previously anonymous. Production sets
    ``PANTHEON_REGISTRY_AUTH_MODE=strict`` (docker-compose.yml /
    docker-compose.control.yml) per architecture-resumption-sa-sd.md §3.3's
    "no anonymous compatibility path" requirement, and is additionally
    enforced by :func:`_require_production_auth_configuration` once the
    durable backend is selected.
    """
    env = _registry_auth_env()
    _require_production_auth_configuration(env)
    try:
        ctx = validate_request_auth(
            authorization=authorization,
            required_roles=required_roles,
            env=env,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _require_verified_identity(ctx)
    _reject_reserved_tenant_claim(ctx)
    return ctx


def _authenticate_registry_write(authorization: Optional[str]) -> AuthContext:
    """Enforce verified-caller auth on a Registry mutation route.

    Returns the resolved ``AuthContext`` so callers can bind the verified
    actor onto the write they are about to perform (see
    :func:`_actor_context`) instead of discarding it.
    """
    return _authenticate_registry(authorization, required_roles=_REGISTRY_WRITE_ROLES)


def _authenticate_registry_read(authorization: Optional[str]) -> AuthContext:
    """Enforce verified-caller auth on a Registry read route.

    Reviewer finding 1: every read route (GET/list) was reachable
    anonymously — a cross-tenant caller (or no caller at all) could read any
    entry. Reads require the same verified-identity bar as writes, plus a
    broader role allowlist (``registry-reader``) for read-only integrations.
    """
    return _authenticate_registry(authorization, required_roles=_REGISTRY_READ_ROLES)


def _reject_reserved_tenant_claim(ctx: AuthContext) -> None:
    """A caller-supplied tenant claim can never equal the reserved builtin
    marker — that would let a forged/misconfigured caller pose as the
    registry's own bootstrap identity and read/write built-in entries as if
    it owned them."""
    for name in _TENANT_CLAIM_NAMES:
        if ctx.claims.get(name) == BUILTIN_TENANT:
            raise HTTPException(
                status_code=403,
                detail=f"tenant claim {BUILTIN_TENANT!r} is reserved and cannot be asserted by a caller.",
            )


def _actor_context(ctx: AuthContext) -> dict[str, Any]:
    """Project a verified AuthContext into the durable ``last_actor`` audit
    binding recorded on the RegistryEntry (see models.RegistryEntry.last_actor)."""
    tenant = None
    for name in _TENANT_CLAIM_NAMES:
        tenant = ctx.claims.get(name)
        if tenant:
            break
    return {
        "actor_id": ctx.actor_id,
        "roles": sorted(ctx.roles),
        "tenant": tenant,
        "token_kind": ctx.token_kind,
    }


def _caller_tenant(ctx: AuthContext) -> Optional[str]:
    for name in _TENANT_CLAIM_NAMES:
        tenant = ctx.claims.get(name)
        if tenant:
            return str(tenant)
    return None


def _can_read(ctx: AuthContext, view: RegistryEntryView) -> bool:
    """Scoped-read authorization — reviewer finding 1.

    Builtins (``owner_tenant == BUILTIN_TENANT``) are public reference data:
    any verified caller may read them. An ``admin`` caller may read across
    tenants. Otherwise the caller's verified tenant must match the entry's
    immutable ``owner_tenant`` exactly; an untenanted (legacy/permissive-mode)
    entry is only visible to an equally untenanted caller — a verified
    tenant-scoped caller gets no implicit access to it
    ("missing tenant legacy rows are not globally authorized",
    architecture-resumption-sa-sd.md §3.1).
    """
    owner_tenant = view.entry.owner_tenant
    if owner_tenant == BUILTIN_TENANT:
        return True
    if "admin" in ctx.roles:
        return True
    return _caller_tenant(ctx) == owner_tenant


def _authorize_read(ctx: AuthContext, view: RegistryEntryView) -> RegistryEntryView:
    if not _can_read(ctx, view):
        raise HTTPException(
            status_code=403,
            detail=f"Registry entry {view.entry.registry_id!r} is not visible to this caller's tenant.",
        )
    return view


def _authorize_write(ctx: AuthContext, view: RegistryEntryView) -> RegistryEntryView:
    """Scoped-write authorization — reviewer finding 1.

    Builtins can never be mutated through a caller-facing route, regardless
    of role or tenant — they are only ever written by the registry's own
    bootstrap code (services/registry/strategy_artifact.py
    ``ensure_builtin_strategy_artifacts``), never by an HTTP caller.
    """
    if view.entry.owner_tenant == BUILTIN_TENANT:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Registry entry {view.entry.registry_id!r} is a built-in artifact and "
                "cannot be mutated by a caller."
            ),
        )
    if not _can_read(ctx, view):
        raise HTTPException(
            status_code=403,
            detail=f"Registry entry {view.entry.registry_id!r} is not writable by this caller's tenant.",
        )
    return view


def _strategy_spec_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _strategy_spec_register_payload(body: StrategySpecRegisterRequest) -> RegistryEntryCreate:
    strategy_id = body.strategy_id.strip()
    if not strategy_id:
        raise RegistryError("strategy_id is required")

    strategy_spec = body.strategy_spec
    if strategy_spec is not None:
        embedded_strategy_id = str(strategy_spec.get("strategy_id") or "").strip()
        if embedded_strategy_id and embedded_strategy_id != strategy_id:
            raise RegistryError(
                "Inline StrategySpec strategy_id must match the registry strategy_id."
            )

    lineage = Lineage.from_dict(body.lineage or {})
    source_seed_id = str(body.source_seed_id or "").strip()
    if source_seed_id:
        source_run_ids = list(lineage.source_run_ids or [])
        if source_seed_id not in source_run_ids:
            source_run_ids.append(source_seed_id)
        lineage.source_run_ids = source_run_ids

    if lineage.is_empty():
        raise RegistryError(
            "StrategySpec registry entries require lineage. "
            "Provide lineage or source_seed_id before registering the artifact."
        )

    if body.storage_ref:
        storage_ref = StorageRef.from_dict(body.storage_ref)
    elif strategy_spec is not None:
        storage_ref = StorageRef(
            backend=StorageBackend.INLINE,
            path="$.entry.metadata.strategy_spec",
        )
    else:
        raise RegistryError(
            "StrategySpec registry entries require storage_ref unless an inline strategy_spec is provided."
        )

    checksum = str(body.checksum or "").strip()
    if strategy_spec is not None:
        computed_checksum = _strategy_spec_checksum(strategy_spec)
        if checksum and checksum != computed_checksum:
            raise RegistryError(
                "StrategySpec registry entry checksum does not match the computed checksum "
                f"of the supplied strategy_spec payload (expected {computed_checksum!r}, "
                f"got {checksum!r})."
            )
        checksum = checksum or computed_checksum
    if not checksum:
        raise RegistryError(
            "StrategySpec registry entries require checksum unless an inline strategy_spec is provided."
        )

    metadata = dict(body.metadata or {})
    if source_seed_id:
        metadata.setdefault("source_seed_id", source_seed_id)
    if strategy_spec is not None:
        # Always bind metadata["strategy_spec"] to the exact payload the
        # checksum above was computed from — never a caller-supplied
        # metadata.strategy_spec that could differ (reviewer finding 3: a
        # ``setdefault`` here let a caller pass both a top-level
        # ``strategy_spec`` (checksummed) and a *different*
        # ``metadata.strategy_spec`` that setdefault then silently kept,
        # producing a stored content/checksum mismatch).
        metadata["strategy_spec"] = strategy_spec

    return RegistryEntryCreate(
        artifact_type=ArtifactType.STRATEGY_SPEC,
        strategy_id=strategy_id,
        version=body.version,
        artifact_state=body.artifact_state,
        lineage=lineage,
        storage_ref=storage_ref,
        checksum=checksum,
        producer_run_id=body.producer_run_id or source_seed_id or None,
        evaluation_summary=body.evaluation_summary,
        rollback_target=body.rollback_target,
        metadata=metadata,
    )


def _validate_strategy_spec_version_lineage(
    registry_service: RegistryService,
    strategy_id: str,
    version: str,
    lineage: Lineage,
) -> None:
    """Reject an out-of-sequence StrategySpec version with no valid parent linkage.

    A StrategySpec revision is either the first version for its strategy_id,
    an exact replay of an already-registered version (idempotency is handled
    by ``register_if_absent``/``_ensure_strategy_spec_registration_matches``,
    not here), a legitimate immediate-next semver bump from the current
    latest known version, or a version that declares ``parent_registry_ids``
    naming an existing StrategySpec entry for this strategy_id. An arbitrary
    version like "9.9.9" with none of the above must be rejected — accepting
    it would let a caller silently jump the immutable revision sequence.
    """
    existing_specs = [
        view
        for view in registry_service.list_by_strategy(strategy_id)
        if view.entry.artifact_type == ArtifactType.STRATEGY_SPEC
    ]
    if not existing_specs:
        return

    existing_versions = {view.entry.version for view in existing_specs}
    if version in existing_versions:
        return

    def _parse_ver(v: str) -> tuple[int, int, int]:
        parts = tuple(int(x) for x in v.split("."))
        return parts  # type: ignore[return-value]

    parent_ids = lineage.parent_registry_ids or []
    if parent_ids:
        valid_parents = {view.entry.registry_id: view.entry.version for view in existing_specs}
        matching = [pid for pid in parent_ids if pid in valid_parents]
        if not matching:
            raise RegistryError(
                f"StrategySpec version {version!r} for strategy_id={strategy_id!r} declares "
                "parent_registry_ids that do not reference any existing StrategySpec entry "
                "for this strategy."
            )
        # A parent link names an actual base digest/version; the new
        # revision must move strictly forward from it, never backward
        # (reviewer finding 4: a valid parent_registry_ids link previously
        # let a caller "downgrade" to e.g. 0.0.1 from a 1.0.0 base with no
        # version-direction check at all).
        newest_parent_version = max((valid_parents[pid] for pid in matching), key=_parse_ver)
        if _parse_ver(version) <= _parse_ver(newest_parent_version):
            raise RegistryError(
                f"StrategySpec version {version!r} for strategy_id={strategy_id!r} must be "
                f"strictly greater than its linked parent's version {newest_parent_version!r}; "
                "a revision cannot downgrade or restate its own base version."
            )
        return

    latest = max(existing_versions, key=_parse_ver)
    major, minor, patch = _parse_ver(latest)
    valid_next = {
        (major + 1, 0, 0),
        (major, minor + 1, 0),
        (major, minor, patch + 1),
    }
    if _parse_ver(version) not in valid_next:
        raise RegistryError(
            f"StrategySpec version {version!r} is not a valid next revision from the "
            f"latest known version {latest!r} for strategy_id={strategy_id!r}, and declares "
            "no parent_registry_ids linking it to an existing entry. Valid next versions: "
            f"{sorted('.'.join(str(p) for p in v) for v in valid_next)}."
        )


def _ensure_strategy_spec_view(view: RegistryEntryView, registry_id: str) -> RegistryEntryView:
    if view.entry.artifact_type != ArtifactType.STRATEGY_SPEC:
        raise RegistryNotFoundError(f"StrategySpec registry entry not found: {registry_id}")
    return view


def _ensure_strategy_spec_registration_matches(
    view: RegistryEntryView,
    create_payload: RegistryEntryCreate,
    registry_id: str,
) -> RegistryEntryView:
    """Validate StrategySpec create-if-absent replay against existing content."""

    if view.entry.registry_id != registry_id:
        # register_if_absent's _REVISION_UNIQUE_FIELDS collision: a
        # *different* registry_id already owns this exact
        # (strategy_id, version, artifact_type) tuple — this is a genuine
        # conflict (immutable revision identity), not a same-key replay to
        # validate content against.
        raise RegistryConflictError(
            f"strategy_id={create_payload.strategy_id!r} version={create_payload.version!r} "
            f"is already registered as registry_id={view.entry.registry_id!r}, not the "
            f"requested registry_id={registry_id!r}."
        )
    view = _ensure_strategy_spec_view(view, registry_id)
    entry = view.entry
    if (
        entry.strategy_id != create_payload.strategy_id
        or entry.version != create_payload.version
        or entry.lineage.to_dict() != create_payload.lineage.to_dict()
        or entry.storage_ref.to_dict() != create_payload.storage_ref.to_dict()
        or entry.checksum != create_payload.checksum
        or entry.producer_run_id != create_payload.producer_run_id
        or entry.evaluation_summary != create_payload.evaluation_summary
        or entry.rollback_target != create_payload.rollback_target
        or entry.metadata != create_payload.metadata
    ):
        raise RegistryError(
            f"StrategySpec registry_id already exists with different content: {registry_id}"
        )
    return view


def _ensure_strategy_artifact_view(
    view: RegistryEntryView,
    registry_id: str,
) -> RegistryEntryView:
    if not _is_strategy_artifact_view(view):
        raise RegistryNotFoundError(
            f"StrategyArtifact registry entry not found: {registry_id}"
        )
    return view


def _is_strategy_artifact_view(view: RegistryEntryView) -> bool:
    """Require a valid embedded payload and a fully consistent envelope."""
    entry = view.entry
    embedded = (entry.metadata or {}).get("strategy_artifact")
    if (
        entry.artifact_type != ArtifactType.EXECUTION_BUNDLE
        or not isinstance(embedded, dict)
    ):
        return False
    try:
        validate_strategy_artifact(embedded)
        checksum = strategy_artifact_checksum(embedded)
    except RegistryError:
        return False
    return bool(
        entry.registry_id == embedded["artifact_id"]
        and entry.strategy_id == embedded["strategy_id"]
        and entry.version == embedded["version"]
        and entry.lineage.to_dict() == embedded["lineage"]
        and entry.checksum == checksum
        and entry.storage_ref.backend == StorageBackend.INLINE
        and entry.storage_ref.path == "$.entry.metadata.strategy_artifact"
    )


def _strategy_artifact_registration(
    payload: StrategyArtifactRegisterRequest,
) -> dict[str, Any]:
    return {
        "registry_id": payload.registry_id,
        "artifact_state": payload.artifact_state.value,
        "strategy_artifact": payload.strategy_artifact,
        "producer_run_id": payload.producer_run_id,
        "evaluation_summary": payload.evaluation_summary,
        "rollback_target": payload.rollback_target,
        "metadata": payload.metadata,
    }


def _register_strategy_artifact(
    registry_service: RegistryService,
    registration: dict[str, Any],
    *,
    actor: Optional[dict[str, Any]] = None,
) -> RegistryEntryView:
    registry_id, create_payload = build_strategy_artifact_registry_payload(registration)
    view, created = registry_service.register_if_absent(create_payload, registry_id, actor=actor)
    if created:
        return view
    if view.entry.registry_id != registry_id:
        raise RegistryConflictError(
            f"strategy_id={create_payload.strategy_id!r} version={create_payload.version!r} "
            f"is already registered as registry_id={view.entry.registry_id!r}, not the "
            f"requested registry_id={registry_id!r}."
        )
    view = _ensure_strategy_artifact_view(view, registry_id)
    expected_artifact = (create_payload.metadata or {}).get("strategy_artifact")
    existing_artifact = (view.entry.metadata or {}).get("strategy_artifact")
    if (
        view.entry.checksum != create_payload.checksum
        or existing_artifact != expected_artifact
        or view.entry.strategy_id != create_payload.strategy_id
        or view.entry.version != create_payload.version
        or view.entry.lineage.to_dict() != create_payload.lineage.to_dict()
        or view.entry.storage_ref.to_dict() != create_payload.storage_ref.to_dict()
        or view.entry.producer_run_id != create_payload.producer_run_id
        or view.entry.evaluation_summary != create_payload.evaluation_summary
        or view.entry.rollback_target != create_payload.rollback_target
        or view.entry.metadata != create_payload.metadata
    ):
        raise RegistryError(
            f"StrategyArtifact registry_id already exists with different content: {registry_id}"
        )
    return view


# -- Registry entry endpoints (§8 operations) -----------------------------

def _resolve_entry_or_draft_payload(
    payload: RegistryEntryOrDraftRequest,
) -> tuple[RegistryEntryCreate, str]:
    """Resolve the two POST /api/registry/entries draft-kinds explicitly.

    A name-only draft (``name`` set, no typed fields) gets a synthesized-but-
    stable strategy identity and registers as a minimal StrategySpec draft —
    architecture-resumption-sa-sd.md §3.2's "name-only draft creation with
    stable strategy identity". A full typed submission (artifact_type,
    strategy_id, version all set) registers exactly as before. Supplying a
    mix of the two is rejected rather than guessing which one was intended.
    """
    name = str(payload.name or "").strip()
    has_full_typed_fields = bool(payload.artifact_type and payload.strategy_id and payload.version)
    has_any_typed_field = bool(payload.artifact_type or payload.strategy_id or payload.version)

    if not has_full_typed_fields:
        if not name:
            raise RegistryError(
                "POST /api/registry/entries requires either a name-only draft "
                "({'name': '...'}) or the full typed fields (artifact_type, "
                "strategy_id, version)."
            )
        if has_any_typed_field:
            raise RegistryError(
                "POST /api/registry/entries cannot mix a name-only draft with partial "
                "typed fields; supply 'name' alone or all of artifact_type/strategy_id/version."
            )
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "draft"
        strategy_id = f"draft-{slug}-{uuid.uuid4().hex[:8]}"
        registry_id = f"reg-{strategy_id}-0.0.1-{uuid.uuid4().hex[:8]}"
        create_payload = RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=strategy_id,
            version="0.0.1",
            artifact_state=ArtifactState.DRAFT,
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.draft"),
            checksum=_strategy_spec_checksum({"name": name, "draft_kind": "name_only"}),
            metadata={"name": name, "draft_kind": "name_only"},
        )
        return create_payload, registry_id

    registry_id = f"reg-{payload.strategy_id}-{payload.version}-{uuid.uuid4().hex[:8]}"
    create_payload = RegistryEntryCreate(
        artifact_type=payload.artifact_type,
        strategy_id=payload.strategy_id,
        version=payload.version,
        artifact_state=payload.artifact_state,
        lineage=Lineage.from_dict(payload.lineage or {}),
        storage_ref=StorageRef.from_dict(payload.storage_ref) if payload.storage_ref else None,
        checksum=payload.checksum or "",
        producer_run_id=payload.producer_run_id,
        evaluation_summary=payload.evaluation_summary,
        rollback_target=payload.rollback_target,
        metadata=payload.metadata,
    )
    return create_payload, registry_id


@app.post("/api/registry/entries", response_model=RegistryEntryView)
async def register_entry(
    payload: RegistryEntryOrDraftRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Create a new draft or candidate registry entry (name-only draft, or full typed)."""
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        create_payload, registry_id = _resolve_entry_or_draft_payload(payload)
        return registry_service.register(create_payload, registry_id, actor=_actor_context(ctx))
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/registry/entries/{registry_id}", response_model=RegistryEntryView)
async def get_entry(registry_id: str, authorization: Optional[str] = Header(default=None)):
    """Read one registry entry with derived deployment_stage (tenant-scoped)."""
    ctx = _authenticate_registry_read(authorization)
    registry_service = get_registry_service()
    try:
        view = registry_service.get(registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _authorize_read(ctx, view)


@app.get(
    "/api/registry/strategies/{strategy_id}/entries",
    response_model=list[RegistryEntryView],
)
async def list_entries(strategy_id: str, authorization: Optional[str] = Header(default=None)):
    """Enumerate all versions within a strategy family visible to this caller's tenant."""
    ctx = _authenticate_registry_read(authorization)
    views = get_registry_service().list_by_strategy(strategy_id)
    return [view for view in views if _can_read(ctx, view)]


@app.post(
    "/api/registry/entries/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_state(
    registry_id: str,
    body: AdvanceRequest,
    authorization: Optional[str] = Header(default=None),
):
    """
    Advance artifact_state through governed transition.

    Does NOT modify deployment_stage — that is owned by DeploymentPlan.
    """
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        current = registry_service.get(registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _authorize_write(ctx, current)
    try:
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch(
    "/api/registry/entries/{registry_id}/metadata",
    response_model=RegistryEntryView,
)
async def update_metadata(
    registry_id: str,
    body: MetadataUpdateRequest,
    response: Response,
    authorization: Optional[str] = Header(default=None),
):
    """Allowed metadata update with CAS.

    This is the operator draft-metadata record kind (§3.2 of
    architecture-resumption-sa-sd.md): it never fabricates or upgrades a
    validated StrategySpec/artifact_state, and it fails closed (409) when
    ``expected_metadata`` does not match the entry's current durable value.
    Requires a verified caller (services.runtime_auth_inbound) — see
    _authenticate_registry_write. Also enforces tenant/builtin write scoping
    (reviewer finding 1) before attempting the CAS.
    """
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        current = registry_service.get(registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _authorize_write(ctx, current)
    try:
        view, replayed = registry_service.update_metadata(
            registry_id,
            expected_metadata=body.expected_metadata,
            new_metadata=body.metadata,
            command_key=body.command_key,
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    response.headers["X-Idempotent-Replay"] = "true" if replayed else "false"
    return view


@app.get(
    "/api/registry/strategies/{strategy_id}/latest-approved",
    response_model=RegistryEntryView,
)
async def latest_approved(strategy_id: str, authorization: Optional[str] = Header(default=None)):
    """Return the newest approved entry for a strategy family (requires a verified caller).

    Note: this is a cross-version aggregate read; it enforces authentication
    but (unlike the single-entry GET routes) does not yet filter the
    resolved entry by tenant scope — see the per-entry scoping on
    ``get_entry``/``list_entries`` for the enforced boundary.
    """
    _authenticate_registry_read(authorization)
    result = get_registry_service().resolve_latest_approved(strategy_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No approved entry for strategy: {strategy_id}")
    return result


@app.get(
    "/api/registry/strategies/{strategy_id}/deployment-view",
    response_model=DeploymentView,
)
async def deployment_view(strategy_id: str, authorization: Optional[str] = Header(default=None)):
    """Return the derived deployment-stage view for a strategy (requires a verified caller)."""
    _authenticate_registry_read(authorization)
    return get_registry_service().resolve_deployment_view(strategy_id)


# -- Internal: deployment summary projection ------------------------------

@app.put(
    "/api/registry/entries/{registry_id}/deployment-summary",
    response_model=RegistryEntryView,
)
async def update_deployment_summary(
    registry_id: str,
    body: DeploymentSummaryUpdate,
    authorization: Optional[str] = Header(default=None),
):
    """
    Update the derived deployment_summary on a registry entry.

    Called by the deployment service when a stage transition occurs.
    The registry does NOT own deployment stage truth — this is a read-model projection.
    """
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        current = registry_service.get(registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _authorize_write(ctx, current)
    try:
        return registry_service.update_deployment_summary(
            registry_id,
            current_stage=body.current_stage,
            deployment_plan_id=body.deployment_plan_id,
            runtime_binding_id=body.runtime_binding_id,
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- StrategySpec registry facade ----------------------------------------

@app.post("/api/registry/strategy-specs", response_model=RegistryEntryView)
async def register_strategy_spec(
    payload: StrategySpecRegisterRequest,
    authorization: Optional[str] = Header(default=None),
):
    """
    Register a StrategySpec artifact through a StrategySpec-specific facade.

    This keeps the registry lifecycle generic while making the research-plane
    StrategySpec path explicit: artifact_type is forced to strategy_spec, lineage
    is required, and inline StrategySpec payloads get deterministic checksums.
    """
    ctx = _authenticate_registry_write(authorization)
    registry_id = (
        payload.registry_id
        or f"reg-strategy-spec-{payload.strategy_id}-{payload.version}-{uuid.uuid4().hex[:8]}"
    )
    registry_service = get_registry_service()
    try:
        create_payload = _strategy_spec_register_payload(payload)
        _validate_strategy_spec_version_lineage(
            registry_service, payload.strategy_id, payload.version, create_payload.lineage,
        )
        view, created = registry_service.register_if_absent(
            create_payload,
            registry_id,
            actor=_actor_context(ctx),
        )
        if created:
            return _ensure_strategy_spec_view(view, registry_id)
        return _ensure_strategy_spec_registration_matches(
            view,
            create_payload,
            registry_id,
        )
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/registry/strategy-specs/{registry_id}", response_model=RegistryEntryView)
async def get_strategy_spec_entry(registry_id: str, authorization: Optional[str] = Header(default=None)):
    """Read one StrategySpec registry entry (tenant-scoped)."""
    ctx = _authenticate_registry_read(authorization)
    registry_service = get_registry_service()
    try:
        view = _ensure_strategy_spec_view(registry_service.get(registry_id), registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _authorize_read(ctx, view)


@app.get(
    "/api/registry/strategies/{strategy_id}/strategy-specs",
    response_model=list[RegistryEntryView],
)
async def list_strategy_spec_entries(
    strategy_id: str,
    artifact_state: Optional[ArtifactState] = None,
    authorization: Optional[str] = Header(default=None),
):
    """List StrategySpec artifact versions for one strategy family (tenant-scoped)."""
    ctx = _authenticate_registry_read(authorization)
    views = [
        view
        for view in get_registry_service().list_by_strategy(strategy_id)
        if view.entry.artifact_type == ArtifactType.STRATEGY_SPEC and _can_read(ctx, view)
    ]
    if artifact_state is not None:
        views = [view for view in views if view.entry.artifact_state == artifact_state]
    return views


@app.post(
    "/api/registry/strategy-specs/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_strategy_spec_state(
    registry_id: str,
    body: AdvanceRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Advance only StrategySpec registry entries through the governed artifact lifecycle."""
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        current = _ensure_strategy_spec_view(registry_service.get(registry_id), registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _authorize_write(ctx, current)
    try:
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- Evolvable StrategyArtifact registry facade (EVOLOOP-003) ------------

@app.post("/api/registry/strategy-artifacts", response_model=RegistryEntryView)
async def register_strategy_artifact(
    payload: StrategyArtifactRegisterRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Register a schema-valid StrategyArtifact as an execution_bundle."""
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        return _register_strategy_artifact(
            registry_service,
            _strategy_artifact_registration(payload),
            actor=_actor_context(ctx),
        )
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/registry/strategy-artifacts/{registry_id}",
    response_model=RegistryEntryView,
)
async def get_strategy_artifact_entry(registry_id: str, authorization: Optional[str] = Header(default=None)):
    """Read one execution_bundle carrying a StrategyArtifact overlay (tenant-scoped; builtins are public)."""
    ctx = _authenticate_registry_read(authorization)
    registry_service = get_registry_service()
    try:
        view = _ensure_strategy_artifact_view(
            registry_service.get(registry_id), registry_id
        )
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _authorize_read(ctx, view)


@app.get(
    "/api/registry/strategies/{strategy_id}/strategy-artifacts",
    response_model=list[RegistryEntryView],
)
async def list_strategy_artifact_entries(
    strategy_id: str,
    artifact_state: Optional[ArtifactState] = None,
    authorization: Optional[str] = Header(default=None),
):
    """List StrategyArtifact revisions for a strategy family (tenant-scoped; builtins are public)."""
    ctx = _authenticate_registry_read(authorization)
    views = [
        view
        for view in get_registry_service().list_by_strategy(strategy_id)
        if _is_strategy_artifact_view(view) and _can_read(ctx, view)
    ]
    if artifact_state is not None:
        views = [view for view in views if view.entry.artifact_state == artifact_state]
    return views


@app.post(
    "/api/registry/strategy-artifacts/{registry_id}/mutate",
    response_model=RegistryEntryView,
)
async def mutate_strategy_artifact_entry(
    registry_id: str,
    body: StrategyArtifactMutationRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Create a candidate child revision from declared mutable parameters."""
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        parent_view = _ensure_strategy_artifact_view(
            registry_service.get(registry_id), registry_id
        )
        _authorize_read(ctx, parent_view)  # mutate reads (not overwrites) the parent
        parent_artifact = (parent_view.entry.metadata or {})["strategy_artifact"]
        child_artifact = mutate_strategy_artifact(
            parent_artifact,
            new_artifact_id=body.new_artifact_id,
            new_version=body.new_version,
            parameter_updates=body.parameter_updates,
            source_run_ids=body.source_run_ids,
            parent_registry_id=registry_id,
        )
        return _register_strategy_artifact(
            registry_service,
            {
                "registry_id": child_artifact["artifact_id"],
                "artifact_state": ArtifactState.CANDIDATE.value,
                "strategy_artifact": child_artifact,
                "producer_run_id": body.source_run_ids[-1]
                if body.source_run_ids
                else None,
            },
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post(
    "/api/registry/strategy-artifacts/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_strategy_artifact_state(
    registry_id: str,
    body: AdvanceRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Advance a StrategyArtifact through the generic governed lifecycle."""
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        current = _ensure_strategy_artifact_view(registry_service.get(registry_id), registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _authorize_write(ctx, current)
    try:
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- AllocationPolicyArtifact registry facade (MPOS-P1-ART-001) ----------
#
# Registers an AllocationPolicyArtifact produced by optimizer-svc into the
# registry governance lifecycle.  The registry does not import optimizer-svc;
# the caller embeds the artifact payload inline and we extract the lineage
# fields (provenance_refs → source_run_ids, conflict_resolution_log_id →
# source_strategy_spec_id) to satisfy the registry lineage requirement.
#
# Key field mapping
# -----------------
#   artifact_type          = allocation_policy
#   strategy_id            = capital_pool_id (pool-scoped strategy identity)
#   lineage.source_run_ids = provenance_refs (PersonaAllocationProposal ids)
#   lineage.source_strategy_spec_id = conflict_resolution_log_id
#   evaluation_summary     = synthesis evidence: method, sponsor, risk, scope
#   metadata               = full AllocationPolicyArtifact dict
#   checksum               = sha256 of the artifact JSON (caller-supplied or computed)
#   producer_run_id        = artifact_id from the optimizer run

_ALLOC_POLICY_ARTIFACT_REQUIRED = [
    "artifact_id",
    "capital_pool_id",
    "scope_ref",
    "sponsor_persona_id",
    "synthesis_method",
    "target_weights",
    "created_at",
    "provenance_refs",
    "conflict_resolution_log_id",
]

_SYNTHESIS_METHODS = {"weighted_fusion", "committee_override", "single_proposal"}


class AllocationPolicyArtifactRegisterRequest(BaseModel):
    """
    Request body for POST /api/registry/allocation-policy-artifacts.

    The caller embeds the full AllocationPolicyArtifact dict in
    ``allocation_policy_artifact``.  ``version`` must be semver (e.g. "1.0.0").
    ``registry_id`` is optional; one is generated if omitted.
    ``checksum`` is optional; computed from the artifact JSON if omitted.
    """
    version: str
    allocation_policy_artifact: dict[str, Any]
    registry_id: Optional[str] = None
    checksum: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


def _validate_alloc_policy_artifact(artifact: dict[str, Any]) -> None:
    missing = [k for k in _ALLOC_POLICY_ARTIFACT_REQUIRED if not artifact.get(k)]
    if missing:
        raise RegistryError(
            "AllocationPolicyArtifact is missing required fields: " + ", ".join(missing)
        )
    if artifact.get("synthesis_method") not in _SYNTHESIS_METHODS:
        raise RegistryError(
            f"allocation_policy_artifact.synthesis_method must be one of "
            f"{sorted(_SYNTHESIS_METHODS)}, got {artifact.get('synthesis_method')!r}"
        )
    if not isinstance(artifact.get("provenance_refs"), list) or not artifact["provenance_refs"]:
        raise RegistryError(
            "allocation_policy_artifact.provenance_refs must be a non-empty list of proposal ids"
        )
    if not isinstance(artifact.get("target_weights"), dict) or not artifact["target_weights"]:
        raise RegistryError(
            "allocation_policy_artifact.target_weights must be a non-empty symbol-to-weight mapping"
        )


def _alloc_policy_register_payload(
    body: AllocationPolicyArtifactRegisterRequest,
) -> RegistryEntryCreate:
    artifact = body.allocation_policy_artifact

    _validate_alloc_policy_artifact(artifact)

    capital_pool_id = str(artifact["capital_pool_id"]).strip()
    if not capital_pool_id:
        raise RegistryError("allocation_policy_artifact.capital_pool_id is required")

    provenance_refs = list(artifact["provenance_refs"])
    conflict_log_id = str(artifact["conflict_resolution_log_id"]).strip()

    lineage = Lineage(
        source_run_ids=provenance_refs,
        source_strategy_spec_id=conflict_log_id or None,
    )

    artifact_json_bytes = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    checksum = str(body.checksum or "").strip()
    if not checksum:
        import hashlib
        checksum = f"sha256:{hashlib.sha256(artifact_json_bytes).hexdigest()}"

    evaluation_summary: dict[str, Any] = {
        "conflict_resolution_log_id": conflict_log_id,
        "synthesis_method": artifact.get("synthesis_method"),
        "sponsor_persona_id": artifact.get("sponsor_persona_id"),
        "scope_ref": artifact.get("scope_ref"),
    }
    risk_budget = artifact.get("risk_budget")
    if risk_budget is not None:
        evaluation_summary["risk_budget"] = risk_budget

    extra_metadata = dict(body.metadata or {})
    extra_metadata["allocation_policy_artifact"] = artifact

    storage_ref = StorageRef(
        backend=StorageBackend.INLINE,
        path="$.entry.metadata.allocation_policy_artifact",
    )

    return RegistryEntryCreate(
        artifact_type=ArtifactType.ALLOCATION_POLICY,
        strategy_id=capital_pool_id,
        version=body.version,
        artifact_state=ArtifactState.CANDIDATE,
        lineage=lineage,
        storage_ref=storage_ref,
        checksum=checksum,
        producer_run_id=str(artifact.get("artifact_id") or "").strip() or None,
        evaluation_summary=evaluation_summary,
        metadata=extra_metadata,
    )


def _ensure_alloc_policy_view(
    view: RegistryEntryView, registry_id: str
) -> RegistryEntryView:
    if view.entry.artifact_type != ArtifactType.ALLOCATION_POLICY:
        raise RegistryNotFoundError(
            f"AllocationPolicyArtifact registry entry not found: {registry_id}"
        )
    return view


@app.post(
    "/api/registry/allocation-policy-artifacts",
    response_model=RegistryEntryView,
)
async def register_allocation_policy_artifact(
    payload: AllocationPolicyArtifactRegisterRequest,
    authorization: Optional[str] = Header(default=None),
):
    """
    Register an AllocationPolicyArtifact produced by optimizer-svc.

    The artifact enters the registry at ``candidate`` state because it already
    carries a ConflictResolutionLog and provenance lineage from optimizer-svc;
    it does not require a separate replication run.  Governance can then advance
    it to ``approved`` via the standard advance endpoint before deployment planning.
    """
    ctx = _authenticate_registry_write(authorization)
    artifact = payload.allocation_policy_artifact
    capital_pool_id = str(artifact.get("capital_pool_id") or "").strip()
    registry_id = (
        payload.registry_id
        or f"reg-alloc-policy-{capital_pool_id}-{payload.version}-{uuid.uuid4().hex[:8]}"
    )
    registry_service = get_registry_service()
    try:
        create_payload = _alloc_policy_register_payload(payload)
        return registry_service.register(create_payload, registry_id, actor=_actor_context(ctx))
    except RegistryConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (RegistryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/api/registry/allocation-policy-artifacts/{registry_id}",
    response_model=RegistryEntryView,
)
async def get_allocation_policy_artifact_entry(registry_id: str, authorization: Optional[str] = Header(default=None)):
    """Read one AllocationPolicyArtifact registry entry (tenant-scoped)."""
    ctx = _authenticate_registry_read(authorization)
    registry_service = get_registry_service()
    try:
        view = _ensure_alloc_policy_view(
            registry_service.get(registry_id), registry_id
        )
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _authorize_read(ctx, view)


@app.get(
    "/api/registry/pools/{capital_pool_id}/allocation-policy-artifacts",
    response_model=list[RegistryEntryView],
)
async def list_allocation_policy_artifacts(
    capital_pool_id: str,
    artifact_state: Optional[ArtifactState] = None,
    authorization: Optional[str] = Header(default=None),
):
    """
    List AllocationPolicyArtifact registry entries for one capital pool (tenant-scoped).

    strategy_id == capital_pool_id for allocation-policy artifacts.
    """
    ctx = _authenticate_registry_read(authorization)
    views = [
        view
        for view in get_registry_service().list_by_strategy(capital_pool_id)
        if view.entry.artifact_type == ArtifactType.ALLOCATION_POLICY and _can_read(ctx, view)
    ]
    if artifact_state is not None:
        views = [view for view in views if view.entry.artifact_state == artifact_state]
    return views


@app.post(
    "/api/registry/allocation-policy-artifacts/{registry_id}/advance",
    response_model=RegistryEntryView,
)
async def advance_allocation_policy_artifact_state(
    registry_id: str,
    body: AdvanceRequest,
    authorization: Optional[str] = Header(default=None),
):
    """
    Advance an AllocationPolicyArtifact entry through the governed artifact lifecycle.

    candidate -> approved makes the artifact eligible for DeploymentPlan creation.
    """
    ctx = _authenticate_registry_write(authorization)
    registry_service = get_registry_service()
    try:
        current = _ensure_alloc_policy_view(registry_service.get(registry_id), registry_id)
    except RegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _authorize_write(ctx, current)
    try:
        return registry_service.advance_artifact_state(
            registry_id,
            body.target_state,
            approver=body.approver,
            approval_decision_id=body.approval_decision_id,
            actor=_actor_context(ctx),
        )
    except RegistryNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# -- Health ---------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-registry"}
