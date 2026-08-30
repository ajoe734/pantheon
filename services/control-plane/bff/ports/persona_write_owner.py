"""Typed HTTP port for the Persona service's durable write owner.

The BFF never imports Persona application stores or opens Persona-owned tables.
Both writes and their read-after-write projections cross the deployed Persona
service boundary with a bounded timeout and a dedicated service credential.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional


_PERSONA_URL_ENVS = (
    "PERSONA_URL",
    "PANTHEON_PERSONA_URL",
    "PANTHEON_PERSONA_API_URL",
)
_PERSONA_SERVICE_TOKEN_ENVS = (
    "PANTHEON_PERSONA_SERVICE_TOKEN",
    "PERSONA_SERVICE_TOKEN",
)


class PersonaWriteOwnerUnavailable(RuntimeError):
    """The authoritative Persona or capability service could not be reached."""

    def __init__(self, dependency: str, reason: str) -> None:
        super().__init__(reason)
        self.dependency = dependency
        self.reason = reason


class PersonaWriteConflict(ValueError):
    """A stable Persona or capability identity has divergent semantics."""


class _PersonaHttpResponseError(RuntimeError):
    def __init__(self, status_code: int, reason: str) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _timeout_from_env() -> float:
    raw = str(
        os.getenv("PANTHEON_PERSONA_TIMEOUT_SECONDS")
        or os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS")
        or "2.0"
    ).strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 2.0


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _error_reason(raw: bytes, fallback: str) -> str:
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return fallback
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:300]
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            if str(message or "").strip():
                return str(message).strip()[:300]
    return fallback


class PersonaRegistryHttpWritePort:
    """Production BFF adapter over the Persona owner's authenticated HTTP API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str | None = None,
        timeout_seconds: float | None = None,
        service_actor_id: str | None = None,
        opener: Any = None,
    ) -> None:
        resolved_url = base_url if base_url is not None else _first_env(_PERSONA_URL_ENVS)
        resolved_token = (
            service_token
            if service_token is not None
            else _first_env(_PERSONA_SERVICE_TOKEN_ENVS)
        )
        self._base_url = str(resolved_url or "").strip().rstrip("/")
        self._service_token = str(resolved_token or "").strip()
        self._timeout_seconds = (
            max(float(timeout_seconds), 0.1)
            if timeout_seconds is not None
            else _timeout_from_env()
        )
        self._service_actor_id = str(
            service_actor_id
            or os.getenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID")
            or "operator-bff"
        ).strip()
        self._opener = opener or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._service_token)

    @staticmethod
    def _persona_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(value)
        payload["id"] = payload.get("persona_id")
        metadata = dict(payload.get("metadata") or {})
        tenant_id = str(metadata.get("tenant_id") or metadata.get("tenantId") or "")
        if tenant_id:
            payload["tenant_id"] = tenant_id
            payload["tenantId"] = tenant_id
        payload["canonicalWriteAuthority"] = "persona_registry_service"
        return payload

    @staticmethod
    def _snapshot_payload(value: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(value)
        payload["id"] = payload.get("snapshot_id")
        payload["canonicalWriteAuthority"] = "persona_capability_service"
        return payload

    def _require_configuration(self, dependency: str, *, write: bool) -> None:
        if not self._base_url:
            raise PersonaWriteOwnerUnavailable(
                dependency,
                "Persona service URL is not configured",
            )
        if write and not self._service_token:
            raise PersonaWriteOwnerUnavailable(
                dependency,
                "Persona service credential is not configured",
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        dependency: str,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        write: bool = False,
    ) -> Any:
        self._require_configuration(dependency, write=write)
        url = f"{self._base_url}{path}"
        if params:
            clean_params = {
                key: value
                for key, value in params.items()
                if value is not None and str(value).strip()
            }
            if clean_params:
                url = f"{url}?{urllib.parse.urlencode(clean_params)}"
        encoded = None
        headers = {"Accept": "application/json"}
        if body is not None:
            encoded = json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        request = urllib.request.Request(
            url,
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            reason = _error_reason(raw, f"Persona service returned HTTP {exc.code}")
            raise _PersonaHttpResponseError(exc.code, reason) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise PersonaWriteOwnerUnavailable(
                dependency,
                f"Persona service request failed: {type(exc).__name__}",
            ) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PersonaWriteOwnerUnavailable(
                dependency,
                "Persona service returned invalid JSON",
            ) from exc

    @staticmethod
    def _raise_write_error(
        exc: _PersonaHttpResponseError,
        dependency: str,
    ) -> None:
        if exc.status_code in {400, 404, 409, 422}:
            raise PersonaWriteConflict(exc.reason) from exc
        raise PersonaWriteOwnerUnavailable(dependency, exc.reason) from exc

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
                "requested_by": actor_id,
            }
        )
        if traits:
            owner_metadata["traits"] = dict(traits)
        body = {
            "actor_id": self._service_actor_id,
            "persona_id": persona_id,
            "name": name,
            "mandate": mandate or archetype,
            "lifecycle_state": lifecycle_state,
            "strategy_family": strategy_family or archetype,
            "owner": actor_id,
            "required_data_sources": list(required_data_sources or []),
            "metadata": owner_metadata,
        }
        try:
            created = self._request(
                "POST",
                "/api/personas",
                dependency="persona_registry_write_owner",
                body=body,
                write=True,
            )
        except _PersonaHttpResponseError as exc:
            if exc.status_code != 409:
                self._raise_write_error(exc, "persona_registry_write_owner")
            existing = self.get_persona(persona_id)
            if existing is None:
                raise PersonaWriteOwnerUnavailable(
                    "persona_registry_write_owner",
                    "Persona create conflicted without canonical readback",
                ) from exc
            existing_metadata = dict(existing.get("metadata") or {})
            identity_fields = ("tenant_id", "agora_user_id", "persona_class")
            if any(
                str(existing_metadata.get(field) or "")
                != str(owner_metadata.get(field) or "")
                for field in identity_fields
            ):
                raise PersonaWriteConflict(
                    f"Persona {persona_id!r} already belongs to another owner scope"
                ) from exc
            return existing
        if not isinstance(created, dict):
            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner",
                "Persona service returned an invalid create response",
            )
        return self._persona_payload(created)

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
        current = self.get_persona(persona_id)
        if current is None:
            return None
        owner_metadata = dict(metadata or {})
        owner_metadata["requested_by"] = str(actor_id or "")
        if archetype is not None:
            owner_metadata["archetype"] = archetype
        if risk_level is not None:
            owner_metadata["risk_level"] = risk_level
        patch: Dict[str, Any] = {
            "actor_id": self._service_actor_id,
            "metadata": owner_metadata,
        }
        if name is not None:
            patch["name"] = name
        if archetype is not None:
            patch["mandate"] = archetype
            patch["strategy_family"] = archetype
        try:
            updated = self._request(
                "PATCH",
                f"/api/personas/{urllib.parse.quote(persona_id, safe='')}",
                dependency="persona_registry_write_owner",
                body=patch,
                write=True,
            )
            target_lifecycle = lifecycle_state
            if target_lifecycle in {"paper_only", "paper_running", "active"}:
                target_lifecycle = "research_only"
            if target_lifecycle == "research_only" and current.get("lifecycle_state") == "draft":
                updated = self._request(
                    "PATCH",
                    f"/api/personas/{urllib.parse.quote(persona_id, safe='')}/lifecycle",
                    dependency="persona_registry_write_owner",
                    body={
                        "actor_id": self._service_actor_id,
                        "target_state": "research_only",
                    },
                    write=True,
                )
        except _PersonaHttpResponseError as exc:
            if exc.status_code == 404:
                return None
            self._raise_write_error(exc, "persona_registry_write_owner")
        if not isinstance(updated, dict):
            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner",
                "Persona service returned an invalid update response",
            )
        return self._persona_payload(updated)

    def get_persona(self, persona_id: str | None) -> Optional[Dict[str, Any]]:
        clean_id = str(persona_id or "").strip()
        if not clean_id:
            return None
        try:
            value = self._request(
                "GET",
                f"/api/personas/{urllib.parse.quote(clean_id, safe='')}",
                dependency="persona_registry_write_owner",
            )
        except _PersonaHttpResponseError as exc:
            if exc.status_code == 404:
                return None
            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner",
                exc.reason,
            ) from exc
        if not isinstance(value, dict):
            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner",
                "Persona service returned an invalid read response",
            )
        return self._persona_payload(value)

    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            values = self._request(
                "GET",
                "/api/personas",
                dependency="persona_registry_write_owner",
                params={
                    "lifecycle_state": kwargs.get("lifecycle_state"),
                    "status": kwargs.get("status"),
                },
            )
        except _PersonaHttpResponseError as exc:
            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner",
                exc.reason,
            ) from exc
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise PersonaWriteOwnerUnavailable(
                "persona_registry_write_owner",
                "Persona service returned an invalid list response",
            )
        payloads = [self._persona_payload(item) for item in values]
        mandate = str(kwargs.get("mandate") or "")
        strategy_family = str(kwargs.get("strategy_family") or "")
        if mandate:
            payloads = [item for item in payloads if item.get("mandate") == mandate]
        if strategy_family:
            payloads = [
                item
                for item in payloads
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
        body = {
            "actor_id": self._service_actor_id,
            "snapshot_id": snapshot_id,
            "persona_id": persona_id,
            "capabilities": _clean_list(capabilities),
            "generated_at": generated_at,
            "source_refs": _clean_list(source_refs),
            "metadata": {
                **dict(metadata or {}),
                "requested_by": str(actor_id or ""),
            },
            "effective_tools": _clean_list(effective_tools),
            "effective_skills": _clean_list(effective_skills),
            "effective_workflows": _clean_list(effective_workflows),
            "restrictions": _clean_list(restrictions),
        }
        try:
            value = self._request(
                "PUT",
                "/api/personas/"
                f"{urllib.parse.quote(persona_id, safe='')}/capability-snapshots/"
                f"{urllib.parse.quote(snapshot_id, safe='')}",
                dependency="persona_capability_write_owner",
                body=body,
                write=True,
            )
        except _PersonaHttpResponseError as exc:
            self._raise_write_error(exc, "persona_capability_write_owner")
        if not isinstance(value, dict):
            raise PersonaWriteOwnerUnavailable(
                "persona_capability_write_owner",
                "Persona service returned an invalid capability response",
            )
        return self._snapshot_payload(value)

    def get_capability_snapshot(
        self,
        snapshot_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        clean_id = str(snapshot_id or "").strip()
        if not clean_id:
            return None
        try:
            value = self._request(
                "GET",
                f"/api/capability-snapshots/{urllib.parse.quote(clean_id, safe='')}",
                dependency="persona_capability_write_owner",
            )
        except _PersonaHttpResponseError as exc:
            if exc.status_code == 404:
                return None
            raise PersonaWriteOwnerUnavailable(
                "persona_capability_write_owner",
                exc.reason,
            ) from exc
        if not isinstance(value, dict):
            raise PersonaWriteOwnerUnavailable(
                "persona_capability_write_owner",
                "Persona service returned an invalid capability read response",
            )
        return self._snapshot_payload(value)

    def get_capability_snapshot_for_persona(
        self,
        persona_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        clean_id = str(persona_id or "").strip()
        if not clean_id:
            return None
        try:
            value = self._request(
                "GET",
                f"/api/personas/{urllib.parse.quote(clean_id, safe='')}/capability-snapshot",
                dependency="persona_capability_write_owner",
            )
        except _PersonaHttpResponseError as exc:
            if exc.status_code == 404:
                return None
            raise PersonaWriteOwnerUnavailable(
                "persona_capability_write_owner",
                exc.reason,
            ) from exc
        if not isinstance(value, dict):
            raise PersonaWriteOwnerUnavailable(
                "persona_capability_write_owner",
                "Persona service returned an invalid capability read response",
            )
        return self._snapshot_payload(value)

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


def create_persona_registry_write_owner() -> PersonaRegistryHttpWritePort:
    """Build the production BFF port from Persona service configuration only."""

    return PersonaRegistryHttpWritePort()


__all__ = [
    "PersonaRegistryHttpWritePort",
    "PersonaWriteConflict",
    "PersonaWriteOwnerUnavailable",
    "create_persona_registry_write_owner",
]
