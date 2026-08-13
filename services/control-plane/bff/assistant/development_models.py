"""Models owned exclusively by removable development-tooling routes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .models import AssistantBaseModel


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
