"""Persistent write owner for Persona registry records.

The Persona service is the only writer exposed by this module.  It stores every
record through a durable owner store and reads the store again for every GET;
there is deliberately no process-local overlay, cache, fixture seed, or response
fallback.  BFF callers are expected to use this HTTP boundary instead of
``ReadSurfaceStore`` mutations.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrllibRequest, urlopen

from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.foundation.reliable_delivery import (
    AtomicJsonRecordStore,
    build_record_store,
)
from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_digest(payload: Any) -> str:
    """Return a stable SHA-256 identity for finite JSON data.

    Mirrors ``services/training-session/persona_target.py::canonical_digest``
    without importing that frozen module, so the Persona owner can
    independently re-derive a digest from actual content instead of trusting
    a caller-claimed digest field.
    """

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TrainingTargetProofInvalid(
            "training target payload is not finite canonical JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


_LIFECYCLE_TRANSITIONS = {
    "draft": frozenset({"research_only"}),
    "research_only": frozenset({"consultable", "frozen"}),
    "consultable": frozenset({"paper_owner", "frozen"}),
    "paper_owner": frozenset({"live_owner", "frozen"}),
    "live_owner": frozenset({"frozen", "retired"}),
    "frozen": frozenset({"research_only", "retired"}),
    "retired": frozenset(),
}
_ADMIN_STATUSES = frozenset({"active", "suspended", "archived"})
_DATA_SOURCE_CADENCES = frozenset(
    {"realtime", "minutely", "hourly", "daily", "weekly", "on_demand"}
)
_DATA_SOURCE_CLASSES = frozenset({"live_push", "live_pull", "seed_only"})
_PERSONA_PLANE_ROLES = frozenset({"persona.admin"})
_GOVERNANCE_PLANE_ROLES = frozenset(
    {
        "automated_gate",
        "governance_committee",
        "governance_reviewer",
        "risk_owner",
    }
)
_DECISION_EXECUTOR_ROLES = frozenset({"admin", "approver", "operator"})
_AUTHENTICATED_MUTATION_ROLES = (
    _PERSONA_PLANE_ROLES | _GOVERNANCE_PLANE_ROLES | _DECISION_EXECUTOR_ROLES
)
_AUTHENTICATED_READ_ROLES = (
    _AUTHENTICATED_MUTATION_ROLES | frozenset({"viewer", "view_only", "reviewer"})
)
_LIFECYCLE_POLICY_ROLES = {
    ("draft", "research_only"): _PERSONA_PLANE_ROLES,
    ("research_only", "consultable"): _GOVERNANCE_PLANE_ROLES,
    ("consultable", "paper_owner"): _GOVERNANCE_PLANE_ROLES,
    ("paper_owner", "live_owner"): _GOVERNANCE_PLANE_ROLES,
    ("research_only", "frozen"): _GOVERNANCE_PLANE_ROLES,
    ("consultable", "frozen"): _GOVERNANCE_PLANE_ROLES,
    ("paper_owner", "frozen"): _GOVERNANCE_PLANE_ROLES,
    ("live_owner", "frozen"): _GOVERNANCE_PLANE_ROLES,
    ("frozen", "research_only"): _GOVERNANCE_PLANE_ROLES,
    ("frozen", "retired"): _GOVERNANCE_PLANE_ROLES,
    ("live_owner", "retired"): _GOVERNANCE_PLANE_ROLES,
}


class PersonaOwnerError(ValueError):
    """Base error for Persona owner validation failures."""


class PersonaAlreadyExists(PersonaOwnerError):
    """Raised when a create collides with a persisted Persona identity."""


class PersonaNotFound(PersonaOwnerError):
    """Raised when a persisted Persona cannot be found."""


class PersonaConcurrentUpdate(PersonaOwnerError):
    """Raised when repeated compare-and-set attempts lose a write race."""


class CapabilitySnapshotConflict(PersonaOwnerError):
    """Raised when a stable snapshot id is replayed with other semantics."""


class CapabilitySnapshotNotFound(PersonaOwnerError):
    """Raised when a persisted capability snapshot cannot be found."""


class PersonaAuthorityError(PersonaOwnerError):
    """Raised when verified caller authority does not own a Persona write."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TrainingTargetTenantMismatch(PersonaAuthorityError):
    """Raised when a caller's tenant does not own the Persona training target."""

    def __init__(self) -> None:
        super().__init__(
            "TRAINING_TARGET_TENANT_MISMATCH",
            "Persona training target tenant_id does not match its bound tenant",
            403,
        )


class TrainingTargetTenantBindingUnavailable(PersonaAuthorityError):
    """Raised when a Persona has no real governed ``tenant_id`` binding.

    ``owner`` is an actor/resource-owner identity captured at creation, not
    a tenant identifier; treating it as one would relabel actor authority
    as tenant authority, exactly the defect root review flagged. A Persona
    created before a real ``tenant_id`` was captured (or created without
    one) has no provable tenant binding, so training-target authority for
    it must fail closed instead of guessing -- a legacy record needs an
    explicit migration to backfill ``tenant_id``, not a silent fallback.
    """

    def __init__(self) -> None:
        super().__init__(
            "TRAINING_TARGET_TENANT_BINDING_UNAVAILABLE",
            "Persona has no governed tenant_id binding; training-target "
            "authority cannot be established",
            503,
        )


class TrainingTargetGenerationConflict(PersonaOwnerError):
    """Raised when a training-target commit's generation is not the exact CAS successor."""


class TrainingTargetIdempotencyConflict(PersonaOwnerError):
    """Raised when a replayed idempotency key targets a different committed payload."""


class TrainingTargetProofInvalid(PersonaOwnerError):
    """Raised when a commit's candidate/control/proof binding does not verify.

    Covers a claimed digest that does not match the actual submitted content,
    an internally inconsistent evaluation proof, a proof bound to a different
    precondition/generation than the one being committed, or a proof whose
    status is not ``passed``. The owner must prove this itself; it cannot
    trust the training-session client validator to have already done so.
    """


class TrainingTargetApprovalInvalid(PersonaOwnerError):
    """Raised when the claimed approval decision does not verify against Governance."""


class TrainingTargetApprovalUnavailable(PersonaAuthorityError):
    """Raised when no Governance approval verifier is configured for a commit.

    A missing verifier must block the commit, not silently mint authority.
    """

    def __init__(self) -> None:
        super().__init__(
            "TRAINING_TARGET_APPROVAL_VERIFIER_UNAVAILABLE",
            "No Governance approval verifier is configured for training-target commits",
            503,
        )


@dataclass(frozen=True)
class PersonaInboundAuthority:
    """Authenticated identity used for Persona owner policy decisions."""

    actor_id: str
    roles: frozenset[str]
    token_kind: str
    tenant_id: str | None = None
    claims: Mapping[str, Any] | None = None


class GovernanceDecisionVerifier(Protocol):
    """Verify one exact Persona lifecycle decision against Governance truth."""

    def verify_persona_lifecycle_decision(
        self,
        *,
        decision_id: str,
        persona_id: str,
        tenant_id: str,
        source_state: str,
        target_state: str,
    ) -> bool: ...


class TrainingTargetApprovalVerifier(Protocol):
    """Verify one exact persona training-target approval against Governance truth.

    This is the owner-side authority check the training-session client
    validator cannot substitute for: a caller hitting this HTTP boundary
    directly must still prove its claimed ``approval_decision_id`` is a real,
    approved, unexpired Governance decision bound to this exact persona,
    tenant, session, and candidate/proof digests.
    """

    def verify_training_target_approval(
        self,
        *,
        approval_decision_id: str,
        approval_decision_ref: str,
        target_version: str,
        persona_id: str,
        tenant_id: str,
        session_id: str,
        candidate_digest: str,
        proof_digest: str,
    ) -> bool: ...


class HttpGovernanceApprovalVerifier:
    """Default ``TrainingTargetApprovalVerifier`` backed by the real Governance API.

    Narrowly scoped adapter: it re-reads the exact approval decision the
    caller claims to have used from Governance's own
    ``/api/governance/approvals/{decision_id}`` endpoint and independently
    checks that it is approved, unexpired, and bound to this exact
    persona/tenant/session/candidate/proof identity. It never trusts a
    caller-supplied approval object; it only trusts what Governance itself
    returns.
    """

    def __init__(
        self, *, base_url: str, service_token: str, timeout_seconds: float = 5.0,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds

    def verify_persona_lifecycle_decision(
        self,
        *,
        decision_id: str,
        persona_id: str,
        tenant_id: str,
        source_state: str,
        target_state: str,
    ) -> bool:
        from services.governance.approval_authority import (
            ApprovalInvalid,
            ApprovalReader,
            ApprovalUnavailable,
        )
        try:
            ApprovalReader(base_url=self._base_url, service_token=self._service_token,
                           token_provider=self._token_provider,
                           timeout_seconds=self._timeout_seconds).verify(
                decision_id, expected={
                    'tenant_id': tenant_id,
                    'target_type': 'persona_lifecycle_transition',
                    'subject.persona_id': persona_id,
                    'subject.from_state': source_state,
                    'subject.to_state': target_state,
                })
        except ApprovalUnavailable:
            raise
        except ApprovalInvalid:
            return False
        return True

    def verify_training_target_approval(
        self,
        *,
        approval_decision_id: str,
        approval_decision_ref: str,
        target_version: str,
        persona_id: str,
        tenant_id: str,
        session_id: str,
        candidate_digest: str,
        proof_digest: str,
    ) -> bool:
        from services.governance.approval_authority import ApprovalReader, ApprovalInvalid
        if approval_decision_ref != approval_decision_id:
            return False
        try:
            ApprovalReader(base_url=self._base_url, service_token=self._service_token,
                           token_provider=self._token_provider,
                           timeout_seconds=self._timeout_seconds).verify(
                approval_decision_id, expected={
                    'tenant_id': tenant_id, 'persona_id': persona_id,
                    'target_type': 'persona_training_target', 'target_id': persona_id,
                    'target_version': target_version, 'session_id': session_id,
                    'candidate_digest': candidate_digest, 'proof_digest': proof_digest,
                })
        except ApprovalInvalid:
            return False
        return True



def _persona_auth_env() -> dict[str, str]:
    """Resolve Persona auth configuration without enabling a permissive default."""

    return {
        "PANTHEON_RUNTIME_AUTH_MODE": (
            os.getenv("PERSONA_AUTH_MODE")
            or os.getenv("PANTHEON_RUNTIME_AUTH_MODE")
            or "strict"
        ),
        "PANTHEON_RUNTIME_JWT_SECRET": (
            os.getenv("PERSONA_JWT_SECRET")
            or os.getenv("PANTHEON_RUNTIME_JWT_SECRET")
            or ""
        ),
        "PANTHEON_RUNTIME_JWT_ISSUER": (
            os.getenv("PERSONA_JWT_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER")
            or ""
        ),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": (
            os.getenv("PERSONA_JWT_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE")
            or ""
        ),
        "PANTHEON_RUNTIME_JWKS_URI": (
            os.getenv("PERSONA_JWKS_URI")
            or os.getenv("PANTHEON_RUNTIME_JWKS_URI")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": (
            os.getenv("PERSONA_OIDC_DISCOVERY_URL")
            or os.getenv("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_ISSUER": (
            os.getenv("PERSONA_OIDC_ISSUER")
            or os.getenv("PANTHEON_RUNTIME_OIDC_ISSUER")
            or ""
        ),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": (
            os.getenv("PERSONA_OIDC_AUDIENCE")
            or os.getenv("PANTHEON_RUNTIME_OIDC_AUDIENCE")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": (
            os.getenv("PERSONA_ROLE_CLAIMS")
            or os.getenv("PANTHEON_RUNTIME_ROLE_CLAIMS")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_MAP": (
            os.getenv("PERSONA_ROLE_MAP")
            or os.getenv("PANTHEON_RUNTIME_ROLE_MAP")
            or ""
        ),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": (
            os.getenv("PERSONA_ROLE_MAP_MODE")
            or os.getenv("PANTHEON_RUNTIME_ROLE_MAP_MODE")
            or ""
        ),
        "PANTHEON_RUNTIME_MFA_REQUIRED": "false",
    }


def _authenticate_persona_mutation(
    authorization: str | None,
    required_roles: frozenset[str] = _AUTHENTICATED_MUTATION_ROLES,
) -> PersonaInboundAuthority:
    configured_service_token = str(
        os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN")
        or os.getenv("PERSONA_SERVICE_TOKEN")
        or ""
    ).strip()
    supplied_service_token = ""
    if str(authorization or "").startswith("Bearer "):
        supplied_service_token = str(authorization)[len("Bearer ") :].strip()
    if (
        configured_service_token
        and supplied_service_token
        and hmac.compare_digest(configured_service_token, supplied_service_token)
    ):
        return PersonaInboundAuthority(
            actor_id=str(
                os.getenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID") or "operator-bff"
            ).strip(),
            roles=_PERSONA_PLANE_ROLES,
            token_kind="service",
        )
    try:
        context: AuthContext = validate_request_auth(
            authorization=authorization,
            required_roles=tuple(sorted(required_roles)),
            mfa_required=False,
            env=_persona_auth_env(),
        )
    except AuthError as exc:
        status = 503 if exc.status_code >= 500 else exc.status_code
        raise PersonaAuthorityError(exc.code, exc.message, status) from exc
    return PersonaInboundAuthority(
        actor_id=context.actor_id,
        roles=context.roles,
        token_kind=context.token_kind,
        tenant_id=str(context.claims.get("tenant_id") or "").strip() or None,
        claims=context.claims,
    )


def _authenticate_persona_read(
    authorization: str | None,
) -> PersonaInboundAuthority:
    return _authenticate_persona_mutation(authorization, _AUTHENTICATED_READ_ROLES)


def resolve_persona_tenant_scope(
    authorization: str | None,
    requested_tenant: str | None = None,
    required_roles: frozenset[str] = _AUTHENTICATED_READ_ROLES,
) -> tuple[PersonaInboundAuthority, str]:
    """Authenticate and select only a tenant admitted by verified claims."""
    authority = _authenticate_persona_mutation(authorization, required_roles)
    if authority.token_kind == "service":
        default = str(os.getenv("PERSONA_DEFAULT_TENANT_ID") or os.getenv("PANTHEON_TENANT_ID") or "").strip()
        chosen = str(requested_tenant or "").strip() or default
        if not chosen or chosen == "*":
            raise PersonaAuthorityError("TENANT_SCOPE_DENIED", "An explicitly admitted tenant is required", 403)
        return authority, chosen
    claims = dict(authority.claims or {})
    primary = ("tenant_id", "tenantId", "tenant.id", "tid", "org_id", "organization.id", "organization_id")
    paths = ("allowed_tenants", "allowedTenants", "tenant_ids", "tenantIds", "tenants", *primary)

    def values(names: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        for path in names:
            value: Any = claims
            for part in path.split("."):
                value = value.get(part) if isinstance(value, Mapping) else None
            for item in value if isinstance(value, (list, tuple, set)) else (value,):
                text = str(item or "").strip()
                if text and text not in found: found.append(text)
        return found

    scoped, admitted = values(primary), values(paths)
    default = str(os.getenv("PERSONA_DEFAULT_TENANT_ID") or os.getenv("PANTHEON_TENANT_ID") or "").strip()
    chosen = str(requested_tenant or "").strip() or (default if default in admitted else "")
    if not chosen and len(scoped or admitted) == 1: chosen = (scoped or admitted)[0]
    if not chosen or chosen == "*" or ("*" not in admitted and chosen not in admitted):
        raise PersonaAuthorityError("TENANT_SCOPE_DENIED", "An explicitly admitted tenant is required", 403)
    return authority, chosen


def _require_owner_persona(
    owner: PersistentPersonaOwner,
    persona_id: str,
    authorization: str | None,
) -> tuple[PersonaInboundAuthority, PersonaBody]:
    authority = _authenticate_persona_mutation(authorization)
    raw = owner._records.get(persona_id)
    if raw is None:
        raise PersonaNotFound(f"Persona {persona_id!r} not found")
    if authority.token_kind != "service":
        tenant = raw.get("tenant_id")
        if not tenant:
            raise PersonaAuthorityError("FORBIDDEN", "Persona has no tenant binding", 403)
        resolve_persona_tenant_scope(authorization, tenant)
    return authority, owner.get(persona_id)


def _bind_authenticated_actor(
    request: BaseModel,
    authority: PersonaInboundAuthority,
) -> Any:
    declared_actor_id = str(getattr(request, "actor_id", None) or "").strip()
    if declared_actor_id != authority.actor_id:
        raise PersonaAuthorityError(
            "ACTOR_ID_MISMATCH",
            "Mutation actor_id does not match the authenticated actor",
            403,
        )
    return request.model_copy(update={"actor_id": authority.actor_id})


def _require_persona_plane_owner(authority: PersonaInboundAuthority) -> None:
    if authority.roles.isdisjoint(_PERSONA_PLANE_ROLES):
        raise PersonaAuthorityError(
            "PERSONA_OWNER_REQUIRED",
            "Persona creation and registry edits require persona.admin authority",
            403,
        )


def _require_lifecycle_authority(
    *,
    authority: PersonaInboundAuthority,
    verifier: GovernanceDecisionVerifier | None,
    decision_id: str | None,
    persona_id: str,
    tenant_id: str | None,
    source_state: str,
    target_state: str,
) -> None:
    transition = (source_state, target_state)
    policy_roles = _LIFECYCLE_POLICY_ROLES.get(transition)
    if policy_roles is None:
        raise PersonaOwnerError(
            f"invalid lifecycle transition {source_state!r} -> {target_state!r}"
        )
    if authority.token_kind == "jwt" and (not tenant_id or authority.tenant_id != tenant_id):
        raise PersonaAuthorityError("LIFECYCLE_TENANT_MISMATCH", "Exact tenant match required", 403)
    if not authority.roles.isdisjoint(policy_roles):
        return

    clean_decision_id = str(decision_id or "").strip()
    if (
        not clean_decision_id
        or verifier is None
        or not tenant_id
        or authority.tenant_id != tenant_id
    ):
        raise PersonaAuthorityError(
            "LIFECYCLE_AUTHORITY_REQUIRED",
            "Lifecycle transition requires its policy owner or a verified Governance decision",
            403,
        )
    try:
        verified = verifier.verify_persona_lifecycle_decision(
            decision_id=clean_decision_id,
            persona_id=persona_id,
            tenant_id=str(tenant_id or ""),
            source_state=source_state,
            target_state=target_state,
        )
    except Exception as exc:
        raise PersonaAuthorityError(
            "GOVERNANCE_AUTHORITY_UNAVAILABLE",
            "Governance decision authority is unavailable",
            503,
        ) from exc
    if not verified:
        raise PersonaAuthorityError(
            "GOVERNANCE_DECISION_INVALID",
            "Governance decision is not approved for this exact Persona lifecycle transition",
            403,
        )


class _OwnerRecordStore(Protocol):
    def compare_and_set(
        self,
        record_id: str,
        expected_payload: dict[str, Any] | None,
        payload: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | None]: ...

    def get(self, record_id: str) -> dict[str, Any] | None: ...

    def list_all(self) -> list[dict[str, Any]]: ...


class RequiredDataSourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    market: str = Field(min_length=1)
    cadence: str
    source_class: str
    connector_candidates: list[str] = Field(default_factory=list)
    policy_gates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_enums(self) -> "RequiredDataSourceBody":
        if self.cadence not in _DATA_SOURCE_CADENCES:
            raise ValueError(
                f"cadence must be one of {sorted(_DATA_SOURCE_CADENCES)}"
            )
        if self.source_class not in _DATA_SOURCE_CLASSES:
            raise ValueError(
                f"source_class must be one of {sorted(_DATA_SOURCE_CLASSES)}"
            )
        return self


class PersonaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mandate: str = Field(min_length=1)
    lifecycle_state: str
    created_at: str
    strategy_family: str | None = None
    workspace_ref: str | None = None
    tool_profile_id: str | None = None
    route_policy_id: str | None = None
    consult_policy_id: str | None = None
    owner: str
    # The real, governed tenant binding, captured only at creation and never
    # patchable. Distinct from ``owner`` (an actor/resource identity that
    # defaults to the creating actor_id): relabeling ``owner`` as tenant
    # authority was the exact defect root review flagged. ``None`` means this
    # Persona has no provable tenant binding (a legacy record, or one created
    # without a real tenant) -- callers that need tenant authority must fail
    # closed rather than guess.
    tenant_id: str | None = None
    status: str = "active"
    updated_at: str | None = None
    created_by: str
    updated_by: str | None = None
    required_data_sources: list[RequiredDataSourceBody] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_state(self) -> "PersonaBody":
        if self.lifecycle_state not in _LIFECYCLE_TRANSITIONS:
            raise ValueError(
                "lifecycle_state must be one of "
                f"{sorted(_LIFECYCLE_TRANSITIONS)}"
            )
        if self.status not in _ADMIN_STATUSES:
            raise ValueError(f"status must be one of {sorted(_ADMIN_STATUSES)}")
        return self


class CapabilitySnapshotBody(BaseModel):
    """Immutable effective capability receipt owned by the Persona service."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=1)
    persona_id: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    allowed_capabilities: list[str] = Field(min_length=1)
    effective_tools: list[str] = Field(default_factory=list)
    effective_skills: list[str] = Field(default_factory=list)
    effective_workflows: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    generated_at: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpsertCapabilitySnapshotRequest(BaseModel):
    """Idempotent capability receipt accepted by the Persona write owner."""

    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    persona_id: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    effective_tools: list[str] = Field(default_factory=list)
    effective_skills: list[str] = Field(default_factory=list)
    effective_workflows: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    generated_at: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_capabilities(self) -> "UpsertCapabilitySnapshotRequest":
        normalized = [str(item).strip() for item in self.capabilities]
        if any(not item for item in normalized):
            raise ValueError("capabilities must contain non-empty values")
        if len(set(normalized)) != len(normalized):
            raise ValueError("capabilities must not contain duplicates")
        return self


class CreatePersonaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    persona_id: str | None = None
    name: str = Field(min_length=1)
    mandate: str = Field(min_length=1)
    lifecycle_state: str = "draft"
    strategy_family: str | None = None
    workspace_ref: str | None = None
    tool_profile_id: str | None = None
    route_policy_id: str | None = None
    consult_policy_id: str | None = None
    owner: str | None = None
    # Real tenant binding, set only here; there is no patch path so a
    # Persona's tenant authority cannot drift after creation. ``None`` is
    # honest: it means this Persona has no provable tenant binding yet.
    tenant_id: str | None = None
    status: str = "active"
    required_data_sources: list[RequiredDataSourceBody] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchPersonaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1)
    mandate: str | None = Field(default=None, min_length=1)
    lifecycle_state: str | None = None
    strategy_family: str | None = None
    workspace_ref: str | None = None
    tool_profile_id: str | None = None
    route_policy_id: str | None = None
    consult_policy_id: str | None = None
    owner: str | None = None
    status: str | None = None
    required_data_sources: list[RequiredDataSourceBody] | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_patch_field(self) -> "PatchPersonaRequest":
        if not (self.model_fields_set - {"actor_id"}):
            raise ValueError("at least one Persona patch field is required")
        return self


class AdvancePersonaLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    target_state: str = Field(min_length=1)
    governance_decision_id: str | None = Field(default=None, min_length=1)


def _is_private_persona(record: Any) -> bool:
    meta = (record.metadata if isinstance(record, PersonaBody) else (record.get("metadata") if isinstance(record, dict) else None)) or {}
    return bool(meta.get("trade_reflections") or meta.get("trade_reflection_idempotency"))


class PersistentPersonaOwner:
    """Persona registry application service over one persistent owner store."""

    def __init__(self, records: _OwnerRecordStore) -> None:
        self._records = records

    @classmethod
    def from_json_path(cls, path: str | Path) -> "PersistentPersonaOwner":
        return cls(AtomicJsonRecordStore(path))

    def create(self, request: CreatePersonaRequest) -> PersonaBody:
        if request.lifecycle_state != "draft":
            raise PersonaOwnerError(
                "Persona creation must start in 'draft'; use the governed lifecycle endpoint"
            )
        persona_id = str(request.persona_id or f"persona-{uuid.uuid4().hex[:12]}")
        created_at = _utc_now()
        record = PersonaBody(
            persona_id=persona_id,
            name=request.name,
            mandate=request.mandate,
            lifecycle_state=request.lifecycle_state,
            created_at=created_at,
            strategy_family=request.strategy_family,
            workspace_ref=request.workspace_ref,
            tool_profile_id=request.tool_profile_id,
            route_policy_id=request.route_policy_id,
            consult_policy_id=request.consult_policy_id,
            owner=request.owner or request.actor_id,
            tenant_id=request.tenant_id,
            status=request.status,
            created_by=request.actor_id,
            required_data_sources=request.required_data_sources,
            metadata=request.metadata,
        ).model_dump(mode="json")
        inserted, existing = self._records.compare_and_set(persona_id, None, record)
        if not inserted:
            raise PersonaAlreadyExists(
                f"Persona {persona_id!r} already exists in the persistent owner store"
            )
        return PersonaBody.model_validate(existing or record)

    def get(self, persona_id: str) -> PersonaBody:
        record = self._records.get(persona_id)
        if record is None:
            raise PersonaNotFound(f"Persona {persona_id!r} not found")
        return PersonaBody.model_validate(record)

    def list(
        self, *, lifecycle_state: str | None = None, status_value: str | None = None, tenant_id: str | None = None,
    ) -> list[PersonaBody]:
        raw = [r for r in self._records.list_all() if tenant_id is None or r.get("tenant_id") == tenant_id or not _is_private_persona(r)]
        records = [PersonaBody.model_validate(r) for r in raw]
        if lifecycle_state is not None: records = [i for i in records if i.lifecycle_state == lifecycle_state]
        if status_value is not None: records = [i for i in records if i.status == status_value]
        return sorted(records, key=lambda item: item.persona_id)


    def patch(self, persona_id: str, request: PatchPersonaRequest) -> PersonaBody:
        for _attempt in range(4):
            current = self._records.get(persona_id)
            if current is None:
                raise PersonaNotFound(f"Persona {persona_id!r} not found")
            updated = self._patched_record(current, request)
            committed, canonical = self._records.compare_and_set(
                persona_id,
                current,
                updated,
            )
            if committed:
                return PersonaBody.model_validate(canonical or updated)
        raise PersonaConcurrentUpdate(
            f"Persona {persona_id!r} changed concurrently; retry against a fresh read"
        )

    def try_metadata_cas(
        self,
        persona_id: str,
        *,
        guard: Callable[[PersonaBody], bool],
        metadata_updates: Mapping[str, Any] | Callable[[PersonaBody], Mapping[str, Any]],
        actor_id: str,
    ) -> tuple[bool, PersonaBody]:
        """One CAS attempt whose precondition is checked against the exact
        snapshot it commits against.

        This exists so a caller-level generation guard cannot be separated
        from the compare-and-set it protects: ``patch()`` re-reads its own
        fresh snapshot on every retry and applies the caller's update to it
        unconditionally, so a guard checked only once by the caller before
        calling ``patch()`` can be satisfied against a stale read and then
        blindly overwritten onto whatever committed in between (the exact
        lost-update race root review reproduced against the real JSON-backed
        owner). Here, ``guard`` and the ``compare_and_set`` run against the
        same read, so any interleaving write that would change the guard's
        answer causes this attempt's CAS to fail instead of silently
        clobbering newer state; the caller's own retry loop re-reads and
        re-evaluates ``guard`` before trying again.

        Returns ``(False, current)`` when ``guard(current)`` is false --
        a legitimate no-op (e.g. a stale generation), not a race. Raises
        ``PersonaConcurrentUpdate`` when the CAS itself loses a race, so the
        caller can retry with a fresh read.
        """

        current_raw = self._records.get(persona_id)
        if current_raw is None:
            raise PersonaNotFound(f"Persona {persona_id!r} not found")
        current = PersonaBody.model_validate(current_raw)
        if not guard(current):
            return False, current
        updates = metadata_updates(current) if callable(metadata_updates) else metadata_updates
        merged_metadata = dict(current.metadata or {})
        merged_metadata.update(updates)
        updated = dict(current_raw)
        updated["metadata"] = merged_metadata
        updated["updated_at"] = _utc_now()
        updated["updated_by"] = actor_id
        updated = PersonaBody.model_validate(updated).model_dump(mode="json")
        committed, canonical = self._records.compare_and_set(
            persona_id, current_raw, updated
        )
        if committed:
            return True, PersonaBody.model_validate(canonical or updated)
        raise PersonaConcurrentUpdate(
            f"Persona {persona_id!r} changed concurrently; retry against a fresh read"
        )

    def advance_lifecycle(
        self,
        persona_id: str,
        request: AdvancePersonaLifecycleRequest,
        *,
        expected_from_state: str,
    ) -> PersonaBody:
        lifecycle_patch = PatchPersonaRequest(
            actor_id=request.actor_id,
            lifecycle_state=request.target_state,
        )
        for _attempt in range(4):
            current = self._records.get(persona_id)
            if current is None:
                raise PersonaNotFound(f"Persona {persona_id!r} not found")
            if str(current.get("lifecycle_state") or "") != expected_from_state:
                raise PersonaConcurrentUpdate(f"Persona {persona_id!r} lifecycle changed during approval verification")
            updated = self._patched_record(
                current,
                lifecycle_patch,
                allow_lifecycle=True,
            )
            if request.governance_decision_id:
                metadata = dict(updated.get("metadata") or {})
                metadata["last_lifecycle_governance_decision_id"] = (
                    request.governance_decision_id
                )
                updated["metadata"] = metadata
            committed, canonical = self._records.compare_and_set(
                persona_id,
                current,
                updated,
            )
            if committed:
                return PersonaBody.model_validate(canonical or updated)
        raise PersonaConcurrentUpdate(
            f"Persona {persona_id!r} changed concurrently; retry against a fresh read"
        )

    @staticmethod
    def _patched_record(
        current: Mapping[str, Any],
        request: PatchPersonaRequest,
        *,
        allow_lifecycle: bool = False,
    ) -> dict[str, Any]:
        record = dict(current)
        patch_fields = request.model_fields_set - {"actor_id"}
        if "lifecycle_state" in patch_fields:
            if not allow_lifecycle:
                raise PersonaOwnerError(
                    "lifecycle_state may only be changed through the governed lifecycle endpoint"
                )
            target_state = str(request.lifecycle_state or "")
            current_state = str(record.get("lifecycle_state") or "")
            if target_state == current_state or target_state not in _LIFECYCLE_TRANSITIONS.get(
                current_state, frozenset()
            ):
                raise PersonaOwnerError(
                    f"invalid lifecycle transition {current_state!r} -> {target_state!r}"
                )
        if "status" in patch_fields and request.status not in _ADMIN_STATUSES:
            raise PersonaOwnerError(
                f"status must be one of {sorted(_ADMIN_STATUSES)}"
            )

        for field_name in patch_fields - {"metadata"}:
            value = getattr(request, field_name)
            if field_name == "required_data_sources" and value is not None:
                record[field_name] = [item.model_dump(mode="json") for item in value]
            else:
                record[field_name] = value
        if "metadata" in patch_fields:
            merged_metadata = dict(record.get("metadata") or {})
            merged_metadata.update(request.metadata or {})
            record["metadata"] = merged_metadata
        record["updated_at"] = _utc_now()
        record["updated_by"] = request.actor_id
        return PersonaBody.model_validate(record).model_dump(mode="json")


class PersistentCapabilitySnapshotOwner:
    """Persona-service owner for immutable capability snapshot receipts."""

    def __init__(self, records: _OwnerRecordStore) -> None:
        self._records = records

    @classmethod
    def from_json_path(cls, path: str | Path) -> "PersistentCapabilitySnapshotOwner":
        return cls(AtomicJsonRecordStore(path))

    @staticmethod
    def _semantic_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in dict(record).items()
            if key != "generated_at"
        }

    def upsert(
        self,
        request: UpsertCapabilitySnapshotRequest,
    ) -> CapabilitySnapshotBody:
        record = CapabilitySnapshotBody(
            snapshot_id=request.snapshot_id,
            persona_id=request.persona_id,
            capabilities=list(request.capabilities),
            allowed_capabilities=list(request.capabilities),
            effective_tools=list(request.effective_tools),
            effective_skills=list(request.effective_skills),
            effective_workflows=list(request.effective_workflows),
            restrictions=list(request.restrictions),
            generated_at=request.generated_at,
            source_refs=list(request.source_refs),
            metadata={
                **request.metadata,
                "written_by": request.actor_id,
                "canonical_write_authority": "persona_service",
            },
        ).model_dump(mode="json")
        for _attempt in range(4):
            current = self._records.get(request.snapshot_id)
            if current is not None:
                if self._semantic_payload(current) == self._semantic_payload(record):
                    return CapabilitySnapshotBody.model_validate(current)
                raise CapabilitySnapshotConflict(
                    f"Capability snapshot {request.snapshot_id!r} already has other semantics"
                )
            committed, canonical = self._records.compare_and_set(
                request.snapshot_id,
                None,
                record,
            )
            if committed:
                return CapabilitySnapshotBody.model_validate(canonical or record)
        raise PersonaConcurrentUpdate(
            f"Capability snapshot {request.snapshot_id!r} changed concurrently"
        )

    def get(self, snapshot_id: str) -> CapabilitySnapshotBody:
        record = self._records.get(snapshot_id)
        if record is None:
            raise CapabilitySnapshotNotFound(
                f"Capability snapshot {snapshot_id!r} not found"
            )
        return CapabilitySnapshotBody.model_validate(record)

    def get_for_persona(self, persona_id: str) -> CapabilitySnapshotBody:
        matches = [
            CapabilitySnapshotBody.model_validate(record)
            for record in self._records.list_all()
            if str(record.get("persona_id") or "") == persona_id
        ]
        if not matches:
            raise CapabilitySnapshotNotFound(
                f"Capability snapshot for Persona {persona_id!r} not found"
            )
        return sorted(
            matches,
            key=lambda item: (item.generated_at, item.snapshot_id),
            reverse=True,
        )[0]


_TRAINING_TARGET_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_TRAINING_TARGET_BINDING_FIELDS = (
    "persona_id",
    "tenant_id",
    "session_id",
    "candidate_digest",
    "control_digest",
    "proof_digest",
    "approval_digest",
    "generation",
)


class CommitPersonaTrainingTargetRequest(BaseModel):
    """Authoritative teaching-target commit accepted by the Persona write owner.

    Field shape mirrors the frozen write body built by
    ``services/training-session/persona_target.py::commit_persona_target`` so this
    owner can be the exact-head authority that validator reads back.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    candidate_digest: str = Field(pattern=_TRAINING_TARGET_DIGEST_PATTERN)
    control_digest: str = Field(pattern=_TRAINING_TARGET_DIGEST_PATTERN)
    proof_digest: str = Field(pattern=_TRAINING_TARGET_DIGEST_PATTERN)
    approval_digest: str = Field(pattern=_TRAINING_TARGET_DIGEST_PATTERN)
    generation: int = Field(ge=1)
    expected_previous_generation: int = Field(ge=0)
    expected_precondition_digest: str = Field(pattern=_TRAINING_TARGET_DIGEST_PATTERN)
    expected_precondition_record_ref: str = Field(min_length=1)
    approval_decision_id: str = Field(min_length=1)
    approval_decision_ref: str = Field(min_length=1)
    candidate: Any
    control_state: Any
    evaluation_proof: Any

    @model_validator(mode="after")
    def validate_generation_successor(self) -> "CommitPersonaTrainingTargetRequest":
        if self.generation != self.expected_previous_generation + 1:
            raise ValueError(
                "generation must be exactly expected_previous_generation + 1"
            )
        return self

    @model_validator(mode="after")
    def validate_training_payloads_present(
        self,
    ) -> "CommitPersonaTrainingTargetRequest":
        # The owner must independently re-derive digests from real content; a
        # commit that omits the content it claims a digest for cannot be
        # verified and must not be accepted as if it were.
        if self.candidate is None or self.control_state is None:
            raise ValueError("candidate and control_state are required")
        if not isinstance(self.evaluation_proof, Mapping):
            raise ValueError("evaluation_proof must be a JSON object")
        return self


class PersistentPersonaTrainingTargetOwner:
    """Persistent, tenant-bound owner for one Persona's training-target authority.

    Serves the frozen ``persona_target.py`` contract: the same durable record is
    read as the pre-commit precondition, re-read as the in-commit pre-readback,
    and read again as the post-commit terminal readback. Compare-and-set on
    ``generation`` is the only accepted write path; a repeated commit at the
    already-committed generation with an identical binding is an idempotent
    replay, and one with a different binding is a hard idempotency conflict.
    There is no in-process cache or fixture fallback -- every read goes back to
    the durable store so a restarted owner process reads back the same truth.
    """

    def __init__(
        self,
        records: _OwnerRecordStore,
        *,
        persona_owner: PersistentPersonaOwner,
        approval_verifier: TrainingTargetApprovalVerifier | None = None,
    ) -> None:
        self._records = records
        self._persona_owner = persona_owner
        self._approval_verifier = approval_verifier

    @classmethod
    def from_json_path(
        cls,
        path: str | Path,
        *,
        persona_owner: PersistentPersonaOwner,
        approval_verifier: TrainingTargetApprovalVerifier | None = None,
    ) -> "PersistentPersonaTrainingTargetOwner":
        return cls(
            AtomicJsonRecordStore(path),
            persona_owner=persona_owner,
            approval_verifier=approval_verifier,
        )

    def _virtual_initial_record(self, persona: PersonaBody) -> dict[str, Any]:
        """A deterministic, unpersisted generation-0 view.

        The tenant is derived from the Persona's own durable, governed
        ``tenant_id`` field -- captured only at creation through the
        ``persona.admin`` gated create path -- never from a caller-asserted
        header. A caller cannot obtain an authoritative-looking precondition
        for a tenant it does not actually own; ``read``/``commit`` reject any
        ``X-Tenant-Id`` that does not match this real field (via
        ``_load_owner_bound_persona``, which also rejects a Persona with no
        ``tenant_id`` at all) before this view is ever returned, so
        ``persona.tenant_id`` is guaranteed non-``None`` here.
        """

        tenant_id = str(persona.tenant_id)
        return {
            "persona_id": persona.persona_id,
            "tenant_id": tenant_id,
            "status": "active",
            "generation": 0,
            "authority_status": "authoritative",
            "controller_record_ref": (
                f"persona-training-target:{persona.persona_id}:0:{tenant_id}"
            ),
            "recorded_at": persona.created_at,
        }

    def _load_owner_bound_persona(
        self, persona_id: str, tenant_id: str
    ) -> PersonaBody:
        """Read the real Persona and require the caller's tenant to be its own.

        The Persona's ``tenant_id`` field is the only genuine, governed
        tenant binding this data model has (captured at creation through the
        ``persona.admin`` gated create path; there is no patch path, so it
        cannot drift afterward). ``owner`` is a distinct actor/resource
        identity, not a tenant -- trusting it (or a caller-supplied
        ``X-Tenant-Id`` header) as tenant authority is exactly the
        fabricated-authority defect root review flagged. A Persona with no
        ``tenant_id`` (a legacy record, or one created without one) has no
        provable tenant binding and fails closed rather than falling back to
        ``owner`` or the caller's header.
        """

        persona = self._persona_owner.get(persona_id)
        if persona.tenant_id is None:
            raise TrainingTargetTenantBindingUnavailable()
        if str(persona.tenant_id) != tenant_id:
            raise TrainingTargetTenantMismatch()
        return persona

    def read(self, *, persona_id: str, tenant_id: str) -> dict[str, Any]:
        # Fail closed against a training-target authority for a Persona that
        # the actual owner store does not know about, or whose real owner
        # does not match the asserted tenant; never fabricate one.
        persona = self._load_owner_bound_persona(persona_id, tenant_id)
        persisted = self._records.get(persona_id)
        if persisted is None:
            return self._virtual_initial_record(persona)
        if persisted.get("tenant_id") != tenant_id:
            raise TrainingTargetTenantMismatch()
        return dict(persisted)

    def _verify_semantic_payload(
        self,
        *,
        persona_id: str,
        tenant_id: str,
        request: CommitPersonaTrainingTargetRequest,
    ) -> None:
        """Independently re-derive every claimed digest from real content.

        A caller cannot commit (or replay) a training target by claiming a
        digest that does not actually match the candidate/control_state it
        submits, nor by attaching an evaluation proof that is internally
        inconsistent, bound to a different precondition/generation, or not
        ``passed``.
        """

        candidate_digest = _canonical_digest(request.candidate)
        if candidate_digest != request.candidate_digest:
            raise TrainingTargetProofInvalid(
                "candidate content does not match the claimed candidate_digest"
            )
        control_digest = _canonical_digest(request.control_state)
        if control_digest != request.control_digest:
            raise TrainingTargetProofInvalid(
                "control_state content does not match the claimed control_digest"
            )
        proof: Mapping[str, Any] = request.evaluation_proof
        if str(proof.get("status") or "").strip().lower() != "passed":
            raise TrainingTargetProofInvalid(
                "evaluation_proof status is not passed"
            )
        unsigned = {
            key: value
            for key, value in dict(proof).items()
            if key not in ("proof_digest", "runtime_evidence")
        }
        if _canonical_digest(unsigned) != request.proof_digest:
            raise TrainingTargetProofInvalid(
                "evaluation_proof proof_digest does not match its own content"
            )
        if _canonical_digest(proof.get("candidate_binding")) != candidate_digest:
            raise TrainingTargetProofInvalid(
                "evaluation_proof candidate_binding digest mismatch"
            )
        if _canonical_digest(proof.get("controls")) != control_digest:
            raise TrainingTargetProofInvalid(
                "evaluation_proof controls digest mismatch"
            )
        precondition = proof.get("target_precondition")
        if not isinstance(precondition, Mapping):
            raise TrainingTargetProofInvalid(
                "evaluation_proof target_precondition is missing"
            )
        if (
            precondition.get("persona_id") != persona_id
            or precondition.get("tenant_id") != tenant_id
            or precondition.get("expected_previous_generation")
            != request.expected_previous_generation
            or precondition.get("target_generation") != request.generation
            or precondition.get("precondition_digest")
            != request.expected_precondition_digest
            or precondition.get("controller_record_ref")
            != request.expected_precondition_record_ref
        ):
            raise TrainingTargetProofInvalid(
                "evaluation_proof target_precondition does not match this commit's binding"
            )
        authority = proof.get("authority")
        policy = authority.get("policy") if isinstance(authority, Mapping) else None
        if (
            not isinstance(policy, Mapping)
            or policy.get("approval_decision_ref") != request.approval_decision_ref
        ):
            raise TrainingTargetProofInvalid(
                "evaluation_proof policy authority does not match approval_decision_ref"
            )

    def _verify_approval_authority(
        self,
        *,
        persona_id: str,
        tenant_id: str,
        request: CommitPersonaTrainingTargetRequest,
    ) -> None:
        """Independently verify the claimed approval against real Governance truth.

        The happy-path training-session client validator does not secure this
        HTTP boundary: a caller hitting it directly must still prove a real,
        approved, unexpired Governance decision exists for this exact
        binding. An unavailable verifier fails closed rather than minting
        authority.
        """

        if self._approval_verifier is None:
            raise TrainingTargetApprovalUnavailable()
        verified = self._approval_verifier.verify_training_target_approval(
            approval_decision_id=request.approval_decision_id,
            approval_decision_ref=request.approval_decision_ref,
            target_version=str(request.generation),
            persona_id=persona_id,
            tenant_id=tenant_id,
            session_id=request.session_id,
            candidate_digest=request.candidate_digest,
            proof_digest=request.proof_digest,
        )
        if not verified:
            raise TrainingTargetApprovalInvalid(
                "approval_decision_id does not verify as an approved, unexpired, "
                "exactly bound Governance decision"
            )

    def _apply_to_persona_owner(
        self,
        persona_id: str,
        request: CommitPersonaTrainingTargetRequest,
        committed: Mapping[str, Any],
    ) -> None:
        """Apply the approved policy/control mutation to the real Persona owner.

        A training-target commit is a real, applied authority change, not a
        second receipt-only store: the actual candidate/control_state content
        (not just its digest) must observably land on the Persona record, and
        read back changed after a restart, once a target is committed.

        Idempotent and order-safe: a retry after a crash between durability
        and application re-applies the same generation's content without
        error, and a lower generation's apply that runs after a higher
        generation already landed is a safe no-op instead of clobbering
        newer state.

        The generation guard is checked by ``PersistentPersonaOwner.
        try_metadata_cas`` against the exact same snapshot its CAS commits
        against, not by a separate pre-read here: a plain pre-read-then-patch
        (the shape root review reproduced against the real JSON-backed
        owner) lets a concurrent higher-generation commit land between the
        read and the patch, and ``patch()``'s own retry loop re-reads fresh
        but applies this stale metadata unconditionally, silently
        overwriting the newer generation. Routing through
        ``try_metadata_cas`` means any such interleaving fails the CAS
        instead, and this loop retries with a fresh guard check.
        """

        def _guard(current: PersonaBody) -> bool:
            existing_metadata = dict(current.metadata or {})
            existing_generation = int(
                existing_metadata.get("training_target_generation") or 0
            )
            return existing_generation < request.generation

        for _attempt in range(4):
            try:
                applied_write, applied = self._persona_owner.try_metadata_cas(
                    persona_id,
                    guard=_guard,
                    metadata_updates={
                        "training_target_generation": request.generation,
                        "training_target_controller_record_ref": committed.get(
                            "controller_record_ref"
                        ),
                        "training_target_control_digest": request.control_digest,
                        "training_target_candidate_digest": request.candidate_digest,
                        "training_target_approval_decision_id": (
                            request.approval_decision_id
                        ),
                        "training_target_candidate": request.candidate,
                        "training_target_control_state": request.control_state,
                    },
                    actor_id="persona-training-target-owner",
                )
            except PersonaConcurrentUpdate:
                continue
            if not applied_write:
                # A same-or-higher generation is already applied; this is a
                # legitimate out-of-order no-op, not a race to retry.
                return
            applied_metadata = dict(applied.metadata or {})
            if (
                int(applied_metadata.get("training_target_generation") or 0)
                >= request.generation
                and applied_metadata.get("training_target_control_digest")
                == request.control_digest
                and applied_metadata.get("training_target_candidate_digest")
                == request.candidate_digest
            ):
                return
        raise PersonaConcurrentUpdate(
            f"Persona {persona_id!r} training-target application changed "
            "concurrently; retry against a fresh read"
        )

    def _finalize_committed(
        self, persona_id: str, expected_generation: int
    ) -> dict[str, Any]:
        """Move a durably-applied ``applying`` record to terminal ``committed``.

        Only issued after :meth:`_apply_to_persona_owner` has proven the real
        Persona record carries this exact generation's applied content --
        never before. If the process crashes before this runs, the record
        stays ``applying`` (not a false terminal ``committed``) and a later
        retry with the same binding re-applies (idempotently) and finalizes.
        """

        for _attempt in range(4):
            current = self._records.get(persona_id)
            if current is None or int(current.get("generation") or 0) != expected_generation:
                raise PersonaOwnerError(
                    f"Persona training target {persona_id!r} record missing or "
                    "changed during finalize"
                )
            if current.get("status") == "committed":
                return current
            finalized = {**current, "status": "committed"}
            ok, canonical = self._records.compare_and_set(
                persona_id, current, finalized
            )
            if ok:
                return canonical or finalized
        raise PersonaConcurrentUpdate(
            f"Persona training target {persona_id!r} changed concurrently "
            "during finalize; retry against a fresh read"
        )

    def commit(
        self,
        *,
        persona_id: str,
        tenant_id: str,
        request: CommitPersonaTrainingTargetRequest,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if request.persona_id != persona_id or request.tenant_id != tenant_id:
            raise PersonaOwnerError(
                "training target commit identity does not match request path/headers"
            )
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise PersonaOwnerError("Idempotency-Key header is required")
        binding = {
            field: getattr(request, field) for field in _TRAINING_TARGET_BINDING_FIELDS
        }
        for _attempt in range(4):
            persona = self._load_owner_bound_persona(persona_id, tenant_id)
            persisted = self._records.get(persona_id)
            if persisted is not None and persisted.get("tenant_id") != tenant_id:
                raise TrainingTargetTenantMismatch()
            current_generation = int((persisted or {}).get("generation") or 0)
            if current_generation == request.generation:
                if persisted is None:
                    raise TrainingTargetGenerationConflict(
                        "persona training target generation is stale"
                    )
                self._verify_semantic_payload(
                    persona_id=persona_id, tenant_id=tenant_id, request=request
                )
                stored_binding = {
                    field: persisted.get(field)
                    for field in _TRAINING_TARGET_BINDING_FIELDS
                }
                if (
                    stored_binding != binding
                    or persisted.get("idempotency_key") != clean_key
                ):
                    raise TrainingTargetIdempotencyConflict(
                        "persona training target idempotency key was reused for a "
                        "different payload"
                    )
                if persisted.get("status") != "committed":
                    # A prior attempt durably recorded this exact generation and
                    # binding but crashed (or lost a race) before the separate
                    # Persona apply/finalize completed. Replaying the identical
                    # binding recovers by re-applying (idempotently) and
                    # finalizing rather than returning a false terminal result.
                    self._apply_to_persona_owner(persona_id, request, persisted)
                    finalized = self._finalize_committed(
                        persona_id, request.generation
                    )
                    replayed = dict(finalized)
                    replayed["replayed"] = True
                    return replayed
                replayed = dict(persisted)
                replayed["replayed"] = True
                return replayed
            if current_generation != request.expected_previous_generation:
                raise TrainingTargetGenerationConflict(
                    "persona training target generation is stale"
                )
            actual_precondition = (
                persisted
                if persisted is not None
                else self._virtual_initial_record(persona)
            )
            actual_precondition_digest = _canonical_digest(actual_precondition)
            actual_precondition_record_ref = actual_precondition.get(
                "controller_record_ref"
            )
            if (
                request.expected_precondition_digest != actual_precondition_digest
                or request.expected_precondition_record_ref
                != actual_precondition_record_ref
            ):
                raise TrainingTargetProofInvalid(
                    "expected_precondition_digest/expected_precondition_record_ref "
                    "does not match the actual current owner record"
                )
            self._verify_semantic_payload(
                persona_id=persona_id, tenant_id=tenant_id, request=request
            )
            self._verify_approval_authority(
                persona_id=persona_id, tenant_id=tenant_id, request=request
            )
            pending = {
                **binding,
                # Durable but not yet terminal: the CAS below only proves this
                # binding was accepted, not that the real Persona owner record
                # has been mutated to match it yet. A crash or failure between
                # this write and the separate Persona patch below must not be
                # observable as a false terminal ``committed`` readback.
                "status": "applying",
                "authority_status": "authoritative",
                "controller_record_ref": (
                    f"persona-training-target:{persona_id}:{request.generation}:"
                    f"{uuid.uuid4().hex}"
                ),
                "recorded_at": _utc_now(),
                "approval_decision_id": request.approval_decision_id,
                "approval_decision_ref": request.approval_decision_ref,
                "expected_precondition_digest": request.expected_precondition_digest,
                "expected_precondition_record_ref": (
                    request.expected_precondition_record_ref
                ),
                "idempotency_key": clean_key,
                "replayed": False,
            }
            committed, canonical = self._records.compare_and_set(
                persona_id, persisted, pending
            )
            if committed:
                result = canonical or pending
                self._apply_to_persona_owner(persona_id, request, result)
                finalized = self._finalize_committed(persona_id, request.generation)
                return finalized
        raise PersonaConcurrentUpdate(
            f"Persona training target {persona_id!r} changed concurrently; "
            "retry against a fresh read"
        )


def build_training_target_approval_verifier() -> TrainingTargetApprovalVerifier | None:
    """Build the real Governance approval verifier from configured env, or None.

    A missing configuration is a real, typed contract dependency -- not
    something this owner may paper over. When unset, every commit fails
    closed with ``TRAINING_TARGET_APPROVAL_VERIFIER_UNAVAILABLE`` (503)
    instead of accepting an unverified approval.
    """

    base_url = str(
        os.getenv("PERSONA_TRAINING_TARGET_GOVERNANCE_BASE_URL") or ""
    ).strip()
    from services.service_token_file import configured_service_token

    variable = "PERSONA_GOVERNANCE_SERVICE_TOKEN"
    if not base_url or not (
        str(os.getenv(variable) or "").strip() or str(os.getenv(variable + "_FILE") or "").strip()
    ):
        return None
    # Read per call so issuer rotation applies; a configured unreadable file
    # fails closed instead of falling back to the env secret.
    return HttpGovernanceApprovalVerifier(
        base_url=base_url, service_token="",
        token_provider=lambda: configured_service_token(variable),
        timeout_seconds=float(os.getenv("PERSONA_GOVERNANCE_TIMEOUT_SECONDS", "5")),
    )


def build_governance_decision_verifier() -> GovernanceDecisionVerifier | None:
    """Real lifecycle decision verifier from env, or None (fails closed)."""

    verifier = build_training_target_approval_verifier()
    return verifier if isinstance(verifier, HttpGovernanceApprovalVerifier) else None


def build_persona_training_target_owner(
    persona_owner: PersistentPersonaOwner,
    *,
    approval_verifier: TrainingTargetApprovalVerifier | None = None,
) -> PersistentPersonaTrainingTargetOwner:
    backend = os.getenv(
        "PERSONA_TRAINING_TARGET_STORE_BACKEND",
        os.getenv("PERSONA_STORE_BACKEND", "json"),
    )
    dsn = (
        os.getenv("PERSONA_TRAINING_TARGET_STORE_DSN")
        or os.getenv("PERSONA_STORE_DSN")
        or os.getenv("DATABASE_URL")
    )
    path = os.getenv(
        "PERSONA_TRAINING_TARGET_STORE_PATH",
        "/tmp/pantheon/persona/training_targets.json",
    )
    records = build_record_store(
        backend=backend,
        dsn=dsn,
        table_name=os.getenv(
            "PERSONA_TRAINING_TARGET_STORE_TABLE",
            "persona.training_targets",
        ),
        json_path=path,
        owner_service="persona-svc",
    )
    return PersistentPersonaTrainingTargetOwner(
        records,
        persona_owner=persona_owner,
        approval_verifier=(
            approval_verifier
            if approval_verifier is not None
            else build_training_target_approval_verifier()
        ),
    )


def build_persona_owner() -> PersistentPersonaOwner:
    backend = os.getenv("PERSONA_STORE_BACKEND", "json")
    dsn = os.getenv("PERSONA_STORE_DSN") or os.getenv("DATABASE_URL")
    path = os.getenv(
        "PERSONA_STORE_PATH",
        "/tmp/pantheon/persona/personas.json",
    )
    records = build_record_store(
        backend=backend,
        dsn=dsn,
        table_name=os.getenv("PERSONA_STORE_TABLE", "persona.personas"),
        json_path=path,
        owner_service="persona-svc",
    )
    return PersistentPersonaOwner(records)


def build_capability_snapshot_owner() -> PersistentCapabilitySnapshotOwner:
    backend = os.getenv(
        "PERSONA_CAPABILITY_STORE_BACKEND",
        os.getenv("PERSONA_STORE_BACKEND", "json"),
    )
    dsn = (
        os.getenv("PERSONA_CAPABILITY_STORE_DSN")
        or os.getenv("PERSONA_STORE_DSN")
        or os.getenv("DATABASE_URL")
    )
    path = os.getenv(
        "PERSONA_CAPABILITY_STORE_PATH",
        "/tmp/pantheon/persona/capability_snapshots.json",
    )
    records = build_record_store(
        backend=backend,
        dsn=dsn,
        table_name=os.getenv(
            "PERSONA_CAPABILITY_STORE_TABLE",
            "persona.capability_snapshots",
        ),
        json_path=path,
        owner_service="persona-svc",
    )
    return PersistentCapabilitySnapshotOwner(records)


from services.persona.trade_reflection_pipeline import (
    ReflectionError,
    ReflectionProvider,
    ReflectionRequest,
    TradeReflectionPipeline,
    facts_snapshot,
)
from services.persona.trade_pattern_review import EpisodeLister, review_pattern, telemetry_episode_lister


class OpenClawReflectionProvider:
    name, model = "openclaw", "openclaw-structured-v1"

    def __init__(self, adapter_url: str | None = None, token: str | None = None) -> None:
        self.adapter_url = (adapter_url or os.getenv("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL") or os.getenv("OPENCLAW_GATEWAY_ADAPTER_URL") or os.getenv("PANTHEON_OPENCLAW_ADAPTER_URL") or os.getenv("OPENCLAW_ADAPTER_URL") or "").rstrip("/")
        self.token = token or os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN") or os.getenv("PERSONA_SERVICE_TOKEN") or ""

    def reflect(self, *, facts: Mapping[str, Any], trigger: str) -> Mapping[str, Any]:
        if not self.adapter_url: raise RuntimeError("OpenClaw adapter is not configured")
        body = {
            "prompt": f"Reflect on trade episode {trigger} with facts: {json.dumps(dict(facts), sort_keys=True)}",
            "extraction_schema": {
                "type": "object",
                "properties": {k: {"type": "object" if k == "expected_vs_actual" else ("string" if k == "attribution" else "array")} for k in ("expected_vs_actual", "attribution", "counterfactuals", "lesson_candidates")},
                "required": ["expected_vs_actual", "attribution", "counterfactuals", "lesson_candidates"],
            },
        }
        req = UrllibRequest(f"{self.adapter_url}/api/openclaw-adapter/assistant/providers/openclaw/structured", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", "X-Operator-Id": "persona-reflection", "X-Pantheon-Service-Token": self.token}, method="POST")
        try:
            with urlopen(req, timeout=10) as resp: return json.loads(resp.read().decode("utf-8"))["data"]["output"]["structured_data"]
        except Exception as exc: raise RuntimeError(f"OpenClaw reflection provider failed: {exc}") from exc


def _default_telemetry_fetcher(episode_id: str, tenant_id: str | None, authorization: str | None) -> dict[str, Any] | None:
    telemetry_url = (os.getenv("PANTHEON_TELEMETRY_API_URL") or os.getenv("PANTHEON_TELEMETRY_SERVICE_URL") or os.getenv("TELEMETRY_URL") or "").rstrip("/")
    if not telemetry_url: return None
    headers = {"Content-Type": "application/json", **({"Authorization": authorization} if authorization else {}), **({"X-Tenant-Id": tenant_id} if tenant_id else {})}
    try:
        with urlopen(UrllibRequest(f"{telemetry_url}/api/telemetry/trade-episodes/{episode_id}", headers=headers, method="GET"), timeout=5) as resp: return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404: return None
        raise
    except Exception: return None


def create_app(
    owner: PersistentPersonaOwner | None = None,
    *,
    capability_owner: PersistentCapabilitySnapshotOwner | None = None,
    training_target_owner: PersistentPersonaTrainingTargetOwner | None = None,
    governance_decision_verifier: GovernanceDecisionVerifier | None = None,
    reflection_provider: ReflectionProvider | None = None,
    telemetry_fetcher: Callable[[str, str | None, str | None], dict[str, Any] | None] | None = None,
    pattern_episode_lister: EpisodeLister | None = None,
) -> FastAPI:
    persistent_owner = owner or build_persona_owner()
    governance_decision_verifier = (
        governance_decision_verifier or build_governance_decision_verifier()
    )
    persistent_capability_owner = capability_owner or build_capability_snapshot_owner()
    persistent_training_target_owner = (
        training_target_owner
        or build_persona_training_target_owner(persistent_owner)
    )
    active_reflection_provider = reflection_provider or OpenClawReflectionProvider()
    active_telemetry_fetcher = telemetry_fetcher or _default_telemetry_fetcher
    active_episode_lister = pattern_episode_lister or telemetry_episode_lister()
    app = FastAPI(
        title="Pantheon Persona Registry Owner",
        version="1.0.0",
        description="Persistent Persona registry write-owner service",
    )
    app.state.persona_owner = persistent_owner

    @app.post(
        "/api/personas",
        response_model=PersonaBody,
        status_code=status.HTTP_201_CREATED,
    )
    def create_persona(
        body: CreatePersonaRequest,
        authorization: str | None = Header(default=None),
    ) -> PersonaBody:
        try:
            authority = _authenticate_persona_mutation(authorization)
            _require_persona_plane_owner(authority)
            body = _bind_authenticated_actor(body, authority)
            return persistent_owner.create(body)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        except PersonaAlreadyExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaOwnerError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/personas", response_model=list[PersonaBody])
    def list_personas(
        lifecycle_state: str | None = Query(default=None), status_value: str | None = Query(default=None, alias="status"),
        authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    ) -> list[PersonaBody]:
        has_private = any(_is_private_persona(r) for r in persistent_owner._records.list_all())
        if not authorization:
            if has_private: raise HTTPException(status_code=401, detail="UNAUTHORIZED: Missing authorization")
            return persistent_owner.list(lifecycle_state=lifecycle_state, status_value=status_value)
        try:
            authority = _authenticate_persona_read(authorization)
            if authority.token_kind == "service" and not tenant_id:
                return persistent_owner.list(lifecycle_state=lifecycle_state, status_value=status_value)
            if not tenant_id and not has_private:
                return persistent_owner.list(lifecycle_state=lifecycle_state, status_value=status_value)
            _, admitted = resolve_persona_tenant_scope(authorization, tenant_id)
            return persistent_owner.list(lifecycle_state=lifecycle_state, status_value=status_value, tenant_id=admitted)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc

    @app.get("/api/personas/{persona_id}", response_model=PersonaBody)
    def get_persona(persona_id: str, authorization: str | None = Header(default=None)) -> PersonaBody:
        raw = persistent_owner._records.get(persona_id)
        if raw is None: raise HTTPException(status_code=404, detail=f"Persona {persona_id!r} not found")
        has_private = _is_private_persona(raw)
        if not authorization:
            if has_private: raise HTTPException(status_code=401, detail="UNAUTHORIZED: Missing authorization")
            return persistent_owner.get(persona_id)
        try:
            authority = _authenticate_persona_read(authorization)
            if has_private and authority.token_kind != "service":
                tenant = raw.get("tenant_id")
                if not tenant: raise HTTPException(status_code=403, detail="FORBIDDEN: Persona has no tenant binding")
                resolve_persona_tenant_scope(authorization, tenant)
            return persistent_owner.get(persona_id)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc

    @app.patch("/api/personas/{persona_id}", response_model=PersonaBody)
    def patch_persona(
        persona_id: str,
        body: PatchPersonaRequest,
        authorization: str | None = Header(default=None),
    ) -> PersonaBody:
        try:
            authority = _authenticate_persona_mutation(authorization)
            _require_persona_plane_owner(authority)
            body = _bind_authenticated_actor(body, authority)
            return persistent_owner.patch(persona_id, body)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersonaConcurrentUpdate as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaOwnerError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch(
        "/api/personas/{persona_id}/lifecycle",
        response_model=PersonaBody,
    )
    def advance_persona_lifecycle(
        persona_id: str,
        body: AdvancePersonaLifecycleRequest,
        authorization: str | None = Header(default=None),
    ) -> PersonaBody:
        try:
            authority = _authenticate_persona_mutation(authorization)
            current = persistent_owner.get(persona_id)
            _require_lifecycle_authority(
                authority=authority,
                verifier=governance_decision_verifier,
                decision_id=body.governance_decision_id,
                persona_id=persona_id,
                tenant_id=current.tenant_id,
                source_state=current.lifecycle_state,
                target_state=body.target_state,
            )
            body = _bind_authenticated_actor(body, authority)
            return persistent_owner.advance_lifecycle(
                persona_id, body, expected_from_state=current.lifecycle_state
            )
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersonaConcurrentUpdate as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaOwnerError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put(
        "/api/personas/{persona_id}/capability-snapshots/{snapshot_id}",
        response_model=CapabilitySnapshotBody,
    )
    def upsert_capability_snapshot(
        persona_id: str,
        snapshot_id: str,
        body: UpsertCapabilitySnapshotRequest,
        authorization: str | None = Header(default=None),
    ) -> CapabilitySnapshotBody:
        try:
            authority = _authenticate_persona_mutation(authorization)
            _require_persona_plane_owner(authority)
            body = _bind_authenticated_actor(body, authority)
            if body.persona_id != persona_id or body.snapshot_id != snapshot_id:
                raise PersonaOwnerError(
                    "Capability snapshot path identity must match the request body"
                )
            persistent_owner.get(persona_id)
            return persistent_capability_owner.upsert(body)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CapabilitySnapshotConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaConcurrentUpdate as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaOwnerError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(
        "/api/capability-snapshots/{snapshot_id}",
        response_model=CapabilitySnapshotBody,
    )
    def get_capability_snapshot(snapshot_id: str) -> CapabilitySnapshotBody:
        try:
            return persistent_capability_owner.get(snapshot_id)
        except CapabilitySnapshotNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/personas/{persona_id}/capability-snapshot",
        response_model=CapabilitySnapshotBody,
    )
    def get_capability_snapshot_for_persona(
        persona_id: str,
    ) -> CapabilitySnapshotBody:
        try:
            persistent_owner.get(persona_id)
            return persistent_capability_owner.get_for_persona(persona_id)
        except (PersonaNotFound, CapabilitySnapshotNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/personas/{persona_id}/training-target")
    def get_persona_training_target(
        persona_id: str,
        tenant_id: str = Header(alias="X-Tenant-Id"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            authority = _authenticate_persona_mutation(authorization)
            _require_persona_plane_owner(authority)
            return persistent_training_target_owner.read(
                persona_id=persona_id, tenant_id=tenant_id
            )
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/personas/{persona_id}/training-target")
    def commit_persona_training_target(
        persona_id: str,
        body: CommitPersonaTrainingTargetRequest,
        tenant_id: str = Header(alias="X-Tenant-Id"),
        idempotency_key: str = Header(alias="Idempotency-Key"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            authority = _authenticate_persona_mutation(authorization)
            _require_persona_plane_owner(authority)
            return persistent_training_target_owner.commit(
                persona_id=persona_id,
                tenant_id=tenant_id,
                request=body,
                idempotency_key=idempotency_key,
            )
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (
            TrainingTargetGenerationConflict,
            TrainingTargetIdempotencyConflict,
            PersonaConcurrentUpdate,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaOwnerError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/personas/{persona_id}/trade-journal/{episode_id}/reflection:retry", status_code=status.HTTP_202_ACCEPTED)
    @app.post("/api/personas/{persona_id}/trade-reflections/{episode_id}:retry", status_code=status.HTTP_202_ACCEPTED)
    def retry_trade_reflection(persona_id: str, episode_id: str, body: dict[str, Any], idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise HTTPException(status_code=400, detail={"error": {"code": "VALIDATION_FAILED", "message": "Idempotency-Key is required"}})
        clean_key = idempotency_key.strip()
        try:
            authority, persona = _require_owner_persona(persistent_owner, persona_id, authorization)
            if not authority.tenant_id: raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": "Exact tenant match required"}})
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail={"error": {"code": exc.code, "message": exc.message}}) from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": str(exc)}}) from exc
        reason = str(body.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=422, detail={"error": {"code": "VALIDATION_FAILED", "message": "reason is required"}})
        content = {"episode_id": episode_id, "reason": reason, "facts_snapshot_ref": body.get("facts_snapshot_ref")}
        claim = {"content": content, "tenant_id": authority.tenant_id, "response": None}

        def _claim_guard(cur: PersonaBody) -> bool:
            p = dict((cur.metadata or {}).get("trade_reflection_idempotency") or {}).get(clean_key)
            if p is None: return True
            if p.get("tenant_id") and p.get("tenant_id") != authority.tenant_id:
                raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": "Exact tenant match required"}})
            if p.get("content") != content or p.get("response") is None:
                raise HTTPException(status_code=409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": "conflict or in-flight", "retryable": False}})
            return False

        for _ in range(5):
            try:
                ok, committed = persistent_owner.try_metadata_cas(persona_id, guard=_claim_guard, metadata_updates=lambda cur: {"trade_reflection_idempotency": {**dict((cur.metadata or {}).get("trade_reflection_idempotency") or {}), clean_key: claim}}, actor_id=authority.actor_id)
                if ok: break
                p = dict((committed.metadata or {}).get("trade_reflection_idempotency") or {}).get(clean_key)
                if p and p.get("response"): return {**p["response"], "meta": {**p["response"].get("meta", {}), "idempotent_replay": True}}
            except PersonaConcurrentUpdate: continue
        else: raise HTTPException(status_code=409, detail={"error": {"code": "CONCURRENT_UPDATE", "message": "Failed to claim invocation"}})

        try:
            try: raw_facts = active_telemetry_fetcher(episode_id, persona.tenant_id, authorization)
            except Exception as exc: raise HTTPException(status_code=503, detail={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": f"Telemetry unavailable: {exc}", "retryable": True}}) from exc
            if not raw_facts: raise HTTPException(status_code=404, detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": f"Trade episode {episode_id} not found"}})
            if (raw_facts.get("persona_id") and raw_facts["persona_id"] != persona_id) or (raw_facts.get("tenant_id") and raw_facts["tenant_id"] != persona.tenant_id):
                raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": "Access denied to episode"}})
            canon_ref, _, _ = facts_snapshot(raw_facts)
            claimed_ref = body.get("facts_snapshot_ref")
            if claimed_ref and claimed_ref != canon_ref:
                raise HTTPException(status_code=409, detail={"error": {"code": "IDEMPOTENCY_CONFLICT", "message": f"Claimed facts_snapshot_ref {claimed_ref} does not match canonical {canon_ref}", "retryable": False}})
            try:
                artifact = TradeReflectionPipeline(active_reflection_provider).process(ReflectionRequest(
                    request_id=f"reflection-{episode_id}-{uuid.uuid4().hex[:8]}", persona_id=persona_id,
                    trade_episode_ids=(episode_id,), trigger="manual_retry", facts=raw_facts, missing_refs=tuple(raw_facts.get("missing_refs") or ()),
                ))
            except Exception as exc: raise HTTPException(status_code=503, detail={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": f"Reflection generation failed: {exc}", "retryable": True}}) from exc
            resp = {
                "data": {"receipt_id": f"owner-{uuid.uuid4().hex[:8]}", "action": "reflection.retry", "persona_id": persona_id, "resource_id": episode_id, "status": "accepted", "facts_snapshot_ref": canon_ref, "reflection_id": artifact["reflection_id"]},
                "audit": {"durable": True, "record_ref": f"persona-metadata:{persona_id}:reflection:{artifact['reflection_id']}"},
            }
            for _ in range(5):
                try:
                    ok, _ = persistent_owner.try_metadata_cas(
                        persona_id, guard=lambda cur: True,
                        metadata_updates=lambda cur: {
                            "trade_reflections": [r for r in (cur.metadata or {}).get("trade_reflections", []) if r.get("trade_episode_id") != episode_id] + [artifact],
                            "trade_reflection_idempotency": {**dict((cur.metadata or {}).get("trade_reflection_idempotency") or {}), clean_key: {"content": content, "tenant_id": authority.tenant_id, "response": resp}},
                        },
                        actor_id=authority.actor_id,
                    )
                    if ok: return resp
                except PersonaConcurrentUpdate: continue
            raise HTTPException(status_code=409, detail={"error": {"code": "CONCURRENT_UPDATE", "message": "Failed to persist reflection"}})
        except Exception:
            try:
                persistent_owner.try_metadata_cas(persona_id, guard=lambda cur: dict((cur.metadata or {}).get("trade_reflection_idempotency") or {}).get(clean_key, {}).get("response") is None, metadata_updates=lambda cur: {"trade_reflection_idempotency": {k: v for k, v in dict((cur.metadata or {}).get("trade_reflection_idempotency") or {}).items() if k != clean_key}}, actor_id=authority.actor_id)
            except Exception: pass
            raise

    @app.post("/api/personas/{persona_id}/trade-reflections:pattern-review")
    def review_trade_pattern(persona_id: str, body: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Produce the scheduled_pattern reflection for one persona; the only writer of that trigger."""
        tenant_id = str(body.get("tenant_id") or "").strip()
        try:
            if not tenant_id: raise PersonaAuthorityError("TENANT_SCOPE_DENIED", "tenant_id is required", 403)
            authority, persona = _require_owner_persona(persistent_owner, persona_id, authorization)
            _, admitted = resolve_persona_tenant_scope(authorization, tenant_id)
            if admitted != tenant_id or persona.tenant_id != tenant_id:
                raise PersonaAuthorityError("TENANT_SCOPE_DENIED", "Exact tenant match required", 403)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail={"error": {"code": exc.code, "message": exc.message}}) from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": str(exc)}}) from exc
        try:
            result = review_pattern(
                persona_id=persona_id, tenant_id=tenant_id,
                existing=list((persona.metadata or {}).get("trade_reflections") or []),
                list_episodes=active_episode_lister, pipeline=TradeReflectionPipeline(active_reflection_provider),
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail={"error": {"code": "DEPENDENCY_UNAVAILABLE", "message": f"Pattern review failed: {exc}", "retryable": True}}) from exc
        artifact = result.pop("artifact", None)
        if artifact is None: return {"data": result}
        identity, snapshot_hash = artifact["trade_episode_id"], artifact["facts_snapshot_hash"]

        def _not_yet_persisted(cur: PersonaBody) -> bool:
            rows = (cur.metadata or {}).get("trade_reflections", [])
            covered = {e for r in rows if r.get("trigger") == "scheduled_pattern" for e in r.get("covered_episode_ids") or ()}
            return not covered.intersection(artifact.get("covered_episode_ids") or ()) and not any(r.get("trade_episode_id") == identity and r.get("facts_snapshot_hash") == snapshot_hash for r in rows)

        for _ in range(5):
            try:
                ok, _ = persistent_owner.try_metadata_cas(
                    persona_id, guard=_not_yet_persisted,
                    metadata_updates=lambda cur: {"trade_reflections": [r for r in (cur.metadata or {}).get("trade_reflections", []) if r.get("trade_episode_id") != identity] + [artifact]},
                    actor_id=authority.actor_id,
                )
                return {"data": {**result, "status": "reviewed" if ok else "unchanged", "reflection_id": artifact["reflection_id"] if ok else None}}
            except PersonaConcurrentUpdate: continue
        raise HTTPException(status_code=409, detail={"error": {"code": "CONCURRENT_UPDATE", "message": "Failed to persist pattern reflection"}})

    @app.get("/api/personas/{persona_id}/trade-reflections")
    def list_trade_reflections(persona_id: str, environment: str | None = Query(default=None), review_state: str | None = Query(default=None), authorization: str | None = Header(default=None)) -> dict[str, Any]:
        try:
            authority, persona = _require_owner_persona(persistent_owner, persona_id, authorization)
            if not authority.tenant_id: raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": "Exact tenant match required"}})
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail={"error": {"code": exc.code, "message": exc.message}}) from exc
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail={"error": {"code": "RESOURCE_NOT_FOUND", "message": str(exc)}}) from exc
        rows = [r for r in list((persona.metadata or {}).get("trade_reflections") or []) if (not environment or r.get("environment") == environment) and (not review_state or r.get("review_state") == review_state)]
        return {"data": rows, "meta": {"source": "persona_reflection", "count": len(rows), "tenant_id": authority.tenant_id}}

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "persona-svc",
            "persistent_record_count": len(persistent_owner.list()),
        }

    return app


app = create_app()


__all__ = [
    "AdvancePersonaLifecycleRequest",
    "CapabilitySnapshotBody",
    "CapabilitySnapshotConflict",
    "CapabilitySnapshotNotFound",
    "CommitPersonaTrainingTargetRequest",
    "CreatePersonaRequest",
    "HttpGovernanceApprovalVerifier",
    "PatchPersonaRequest",
    "PersistentCapabilitySnapshotOwner",
    "PersistentPersonaOwner",
    "PersistentPersonaTrainingTargetOwner",
    "PersonaAlreadyExists",
    "PersonaAuthorityError",
    "PersonaBody",
    "PersonaConcurrentUpdate",
    "PersonaInboundAuthority",
    "PersonaNotFound",
    "PersonaOwnerError",
    "GovernanceDecisionVerifier",
    "RequiredDataSourceBody",
    "TrainingTargetApprovalInvalid",
    "TrainingTargetApprovalUnavailable",
    "TrainingTargetApprovalVerifier",
    "TrainingTargetGenerationConflict",
    "TrainingTargetIdempotencyConflict",
    "TrainingTargetProofInvalid",
    "TrainingTargetTenantBindingUnavailable",
    "TrainingTargetTenantMismatch",
    "UpsertCapabilitySnapshotRequest",
    "app",
    "build_capability_snapshot_owner",
    "build_persona_owner",
    "build_persona_training_target_owner",
    "build_training_target_approval_verifier",
    "OpenClawReflectionProvider",
    "create_app",
]
