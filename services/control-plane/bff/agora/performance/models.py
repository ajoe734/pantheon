"""Typed contracts for Agora Strategy Performance truth.

The models deliberately distinguish an unavailable source from a measured
zero or an empty authoritative result.  Suggestions carry only source-owned
content and never imply execution authority.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


AvailabilityState = Literal["available", "partial", "unavailable"]
SuggestionStatus = Literal["proposed", "applied", "rejected", "returned_to_workshop"]


class SourceAvailability(BaseModel):
    model_config = {"extra": "forbid"}

    status: AvailabilityState
    as_of: Optional[str] = None
    source_ids: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class ComplianceMetric(BaseModel):
    model_config = {"extra": "forbid"}

    metric_id: str = Field(min_length=1)
    label: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    calculation_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    as_of: str = Field(min_length=1)
    evidence_refs: List[str] = Field(default_factory=list)


class ComplianceProjection(BaseModel):
    model_config = {"extra": "forbid"}

    availability: SourceAvailability
    metrics: List[ComplianceMetric] = Field(default_factory=list)


class InterventionRecord(BaseModel):
    model_config = {"extra": "forbid"}

    intervention_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    evidence_refs: List[str] = Field(min_length=1)


class InterventionAggregate(BaseModel):
    model_config = {"extra": "forbid"}

    total: int = Field(ge=0)
    by_status: Dict[str, int] = Field(default_factory=dict)


class InterventionProjection(BaseModel):
    model_config = {"extra": "forbid"}

    availability: SourceAvailability
    aggregate: Optional[InterventionAggregate] = None
    items: List[InterventionRecord] = Field(default_factory=list)


class ExecutionHistoryRow(BaseModel):
    model_config = {"extra": "forbid"}

    journey_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    decision_ids: List[str] = Field(default_factory=list)
    order_ids: List[str] = Field(default_factory=list)
    fill_ids: List[str] = Field(default_factory=list)
    reconciliation_ids: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(min_length=1)
    source_id: Literal["canonical_trade_journey_projector"] = (
        "canonical_trade_journey_projector"
    )

    @model_validator(mode="after")
    def require_execution_identity(self) -> "ExecutionHistoryRow":
        if not any(
            (
                self.decision_ids,
                self.order_ids,
                self.fill_ids,
                self.reconciliation_ids,
            )
        ):
            raise ValueError("execution history requires a canonical lifecycle identity")
        return self


class ExecutionHistoryProjection(BaseModel):
    model_config = {"extra": "forbid"}

    availability: SourceAvailability
    items: List[ExecutionHistoryRow] = Field(default_factory=list)


class PerformanceWarning(BaseModel):
    model_config = {"extra": "forbid"}

    warning_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    severity: Literal["info", "warning", "high", "critical"]
    occurred_at: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    evidence_refs: List[str] = Field(default_factory=list)
    message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class WarningProjection(BaseModel):
    model_config = {"extra": "forbid"}

    availability: SourceAvailability
    items: List[PerformanceWarning] = Field(default_factory=list)


class SuggestionProvenance(BaseModel):
    model_config = {"extra": "forbid"}

    source_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    produced_at: str = Field(min_length=1)
    source_version: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)


class AdjustmentSuggestion(BaseModel):
    model_config = {"extra": "forbid"}

    suggestion_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    period: Literal["latest", "7d", "30d", "all"]
    status: SuggestionStatus = "proposed"
    version: int = Field(default=1, ge=1)
    title: Optional[str] = None
    rationale: Optional[str] = None
    expected_effect: Optional[Dict[str, Any]] = None
    expected_risk: Optional[Dict[str, Any]] = None
    provenance: SuggestionProvenance
    as_of: str = Field(min_length=1)
    updated_at: Optional[str] = None
    no_order_route_proof: Literal["agora_suggestion_state_only"] = (
        "agora_suggestion_state_only"
    )


class SuggestionProjection(BaseModel):
    model_config = {"extra": "forbid"}

    availability: SourceAvailability
    items: List[AdjustmentSuggestion] = Field(default_factory=list)


class PerformanceFreshness(BaseModel):
    model_config = {"extra": "forbid"}

    status: AvailabilityState
    snapshot_at: str = Field(min_length=1)
    as_of: Optional[str] = None
    source_watermarks: Dict[str, str] = Field(default_factory=dict)
    projection_revision: Optional[int] = None
    projection_generation: Optional[int] = None
    unavailable_sources: List[str] = Field(default_factory=list)


class StrategyPerformanceProjection(BaseModel):
    model_config = {"extra": "forbid"}

    strategy_id: str = Field(min_length=1)
    period: Literal["latest", "7d", "30d", "all"]
    environment: Literal["paper", "broker_sandbox", "canary", "live"]
    availability: AvailabilityState
    freshness: PerformanceFreshness
    compliance: ComplianceProjection
    interventions: InterventionProjection
    execution_history: ExecutionHistoryProjection
    warnings: WarningProjection
    adjustment_suggestions: SuggestionProjection
    no_order_route_proof: Literal["agora_performance_read_only"] = (
        "agora_performance_read_only"
    )


class PerformanceProjectionEnvelope(BaseModel):
    model_config = {"extra": "forbid"}

    data: StrategyPerformanceProjection
    meta: Dict[str, Any]


class SuggestionActionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    action: Literal["apply", "reject", "return_to_workshop"]
    expected_version: int = Field(ge=1)
    reason: Optional[str] = Field(default=None, max_length=2000)


class SuggestionActionReceipt(BaseModel):
    model_config = {"extra": "forbid"}

    receipt_id: str = Field(min_length=1)
    audit_event_id: str = Field(min_length=1)
    suggestion_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    action: Literal["apply", "reject", "return_to_workshop"]
    previous_status: SuggestionStatus
    status: SuggestionStatus
    previous_version: int = Field(ge=1)
    version: int = Field(ge=2)
    actor_id: str = Field(min_length=1)
    reason: Optional[str] = None
    recorded_at: str = Field(min_length=1)
    authoritative_readback: AdjustmentSuggestion
    idempotent_replay: bool = False
    execution_authority: Literal["none"] = "none"
    no_order_route_proof: Literal["agora_suggestion_state_only"] = (
        "agora_suggestion_state_only"
    )


class SuggestionActionEnvelope(BaseModel):
    model_config = {"extra": "forbid"}

    data: SuggestionActionReceipt
    meta: Dict[str, Any]
