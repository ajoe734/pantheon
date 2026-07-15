"""Pydantic models for the signed task packet bridge (ASST-INTEG-006).

Version: pantheon.assistant.dev-task.v1

The Dev Bridge emits a signed DevTaskPacket from BFF rather than executing
shell commands directly from the Web API layer.  The dispatcher receives the
signed packet, verifies the signature, checks replay protection, and
materialises the tasks through scripts/ai_status.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    artifacts: List[str] = Field(default_factory=list)
    acceptance: List[str] = Field(default_factory=list)
    summary: Optional[str] = None


class BridgeConstraints(BridgeBaseModel):
    """Guardrails that must be respected when materialising tasks."""
    allowed_repos: List[str] = Field(default_factory=lambda: ["pantheon"], alias="allowedRepos")
    requires_branch_pr_merge: bool = Field(default=True, alias="requiresBranchPrMerge")
    no_direct_shell_from_web: bool = Field(default=True, alias="noDirectShellFromWeb")


class PacketSignature(BridgeBaseModel):
    """HMAC-SHA256 signature over the canonical packet payload."""
    key_id: str = Field(alias="keyId")
    algorithm: str = "HMAC-SHA256"
    value: str


# ---------------------------------------------------------------------------
# Top-level packet
# ---------------------------------------------------------------------------

class DevTaskPacket(BridgeBaseModel):
    """Signed task packet emitted by BFF assistant dev bridge.

    BFF never executes shell directly from the Web API layer.  Instead it
    builds this packet, signs it, and hands it to the dispatcher which
    verifies the signature, applies replay protection, and materialises each
    task through scripts/ai_status.py.
    """
    version: str = "pantheon.assistant.dev-task.v1"
    packet_id: str = Field(alias="packetId")
    intent: str = "generate_sa_sd_and_dispatch"
    emitted_at: str = Field(alias="emittedAt")

    actor: BridgeActor
    mode: str

    source_conversation_id: str = Field(alias="sourceConversationId")
    source_turn_ids: List[str] = Field(default_factory=list, alias="sourceTurnIds")

    documents: List[BridgeDocument] = Field(default_factory=list)
    tasks: List[BridgeTask] = Field(default_factory=list)
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
