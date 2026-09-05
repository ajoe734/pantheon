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
from typing import Any, Mapping, Protocol
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
    """Authenticated identity used for Persona mutation policy decisions."""

    actor_id: str
    roles: frozenset[str]
    token_kind: str


class GovernanceDecisionVerifier(Protocol):
    """Verify one exact Persona lifecycle decision against Governance truth."""

    def verify_persona_lifecycle_decision(
        self,
        *,
        decision_id: str,
        persona_id: str,
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
        self, *, base_url: str, service_token: str, timeout_seconds: float = 5.0
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout_seconds = timeout_seconds

    def verify_training_target_approval(
        self,
        *,
        approval_decision_id: str,
        approval_decision_ref: str,
        persona_id: str,
        tenant_id: str,
        session_id: str,
        candidate_digest: str,
        proof_digest: str,
    ) -> bool:
        url = f"{self._base_url}/api/governance/approvals/{approval_decision_id}"
        request = UrllibRequest(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._service_token}",
                "X-Tenant-Id": tenant_id,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                if int(response.status) != 200:
                    return False
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return False
        if not isinstance(body, Mapping):
            return False
        decision = body.get("approval")
        if not isinstance(decision, Mapping):
            decision = body.get("approval_decision")
        if not isinstance(decision, Mapping):
            decision = body

        lifecycle = str(decision.get("decision_state") or "").strip().lower()
        outcome = str(decision.get("decision") or "").strip().lower()
        if lifecycle not in {"decided", "approved"}:
            return False
        if outcome and outcome != "approved":
            return False
        if not outcome and lifecycle != "approved":
            return False
        if str(decision.get("persona_id") or "") != persona_id:
            return False
        if str(decision.get("tenant_id") or "") != tenant_id:
            return False
        if str(decision.get("session_id") or "") != session_id:
            return False
        if str(decision.get("candidate_digest") or "") != candidate_digest:
            return False
        if str(decision.get("proof_digest") or "") != proof_digest:
            return False
        declared_ref = decision.get("approval_decision_ref")
        identity = decision.get("decision_id") or decision.get("approval_id")
        if declared_ref is not None:
            if str(declared_ref) != approval_decision_ref:
                return False
        elif str(identity or "") != approval_decision_ref:
            return False
        expires_at = decision.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at.strip():
            return False
        try:
            normalized = (
                expires_at[:-1] + "+00:00" if expires_at.endswith("Z") else expires_at
            )
            parsed_expiry = datetime.fromisoformat(normalized)
        except ValueError:
            return False
        if parsed_expiry.tzinfo is None:
            return False
        if parsed_expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
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
            required_roles=tuple(sorted(_AUTHENTICATED_MUTATION_ROLES)),
            mfa_required=False,
            env=_persona_auth_env(),
        )
    except AuthError as exc:
        raise PersonaAuthorityError(exc.code, exc.message, exc.status_code) from exc
    return PersonaInboundAuthority(
        actor_id=context.actor_id,
        roles=context.roles,
        token_kind=context.token_kind,
    )


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
    source_state: str,
    target_state: str,
) -> None:
    transition = (source_state, target_state)
    policy_roles = _LIFECYCLE_POLICY_ROLES.get(transition)
    if policy_roles is None:
        raise PersonaOwnerError(
            f"invalid lifecycle transition {source_state!r} -> {target_state!r}"
        )
    if not authority.roles.isdisjoint(policy_roles):
        return

    clean_decision_id = str(decision_id or "").strip()
    if not clean_decision_id or verifier is None:
        raise PersonaAuthorityError(
            "LIFECYCLE_AUTHORITY_REQUIRED",
            "Lifecycle transition requires its policy owner or a verified Governance decision",
            403,
        )
    try:
        verified = verifier.verify_persona_lifecycle_decision(
            decision_id=clean_decision_id,
            persona_id=persona_id,
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
        self,
        *,
        lifecycle_state: str | None = None,
        status_value: str | None = None,
    ) -> list[PersonaBody]:
        records = [PersonaBody.model_validate(record) for record in self._records.list_all()]
        if lifecycle_state is not None:
            records = [item for item in records if item.lifecycle_state == lifecycle_state]
        if status_value is not None:
            records = [item for item in records if item.status == status_value]
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

    def advance_lifecycle(
        self,
        persona_id: str,
        request: AdvancePersonaLifecycleRequest,
    ) -> PersonaBody:
        lifecycle_patch = PatchPersonaRequest(
            actor_id=request.actor_id,
            lifecycle_state=request.target_state,
        )
        for _attempt in range(4):
            current = self._records.get(persona_id)
            if current is None:
                raise PersonaNotFound(f"Persona {persona_id!r} not found")
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
        ``owner`` field -- established only through the ``persona.admin``
        gated create/patch path -- never from a caller-asserted header. A
        caller cannot obtain an authoritative-looking precondition for a
        tenant it does not actually own; ``read``/``commit`` reject any
        ``X-Tenant-Id`` that does not match this real owner field before this
        view is ever returned.
        """

        tenant_id = str(persona.owner)
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
        """Read the real Persona and require the caller's tenant to be its owner.

        The Persona's ``owner`` field is the only genuine, governed tenant
        binding this data model currently has (set at creation and only
        mutable through the ``persona.admin`` gated patch path). Trusting a
        caller-supplied ``X-Tenant-Id`` header instead of this field is
        exactly the fabricated-authority defect root review flagged.
        """

        persona = self._persona_owner.get(persona_id)
        if str(persona.owner) != tenant_id:
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
        """

        for _attempt in range(4):
            current = self._persona_owner.get(persona_id)
            existing_metadata = dict(current.metadata or {})
            existing_generation = int(
                existing_metadata.get("training_target_generation") or 0
            )
            if existing_generation >= request.generation:
                return
            try:
                self._persona_owner.patch(
                    persona_id,
                    PatchPersonaRequest(
                        actor_id="persona-training-target-owner",
                        metadata={
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
                    ),
                )
            except PersonaConcurrentUpdate:
                continue
            applied = self._persona_owner.get(persona_id)
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
    service_token = str(
        os.getenv("PANTHEON_GOVERNANCE_SERVICE_TOKEN")
        or os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN")
        or ""
    ).strip()
    if not base_url or not service_token:
        return None
    return HttpGovernanceApprovalVerifier(
        base_url=base_url, service_token=service_token
    )


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


def create_app(
    owner: PersistentPersonaOwner | None = None,
    *,
    capability_owner: PersistentCapabilitySnapshotOwner | None = None,
    training_target_owner: PersistentPersonaTrainingTargetOwner | None = None,
    governance_decision_verifier: GovernanceDecisionVerifier | None = None,
) -> FastAPI:
    persistent_owner = owner or build_persona_owner()
    persistent_capability_owner = capability_owner or build_capability_snapshot_owner()
    persistent_training_target_owner = (
        training_target_owner
        or build_persona_training_target_owner(persistent_owner)
    )
    app = FastAPI(
        title="Pantheon Persona Registry Owner",
        version="1.0.0",
        description="Persistent Persona registry write-owner service",
    )

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
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        except PersonaAlreadyExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersonaOwnerError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/personas", response_model=list[PersonaBody])
    def list_personas(
        lifecycle_state: str | None = Query(default=None),
        status_value: str | None = Query(default=None, alias="status"),
    ) -> list[PersonaBody]:
        return persistent_owner.list(
            lifecycle_state=lifecycle_state,
            status_value=status_value,
        )

    @app.get("/api/personas/{persona_id}", response_model=PersonaBody)
    def get_persona(persona_id: str) -> PersonaBody:
        try:
            return persistent_owner.get(persona_id)
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
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
                source_state=current.lifecycle_state,
                target_state=body.target_state,
            )
            body = _bind_authenticated_actor(body, authority)
            return persistent_owner.advance_lifecycle(persona_id, body)
        except PersonaAuthorityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
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
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
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
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
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
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
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
    "TrainingTargetTenantMismatch",
    "UpsertCapabilitySnapshotRequest",
    "app",
    "build_capability_snapshot_owner",
    "build_persona_owner",
    "build_persona_training_target_owner",
    "build_training_target_approval_verifier",
    "create_app",
]
