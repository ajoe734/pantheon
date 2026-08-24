"""Canonical source management contracts and composed models (SD-SRCM-01).

Defines the core data models for:
- ConnectorDefinition
- DataSourceEntryV2
- SourceDesiredState
- SourceObservedState
- ManagementDataSourceDTO

And enforces strict isolation between definition, instance, desired, and observed state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require(value: Any, name: str) -> str:
    s = str(value or "").strip()
    if not s:
        raise SourceManagementContractError(f"{name} is required")
    return s


class SourceManagementContractError(ValueError):
    """Raised when source management contracts or invariants are violated."""


class DefinitionState(str, Enum):
    SUPPORTED = "supported"
    DISABLED_BY_BUILD = "disabled_by_build"
    EXPERIMENTAL = "experimental"


class DesiredLifecycleState(str, Enum):
    CONFIGURED_DISABLED = "configured_disabled"
    VALIDATED_DISABLED = "validated_disabled"
    CANARY_PASSED_DISABLED = "canary_passed_disabled"
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED_DISABLED = "degraded_disabled"
    RETIRED = "retired"


class EffectiveLifecycleState(str, Enum):
    CONFIGURED_DISABLED = "configured_disabled"
    VALIDATED_DISABLED = "validated_disabled"
    CANARY_PASSED_DISABLED = "canary_passed_disabled"
    ENABLED = "enabled"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    DEGRADED_DISABLED = "degraded_disabled"
    RETIRED = "retired"


class ReconciliationStatus(str, Enum):
    CONVERGED = "converged"
    RECONCILING = "reconciling"
    DRIFTED = "drifted"
    FAILED = "failed"
    STALE = "stale"


class CredentialState(str, Enum):
    NOT_REQUIRED = "not_required"
    READY = "ready"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    INVALID = "invalid"
    EXPIRED = "expired"


class ValidationState(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    STALE = "stale"


class CanaryState(str, Enum):
    NOT_RUN = "not_run"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    EXPIRED = "expired"


class HealthState(str, Enum):
    HEALTHY = "healthy"
    FRESH = "fresh"
    DEGRADED = "degraded"
    STALE = "stale"
    FAILING = "failing"
    UNKNOWN = "unknown"


# Invariant check: reject any secret material in public config / metadata
_SUSPICIOUS_SECRET_KEYS = frozenset({
    "api_key", "apikey", "secret", "password", "token", "auth_token", "private_key", "secret_key"
})


def assert_no_raw_secrets(payload: Mapping[str, Any], path: str = "") -> None:
    """Recursively check that no inline secret values are present."""
    for k, v in payload.items():
        curr_path = f"{path}.{k}" if path else str(k)
        lower_k = str(k).lower()
        if lower_k in _SUSPICIOUS_SECRET_KEYS:
            if isinstance(v, str) and not (v.startswith("env://") or v.startswith("vault://") or v.startswith("ref://") or v == ""):
                raise SourceManagementContractError(
                    f"Raw secret material detected at {curr_path}: inline secrets are strictly forbidden; use secret_ref_id"
                )
        if isinstance(v, Mapping):
            assert_no_raw_secrets(v, curr_path)


@dataclass(frozen=True)
class SourceDesiredState:
    """Operator and controller intent for a data source instance."""

    source_instance_id: str
    revision: int
    desired_lifecycle: DesiredLifecycleState | str
    definition_id: str
    definition_deployment_sha: str
    connector_config: Mapping[str, Any]
    schedule: Mapping[str, Any]
    limits: Mapping[str, Any]
    allowed_hosts: Sequence[str]
    universe_policy_ref: str | None = None
    last_command_receipt_id: str | None = None
    updated_at: str = field(default_factory=_utc_now)
    updated_by: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    schema_version: str = field(default="source_desired_state.v1", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", "source_desired_state.v1")
        object.__setattr__(self, "source_instance_id", _require(self.source_instance_id, "source_instance_id"))
        object.__setattr__(self, "definition_id", _require(self.definition_id, "definition_id"))
        object.__setattr__(self, "definition_deployment_sha", _require(self.definition_deployment_sha, "definition_deployment_sha"))

        if self.revision < 1:
            raise SourceManagementContractError("revision must be >= 1")

        try:
            dl_val = self.desired_lifecycle.value if isinstance(self.desired_lifecycle, Enum) else str(self.desired_lifecycle)
            dl = DesiredLifecycleState(dl_val)
        except ValueError:
            allowed = ", ".join(s.value for s in DesiredLifecycleState)
            raise SourceManagementContractError(f"desired_lifecycle must be one of: {allowed}")
        object.__setattr__(self, "desired_lifecycle", dl)

        cfg = dict(self.connector_config)
        if "public" not in cfg:
            cfg["public"] = {}
        assert_no_raw_secrets(cfg)
        object.__setattr__(self, "connector_config", cfg)

        sched = dict(self.schedule)
        if "enabled" not in sched:
            sched["enabled"] = False
        if not str(sched.get("cadence", "")).strip():
            raise SourceManagementContractError("schedule.cadence is required")
        object.__setattr__(self, "schedule", sched)

        limits = dict(self.limits)
        for req_limit in ("max_records", "max_bytes", "timeout_seconds"):
            if req_limit not in limits or int(limits[req_limit]) < 1:
                raise SourceManagementContractError(f"limits.{req_limit} must be >= 1")
        object.__setattr__(self, "limits", limits)

        object.__setattr__(self, "allowed_hosts", tuple(str(h).strip() for h in self.allowed_hosts if str(h).strip()))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_instance_id": self.source_instance_id,
            "revision": self.revision,
            "desired_lifecycle": self.desired_lifecycle.value if isinstance(self.desired_lifecycle, DesiredLifecycleState) else str(self.desired_lifecycle),
            "definition_id": self.definition_id,
            "definition_deployment_sha": self.definition_deployment_sha,
            "connector_config": dict(self.connector_config),
            "schedule": dict(self.schedule),
            "universe_policy_ref": self.universe_policy_ref,
            "limits": dict(self.limits),
            "allowed_hosts": list(self.allowed_hosts),
            "last_command_receipt_id": self.last_command_receipt_id,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceDesiredState":
        return cls(
            source_instance_id=str(data["source_instance_id"]),
            revision=int(data["revision"]),
            desired_lifecycle=str(data["desired_lifecycle"]),
            definition_id=str(data["definition_id"]),
            definition_deployment_sha=str(data["definition_deployment_sha"]),
            connector_config=dict(data.get("connector_config") or {}),
            schedule=dict(data.get("schedule") or {}),
            limits=dict(data.get("limits") or {}),
            allowed_hosts=list(data.get("allowed_hosts") or []),
            universe_policy_ref=data.get("universe_policy_ref"),
            last_command_receipt_id=data.get("last_command_receipt_id"),
            updated_at=str(data.get("updated_at") or _utc_now()),
            updated_by=data.get("updated_by"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SourceObservedState:
    """Source runtime and controller observed state snapshot."""

    source_instance_id: str
    desired_revision: int
    observed_revision: int
    reconciliation_status: ReconciliationStatus | str
    effective_lifecycle: EffectiveLifecycleState | str
    definition: Mapping[str, Any]
    credential_state: CredentialState | str
    validation_state: ValidationState | str
    canary_state: CanaryState | str
    health_state: HealthState | str
    freshness: Mapping[str, Any] = field(default_factory=dict)
    last_run: Mapping[str, Any] = field(default_factory=dict)
    dlq_unresolved_count: int = 0
    quota: Mapping[str, Any] = field(default_factory=dict)
    usage: Mapping[str, Any] = field(default_factory=dict)
    dependent_refs: Sequence[str] = field(default_factory=tuple)
    reasons: Sequence[str] = field(default_factory=tuple)
    observed_at: str = field(default_factory=_utc_now)

    schema_version: str = field(default="source_observed_state.v1", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", "source_observed_state.v1")
        object.__setattr__(self, "source_instance_id", _require(self.source_instance_id, "source_instance_id"))

        if self.desired_revision < 1 or self.observed_revision < 1:
            raise SourceManagementContractError("desired_revision and observed_revision must be >= 1")

        try:
            rs_val = self.reconciliation_status.value if isinstance(self.reconciliation_status, Enum) else str(self.reconciliation_status)
            rs = ReconciliationStatus(rs_val)
        except ValueError:
            allowed = ", ".join(s.value for s in ReconciliationStatus)
            raise SourceManagementContractError(f"reconciliation_status must be one of: {allowed}")
        object.__setattr__(self, "reconciliation_status", rs)

        try:
            el_val = self.effective_lifecycle.value if isinstance(self.effective_lifecycle, Enum) else str(self.effective_lifecycle)
            el = EffectiveLifecycleState(el_val)
        except ValueError:
            allowed = ", ".join(s.value for s in EffectiveLifecycleState)
            raise SourceManagementContractError(f"effective_lifecycle must be one of: {allowed}")
        object.__setattr__(self, "effective_lifecycle", el)

        try:
            cs_val = self.credential_state.value if isinstance(self.credential_state, Enum) else str(self.credential_state)
            cs = CredentialState(cs_val)
        except ValueError:
            allowed = ", ".join(s.value for s in CredentialState)
            raise SourceManagementContractError(f"credential_state must be one of: {allowed}")
        object.__setattr__(self, "credential_state", cs)

        try:
            vs_val = self.validation_state.value if isinstance(self.validation_state, Enum) else str(self.validation_state)
            vs = ValidationState(vs_val)
        except ValueError:
            allowed = ", ".join(s.value for s in ValidationState)
            raise SourceManagementContractError(f"validation_state must be one of: {allowed}")
        object.__setattr__(self, "validation_state", vs)

        try:
            cans_val = self.canary_state.value if isinstance(self.canary_state, Enum) else str(self.canary_state)
            cans = CanaryState(cans_val)
        except ValueError:
            allowed = ", ".join(s.value for s in CanaryState)
            raise SourceManagementContractError(f"canary_state must be one of: {allowed}")
        object.__setattr__(self, "canary_state", cans)

        try:
            hs_val = self.health_state.value if isinstance(self.health_state, Enum) else str(self.health_state)
            hs = HealthState(hs_val)
        except ValueError:
            allowed = ", ".join(s.value for s in HealthState)
            raise SourceManagementContractError(f"health_state must be one of: {allowed}")
        object.__setattr__(self, "health_state", hs)

        defn = dict(self.definition)
        for req_def in ("definition_id", "deployment_sha", "state"):
            if req_def not in defn:
                raise SourceManagementContractError(f"definition.{req_def} is required")
        object.__setattr__(self, "definition", defn)

        object.__setattr__(self, "freshness", dict(self.freshness))
        object.__setattr__(self, "last_run", dict(self.last_run))
        object.__setattr__(self, "quota", dict(self.quota))
        object.__setattr__(self, "usage", dict(self.usage))
        object.__setattr__(self, "dependent_refs", tuple(str(r).strip() for r in self.dependent_refs if str(r).strip()))
        object.__setattr__(self, "reasons", tuple(str(r).strip() for r in self.reasons if str(r).strip()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_instance_id": self.source_instance_id,
            "desired_revision": self.desired_revision,
            "observed_revision": self.observed_revision,
            "reconciliation_status": self.reconciliation_status.value if isinstance(self.reconciliation_status, ReconciliationStatus) else str(self.reconciliation_status),
            "effective_lifecycle": self.effective_lifecycle.value if isinstance(self.effective_lifecycle, EffectiveLifecycleState) else str(self.effective_lifecycle),
            "definition": dict(self.definition),
            "credential_state": self.credential_state.value if isinstance(self.credential_state, CredentialState) else str(self.credential_state),
            "validation_state": self.validation_state.value if isinstance(self.validation_state, ValidationState) else str(self.validation_state),
            "canary_state": self.canary_state.value if isinstance(self.canary_state, CanaryState) else str(self.canary_state),
            "health_state": self.health_state.value if isinstance(self.health_state, HealthState) else str(self.health_state),
            "freshness": dict(self.freshness),
            "last_run": dict(self.last_run),
            "dlq_unresolved_count": self.dlq_unresolved_count,
            "quota": dict(self.quota),
            "usage": dict(self.usage),
            "dependent_refs": list(self.dependent_refs),
            "reasons": list(self.reasons),
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceObservedState":
        return cls(
            source_instance_id=str(data["source_instance_id"]),
            desired_revision=int(data["desired_revision"]),
            observed_revision=int(data["observed_revision"]),
            reconciliation_status=str(data["reconciliation_status"]),
            effective_lifecycle=str(data["effective_lifecycle"]),
            definition=dict(data.get("definition") or {}),
            credential_state=str(data["credential_state"]),
            validation_state=str(data["validation_state"]),
            canary_state=str(data["canary_state"]),
            health_state=str(data["health_state"]),
            freshness=dict(data.get("freshness") or {}),
            last_run=dict(data.get("last_run") or {}),
            dlq_unresolved_count=int(data.get("dlq_unresolved_count", 0)),
            quota=dict(data.get("quota") or {}),
            usage=dict(data.get("usage") or {}),
            dependent_refs=list(data.get("dependent_refs") or []),
            reasons=list(data.get("reasons") or []),
            observed_at=str(data.get("observed_at") or _utc_now()),
        )


@dataclass(frozen=True)
class ManagementDataSourceDTO:
    """Composed DTO exposed by BFF to Management frontend."""

    source_instance_id: str
    definition: Mapping[str, Any]
    instance: Mapping[str, Any]
    desired: Mapping[str, Any]
    observed: Mapping[str, Any]
    allowed_actions: Mapping[str, Any]
    lineage_summary: Mapping[str, Any] = field(default_factory=dict)

    schema_version: str = field(default="management_data_source.v2", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", "management_data_source.v2")
        object.__setattr__(self, "source_instance_id", _require(self.source_instance_id, "source_instance_id"))
        object.__setattr__(self, "definition", dict(self.definition))
        object.__setattr__(self, "instance", dict(self.instance))
        object.__setattr__(self, "desired", dict(self.desired))
        object.__setattr__(self, "observed", dict(self.observed))
        object.__setattr__(self, "allowed_actions", dict(self.allowed_actions))
        object.__setattr__(self, "lineage_summary", dict(self.lineage_summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_instance_id": self.source_instance_id,
            "definition": dict(self.definition),
            "instance": dict(self.instance),
            "desired": dict(self.desired),
            "observed": dict(self.observed),
            "allowed_actions": dict(self.allowed_actions),
            "lineage_summary": dict(self.lineage_summary),
        }
