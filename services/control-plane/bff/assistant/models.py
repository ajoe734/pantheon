from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AssistantBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class AssistantMode(str, Enum):
    USER = "user"
    KERNEL_OBSERVE = "kernel_observe"
    KERNEL_DEBUG = "kernel_debug"
    KERNEL_REPAIR = "kernel_repair"


class AssistantFocus(AssistantBaseModel):
    entity_type: str = Field(alias="entityType")
    entity_id: str = Field(alias="entityId")
    label: Optional[str] = None
    route: Optional[str] = None


class AssistantContextRef(AssistantBaseModel):
    ref_id: Optional[str] = Field(default=None, alias="refId")
    source_id: Optional[str] = Field(default=None, alias="sourceId")
    entity_type: Optional[str] = Field(default=None, alias="entityType")
    entity_id: Optional[str] = Field(default=None, alias="entityId")
    href: Optional[str] = None
    label: Optional[str] = None


class AssistantFrontendContext(AssistantBaseModel):
    route: str = "/"
    selected_entity: Optional[Dict[str, Any]] = Field(default=None, alias="selectedEntity")
    visible_errors: List[Dict[str, Any]] = Field(default_factory=list, alias="visibleErrors")
    context_refs: List[AssistantContextRef] = Field(default_factory=list, alias="contextRefs")


class AssistantContextPackRequest(AssistantBaseModel):
    mode: AssistantMode = AssistantMode.USER
    include: List[str] = Field(default_factory=list)
    question: Optional[str] = None
    frontend: AssistantFrontendContext = Field(default_factory=AssistantFrontendContext)
    focus: Optional[AssistantFocus] = None
    context_refs: List[AssistantContextRef] = Field(default_factory=list, alias="contextRefs")
    route: Optional[str] = None
    selected_entity: Optional[Dict[str, Any]] = Field(default=None, alias="selectedEntity")
    visible_errors: List[Dict[str, Any]] = Field(default_factory=list, alias="visibleErrors")


class AssistantActorContext(AssistantBaseModel):
    operator_id: str = Field(alias="operatorId")
    roles: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)


class AssistantBackendContext(AssistantBaseModel):
    control_room: Optional[Dict[str, Any]] = None
    jobs: Optional[Dict[str, Any]] = None
    alerts: Optional[Dict[str, Any]] = None
    audit: Optional[Dict[str, Any]] = None
    persona_health: Optional[Dict[str, Any]] = None
    strategy_health: Optional[Dict[str, Any]] = None
    management_nl: Optional[Dict[str, Any]] = None
    orchestrator_status: Optional[Dict[str, Any]] = None
    recent_sse: List[Dict[str, Any]] = Field(default_factory=list)


class AssistantInternalDebugContext(AssistantBaseModel):
    health_probes: List[Dict[str, Any]] = Field(default_factory=list)
    sanitized_logs: List[Dict[str, Any]] = Field(default_factory=list)
    repo_status: Optional[Dict[str, Any]] = None


class AssistantSourceRef(AssistantBaseModel):
    source_id: str = Field(alias="sourceId")
    href: str
    snapshot_at: str = Field(alias="snapshotAt")
    status: str
    staleness: Dict[str, Any] = Field(default_factory=dict)
    source_kind: str = Field(default="bff", alias="sourceKind")


class AssistantOmittedSource(AssistantBaseModel):
    source_id: str = Field(alias="sourceId")
    reason: str
    message: str


class AssistantRedactionSummary(AssistantBaseModel):
    enabled: bool = True
    redacted_fields: int = Field(default=0, alias="redactedFields")
    ruleset_version: str = Field(default="assistant-context-pack-v1", alias="rulesetVersion")


class AssistantUiHintSection(AssistantBaseModel):
    hint_only: bool = Field(default=True, alias="hintOnly")
    authority: str = "frontend_hint_only"
    context: AssistantFrontendContext = Field(default_factory=AssistantFrontendContext)
    source_refs: List[AssistantSourceRef] = Field(default_factory=list, alias="sourceRefs")


class AssistantBffReadSection(AssistantBaseModel):
    rbac_enforced: bool = Field(default=True, alias="rbacEnforced")
    tenant_filtered: bool = Field(default=True, alias="tenantFiltered")
    context: AssistantBackendContext = Field(default_factory=AssistantBackendContext)
    source_refs: List[AssistantSourceRef] = Field(default_factory=list, alias="sourceRefs")
    access: Dict[str, Any] = Field(default_factory=dict)


class AssistantDocsRagSection(AssistantBaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    source_refs: List[AssistantSourceRef] = Field(default_factory=list, alias="sourceRefs")


class AssistantContextPack(AssistantBaseModel):
    context_pack_id: str = Field(alias="contextPackId")
    session_id: str = Field(alias="sessionId")
    mode: AssistantMode
    question: Optional[str] = None
    actor: AssistantActorContext
    snapshot_at: str = Field(alias="snapshotAt")
    frontend: AssistantFrontendContext
    backend: AssistantBackendContext
    internal_debug: AssistantInternalDebugContext = Field(alias="internalDebug")
    sources: List[AssistantSourceRef] = Field(default_factory=list)
    source_refs: List[AssistantSourceRef] = Field(default_factory=list, alias="sourceRefs")
    ui_hints: AssistantUiHintSection = Field(default_factory=AssistantUiHintSection, alias="uiHints")
    bff_reads: AssistantBffReadSection = Field(default_factory=AssistantBffReadSection, alias="bffReads")
    docs_rag: AssistantDocsRagSection = Field(default_factory=AssistantDocsRagSection, alias="docsRag")
    redaction: AssistantRedactionSummary
    omitted_sources: List[AssistantOmittedSource] = Field(default_factory=list, alias="omittedSources")


class AssistantContextPackResponse(AssistantBaseModel):
    data: AssistantContextPack
    meta: Dict[str, Any] = Field(default_factory=dict)


class OrchestratorWorkerStatus(AssistantBaseModel):
    run_id: str = Field(alias="runId")
    task_id: Optional[str] = Field(default=None, alias="taskId")
    agent: str
    provider: Optional[str] = None
    status: str
    started_at: Optional[str] = Field(default=None, alias="startedAt")
    last_event_at: Optional[str] = Field(default=None, alias="lastEventAt")
    last_error: Optional[str] = Field(default=None, alias="lastError")
    queue_event_id: Optional[str] = Field(default=None, alias="queueEventId")
    dispatch_reason: Optional[str] = Field(default=None, alias="dispatchReason")
    delivery_mode: Optional[str] = Field(default=None, alias="deliveryMode")


class OrchestratorTaskStatus(AssistantBaseModel):
    id: str
    title: str
    owner: str
    reviewer: str
    status: str
    phase: Optional[str] = None
    next: Optional[str] = None
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")
    depends_on: List[str] = Field(default_factory=list, alias="dependsOn")
    artifacts: List[str] = Field(default_factory=list)
    acceptance: List[str] = Field(default_factory=list)
    summary_zh: Optional[str] = Field(default=None, alias="summaryZh")
    waiting_for: Optional[str] = Field(default=None, alias="waitingFor")
    failure_streak: int = Field(default=0, alias="failureStreak")
    brief_path: Optional[str] = Field(default=None, alias="briefPath")
    blockers: List[Dict[str, Any]] = Field(default_factory=list)
    github: Optional[Dict[str, Any]] = None
    deployment: Optional[Dict[str, Any]] = None
    delivery: Optional[Dict[str, Any]] = None


class OrchestratorStatusResponse(AssistantBaseModel):
    snapshot_at: str = Field(alias="snapshotAt")
    project: str
    sprint: str
    objective: str
    source_refs: List[Dict[str, Any]] = Field(default_factory=list, alias="sourceRefs")
    tasks: List[OrchestratorTaskStatus]
    workers: List[OrchestratorWorkerStatus]
    queue: List[Dict[str, Any]] = Field(default_factory=list)
    handoffs: List[Dict[str, Any]]
    blockers: List[Dict[str, Any]]
    supervisor: Dict[str, Any]
    delivery_health: Dict[str, Any] = Field(default_factory=dict, alias="deliveryHealth")
    provider_readiness: Dict[str, Any] = Field(default_factory=dict, alias="providerReadiness")
    openclaw_tool_policy: Dict[str, Any] = Field(default_factory=dict, alias="openclawToolPolicy")
    assistant_dev_bridge: Dict[str, Any] = Field(default_factory=dict, alias="assistantDevBridge")
    coordination: Optional[Dict[str, Any]] = None
