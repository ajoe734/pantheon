"""Typed BFF port for the Persona service's durable write owner.

This port is intentionally separate from :class:`ReadSurfacePorts`.  It adapts
the mounted BFF's narrow servant provisioning calls to the authoritative
Persona service application stores while also exposing owner-backed reads for
the read-only Persona projections.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from services.persona.write_owner import (
    AdvancePersonaLifecycleRequest,
    CapabilitySnapshotConflict,
    CapabilitySnapshotNotFound,
    CreatePersonaRequest,
    PatchPersonaRequest,
    PersistentCapabilitySnapshotOwner,
    PersistentPersonaOwner,
    PersonaAlreadyExists,
    PersonaConcurrentUpdate,
    PersonaNotFound,
    PersonaOwnerError,
    UpsertCapabilitySnapshotRequest,
    build_capability_snapshot_owner,
    build_persona_owner,
)


class PersonaWriteOwnerUnavailable(RuntimeError):
    """The authoritative Persona or capability store could not be reached."""

    def __init__(self, dependency: str, reason: str) -> None:
        super().__init__(reason)
        self.dependency = dependency
        self.reason = reason


class PersonaWriteConflict(ValueError):
    """A stable Persona or capability identity has divergent semantics."""


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(item).strip() for item in value if str(item).strip()]


class PersistentPersonaRegistryWritePort:
    """Production adapter over Persona-owned persistent application stores."""

    def __init__(
        self,
        *,
        persona_owner: PersistentPersonaOwner,
        capability_owner: PersistentCapabilitySnapshotOwner,
    ) -> None:
        self._persona_owner = persona_owner
        self._capability_owner = capability_owner

    @staticmethod
    def _persona_payload(value: Any) -> Dict[str, Any]:
        payload = value.model_dump(mode="json")
        payload["id"] = payload["persona_id"]
        metadata = dict(payload.get("metadata") or {})
        tenant_id = str(metadata.get("tenant_id") or metadata.get("tenantId") or "")
        if tenant_id:
            payload["tenant_id"] = tenant_id
            payload["tenantId"] = tenant_id
        payload["canonicalWriteAuthority"] = "persona_registry_service"
        return payload

    @staticmethod
    def _snapshot_payload(value: Any) -> Dict[str, Any]:
        payload = value.model_dump(mode="json")
        payload["id"] = payload["snapshot_id"]
        payload["canonicalWriteAuthority"] = "persona_capability_service"
        return payload

    @staticmethod
    def _unavailable(dependency: str, exc: Exception) -> PersonaWriteOwnerUnavailable:
        return PersonaWriteOwnerUnavailable(dependency, str(exc)[:300] or type(exc).__name__)

    def create_persona(
        self,
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        created_at: str | None = None,
        archetype: str = "generalist",
        lifecycle_state: str = "draft",
        risk_level: str = "low",
        mandate: str | None = None,
        strategy_family: str | None = None,
        traits: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        required_data_sources: list[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        del created_at
        owner_metadata = dict(metadata or {})
        owner_metadata.update(
            {
                "archetype": archetype,
                "risk_level": risk_level,
            }
        )
        if traits:
            owner_metadata["traits"] = dict(traits)
        try:
            created = self._persona_owner.create(
                CreatePersonaRequest(
                    actor_id=actor_id,
                    persona_id=persona_id,
                    name=name,
                    mandate=mandate or archetype,
                    lifecycle_state=lifecycle_state,
                    strategy_family=strategy_family or archetype,
                    owner=actor_id,
                    required_data_sources=list(required_data_sources or []),
                    metadata=owner_metadata,
                )
            )
            return self._persona_payload(created)
        except PersonaAlreadyExists:
            try:
                existing = self._persona_owner.get(persona_id)
            except Exception as exc:  # noqa: BLE001
                raise self._unavailable("persona_registry_write_owner", exc) from exc
            existing_metadata = dict(existing.metadata or {})
            identity_fields = ("tenant_id", "agora_user_id", "persona_class")
            if any(
                str(existing_metadata.get(field) or "")
                != str(owner_metadata.get(field) or "")
                for field in identity_fields
            ):
                raise PersonaWriteConflict(
                    f"Persona {persona_id!r} already belongs to another owner scope"
                )
            return self._persona_payload(existing)
        except PersonaOwnerError as exc:
            raise PersonaWriteConflict(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_registry_write_owner", exc) from exc

    def update_persona(
        self,
        persona_id: str,
        *,
        name: str | None = None,
        actor_id: str | None = None,
        updated_at: str | None = None,
        archetype: str | None = None,
        lifecycle_state: str | None = None,
        risk_level: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        del updated_at
        clean_actor = str(actor_id or "persona-service")
        owner_metadata = dict(metadata or {})
        if archetype is not None:
            owner_metadata["archetype"] = archetype
        if risk_level is not None:
            owner_metadata["risk_level"] = risk_level
        try:
            current = self._persona_owner.get(persona_id)
            patch_fields: Dict[str, Any] = {
                "actor_id": clean_actor,
                "metadata": owner_metadata,
            }
            if name is not None:
                patch_fields["name"] = name
            if archetype is not None:
                patch_fields["mandate"] = archetype
                patch_fields["strategy_family"] = archetype
            updated = self._persona_owner.patch(
                persona_id,
                PatchPersonaRequest(**patch_fields),
            )
            if lifecycle_state == "paper_only" and current.lifecycle_state == "draft":
                updated = self._persona_owner.advance_lifecycle(
                    persona_id,
                    AdvancePersonaLifecycleRequest(
                        actor_id=clean_actor,
                        target_state="research_only",
                    ),
                )
            return self._persona_payload(updated)
        except PersonaNotFound:
            return None
        except (PersonaConcurrentUpdate, PersonaOwnerError) as exc:
            raise PersonaWriteConflict(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_registry_write_owner", exc) from exc

    def get_persona(self, persona_id: str | None) -> Optional[Dict[str, Any]]:
        if not str(persona_id or "").strip():
            return None
        try:
            return self._persona_payload(self._persona_owner.get(str(persona_id)))
        except PersonaNotFound:
            return None
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_registry_write_owner", exc) from exc

    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            records = self._persona_owner.list(
                lifecycle_state=kwargs.get("lifecycle_state"),
                status_value=kwargs.get("status"),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_registry_write_owner", exc) from exc
        mandate = str(kwargs.get("mandate") or "")
        strategy_family = str(kwargs.get("strategy_family") or "")
        payloads = [self._persona_payload(record) for record in records]
        if mandate:
            payloads = [item for item in payloads if item.get("mandate") == mandate]
        if strategy_family:
            payloads = [
                item for item in payloads
                if item.get("strategy_family") == strategy_family
            ]
        return payloads

    def upsert_persona_capability_snapshot(
        self,
        *,
        snapshot_id: str,
        persona_id: str,
        capabilities: list[str],
        generated_at: str,
        source_refs: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        actor_id: str | None = None,
        effective_tools: list[str] | None = None,
        effective_skills: list[str] | None = None,
        effective_workflows: list[str] | None = None,
        restrictions: list[str] | None = None,
        **_kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            self._persona_owner.get(persona_id)
            snapshot = self._capability_owner.upsert(
                UpsertCapabilitySnapshotRequest(
                    actor_id=str(actor_id or "persona-service"),
                    snapshot_id=snapshot_id,
                    persona_id=persona_id,
                    capabilities=_clean_list(capabilities),
                    generated_at=generated_at,
                    source_refs=_clean_list(source_refs),
                    metadata=dict(metadata or {}),
                    effective_tools=_clean_list(effective_tools),
                    effective_skills=_clean_list(effective_skills),
                    effective_workflows=_clean_list(effective_workflows),
                    restrictions=_clean_list(restrictions),
                )
            )
            return self._snapshot_payload(snapshot)
        except (CapabilitySnapshotConflict, PersonaConcurrentUpdate) as exc:
            raise PersonaWriteConflict(str(exc)) from exc
        except PersonaNotFound as exc:
            raise PersonaWriteConflict(str(exc)) from exc
        except PersonaOwnerError as exc:
            raise PersonaWriteConflict(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_capability_write_owner", exc) from exc

    def get_capability_snapshot(
        self,
        snapshot_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        if not str(snapshot_id or "").strip():
            return None
        try:
            return self._snapshot_payload(self._capability_owner.get(str(snapshot_id)))
        except CapabilitySnapshotNotFound:
            return None
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_capability_write_owner", exc) from exc

    def get_capability_snapshot_for_persona(
        self,
        persona_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        if not str(persona_id or "").strip():
            return None
        try:
            return self._snapshot_payload(
                self._capability_owner.get_for_persona(str(persona_id))
            )
        except CapabilitySnapshotNotFound:
            return None
        except Exception as exc:  # noqa: BLE001
            raise self._unavailable("persona_capability_write_owner", exc) from exc

    def get_persona_capabilities(
        self,
        persona_id: str,
    ) -> Optional[Dict[str, Any]]:
        return self.get_capability_snapshot_for_persona(persona_id)

    # Read-only Persona projection compatibility. These relationships remain
    # owned by their own services and are never written by this adapter.
    def get_bindings_for_persona(self, _persona_id: str | None) -> list[Dict[str, Any]]:
        return []

    def list_sessions_for_persona(
        self,
        _persona_id: str,
        **_kwargs: Any,
    ) -> list[Dict[str, Any]]:
        return []

    def list_teaching_sessions_for_persona(
        self,
        _persona_id: str,
        **_kwargs: Any,
    ) -> list[Dict[str, Any]]:
        return []


def create_persona_registry_write_owner() -> PersistentPersonaRegistryWritePort:
    """Build the production port from the Persona service's configured stores."""

    return PersistentPersonaRegistryWritePort(
        persona_owner=build_persona_owner(),
        capability_owner=build_capability_snapshot_owner(),
    )


__all__ = [
    "PersistentPersonaRegistryWritePort",
    "PersonaWriteConflict",
    "PersonaWriteOwnerUnavailable",
    "create_persona_registry_write_owner",
]
