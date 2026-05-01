"""Pantheon runtime context model and source validation.

This module is intentionally side-effect free. Bootstrap code can load a
PantheonRuntimeContext from an auditable launch manifest or from whitelisted
environment variables without importing control-plane services.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
)


_VALID_STAGES = {"dev", "paper", "staging", "canary", "live", "prod", "production"}
_MANAGED_STAGES = {"paper", "staging", "canary", "live", "prod", "production"}
_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "bearer",
    "broker_secret",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_SECRET_REFERENCE_SUFFIXES = (
    "_ref",
    "_id",
    "_ids",
    "_profile",
    "_status",
    "_status_ref",
)
_SECRET_REFERENCE_KEYS = {
    "auth_profile_ref",
    "required_secret_keys",
    "secret_material_path_ref",
    "secret_ref",
    "secret_refs",
    "secret_status",
    "token_file",
}


class RuntimeContextError(ValueError):
    """Raised when runtime context cannot be loaded or validated safely."""


class RuntimeContextSource(str, Enum):
    LAUNCH_MANIFEST = "launch_manifest"
    ENV_VARS = "env_vars"
    LOCAL_DEV_SEED = "local_dev_seed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RuntimeContextArtifact:
    artifact_id: str
    artifact_version: str
    artifact_checksum: str
    strategy_id: str


@dataclass(frozen=True)
class RuntimeContextCapital:
    capital_pool_id: str
    persona_capital_binding_id: str | None = None


@dataclass(frozen=True)
class RuntimeContextBridge:
    repo: str
    path: str
    commit: str
    runtime_adapter_version: str = "0.1.0"


@dataclass(frozen=True)
class RuntimeContextTrace:
    trace_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class PantheonRuntimeContext:
    runtime_binding_id: str
    runtime_id: str
    deployment_plan_id: str
    deployment_stage: str
    runtime_role: str
    artifact: RuntimeContextArtifact
    capital: RuntimeContextCapital
    bridge: RuntimeContextBridge
    trace: RuntimeContextTrace
    context_source: RuntimeContextSource

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["context_source"] = self.context_source.value
        return payload

    @classmethod
    def from_manifest(
        cls,
        manifest_path: str | Path,
        *,
        expected_stage: str | None = None,
        managed_runtime: bool = True,
    ) -> "PantheonRuntimeContext":
        try:
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeContextError(f"launch manifest is not valid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise RuntimeContextError("launch manifest must be a JSON object")
        return cls.from_mapping(
            payload,
            source=RuntimeContextSource.LAUNCH_MANIFEST,
            expected_stage=expected_stage,
            managed_runtime=managed_runtime,
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        expected_stage: str | None = None,
        managed_runtime: bool = True,
    ) -> "PantheonRuntimeContext":
        _reject_raw_secret_env(env)
        payload = {
            "runtime_binding_id": env.get("PANTHEON_RUNTIME_BINDING_ID"),
            "runtime_id": env.get("PANTHEON_RUNTIME_ID") or env.get("PANTHEON_PAPER_RUNTIME_ID"),
            "deployment_plan_id": env.get("PANTHEON_DEPLOYMENT_PLAN_ID"),
            "deployment_stage": env.get("PANTHEON_DEPLOYMENT_STAGE") or env.get("PANTHEON_RUNTIME_MODE"),
            "runtime_role": env.get("PANTHEON_RUNTIME_ROLE"),
            "artifact": {
                "artifact_id": env.get("PANTHEON_ARTIFACT_ID"),
                "artifact_version": env.get("PANTHEON_ARTIFACT_VERSION"),
                "artifact_checksum": env.get("PANTHEON_ARTIFACT_CHECKSUM"),
                "strategy_id": env.get("PANTHEON_STRATEGY_ID"),
            },
            "capital": {
                "capital_pool_id": env.get("PANTHEON_CAPITAL_POOL_ID"),
                "persona_capital_binding_id": env.get("PANTHEON_PERSONA_CAPITAL_BINDING_ID"),
            },
            "bridge": {
                "repo": env.get("PANTHEON_ENGINE_BRIDGE_REMOTE"),
                "path": env.get("PANTHEON_ENGINE_BRIDGE_SOURCE_PATH")
                or env.get("PANTHEON_ENGINE_BRIDGE_PATH"),
                "commit": env.get("PANTHEON_ENGINE_BRIDGE_COMMIT"),
                "runtime_adapter_version": env.get("PANTHEON_RUNTIME_ADAPTER_VERSION") or "0.1.0",
            },
            "trace": {
                "trace_id": env.get("PANTHEON_TRACE_ID"),
                "correlation_id": env.get("PANTHEON_CORRELATION_ID") or env.get("PANTHEON_REQUEST_ID"),
            },
        }
        return cls.from_mapping(
            payload,
            source=RuntimeContextSource.ENV_VARS,
            expected_stage=expected_stage,
            managed_runtime=managed_runtime,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        source: RuntimeContextSource | str,
        expected_stage: str | None = None,
        managed_runtime: bool = True,
    ) -> "PantheonRuntimeContext":
        _reject_raw_secrets(value, "runtime_context")
        payload = _normalize_payload(value)
        _reject_raw_secrets(payload, "runtime_context")

        source_mode = RuntimeContextSource(source)
        stage = _normalize_stage(_required(payload, "deployment_stage"))
        expected = _normalize_stage(expected_stage) if expected_stage else None
        if expected and stage != expected:
            raise RuntimeContextError(
                f"deployment_stage {stage!r} does not match expected stage {expected!r}"
            )

        context = cls(
            runtime_binding_id=_required(payload, "runtime_binding_id"),
            runtime_id=_required(payload, "runtime_id"),
            deployment_plan_id=_required(payload, "deployment_plan_id"),
            deployment_stage=stage,
            runtime_role=_string_or_default(payload.get("runtime_role"), stage),
            artifact=RuntimeContextArtifact(
                artifact_id=_required(payload, "artifact.artifact_id"),
                artifact_version=_required(payload, "artifact.artifact_version"),
                artifact_checksum=_required(payload, "artifact.artifact_checksum", "artifact.checksum"),
                strategy_id=_required(payload, "artifact.strategy_id"),
            ),
            capital=RuntimeContextCapital(
                capital_pool_id=_required(payload, "capital.capital_pool_id"),
                persona_capital_binding_id=_optional(payload, "capital.persona_capital_binding_id"),
            ),
            bridge=RuntimeContextBridge(
                repo=_required(payload, "bridge.repo", "bridge.remote"),
                path=_required(payload, "bridge.source_path", "bridge.path"),
                commit=_required(payload, "bridge.commit"),
                runtime_adapter_version=_string_or_default(
                    _optional(payload, "bridge.runtime_adapter_version"),
                    "0.1.0",
                ),
            ),
            trace=RuntimeContextTrace(
                trace_id=_optional(payload, "trace.trace_id") or _optional(payload, "trace_id"),
                correlation_id=_optional(payload, "trace.correlation_id")
                or _optional(payload, "correlation_id")
                or _optional(payload, "request_id"),
            ),
            context_source=source_mode,
        )
        context.validate(managed_runtime=managed_runtime)
        return context

    def validate(self, *, managed_runtime: bool = True) -> None:
        if managed_runtime and self.deployment_stage in _MANAGED_STAGES and not self.runtime_binding_id:
            raise RuntimeContextError("runtime_binding_id is required for deployment-managed runtime")
        if self.bridge.repo != PANTHEON_LEAN_REMOTE:
            raise RuntimeContextError(
                f"bridge.repo must be {PANTHEON_LEAN_REMOTE!r}, got {self.bridge.repo!r}"
            )
        if self.bridge.path != PANTHEON_LEAN_SOURCE_PATH:
            raise RuntimeContextError(
                f"bridge.path must be {PANTHEON_LEAN_SOURCE_PATH!r}, got {self.bridge.path!r}"
            )
        _reject_raw_secrets(self.to_dict(), "runtime_context")


def _normalize_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    if "runtime_context" in payload and isinstance(payload["runtime_context"], Mapping):
        payload = dict(payload["runtime_context"])
    if "artifact" not in payload:
        payload["artifact"] = {
            "artifact_id": payload.get("artifact_id"),
            "artifact_version": payload.get("artifact_version"),
            "artifact_checksum": payload.get("artifact_checksum") or payload.get("checksum"),
            "strategy_id": payload.get("strategy_id"),
        }
    if "capital" not in payload:
        payload["capital"] = {
            "capital_pool_id": payload.get("capital_pool_id"),
            "persona_capital_binding_id": payload.get("persona_capital_binding_id"),
        }
    if "bridge" not in payload:
        payload["bridge"] = {
            "repo": payload.get("engine_bridge_repo"),
            "path": payload.get("engine_bridge_path") or payload.get("engine_bridge_source_path"),
            "commit": payload.get("engine_bridge_commit"),
            "runtime_adapter_version": payload.get("runtime_adapter_version") or "0.1.0",
        }
    if "trace" not in payload:
        payload["trace"] = {
            "trace_id": payload.get("trace_id"),
            "correlation_id": payload.get("correlation_id") or payload.get("request_id"),
        }
    if "runtime_binding_id" not in payload and payload.get("binding_id"):
        payload["runtime_binding_id"] = payload.get("binding_id")
    if "deployment_plan_id" not in payload and payload.get("plan_id"):
        payload["deployment_plan_id"] = payload.get("plan_id")
    if "deployment_stage" not in payload and payload.get("deployment_mode"):
        payload["deployment_stage"] = payload.get("deployment_mode")
    return payload


def _required(mapping: Mapping[str, Any], *paths: str) -> str:
    value = _optional(mapping, *paths)
    if not value:
        raise RuntimeContextError(f"{paths[0]} is required")
    return value


def _optional(mapping: Mapping[str, Any], *paths: str) -> str | None:
    for path in paths:
        value = _get_path(mapping, path)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _get_path(mapping: Mapping[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _normalize_stage(value: str) -> str:
    stage = str(value or "").strip().lower()
    if stage not in _VALID_STAGES:
        raise RuntimeContextError(f"invalid deployment_stage: {value!r}")
    if stage == "production":
        return "prod"
    return stage


def _string_or_default(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _reject_raw_secret_env(env: Mapping[str, str]) -> None:
    for key, value in env.items():
        key_text = str(key).strip().lower()
        if not key_text.startswith("pantheon_"):
            continue
        if _looks_like_raw_secret_key(key_text) and str(value or "").strip():
            raise RuntimeContextError(f"{key} must not be used as runtime context input")


def _reject_raw_secrets(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).strip().lower()
            child_path = f"{path}.{key}"
            if _looks_like_raw_secret_key(key_text) and _has_non_empty_value(child):
                raise RuntimeContextError(f"{child_path} must not include raw secrets")
            _reject_raw_secrets(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_secrets(child, f"{path}[{index}]")


def _looks_like_raw_secret_key(key: str) -> bool:
    if key in _SECRET_REFERENCE_KEYS or key.endswith(_SECRET_REFERENCE_SUFFIXES):
        return False
    return any(marker in key for marker in _SECRET_KEY_MARKERS)


def _has_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
