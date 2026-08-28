"""Request/response models for the strategy-workshop route modules.

Moved out of router.py so routes/session.py, routes/versions.py, and
routes/execution.py can share one model definition each without a
circular import back into router.py (ACG-06-004).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

class WorkshopCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    initial_message: str = Field(min_length=1)
    title: Optional[str] = None
    strategy_spec_ref: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkshopMessageRequest(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1)
    attachment_refs: List[str] = Field(default_factory=list)


class WorkshopReadinessReassessRequest(BaseModel):
    model_config = {"extra": "forbid"}

    force: bool = False


class WorkshopCompletenessSnapshotRequest(BaseModel):
    model_config = {"extra": "forbid"}

    strategy_version_id: Optional[str] = None
    state_map_json: Dict[str, Any] = Field(default_factory=dict)
    blocking_items_json: List[Any] = Field(default_factory=list)
    next_question_json: Dict[str, Any] = Field(default_factory=dict)
    persist_readiness: bool = True


class WorkshopVersionCreateRequest(BaseModel):
    """Create a Registry-owned immutable StrategySpec draft version."""

    model_config = {"extra": "forbid"}

    expected_workshop_version: Optional[int] = Field(default=None, ge=1)
    patch: List[Dict[str, Any]] = Field(min_length=1)
    base_document_sha256: Optional[str] = None
    reason: Optional[str] = None


class WorkshopResearchRunRequest(BaseModel):
    model_config = {"extra": "forbid"}

    research_context: str = Field(min_length=1)
    strategy_version_ref: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    approval_decision_id: str = Field(min_length=1)
    adapter: str = "handoff_only"
    requested_mode: str = "handoff_only"
    dispatch_mode: str = "handoff_only"


class WorkshopConsultationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    consultation_type: str
    subject: str = Field(min_length=1)
    context_refs: List[str] = Field(default_factory=list)


class WorkshopConcludeRequest(BaseModel):
    model_config = {"extra": "forbid"}

    final_version_id: Optional[str] = None
    conclusion_notes: Optional[str] = None
    approval_decision_id: str = Field(min_length=1)

