"""
BP5-SVC-002: Pure-stdlib data models for the split artifact_state / deployment_stage model.

Uses only dataclasses + enums — no pydantic, no external deps.
Canonical enums and data shapes per TARGET_ARCHITECTURE.md and services/registry/contract.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Canonical enums
# ---------------------------------------------------------------------------

class ArtifactState(str, Enum):
    """Registry lifecycle — §3 of contract.md."""
    DRAFT = "draft"
    CANDIDATE = "candidate"
    APPROVED = "approved"
    RETIRED = "retired"


class DeploymentStage(str, Enum):
    """Deployment/runtime placement — §4 of contract.md."""
    NONE = "none"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    FROZEN = "frozen"


class ArtifactType(str, Enum):
    """Supported artifact types — §2 of contract.md."""
    STRATEGY_SPEC = "strategy_spec"
    MODEL_ARTIFACT = "model_artifact"
    BEHAVIOR_POLICY = "behavior_policy"
    FEATURE_SET = "feature_set"
    PROMPT_BUNDLE = "prompt_bundle"
    SIGNAL_SNAPSHOT = "signal_snapshot"
    EXECUTION_BUNDLE = "execution_bundle"
    EVALUATION_RESULT = "evaluation_result"
    CRITIQUE_RESULT = "critique_result"
    OPTIMIZER_RESULT = "optimizer_result"
    ALLOCATION_POLICY = "allocation_policy"


class StorageBackend(str, Enum):
    OBJECT_STORE = "object_store"
    GCS = "gcs"
    DB = "db"
    INLINE = "inline"


# ---------------------------------------------------------------------------
# Allowed artifact-state transitions
# ---------------------------------------------------------------------------

ALLOWED_ARTIFACT_TRANSITIONS: dict[ArtifactState, list[ArtifactState]] = {
    ArtifactState.DRAFT: [ArtifactState.CANDIDATE, ArtifactState.RETIRED],
    ArtifactState.CANDIDATE: [ArtifactState.APPROVED, ArtifactState.RETIRED],
    ArtifactState.APPROVED: [ArtifactState.RETIRED],
    ArtifactState.RETIRED: [],
}

# Reserved tenant identity for checked-in bootstrap builtins (e.g. built-in
# StrategyArtifacts registered at service startup) — architecture-resumption-
# sa-sd.md §3.1/§3.3. No caller-supplied JWT/structured-token tenant claim may
# ever resolve to this value; it is assigned only by the registry's own
# bootstrap code path (services/registry/strategy_artifact.py) so a builtin's
# owner identity can never be forged by a caller and a builtin can never be
# mutated through a caller-facing route.
BUILTIN_TENANT = "__builtin__"

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class Lineage:
    """Lineage §6 of contract.md — required for approved artifacts."""
    parent_registry_ids: Optional[List[str]] = None
    source_run_ids: Optional[List[str]] = None
    source_dataset_refs: Optional[List[str]] = None
    source_strategy_spec_id: Optional[str] = None

    def has_source_reference(self) -> bool:
        return bool(
            self.source_run_ids
            or self.source_strategy_spec_id
            or self.source_dataset_refs
        )

    def is_empty(self) -> bool:
        return not bool(
            self.parent_registry_ids
            or self.source_run_ids
            or self.source_dataset_refs
            or self.source_strategy_spec_id
        )

    def to_dict(self) -> dict:
        d: dict = {}
        if self.parent_registry_ids:
            d["parent_registry_ids"] = self.parent_registry_ids
        if self.source_run_ids:
            d["source_run_ids"] = self.source_run_ids
        if self.source_dataset_refs:
            d["source_dataset_refs"] = self.source_dataset_refs
        if self.source_strategy_spec_id:
            d["source_strategy_spec_id"] = self.source_strategy_spec_id
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Lineage":
        if d is None:
            return cls()
        return cls(
            parent_registry_ids=d.get("parent_registry_ids"),
            source_run_ids=d.get("source_run_ids"),
            source_dataset_refs=d.get("source_dataset_refs"),
            source_strategy_spec_id=d.get("source_strategy_spec_id"),
        )


@dataclass
class StorageRef:
    backend: StorageBackend
    path: str

    def to_dict(self) -> dict:
        return {"backend": self.backend.value if isinstance(self.backend, StorageBackend) else self.backend, "path": self.path}

    @classmethod
    def from_dict(cls, d: dict) -> "StorageRef":
        backend = d["backend"]
        if isinstance(backend, str):
            backend = StorageBackend(backend)
        return cls(backend=backend, path=d["path"])


@dataclass
class DeploymentSummary:
    """Derived, non-authoritative deployment read-model view — §5 of contract.md."""
    current_stage: Optional[DeploymentStage] = None
    deployment_plan_id: Optional[str] = None
    runtime_binding_id: Optional[str] = None
    last_transition_at: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {}
        if self.current_stage:
            d["current_stage"] = self.current_stage.value if isinstance(self.current_stage, DeploymentStage) else self.current_stage
        if self.deployment_plan_id:
            d["deployment_plan_id"] = self.deployment_plan_id
        if self.runtime_binding_id:
            d["runtime_binding_id"] = self.runtime_binding_id
        if self.last_transition_at:
            d["last_transition_at"] = self.last_transition_at
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "DeploymentSummary":
        if d is None:
            return cls()
        current_stage = d.get("current_stage")
        if isinstance(current_stage, str):
            current_stage = DeploymentStage(current_stage)
        return cls(
            current_stage=current_stage,
            deployment_plan_id=d.get("deployment_plan_id"),
            runtime_binding_id=d.get("runtime_binding_id"),
            last_transition_at=d.get("last_transition_at"),
        )


# ---------------------------------------------------------------------------
# Core registry entry
# ---------------------------------------------------------------------------

@dataclass
class RegistryEntryCreate:
    """Input model for register() — creates a draft or candidate entry."""
    artifact_type: ArtifactType
    strategy_id: str
    version: str
    artifact_state: ArtifactState = ArtifactState.DRAFT
    lineage: Lineage = field(default_factory=Lineage)
    storage_ref: Optional[StorageRef] = None
    checksum: str = ""
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    rollback_target: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    def __post_init__(self):
        if isinstance(self.artifact_type, str):
            self.artifact_type = ArtifactType(self.artifact_type)
        if isinstance(self.artifact_state, str):
            self.artifact_state = ArtifactState(self.artifact_state)
        if isinstance(self.lineage, dict):
            self.lineage = Lineage.from_dict(self.lineage)
        if isinstance(self.storage_ref, dict):
            self.storage_ref = StorageRef.from_dict(self.storage_ref)
        if not _SEMVER_RE.match(self.version):
            raise ValueError(f"Invalid semver: {self.version}")
        if self.storage_ref is None:
            self.storage_ref = StorageRef(backend=StorageBackend.OBJECT_STORE, path="")


@dataclass
class RegistryEntry:
    """Full registry entry — §5 of contract.md."""
    registry_id: str
    artifact_type: ArtifactType
    strategy_id: str
    version: str
    artifact_state: ArtifactState
    lineage: Lineage
    storage_ref: StorageRef
    checksum: str
    producer_run_id: Optional[str] = None
    evaluation_summary: Optional[dict[str, Any]] = None
    approval_decision_id: Optional[str] = None
    approval_evidence: Optional[dict[str, Any]] = None
    approver: Optional[str] = None
    approved_at: Optional[str] = None
    rollback_target: Optional[str] = None
    deployment_summary: Optional[DeploymentSummary] = None
    metadata: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_actor: Optional[dict[str, Any]] = None
    """Verified-caller audit binding for the most recent write (actor_id,
    roles, tenant, token_kind) — see services.runtime_auth_inbound.AuthContext.
    Not part of any StrategySpec/StrategyArtifact/AllocationPolicyArtifact
    immutable payload; purely an audit projection of who last mutated this
    row, so it is exempt from the reserved-key immutability check in
    RegistryService.update_metadata."""
    owner_tenant: Optional[str] = None
    """Immutable owner-tenant identity bound at creation time from the
    verified creating caller's tenant claim (never from a caller-supplied
    body/header value — see services.runtime_auth_inbound.AuthContext).
    Reads/writes are scoped against this field (see services/registry/service.py
    ``_authorize_read``/``_authorize_write``); it is never reassigned by a
    later mutation, unlike ``last_actor``. ``BUILTIN_TENANT`` marks a
    checked-in bootstrap artifact — see that constant's docstring."""

    def to_dict(self) -> dict:
        """Durable JSONB payload for a Postgres owner store row.

        Every field round-trips through :meth:`from_dict`; enum values are
        serialized as their plain string value so the payload is a portable
        JSON document rather than a Python-specific pickle.
        """
        return {
            "registry_id": self.registry_id,
            "artifact_type": self.artifact_type.value,
            "strategy_id": self.strategy_id,
            "version": self.version,
            "artifact_state": self.artifact_state.value,
            "lineage": self.lineage.to_dict(),
            "storage_ref": self.storage_ref.to_dict(),
            "checksum": self.checksum,
            "producer_run_id": self.producer_run_id,
            "evaluation_summary": self.evaluation_summary,
            "approval_decision_id": self.approval_decision_id,
            "approval_evidence": self.approval_evidence,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "rollback_target": self.rollback_target,
            "deployment_summary": self.deployment_summary.to_dict() if self.deployment_summary else None,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_actor": self.last_actor,
            "owner_tenant": self.owner_tenant,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RegistryEntry":
        deployment_summary = d.get("deployment_summary")
        return cls(
            registry_id=d["registry_id"],
            artifact_type=ArtifactType(d["artifact_type"]),
            strategy_id=d["strategy_id"],
            version=d["version"],
            artifact_state=ArtifactState(d["artifact_state"]),
            lineage=Lineage.from_dict(d.get("lineage")),
            storage_ref=StorageRef.from_dict(d["storage_ref"]),
            checksum=d.get("checksum", ""),
            producer_run_id=d.get("producer_run_id"),
            evaluation_summary=d.get("evaluation_summary"),
            approval_decision_id=d.get("approval_decision_id"),
            approval_evidence=d.get("approval_evidence"),
            approver=d.get("approver"),
            approved_at=d.get("approved_at"),
            rollback_target=d.get("rollback_target"),
            deployment_summary=DeploymentSummary.from_dict(deployment_summary) if deployment_summary else None,
            metadata=d.get("metadata"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            last_actor=d.get("last_actor"),
            owner_tenant=d.get("owner_tenant"),
        )


@dataclass
class RegistryEntryView:
    """Read-model view returned by API — includes derived deployment_stage."""
    entry: RegistryEntry
    deployment_stage: DeploymentStage = DeploymentStage.NONE


@dataclass
class DeploymentView:
    """Composed deployment-stage view — §8 resolve_deployment_view()."""
    strategy_id: str
    current_stage: DeploymentStage = DeploymentStage.NONE
    latest_approved_registry_id: Optional[str] = None
    latest_approved_version: Optional[str] = None
    deployment_plan_id: Optional[str] = None
    runtime_binding_id: Optional[str] = None
    last_transition_at: Optional[str] = None
