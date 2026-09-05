"""Persistent write owner for Persona registry records.

The Persona service is the only writer exposed by this module.  It stores every
record through a durable owner store and reads the store again for every GET;
there is deliberately no process-local overlay, cache, fixture seed, or response
fallback.  BFF callers are expected to use this HTTP boundary instead of
``ReadSurfaceStore`` mutations.
"""
from __future__ import annotations

import hmac
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.foundation.reliable_delivery import (
    AtomicJsonRecordStore,
    build_record_store,
)
from services.runtime_auth_inbound import AuthContext, AuthError, validate_request_auth


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    candidate: Any = None
    control_state: Any = None
    evaluation_proof: Any = None

    @model_validator(mode="after")
    def validate_generation_successor(self) -> "CommitPersonaTrainingTargetRequest":
        if self.generation != self.expected_previous_generation + 1:
            raise ValueError(
                "generation must be exactly expected_previous_generation + 1"
            )
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
    ) -> None:
        self._records = records
        self._persona_owner = persona_owner

    @classmethod
    def from_json_path(
        cls, path: str | Path, *, persona_owner: PersistentPersonaOwner
    ) -> "PersistentPersonaTrainingTargetOwner":
        return cls(AtomicJsonRecordStore(path), persona_owner=persona_owner)

    def _load_or_init(self, persona_id: str, tenant_id: str) -> dict[str, Any]:
        # Fail closed against a training-target authority for a Persona that the
        # actual owner store does not know about; never fabricate one.
        self._persona_owner.get(persona_id)
        current = self._records.get(persona_id)
        if current is None:
            initial = {
                "persona_id": persona_id,
                "tenant_id": tenant_id,
                "status": "active",
                "generation": 0,
                "authority_status": "authoritative",
                "controller_record_ref": (
                    f"persona-training-target:{persona_id}:0:{uuid.uuid4().hex}"
                ),
                "recorded_at": _utc_now(),
            }
            _committed, canonical = self._records.compare_and_set(
                persona_id, None, initial
            )
            current = canonical or initial
        if current.get("tenant_id") != tenant_id:
            raise TrainingTargetTenantMismatch()
        return current

    def read(self, *, persona_id: str, tenant_id: str) -> dict[str, Any]:
        return dict(self._load_or_init(persona_id, tenant_id))

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
            current = self._load_or_init(persona_id, tenant_id)
            current_generation = int(current.get("generation") or 0)
            if current_generation == request.generation:
                if current.get("status") != "committed":
                    raise TrainingTargetGenerationConflict(
                        "persona training target generation is stale"
                    )
                stored_binding = {
                    field: current.get(field) for field in _TRAINING_TARGET_BINDING_FIELDS
                }
                if stored_binding != binding:
                    raise TrainingTargetIdempotencyConflict(
                        "persona training target idempotency key was reused for a "
                        "different payload"
                    )
                replayed = dict(current)
                replayed["replayed"] = True
                return replayed
            if current_generation != request.expected_previous_generation:
                raise TrainingTargetGenerationConflict(
                    "persona training target generation is stale"
                )
            updated = {
                **binding,
                "status": "committed",
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
                persona_id, current, updated
            )
            if committed:
                return canonical or updated
        raise PersonaConcurrentUpdate(
            f"Persona training target {persona_id!r} changed concurrently; "
            "retry against a fresh read"
        )


def build_persona_training_target_owner(
    persona_owner: PersistentPersonaOwner,
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
    return PersistentPersonaTrainingTargetOwner(records, persona_owner=persona_owner)


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
    "TrainingTargetGenerationConflict",
    "TrainingTargetIdempotencyConflict",
    "TrainingTargetTenantMismatch",
    "UpsertCapabilitySnapshotRequest",
    "app",
    "build_capability_snapshot_owner",
    "build_persona_owner",
    "build_persona_training_target_owner",
    "create_app",
]
