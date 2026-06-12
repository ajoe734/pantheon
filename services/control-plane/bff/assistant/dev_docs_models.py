"""Data models for the SA/SD document generator (ASST-INTEG-005)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DevDocsBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ConversationTurnRef(DevDocsBaseModel):
    turn_id: str = Field(alias="turnId")
    role: str
    excerpt: str
    conversation_id: Optional[str] = Field(default=None, alias="conversationId")


class DocSourceRef(DevDocsBaseModel):
    """Citation linking a generated artifact back to its source."""
    source_id: str = Field(alias="sourceId")
    href: Optional[str] = None
    snapshot_at: Optional[str] = Field(default=None, alias="snapshotAt")
    kind: str = "bff"
    note: Optional[str] = None


class RequirementCapture(DevDocsBaseModel):
    """Requirement extracted from a Management AI conversation."""
    capture_id: str = Field(alias="captureId")
    conversation_id: str = Field(alias="conversationId")
    generated_at: str = Field(alias="generatedAt")

    problem: str
    actors: List[str] = Field(default_factory=list)
    user_intent: str = Field(alias="userIntent")
    affected_modules: List[str] = Field(default_factory=list, alias="affectedModules")
    constraints: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list, alias="openQuestions")

    source_turn_refs: List[ConversationTurnRef] = Field(
        default_factory=list, alias="sourceTurnRefs"
    )
    source_refs: List[DocSourceRef] = Field(default_factory=list, alias="sourceRefs")


class SystemAnalysis(DevDocsBaseModel):
    """SA: current state analysis generated from requirement capture and context pack."""
    sa_id: str = Field(alias="saId")
    capture_id: str = Field(alias="captureId")
    generated_at: str = Field(alias="generatedAt")
    title: str

    current_state: str = Field(alias="currentState")
    roles: List[str] = Field(default_factory=list)
    flows: List[str] = Field(default_factory=list)
    data: List[str] = Field(default_factory=list)
    risk: List[str] = Field(default_factory=list)
    edge_cases: List[str] = Field(default_factory=list, alias="edgeCases")
    acceptance_scenarios: List[str] = Field(default_factory=list, alias="acceptanceScenarios")

    source_refs: List[DocSourceRef] = Field(default_factory=list, alias="sourceRefs")


class SystemDesign(DevDocsBaseModel):
    """SD: system design artifact generated from SA + requirements."""
    sd_id: str = Field(alias="sdId")
    sa_id: str = Field(alias="saId")
    generated_at: str = Field(alias="generatedAt")
    title: str

    architecture: str
    api_contract: List[str] = Field(default_factory=list, alias="apiContract")
    db_migration: List[str] = Field(default_factory=list, alias="dbMigration")
    ui_routes: List[str] = Field(default_factory=list, alias="uiRoutes")
    tool_action_contract: List[str] = Field(default_factory=list, alias="toolActionContract")
    tests: List[str] = Field(default_factory=list)
    rollout: str
    rollback: str

    source_refs: List[DocSourceRef] = Field(default_factory=list, alias="sourceRefs")


class ExecutionTask(DevDocsBaseModel):
    """Generated supervisor/autoworker task with full assignment metadata."""
    task_id: str = Field(alias="taskId")
    title: str
    summary: str
    owner: str
    reviewer: str
    phase: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list, alias="dependsOn")
    artifacts: List[str] = Field(default_factory=list)
    acceptance: List[str] = Field(default_factory=list)
    source_refs: List[DocSourceRef] = Field(default_factory=list, alias="sourceRefs")


class ArchiveLocations(DevDocsBaseModel):
    """Canonical target paths where each document family is written."""
    requirement_capture: Optional[str] = Field(default=None, alias="requirementCapture")
    system_analysis: Optional[str] = Field(default=None, alias="systemAnalysis")
    system_design: Optional[str] = Field(default=None, alias="systemDesign")
    architecture_docs: List[str] = Field(default_factory=list, alias="architectureDocs")
    ui_docs: List[str] = Field(default_factory=list, alias="uiDocs")
    task_briefs: List[str] = Field(default_factory=list, alias="taskBriefs")


class DevDocPacket(DevDocsBaseModel):
    """Complete SA/SD generation result ready for archiving."""
    packet_id: str = Field(alias="packetId")
    conversation_id: str = Field(alias="conversationId")
    generated_at: str = Field(alias="generatedAt")
    actor_id: str = Field(alias="actorId")

    requirement_capture: RequirementCapture = Field(alias="requirementCapture")
    system_analysis: SystemAnalysis = Field(alias="systemAnalysis")
    system_design: SystemDesign = Field(alias="systemDesign")
    execution_tasks: List[ExecutionTask] = Field(default_factory=list, alias="executionTasks")

    archive_locations: Optional[ArchiveLocations] = Field(default=None, alias="archiveLocations")
    source_refs: List[DocSourceRef] = Field(default_factory=list, alias="sourceRefs")


class DevDocGenerateRequest(DevDocsBaseModel):
    """BFF request payload for the /dev-docs/generate endpoint."""
    conversation_id: str = Field(alias="conversationId")
    turn_ids: List[str] = Field(default_factory=list, alias="turnIds")
    feature_summary: str = Field(alias="featureSummary")
    affected_modules: List[str] = Field(default_factory=list, alias="affectedModules")
    proposed_owner: Optional[str] = Field(default=None, alias="proposedOwner")
    proposed_reviewer: Optional[str] = Field(default=None, alias="proposedReviewer")
    archive: bool = True
    extra_context: Optional[Dict[str, Any]] = Field(default=None, alias="extraContext")


class DevDocGenerateResponse(DevDocsBaseModel):
    data: DevDocPacket
    meta: Dict[str, Any] = Field(default_factory=dict)
