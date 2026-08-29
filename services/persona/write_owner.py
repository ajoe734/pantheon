"""Persistent write owner for Persona registry records.

The Persona service is the only writer exposed by this module.  It stores every
record through a durable owner store and reads the store again for every GET;
there is deliberately no process-local overlay, cache, fixture seed, or response
fallback.  BFF callers are expected to use this HTTP boundary instead of
``ReadSurfaceStore`` mutations.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.foundation.reliable_delivery import (
    AtomicJsonRecordStore,
    build_record_store,
)


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


class PersonaOwnerError(ValueError):
    """Base error for Persona owner validation failures."""


class PersonaAlreadyExists(PersonaOwnerError):
    """Raised when a create collides with a persisted Persona identity."""


class PersonaNotFound(PersonaOwnerError):
    """Raised when a persisted Persona cannot be found."""


class PersonaConcurrentUpdate(PersonaOwnerError):
    """Raised when repeated compare-and-set attempts lose a write race."""


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


class PersistentPersonaOwner:
    """Persona registry application service over one persistent owner store."""

    def __init__(self, records: _OwnerRecordStore) -> None:
        self._records = records

    @classmethod
    def from_json_path(cls, path: str | Path) -> "PersistentPersonaOwner":
        return cls(AtomicJsonRecordStore(path))

    def create(self, request: CreatePersonaRequest) -> PersonaBody:
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
        return self.patch(
            persona_id,
            PatchPersonaRequest(
                actor_id=request.actor_id,
                lifecycle_state=request.target_state,
            ),
        )

    @staticmethod
    def _patched_record(
        current: Mapping[str, Any],
        request: PatchPersonaRequest,
    ) -> dict[str, Any]:
        record = dict(current)
        patch_fields = request.model_fields_set - {"actor_id"}
        if "lifecycle_state" in patch_fields:
            target_state = str(request.lifecycle_state or "")
            current_state = str(record.get("lifecycle_state") or "")
            if target_state != current_state and target_state not in _LIFECYCLE_TRANSITIONS.get(
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


def create_app(owner: PersistentPersonaOwner | None = None) -> FastAPI:
    persistent_owner = owner or build_persona_owner()
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
    def create_persona(body: CreatePersonaRequest) -> PersonaBody:
        try:
            return persistent_owner.create(body)
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
    def patch_persona(persona_id: str, body: PatchPersonaRequest) -> PersonaBody:
        try:
            return persistent_owner.patch(persona_id, body)
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
    ) -> PersonaBody:
        try:
            return persistent_owner.advance_lifecycle(persona_id, body)
        except PersonaNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersonaConcurrentUpdate as exc:
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
    "CreatePersonaRequest",
    "PatchPersonaRequest",
    "PersistentPersonaOwner",
    "PersonaAlreadyExists",
    "PersonaBody",
    "PersonaConcurrentUpdate",
    "PersonaNotFound",
    "PersonaOwnerError",
    "RequiredDataSourceBody",
    "app",
    "build_persona_owner",
    "create_app",
]
