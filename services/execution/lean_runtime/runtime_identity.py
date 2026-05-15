"""Runtime identity and authority context for paper/live execution containers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from services.runtime_auth import RuntimeManagerAuthConfig, resolve_runtime_manager_auth


def _clean(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _runtime_id(env: Mapping[str, str]) -> str | None:
    return (
        _clean(env.get("PANTHEON_RUNTIME_ID"))
        or _clean(env.get("PANTHEON_PAPER_RUNTIME_ID"))
        or _clean(env.get("PANTHEON_LIVE_RUNTIME_ID"))
    )


@dataclass(frozen=True)
class RuntimeIdentity:
    runtime_role: str
    runtime_mode: str
    runtime_id: str | None
    runtime_manager_url: str | None
    telemetry_url: str | None
    workspace_ref: str | None
    auth_profile_ref: str | None
    persona_id: str | None
    session_id: str | None
    trace_id: str | None
    request_id: str | None
    binding_id: str | None
    capital_pool_id: str | None
    artifact_id: str | None
    artifact_version: str | None
    deployment_stage: str | None
    plan_id: str | None
    persona_capital_binding_id: str | None
    runtime_manager_auth: RuntimeManagerAuthConfig

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeIdentity":
        env_map = env or os.environ
        return cls(
            runtime_role=_clean(env_map.get("PANTHEON_RUNTIME_ROLE")) or "pantheon-lean-paper-runtime",
            runtime_mode=_clean(env_map.get("PANTHEON_RUNTIME_MODE")) or "paper",
            runtime_id=_runtime_id(env_map),
            runtime_manager_url=_clean(env_map.get("PANTHEON_RUNTIME_MANAGER_URL")),
            telemetry_url=_clean(env_map.get("PANTHEON_TELEMETRY_URL")),
            workspace_ref=_clean(env_map.get("PANTHEON_WORKSPACE_REF")),
            auth_profile_ref=_clean(env_map.get("PANTHEON_AUTH_PROFILE_REF")),
            persona_id=_clean(env_map.get("PANTHEON_PERSONA_ID")),
            session_id=_clean(env_map.get("PANTHEON_SESSION_ID")),
            trace_id=_clean(env_map.get("PANTHEON_TRACE_ID")),
            request_id=_clean(env_map.get("PANTHEON_REQUEST_ID")),
            binding_id=_clean(env_map.get("PANTHEON_RUNTIME_BINDING_ID")),
            capital_pool_id=_clean(env_map.get("PANTHEON_CAPITAL_POOL_ID")),
            artifact_id=_clean(env_map.get("PANTHEON_ARTIFACT_ID")),
            artifact_version=_clean(env_map.get("PANTHEON_ARTIFACT_VERSION")),
            deployment_stage=_clean(env_map.get("PANTHEON_DEPLOYMENT_STAGE")),
            plan_id=_clean(env_map.get("PANTHEON_DEPLOYMENT_PLAN_ID")),
            persona_capital_binding_id=_clean(env_map.get("PANTHEON_PERSONA_CAPITAL_BINDING_ID")),
            runtime_manager_auth=resolve_runtime_manager_auth(env=env_map),
        )

    @property
    def isolation_ready(self) -> bool:
        return bool(self.workspace_ref and self.auth_profile_ref)

    @property
    def binding_context_complete(self) -> bool:
        required = (
            self.binding_id,
            self.runtime_id,
            self.capital_pool_id,
            self.artifact_id,
            self.artifact_version,
            self.deployment_stage,
            self.plan_id,
            self.persona_capital_binding_id,
        )
        return all(required)

    def authority_refs(self) -> dict[str, Any]:
        refs: dict[str, Any] = {
            "write_owner": "runtime-manager",
            "authority_source": "runtime_binding",
            "runtime_role": self.runtime_role,
            "runtime_mode": self.runtime_mode,
        }
        optional = {
            "workspace_ref": self.workspace_ref,
            "auth_profile_ref": self.auth_profile_ref,
            "persona_id": self.persona_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
        }
        refs.update({key: value for key, value in optional.items() if value})
        return refs

    def telemetry_binding_context(self) -> dict[str, Any]:
        context = {
            "binding_id": self.binding_id,
            "runtime_id": self.runtime_id,
            "capital_pool_id": self.capital_pool_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "deployment_stage": self.deployment_stage,
            "plan_id": self.plan_id,
            "persona_capital_binding_id": self.persona_capital_binding_id,
        }
        filtered = {key: value for key, value in context.items() if value}
        authority_refs = self.authority_refs()
        if authority_refs:
            filtered["authority_refs"] = authority_refs
        return filtered

    def to_health_payload(self) -> dict[str, Any]:
        return {
            "runtime_role": self.runtime_role,
            "runtime_mode": self.runtime_mode,
            "runtime_id": self.runtime_id,
            "runtime_manager_url": self.runtime_manager_url,
            "telemetry_url": self.telemetry_url,
            "workspace_ref": self.workspace_ref,
            "auth_profile_ref": self.auth_profile_ref,
            "persona_id": self.persona_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "isolation_ready": self.isolation_ready,
            "binding_context_complete": self.binding_context_complete,
            "runtime_manager_auth": self.runtime_manager_auth.status_dict(),
            "authority_refs": self.authority_refs(),
        }
