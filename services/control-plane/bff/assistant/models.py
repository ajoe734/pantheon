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
    redaction: AssistantRedactionSummary
    omitted_sources: List[AssistantOmittedSource] = Field(default_factory=list, alias="omittedSources")


class AssistantContextPackResponse(AssistantBaseModel):
    data: AssistantContextPack
    meta: Dict[str, Any] = Field(default_factory=dict)

