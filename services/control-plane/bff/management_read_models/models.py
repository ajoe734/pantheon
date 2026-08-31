from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SurfaceStatus(BaseModel):
    status: str  # ok, degraded, unavailable
    source: str  # store, service, audit, event_store, missing
    message: Optional[str] = None
    staleness: Optional[Dict[str, Any]] = None


class ReadModelMeta(BaseModel):
    status: str
    source: str
    snapshot_at: str
    surfaces: Dict[str, Any] = Field(default_factory=dict)
    degradation: Optional[Dict[str, Any]] = None


try:
    from console_gap.contracts import PageInfo
except ImportError:
    class PageInfo(BaseModel):  # type: ignore[no-redef]
        next_page_token: Optional[str] = None
        total: int = 0



# Formula jobs models
class FormulaJobItem(BaseModel):
    job_id: str
    formula_id: str
    formula_version: str
    owner_id: str
    status: str  # admitted, running, completed, failed, cancelled
    submitted_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    chart_lineage: List[Dict[str, Any]] = Field(default_factory=list)
    source_identity: str = "formula_job_executor"
    freshness: str


class FormulaJobsData(BaseModel):
    id: str = "management-formula-jobs"
    items: List[FormulaJobItem] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    status: str
    source: str


class FormulaJobsEnvelope(BaseModel):
    data: FormulaJobsData
    page_info: PageInfo
    meta: ReadModelMeta


# Activity models
class ActivityItem(BaseModel):
    event_id: str
    entry_id: Optional[str] = None
    event_type: str
    aggregate_id: Optional[str] = None
    actor_id: str
    timestamp: str
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)
    source_identity: str
    freshness: str


class ActivityData(BaseModel):
    id: str = "management-activity"
    items: List[ActivityItem] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    status: str
    source: str


class ActivityEnvelope(BaseModel):
    data: ActivityData
    page_info: PageInfo
    meta: ReadModelMeta


# Strategy Paper Telemetry models
class PaperTelemetrySeriesPoint(BaseModel):
    timestamp: str
    equity: float
    drawdown_pct: float
    open_positions: int
    daily_pnl: float


class PaperTelemetryItem(BaseModel):
    strategy_id: str
    persona_id: Optional[str] = None
    paper_ledger_id: str
    status: str
    last_signal_at: Optional[str] = None
    series: List[PaperTelemetrySeriesPoint] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    source_identity: str
    freshness: str


class PaperTelemetryData(BaseModel):
    id: str = "management-paper-telemetry"
    items: List[PaperTelemetryItem] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    status: str
    source: str


class PaperTelemetryEnvelope(BaseModel):
    data: PaperTelemetryData
    page_info: PageInfo
    meta: ReadModelMeta


# Postmortem models
class PostmortemItem(BaseModel):
    postmortem_id: str
    incident_id: str
    title: str
    severity: str
    status: str  # open, under_review, resolved, closed
    created_at: str
    resolved_at: Optional[str] = None
    root_cause: Optional[str] = None
    impact_summary: Optional[str] = None
    action_items: List[Dict[str, Any]] = Field(default_factory=list)
    source_identity: str
    freshness: str


class PostmortemsData(BaseModel):
    id: str = "management-postmortems"
    items: List[PostmortemItem] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    status: str
    source: str


class PostmortemsEnvelope(BaseModel):
    data: PostmortemsData
    page_info: PageInfo
    meta: ReadModelMeta


class PostmortemDetailEnvelope(BaseModel):
    data: Optional[PostmortemItem] = None
    meta: ReadModelMeta


# Review and Approval Queue models
class ReviewQueueItem(BaseModel):
    item_id: str
    item_type: str  # DeploymentPlan, EvolutionDecision, ApprovalDecision
    risk_level: Optional[str] = None
    submitted_at: Optional[str] = None
    submitted_by: Optional[str] = None
    governance_outcome: Optional[str] = None
    allowedActions: Dict[str, Any] = Field(default_factory=dict)
    review_summary: Dict[str, Any] = Field(default_factory=dict)


class ApprovalQueueItem(BaseModel):
    decision_id: str
    decision_type: str
    risk_level: Optional[str] = None
    submitted_at: Optional[str] = None
    submitted_by: Optional[str] = None
    decision_state: str
    allowedActions: Dict[str, Any] = Field(default_factory=dict)
    decision_context: Dict[str, Any] = Field(default_factory=dict)

