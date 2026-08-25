"""Pydantic models for the local signed task packet bridge (ASST-INTEG-006).

Version: pantheon.assistant.dev-task.v1

The bridge emits a signed DevTaskPacket for the local supervisor.  It is part
of development tooling and is never imported by the product BFF.  The
dispatcher verifies the signature, checks replay protection, and materialises
the tasks through scripts/ai_status.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Sixteen tasks keep the worst-case governed assign + per-task authoritative
# readback budget below the 300-second compatibility claim TTL even when every
# subprocess consumes the ten-second hard timeout.  The OS packet fence in the
# dispatcher remains the concurrency authority; this bound also prevents one
# signed packet from monopolising a supervisor tick indefinitely.
MAX_TASKS_PER_PACKET = 16
try:
    from ..dispatch_policy import normalize_execution_resources
except (ImportError, ValueError):
    from dispatch_policy import normalize_execution_resources


class BridgeBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# Packet sub-models
# ---------------------------------------------------------------------------

class BridgeActor(BridgeBaseModel):
    """Identity and capability context of the actor who triggered the bridge."""
    id: str
    roles: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)


class BridgeDocument(BridgeBaseModel):
    """Reference to an SA/SD or planning document produced by the generator."""
    path: str
    kind: str = "SA_SD_PLAN"
    source_refs: List[str] = Field(default_factory=list, alias="sourceRefs")


class BridgeTask(BridgeBaseModel):
    """Execution task specification destined for scripts/ai_status.py assign."""
    id: str
    title: str
    owner: str
    reviewer: str
    phase: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list, alias="dependsOn")
    dependency_tracks: Dict[str, str] = Field(
        default_factory=dict,
        alias="dependencyTracks",
    )
    execution_resources: List[str] = Field(
        default_factory=list,
        alias="executionResources",
    )
    artifacts: List[str] = Field(default_factory=list)
    acceptance: List[str] = Field(default_factory=list)
    summary: Optional[str] = None

    @field_validator("execution_resources", mode="before")
    @classmethod
    def validate_execution_resources(cls, v: Any) -> List[str]:
        return normalize_execution_resources(v)


class BridgeConstraints(BridgeBaseModel):
    """Guardrails that must be respected when materialising tasks."""
    allowed_repos: List[str] = Field(default_factory=lambda: ["pantheon"], alias="allowedRepos")
    requires_branch_pr_merge: bool = Field(default=True, alias="requiresBranchPrMerge")
    no_direct_shell_from_web: bool = Field(default=True, alias="noDirectShellFromWeb")


class PacketSignature(BridgeBaseModel):
    """Ed25519 signature over the canonical packet payload."""
    key_id: str = Field(alias="keyId")
    algorithm: str = "Ed25519"
    value: str


class BridgeOperatorAuthorization(BridgeBaseModel):
    """Local operator authority, distinct from packet source."""

    operator_id: str = Field(alias="operatorId")
    control_activation_id: str = Field(alias="controlActivationId")
    capability: str
    mfa_verified: bool = Field(alias="mfaVerified")
    issued_at: str = Field(alias="issuedAt")
    expires_at: str = Field(alias="expiresAt")
    nonce: str


# ---------------------------------------------------------------------------
# Top-level packet
# ---------------------------------------------------------------------------

class DevTaskPacket(BridgeBaseModel):
    """Signed task packet emitted by the local development bridge.

    It never executes a task directly; it hands the packet to the dispatcher,
    which verifies it, applies replay protection, and materialises each task
    through scripts/ai_status.py.
    """
    version: str = "pantheon.assistant.dev-task.v1"
    packet_id: str = Field(alias="packetId")
    intent: str = "generate_sa_sd_and_dispatch"
    emitted_at: str = Field(alias="emittedAt")

    actor: BridgeActor
    operator_authorization: Optional[BridgeOperatorAuthorization] = Field(
        default=None, alias="operatorAuthorization"
    )
    mode: str

    source_conversation_id: str = Field(alias="sourceConversationId")
    source_turn_ids: List[str] = Field(default_factory=list, alias="sourceTurnIds")

    documents: List[BridgeDocument] = Field(default_factory=list)
    tasks: List[BridgeTask] = Field(
        default_factory=list,
        max_length=MAX_TASKS_PER_PACKET,
    )
    constraints: BridgeConstraints = Field(default_factory=BridgeConstraints)

    signature: Optional[PacketSignature] = None

    audit_conversation_href: Optional[str] = Field(
        default=None,
        alias="auditConversationHref",
    )


# ---------------------------------------------------------------------------
# Dispatch request / result
# ---------------------------------------------------------------------------

class BridgeDispatchRequest(BridgeBaseModel):
    """Request payload for the dispatcher — carries the signed packet."""
    packet: DevTaskPacket
    repo_root: Optional[str] = Field(default=None, alias="repoRoot")
    dry_run: bool = Field(default=False, alias="dryRun")


class TaskDispatchRecord(BridgeBaseModel):
    """Per-task outcome returned by the dispatcher."""
    task_id: str = Field(alias="taskId")
    owner: str
    reviewer: str
    status: str = "dispatched"
    error: Optional[str] = None


class BridgeDispatchResult(BridgeBaseModel):
    """Result returned by dispatch_task_packet()."""
    packet_id: str = Field(alias="packetId")
    dispatched_at: str = Field(alias="dispatchedAt")
    task_records: List[TaskDispatchRecord] = Field(
        default_factory=list, alias="taskRecords"
    )
    replay_rejected: bool = Field(default=False, alias="replayRejected")
    dry_run: bool = Field(default=False, alias="dryRun")
    audit_refs: Dict[str, Any] = Field(default_factory=dict, alias="auditRefs")
    admission_record: Optional[Dict[str, Any]] = Field(
        default=None, alias="admissionRecord"
    )
    admission_status: str = Field(default="not_attempted", alias="admissionStatus")
    retryable: bool = False
    errors: List[str] = Field(default_factory=list)
