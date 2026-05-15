"""
Pydantic request / response models for the Incident Service API.

Wire-layer models only.  Business-logic objects live in
services/incident/incident.py (INC-001 backbone).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared enums (mirrored from INC-001 for wire-layer use)
# ---------------------------------------------------------------------------

class IncidentSeverityEnum(str):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentStatusEnum(str):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateIncidentRequest(BaseModel):
    incident_id: Optional[str] = None
    title: str
    status: str = "open"
    severity: str
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str
    telemetry_event_ids: List[str] = Field(default_factory=list)
    evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None


class UpdateIncidentStatusRequest(BaseModel):
    status: str
    resolved_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class IncidentResponse(BaseModel):
    incident_id: str
    title: str
    status: str
    severity: str
    created_at: str
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str
    resolved_at: Optional[str] = None
    telemetry_event_ids: List[str] = Field(default_factory=list)
    evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None

    model_config = {"from_attributes": True}


class OperatorIncidentPayload(BaseModel):
    """Enriched operator view of an IncidentCase — all evidence fields inlined."""
    incident_id: str
    title: str
    status: str
    severity: str
    created_at: str
    resolved_at: Optional[str] = None

    # Evidence linkage (canonical foreign references, not resolved objects)
    binding_id: str
    deployment_stage: str
    deployment_plan_id: str
    capital_pool_id: str
    persona_capital_binding_id: str
    artifact_id: str
    artifact_version: str
    runtime_id: str
    trace_id: str

    # Optional enrichment
    evidence_summary: Optional[str] = None
    lineage_ref: Optional[str] = None
    telemetry_event_ids: List[str] = Field(default_factory=list)

    # Computed convenience fields (assembled by the service layer)
    is_open: bool = False
    postmortem_id: Optional[str] = None          # set when a postmortem exists for this incident
    linked_evolution_decision_id: Optional[str] = None  # propagated from postmortem

    model_config = {"from_attributes": True}
