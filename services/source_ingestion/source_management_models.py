"""Canonical source management contracts and composed models (SD-SRCM-01, SD-SRCM-02).

Defines the core data models for:
- ConnectorDefinition
- DataSourceEntryV2
- SourceDesiredState
- SourceObservedState
- ManagementDataSourceDTO
- SourceManagementCommand
- SourceManagementReceipt
- SourceCanaryResult

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


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


class CommandType(str, Enum):
    CREATE = "create"
    VALIDATE = "validate"
    CANARY = "canary"
    ENABLE = "enable"
    DISABLE = "disable"
    DEGRADE = "degrade"
    RESUME = "resume"
    CHANGE_SCHEDULE = "change_schedule"
    REPLACE = "replace"
    RETIRE = "retire"


class ReceiptStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class CanaryStatus(str, Enum):
    PASSED = "passed"
    PARTIAL = "partial"
    FAILED = "failed"


class CanaryStageStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CanaryStageName(str, Enum):
    DEFINITION_RESOLVED = "definition_resolved"
    CREDENTIAL_READY = "credential_ready"
    EGRESS_POLICY_ADMITTED = "egress_policy_admitted"
    PROVIDER_READ = "provider_read"
    SOURCE_NORMALIZED = "source_normalized"
    EVIDENCE_PERSISTED = "evidence_persisted"
    SEARCH_REFRESHED = "search_refreshed"
    GOVERNED_SEARCH_READBACK = "governed_search_readback"
    COMPLETED = "completed"


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


@dataclass(frozen=True)
class CanaryStage:
    """Stage execution record within a bounded canary run."""

    stage_name: CanaryStageName | str
    status: CanaryStageStatus | str
    started_at: str
    completed_at: str
    details: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        try:
            sn_val = self.stage_name.value if isinstance(self.stage_name, Enum) else str(self.stage_name)
            sn = CanaryStageName(sn_val)
        except ValueError:
            allowed = ", ".join(s.value for s in CanaryStageName)
            raise SourceManagementContractError(f"stage_name must be one of: {allowed}")
        object.__setattr__(self, "stage_name", sn)

        try:
            st_val = self.status.value if isinstance(self.status, Enum) else str(self.status)
            st = CanaryStageStatus(st_val)
        except ValueError:
            allowed = ", ".join(s.value for s in CanaryStageStatus)
            raise SourceManagementContractError(f"stage status must be one of: {allowed}")
        object.__setattr__(self, "status", st)
        object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_name": self.stage_name.value if isinstance(self.stage_name, CanaryStageName) else str(self.stage_name),
            "status": self.status.value if isinstance(self.status, CanaryStageStatus) else str(self.status),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "details": dict(self.details),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanaryStage":
        return cls(
            stage_name=str(data["stage_name"]),
            status=str(data["status"]),
            started_at=str(data["started_at"]),
            completed_at=str(data["completed_at"]),
            details=dict(data.get("details") or {}),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class SourceCanaryResult:
    """Bounded activation and readback verification evidence."""

    canary_id: str
    source_instance_id: str
    definition_id: str
    definition_deployment_sha: str
    limits: Mapping[str, Any]
    allowed_hosts: Sequence[str]
    status: CanaryStatus | str
    stages: Sequence[CanaryStage]
    license_scope: str
    entitlement_tags: Sequence[str]
    started_at: str
    completed_at: str
    row_count: int = 0
    rejected_count: int = 0
    ingest_run_id: str | None = None
    watermark: str | None = None
    evidence_bundle_id: str | None = None
    search_snapshot_id: str | None = None
    query_readback_ref: str | None = None

    schema_version: str = field(default="source_canary_result.v1", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", "source_canary_result.v1")
        object.__setattr__(self, "canary_id", _require(self.canary_id, "canary_id"))
        object.__setattr__(self, "source_instance_id", _require(self.source_instance_id, "source_instance_id"))
        object.__setattr__(self, "definition_id", _require(self.definition_id, "definition_id"))
        object.__setattr__(self, "definition_deployment_sha", _require(self.definition_deployment_sha, "definition_deployment_sha"))
        object.__setattr__(self, "license_scope", _require(self.license_scope, "license_scope"))

        try:
            cs_val = self.status.value if isinstance(self.status, Enum) else str(self.status)
            cs = CanaryStatus(cs_val)
        except ValueError:
            allowed = ", ".join(s.value for s in CanaryStatus)
            raise SourceManagementContractError(f"canary status must be one of: {allowed}")
        object.__setattr__(self, "status", cs)

        limits = dict(self.limits)
        for req_limit in ("max_records", "max_bytes", "timeout_seconds"):
            if req_limit not in limits or int(limits[req_limit]) < 1:
                raise SourceManagementContractError(f"canary limits.{req_limit} must be >= 1")
        object.__setattr__(self, "limits", limits)

        object.__setattr__(self, "allowed_hosts", tuple(str(h).strip() for h in self.allowed_hosts if str(h).strip()))
        object.__setattr__(self, "entitlement_tags", tuple(str(t).strip() for t in self.entitlement_tags if str(t).strip()))
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "row_count", max(0, int(self.row_count or 0)))
        object.__setattr__(self, "rejected_count", max(0, int(self.rejected_count or 0)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "canary_id": self.canary_id,
            "source_instance_id": self.source_instance_id,
            "definition_id": self.definition_id,
            "definition_deployment_sha": self.definition_deployment_sha,
            "limits": dict(self.limits),
            "allowed_hosts": list(self.allowed_hosts),
            "status": self.status.value if isinstance(self.status, CanaryStatus) else str(self.status),
            "stages": [s.to_dict() for s in self.stages],
            "row_count": self.row_count,
            "rejected_count": self.rejected_count,
            "ingest_run_id": self.ingest_run_id,
            "watermark": self.watermark,
            "evidence_bundle_id": self.evidence_bundle_id,
            "search_snapshot_id": self.search_snapshot_id,
            "query_readback_ref": self.query_readback_ref,
            "license_scope": self.license_scope,
            "entitlement_tags": list(self.entitlement_tags),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceCanaryResult":
        stages_data = data.get("stages") or []
        stages = [
            s if isinstance(s, CanaryStage) else CanaryStage.from_dict(s)
            for s in stages_data
            if isinstance(s, (CanaryStage, Mapping))
        ]
        return cls(
            canary_id=str(data["canary_id"]),
            source_instance_id=str(data["source_instance_id"]),
            definition_id=str(data["definition_id"]),
            definition_deployment_sha=str(data["definition_deployment_sha"]),
            limits=dict(data.get("limits") or {}),
            allowed_hosts=list(data.get("allowed_hosts") or []),
            status=str(data["status"]),
            stages=stages,
            row_count=int(data.get("row_count") or 0),
            rejected_count=int(data.get("rejected_count") or 0),
            ingest_run_id=data.get("ingest_run_id"),
            watermark=data.get("watermark"),
            evidence_bundle_id=data.get("evidence_bundle_id"),
            search_snapshot_id=data.get("search_snapshot_id"),
            query_readback_ref=data.get("query_readback_ref"),
            license_scope=str(data["license_scope"]),
            entitlement_tags=list(data.get("entitlement_tags") or []),
            started_at=str(data["started_at"]),
            completed_at=str(data["completed_at"]),
        )


@dataclass(frozen=True)
class SourceManagementCommand:
    """Internal governed mutation command for data source instances."""

    command_id: str
    idempotency_key: str
    command_type: CommandType | str
    source_instance_id: str
    expected_revision: int | None
    actor: Mapping[str, Any]
    reason: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    requested_at: str = field(default_factory=_utc_now)

    schema_version: str = field(default="source_management_command.v1", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", "source_management_command.v1")
        object.__setattr__(self, "command_id", _require(self.command_id, "command_id"))
        object.__setattr__(self, "idempotency_key", _require(self.idempotency_key, "idempotency_key"))
        object.__setattr__(self, "source_instance_id", _require(self.source_instance_id, "source_instance_id"))
        object.__setattr__(self, "reason", _require(self.reason, "reason"))

        try:
            ct_val = self.command_type.value if isinstance(self.command_type, Enum) else str(self.command_type)
            ct = CommandType(ct_val)
        except ValueError:
            allowed = ", ".join(t.value for t in CommandType)
            raise SourceManagementContractError(f"command_type must be one of: {allowed}")
        object.__setattr__(self, "command_type", ct)

        if self.expected_revision is not None and self.expected_revision < 0:
            raise SourceManagementContractError("expected_revision must be >= 0 or None")

        actor = dict(self.actor)
        if not str(actor.get("actor_id") or "").strip():
            raise SourceManagementContractError("actor.actor_id is required")
        if not str(actor.get("actor_type") or "").strip():
            raise SourceManagementContractError("actor.actor_type is required")
        object.__setattr__(self, "actor", actor)

        params = dict(self.parameters)
        assert_no_raw_secrets(params)
        object.__setattr__(self, "parameters", params)

    @property
    def idempotency_key_hash(self) -> str:
        return hashlib.sha256(self.idempotency_key.encode("utf-8")).hexdigest()

    @property
    def canonical_fingerprint(self) -> str:
        payload = {
            "command_type": self.command_type.value if isinstance(self.command_type, CommandType) else str(self.command_type),
            "source_instance_id": self.source_instance_id,
            "expected_revision": self.expected_revision,
            "parameters": dict(self.parameters),
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "command_type": self.command_type.value if isinstance(self.command_type, CommandType) else str(self.command_type),
            "source_instance_id": self.source_instance_id,
            "expected_revision": self.expected_revision,
            "actor": dict(self.actor),
            "reason": self.reason,
            "parameters": dict(self.parameters),
            "trace_id": self.trace_id,
            "requested_at": self.requested_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceManagementCommand":
        return cls(
            command_id=str(data["command_id"]),
            idempotency_key=str(data["idempotency_key"]),
            command_type=str(data["command_type"]),
            source_instance_id=str(data["source_instance_id"]),
            expected_revision=data.get("expected_revision"),
            actor=dict(data.get("actor") or {}),
            reason=str(data["reason"]),
            parameters=dict(data.get("parameters") or {}),
            trace_id=data.get("trace_id"),
            requested_at=str(data.get("requested_at") or _utc_now()),
        )


@dataclass(frozen=True)
class SourceManagementReceipt:
    """Durable execution and readback receipt for source management commands."""

    receipt_id: str
    command_id: str
    idempotency_key_hash: str
    source_instance_id: str
    command_type: CommandType | str
    status: ReceiptStatus | str
    before_revision: int
    after_revision: int
    effect_refs: Sequence[str]
    readback: Mapping[str, Any]
    actor_id: str
    service_deployment_sha: str
    created_at: str = field(default_factory=_utc_now)
    completed_at: str | None = None
    failure: Mapping[str, Any] | None = None
    trace_id: str | None = None

    schema_version: str = field(default="source_management_receipt.v1", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", "source_management_receipt.v1")
        object.__setattr__(self, "receipt_id", _require(self.receipt_id, "receipt_id"))
        object.__setattr__(self, "command_id", _require(self.command_id, "command_id"))
        object.__setattr__(self, "idempotency_key_hash", _require(self.idempotency_key_hash, "idempotency_key_hash"))
        object.__setattr__(self, "source_instance_id", _require(self.source_instance_id, "source_instance_id"))
        object.__setattr__(self, "actor_id", _require(self.actor_id, "actor_id"))
        object.__setattr__(self, "service_deployment_sha", _require(self.service_deployment_sha, "service_deployment_sha"))

        try:
            ct_val = self.command_type.value if isinstance(self.command_type, Enum) else str(self.command_type)
            ct = CommandType(ct_val)
        except ValueError:
            allowed = ", ".join(t.value for t in CommandType)
            raise SourceManagementContractError(f"command_type must be one of: {allowed}")
        object.__setattr__(self, "command_type", ct)

        try:
            st_val = self.status.value if isinstance(self.status, Enum) else str(self.status)
            st = ReceiptStatus(st_val)
        except ValueError:
            allowed = ", ".join(s.value for s in ReceiptStatus)
            raise SourceManagementContractError(f"receipt status must be one of: {allowed}")
        object.__setattr__(self, "status", st)

        if self.before_revision < 0 or self.after_revision < 0:
            raise SourceManagementContractError("before_revision and after_revision must be >= 0")

        object.__setattr__(self, "effect_refs", tuple(str(r).strip() for r in self.effect_refs if str(r).strip()))
        object.__setattr__(self, "readback", dict(self.readback))
        if self.failure is not None:
            object.__setattr__(self, "failure", dict(self.failure))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "command_id": self.command_id,
            "idempotency_key_hash": self.idempotency_key_hash,
            "source_instance_id": self.source_instance_id,
            "command_type": self.command_type.value if isinstance(self.command_type, CommandType) else str(self.command_type),
            "status": self.status.value if isinstance(self.status, ReceiptStatus) else str(self.status),
            "before_revision": self.before_revision,
            "after_revision": self.after_revision,
            "effect_refs": list(self.effect_refs),
            "readback": dict(self.readback),
            "failure": dict(self.failure) if self.failure is not None else None,
            "actor_id": self.actor_id,
            "trace_id": self.trace_id,
            "service_deployment_sha": self.service_deployment_sha,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceManagementReceipt":
        return cls(
            receipt_id=str(data["receipt_id"]),
            command_id=str(data["command_id"]),
            idempotency_key_hash=str(data["idempotency_key_hash"]),
            source_instance_id=str(data["source_instance_id"]),
            command_type=str(data["command_type"]),
            status=str(data["status"]),
            before_revision=int(data["before_revision"]),
            after_revision=int(data["after_revision"]),
            effect_refs=list(data.get("effect_refs") or []),
            readback=dict(data.get("readback") or {}),
            failure=dict(data["failure"]) if data.get("failure") is not None else None,
            actor_id=str(data["actor_id"]),
            trace_id=data.get("trace_id"),
            service_deployment_sha=str(data["service_deployment_sha"]),
            created_at=str(data.get("created_at") or _utc_now()),
            completed_at=data.get("completed_at"),
        )
