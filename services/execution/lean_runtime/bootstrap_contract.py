"""Runtime bootstrap request contract and materializer.

This module is intentionally pure: it accepts DeploymentPlan/RuntimeBinding-like
objects or dictionaries and returns a serializable RuntimeBootstrapRequest. The
later bootstrap wiring can pass the request through a manifest or environment
without importing governance-plane modules from the execution container.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping


PANTHEON_LEAN_REMOTE = "ajoe734/pantheon-lean.git"
PANTHEON_LEAN_SOURCE_PATH = "pantheon/lean"
PANTHEON_LEAN_RUNTIME_PATH = "/workspace/lean"
DEFAULT_RUNTIME_CONFIG_REF = "/workspace/lean/Launcher/config.json"

_VALID_STAGES = {"paper", "canary", "live", "frozen"}
_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "bearer",
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
    "_keys",
    "_path",
    "_profile",
    "_status",
    "_status_ref",
)
_SECRET_REFERENCE_KEYS = {
    "auth_profile_ref",
    "required_secret_keys",
    "secret_ref",
    "secret_refs",
    "secret_status",
    "token_file",
}


class BootstrapContractError(ValueError):
    """Raised when a RuntimeBootstrapRequest cannot be materialized safely."""


@dataclass(frozen=True)
class RuntimeBridgeIdentity:
    path: str
    remote: str
    commit: str
    source_path: str = PANTHEON_LEAN_SOURCE_PATH

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeBootstrapArtifact:
    artifact_id: str
    artifact_version: str
    checksum: str
    strategy_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeBootstrapCapital:
    capital_pool_id: str
    persona_capital_binding_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeBootstrapConfig:
    config_ref: str
    paper_mode: bool
    live_broker_enabled: bool = False
    health_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeBootstrapRequest:
    request_id: str
    trace_id: str
    runtime_binding_id: str
    deployment_plan_id: str
    runtime_role: str
    deployment_stage: str
    runtime_id: str
    bridge: RuntimeBridgeIdentity
    artifact: RuntimeBootstrapArtifact
    capital: RuntimeBootstrapCapital
    runtime_config: RuntimeBootstrapConfig
    approval_decision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.approval_decision_id is None:
            payload.pop("approval_decision_id", None)
        if not self.metadata:
            payload.pop("metadata", None)
        return payload

    def to_runtime_env(self) -> dict[str, str]:
        """Return non-secret environment variables for runtime_bootstrap wiring."""
        env = {
            "PANTHEON_REQUEST_ID": self.request_id,
            "PANTHEON_TRACE_ID": self.trace_id,
            "PANTHEON_RUNTIME_BINDING_ID": self.runtime_binding_id,
            "PANTHEON_DEPLOYMENT_PLAN_ID": self.deployment_plan_id,
            "PANTHEON_RUNTIME_ROLE": self.runtime_role,
            "PANTHEON_RUNTIME_MODE": self.deployment_stage,
            "PANTHEON_RUNTIME_ID": self.runtime_id,
            "PANTHEON_DEPLOYMENT_STAGE": self.deployment_stage,
            "PANTHEON_ARTIFACT_ID": self.artifact.artifact_id,
            "PANTHEON_ARTIFACT_VERSION": self.artifact.artifact_version,
            "PANTHEON_ARTIFACT_CHECKSUM": self.artifact.checksum,
            "PANTHEON_STRATEGY_ID": self.artifact.strategy_id,
            "PANTHEON_CAPITAL_POOL_ID": self.capital.capital_pool_id,
            "PANTHEON_PERSONA_CAPITAL_BINDING_ID": self.capital.persona_capital_binding_id,
            "PANTHEON_ENGINE_BRIDGE_PATH": self.bridge.path,
            "PANTHEON_ENGINE_BRIDGE_SOURCE_PATH": self.bridge.source_path,
            "PANTHEON_ENGINE_BRIDGE_REMOTE": self.bridge.remote,
            "PANTHEON_ENGINE_BRIDGE_COMMIT": self.bridge.commit,
            "PANTHEON_RUNTIME_CONFIG_REF": self.runtime_config.config_ref,
            "PANTHEON_PAPER_MODE": str(self.runtime_config.paper_mode).lower(),
            "PANTHEON_LIVE_BROKER_ENABLED": str(self.runtime_config.live_broker_enabled).lower(),
            "PANTHEON_HEALTH_ONLY": str(self.runtime_config.health_only).lower(),
        }
        if self.approval_decision_id:
            env["PANTHEON_APPROVAL_DECISION_ID"] = self.approval_decision_id
        return env


def materialize_runtime_bootstrap_request(
    *,
    deployment_plan: Mapping[str, Any] | Any,
    runtime_binding: Mapping[str, Any] | Any,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> RuntimeBootstrapRequest:
    """Build a RuntimeBootstrapRequest from plan and binding evidence.

    The materializer accepts both the current Pantheon field names
    (``plan_id``, ``binding_id``, ``deployment_mode``) and the SD aliases
    (``deployment_plan_id``, ``runtime_binding_id``, ``deployment_stage``).
    """
    plan = _to_mapping(deployment_plan, "deployment_plan")
    binding = _to_mapping(runtime_binding, "runtime_binding")
    _reject_raw_secrets(plan, "deployment_plan")
    _reject_raw_secrets(binding, "runtime_binding")
    _reject_lean_platform_target(plan, "deployment_plan")
    _reject_lean_platform_target(binding, "runtime_binding")

    plan_id = _first_non_empty(plan, "deployment_plan_id", "plan_id")
    binding_id = _first_non_empty(binding, "runtime_binding_id", "binding_id")
    if not plan_id:
        raise BootstrapContractError("deployment_plan_id is required")
    if not binding_id:
        raise BootstrapContractError("runtime_binding_id is required")

    binding_plan_id = _first_non_empty(binding, "deployment_plan_id", "plan_id")
    if binding_plan_id and binding_plan_id != plan_id:
        raise BootstrapContractError(
            f"RuntimeBinding plan reference {binding_plan_id!r} does not match DeploymentPlan {plan_id!r}"
        )

    stage = _normalize_stage(_first_non_empty(binding, "deployment_stage", "deployment_mode"))
    plan_stage = _normalize_stage(_first_non_empty(plan, "target_stage", "deployment_stage"))
    if plan_stage and stage and plan_stage != stage:
        raise BootstrapContractError(
            f"RuntimeBinding deployment stage {stage!r} does not match DeploymentPlan target_stage {plan_stage!r}"
        )
    deployment_stage = stage or plan_stage
    if not deployment_stage:
        raise BootstrapContractError("deployment_stage is required")

    artifact_id = _matching_required(plan, binding, "artifact_id")
    artifact_version = _matching_required(plan, binding, "artifact_version")
    capital_pool_id = _matching_required(plan, binding, "capital_pool_id")
    persona_capital_binding_id = _matching_required(
        plan,
        binding,
        "persona_capital_binding_id",
        binding_required=True,
        plan_required=False,
    )
    runtime_id = _first_non_empty(binding, "runtime_id")
    strategy_id = (
        _first_non_empty(plan, "strategy_id", "artifact.strategy_id")
        or _metadata_value(plan, "strategy_id")
        or _metadata_value(binding, "strategy_id")
    )
    checksum = _first_non_empty(
        plan,
        "artifact_checksum",
        "checksum",
        "artifact.checksum",
    ) or _metadata_value(plan, "artifact_checksum", "checksum") or _metadata_value(
        binding,
        "artifact_checksum",
        "checksum",
    )

    if not runtime_id:
        raise BootstrapContractError("runtime_id is required")
    if not strategy_id:
        raise BootstrapContractError("strategy_id is required")
    if not checksum:
        raise BootstrapContractError("artifact checksum is required")

    bridge = _materialize_bridge(plan, binding)
    runtime_role = _runtime_role(plan, binding, deployment_stage)
    runtime_config = _materialize_runtime_config(plan, binding, deployment_stage)

    return RuntimeBootstrapRequest(
        request_id=request_id or _generated_id("rbr"),
        trace_id=trace_id or _generated_id("trace"),
        runtime_binding_id=binding_id,
        deployment_plan_id=plan_id,
        runtime_role=runtime_role,
        deployment_stage=deployment_stage,
        runtime_id=runtime_id,
        bridge=bridge,
        artifact=RuntimeBootstrapArtifact(
            artifact_id=artifact_id,
            artifact_version=artifact_version,
            checksum=checksum,
            strategy_id=strategy_id,
        ),
        capital=RuntimeBootstrapCapital(
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        ),
        runtime_config=runtime_config,
        approval_decision_id=_first_non_empty(plan, "approval_decision_id"),
        metadata={
            "source": "deployment_plan_runtime_binding",
            "write_owner": "runtime-manager",
        },
    )


def _to_mapping(value: Mapping[str, Any] | Any, label: str) -> dict[str, Any]:
    if value is None:
        raise BootstrapContractError(f"{label} is required")
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        if not isinstance(result, Mapping):
            raise BootstrapContractError(f"{label}.to_dict() must return a mapping")
        return dict(result)
    if is_dataclass(value):
        return asdict(value)
    raise BootstrapContractError(f"{label} must be a mapping or expose to_dict()")


def _generated_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _stringify(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value).strip()


def _first_non_empty(mapping: Mapping[str, Any], *paths: str) -> str | None:
    for path in paths:
        value = _get_path(mapping, path)
        if value is not None and _stringify(value):
            return _stringify(value)
    return None


def _get_path(mapping: Mapping[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _metadata_value(mapping: Mapping[str, Any], *keys: str) -> str | None:
    metadata = mapping.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    return _first_non_empty(metadata, *keys)


def _normalize_stage(value: str | None) -> str | None:
    if not value:
        return None
    stage = value.strip().lower()
    if stage not in _VALID_STAGES:
        raise BootstrapContractError(f"Invalid deployment_stage: {value!r}")
    return stage


def _matching_required(
    plan: Mapping[str, Any],
    binding: Mapping[str, Any],
    field_name: str,
    *,
    binding_required: bool = True,
    plan_required: bool = True,
) -> str:
    plan_value = _first_non_empty(plan, field_name)
    binding_value = _first_non_empty(binding, field_name)
    if plan_required and not plan_value:
        raise BootstrapContractError(f"DeploymentPlan {field_name} is required")
    if binding_required and not binding_value:
        raise BootstrapContractError(f"RuntimeBinding {field_name} is required")
    if plan_value and binding_value and plan_value != binding_value:
        raise BootstrapContractError(
            f"RuntimeBinding {field_name} {binding_value!r} does not match DeploymentPlan {plan_value!r}"
        )
    return binding_value or plan_value or ""


def _materialize_bridge(
    plan: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> RuntimeBridgeIdentity:
    runtime_path = (
        _first_non_empty(binding, "bridge.path", "runtime_bridge_path")
        or _metadata_value(binding, "bridge_path", "runtime_bridge_path")
        or _metadata_value(plan, "bridge_path", "runtime_bridge_path")
        or PANTHEON_LEAN_RUNTIME_PATH
    )
    source_path = (
        _first_non_empty(binding, "engine_bridge_path", "bridge.source_path", "bridge.repo_path")
        or _metadata_value(binding, "engine_bridge_path", "bridge_source_path", "bridge_repo_path")
        or _metadata_value(plan, "engine_bridge_path", "bridge_source_path", "bridge_repo_path")
        or PANTHEON_LEAN_SOURCE_PATH
    )
    remote = (
        _first_non_empty(binding, "engine_bridge_repo", "bridge.remote")
        or _metadata_value(binding, "engine_bridge_repo", "bridge_remote")
        or _metadata_value(plan, "engine_bridge_repo", "bridge_remote")
        or PANTHEON_LEAN_REMOTE
    )
    commit = (
        _first_non_empty(binding, "engine_bridge_commit", "bridge.commit")
        or _metadata_value(binding, "engine_bridge_commit", "bridge_commit")
        or _metadata_value(plan, "engine_bridge_commit", "bridge_commit")
    )
    if not commit:
        raise BootstrapContractError("engine_bridge_commit is required")

    bridge_payload = {
        "path": runtime_path,
        "remote": remote,
        "commit": commit,
        "source_path": source_path,
    }
    _reject_lean_platform_target(bridge_payload, "bridge")
    if remote != PANTHEON_LEAN_REMOTE:
        raise BootstrapContractError(
            f"engine_bridge_repo must be {PANTHEON_LEAN_REMOTE!r}, got {remote!r}"
        )
    if source_path != PANTHEON_LEAN_SOURCE_PATH:
        raise BootstrapContractError(
            f"engine_bridge_path must be {PANTHEON_LEAN_SOURCE_PATH!r}, got {source_path!r}"
        )
    return RuntimeBridgeIdentity(
        path=runtime_path,
        remote=remote,
        commit=commit,
        source_path=source_path,
    )


def _runtime_role(plan: Mapping[str, Any], binding: Mapping[str, Any], stage: str) -> str:
    return (
        _first_non_empty(plan, "runtime_role")
        or _metadata_value(plan, "runtime_role")
        or _first_non_empty(binding, "runtime_role")
        or _metadata_value(binding, "runtime_role")
        or stage
    )


def _materialize_runtime_config(
    plan: Mapping[str, Any],
    binding: Mapping[str, Any],
    stage: str,
) -> RuntimeBootstrapConfig:
    config_ref = (
        _first_non_empty(plan, "runtime_config_ref", "runtime_config.config_ref")
        or _metadata_value(plan, "runtime_config_ref", "config_ref")
        or _metadata_value(binding, "runtime_config_ref", "config_ref")
        or DEFAULT_RUNTIME_CONFIG_REF
    )
    requested_live_broker = _boolish(
        _first_non_empty(plan, "runtime_config.live_broker_enabled")
        or _metadata_value(plan, "live_broker_enabled")
        or _metadata_value(binding, "live_broker_enabled")
    )
    if requested_live_broker:
        raise BootstrapContractError("live_broker_enabled cannot be true in the P0 bootstrap contract")
    return RuntimeBootstrapConfig(
        config_ref=config_ref,
        paper_mode=stage == "paper",
        live_broker_enabled=False,
        health_only=stage in {"canary", "live"},
    )


def _boolish(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _reject_lean_platform_target(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_lean_platform_target(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_lean_platform_target(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and "lean-platform" in value.lower():
        raise BootstrapContractError(f"{path} targets lean-platform, which is not a P0 execution target")


def _reject_raw_secrets(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).strip().lower()
            child_path = f"{path}.{key}"
            if _looks_like_raw_secret_key(key_text) and _has_non_empty_value(child):
                raise BootstrapContractError(f"{child_path} must not include raw secrets")
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
