"""
Replication Gate Input/Output Schemas

Defines the contract for candidates entering the replication gate and
what exits the gate to the registry (REG-001).
"""

from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
import json


class ReplicationStatus(str, Enum):
    """Status of a replication attempt."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class CandidateAdmissionStatus(str, Enum):
    """Final admission status after replication gate."""
    ADMITTED = "admitted"
    REJECTED = "rejected"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass
class ReplicationCriteria:
    """Single replication criterion to validate."""
    criterion_id: str
    name: str
    description: str
    required: bool
    check_fn: Optional[callable] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (excludes callable)."""
        return {
            "criterion_id": self.criterion_id,
            "name": self.name,
            "description": self.description,
            "required": self.required,
        }


@dataclass
class ReplicationResult:
    """Result of a single replication check."""
    criterion_id: str
    passed: bool
    evidence: str
    details: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "passed": self.passed,
            "evidence": self.evidence,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class ReplicationRequest:
    """Candidate research entering the replication gate."""
    candidate_id: str
    source_task_id: str  # e.g., "RS-002"
    research_handoff: Dict[str, Any]
    proposed_strategy_spec: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_task_id": self.source_task_id,
            "research_handoff": self.research_handoff,
            "proposed_strategy_spec": self.proposed_strategy_spec,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplicationRequest":
        return cls(
            candidate_id=data["candidate_id"],
            source_task_id=data["source_task_id"],
            research_handoff=data["research_handoff"],
            proposed_strategy_spec=data["proposed_strategy_spec"],
            metadata=data.get("metadata"),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
        )


@dataclass
class ReplicationResponse:
    """Result of replication gate evaluation."""
    gate_run_id: str
    candidate_id: str
    admission_status: CandidateAdmissionStatus
    replication_status: ReplicationStatus
    results: List[ReplicationResult]
    summary: str
    metadata: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_run_id": self.gate_run_id,
            "candidate_id": self.candidate_id,
            "admission_status": self.admission_status.value,
            "replication_status": self.replication_status.value,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @property
    def passed(self) -> bool:
        """True if admission was granted."""
        return self.admission_status == CandidateAdmissionStatus.ADMITTED

    @property
    def replication_passed(self) -> bool:
        """True if replication tests passed."""
        return self.replication_status == ReplicationStatus.PASSED


@dataclass
class RegistryPromotionRequest:
    """Request to promote admitted candidate to registry."""
    gate_run_id: str
    candidate_id: str
    registry_entry: Dict[str, Any]
    replication_proof: Dict[str, Any]  # Evidence from gate_response
    lineage: Dict[str, Any]
    storage_backend: str  # "object_store", "gcs", "db", "inline"
    storage_path: str
    metadata: Optional[Dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_run_id": self.gate_run_id,
            "candidate_id": self.candidate_id,
            "registry_entry": self.registry_entry,
            "replication_proof": self.replication_proof,
            "lineage": self.lineage,
            "storage_backend": self.storage_backend,
            "storage_path": self.storage_path,
            "metadata": self.metadata or {},
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
