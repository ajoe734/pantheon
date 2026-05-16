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
    approver: Optional[str] = None
    approved_at: Optional[str] = None
    rollback_target: Optional[str] = None
    deployment_summary: Optional[DeploymentSummary] = None
    metadata: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


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
