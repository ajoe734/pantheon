"""
Pydantic request / response models for the Postmortem Service API.

Wire-layer models only.  Business-logic objects live in
services/incident/incident.py (INC-001 backbone).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreatePostmortemRequest(BaseModel):
    postmortem_id: Optional[str] = None
    title: str
    status: str = "draft"
    incident_id: str

    # Evidence fields — must match the referenced IncidentCase exactly
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str

    # Content
    root_cause: str
    contributing_factors: List[str] = Field(default_factory=list)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    author_ids: List[str] = Field(default_factory=list)
    telemetry_event_ids: List[str] = Field(default_factory=list)
    reconciliation_ids: List[str] = Field(default_factory=list)
    incident_cluster_id: Optional[str] = None
    incident_evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None


class UpdatePostmortemStatusRequest(BaseModel):
    status: str
    published_at: Optional[str] = None


class LinkEvolutionDecisionRequest(BaseModel):
    evolution_decision_id: str


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class PostmortemResponse(BaseModel):
    postmortem_id: str
    title: str
    status: str
    created_at: str
    incident_id: str

    # Evidence
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str

    # Content
    root_cause: str
    contributing_factors: List[str] = Field(default_factory=list)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    author_ids: List[str] = Field(default_factory=list)
    telemetry_event_ids: List[str] = Field(default_factory=list)
    reconciliation_ids: List[str] = Field(default_factory=list)
    incident_cluster_id: Optional[str] = None
    incident_evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None

    # Optional
    published_at: Optional[str] = None
    linked_evolution_decision_id: Optional[str] = None

    model_config = {"from_attributes": True}


class OperatorPostmortemPayload(BaseModel):
    """Enriched operator view of a Postmortem with parent incident context."""

    postmortem_id: str
    title: str
    status: str
    created_at: str
    published_at: Optional[str] = None
    incident_id: str
    incident_status: str
    incident_severity: str
    incident_created_at: str
    incident_resolved_at: Optional[str] = None
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str
    root_cause: str
    contributing_factors: List[str] = Field(default_factory=list)
    timeline: List[Dict[str, Any]] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    author_ids: List[str] = Field(default_factory=list)
    telemetry_event_ids: List[str] = Field(default_factory=list)
    reconciliation_ids: List[str] = Field(default_factory=list)
    incident_cluster_id: Optional[str] = None
    incident_evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None
    linked_evolution_decision_id: Optional[str] = None

    model_config = {"from_attributes": True}
