"""Production producer projecting telemetry, paper, and risk outcomes into durable performance suggestions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .models import AdjustmentSuggestion, SuggestionProvenance
from .store import PerformanceSuggestionStore


class PerformanceOutcomeEvaluationInput(BaseModel):
    model_config = {"extra": "forbid"}

    strategy_id: str = Field(min_length=1)
    period: Literal["latest", "7d", "30d", "all"] = "latest"
    outcome_type: Literal[
        "drawdown_breach",
        "execution_drift",
        "slippage_anomaly",
        "sharpe_degradation",
        "regime_shift",
        "turnover_excess",
    ]
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    expected_effect: Optional[Dict[str, Any]] = None
    expected_risk: Optional[Dict[str, Any]] = None
    source_id: str = Field(min_length=1)
    source_type: str = Field(default="telemetry_engine")
    source_version: Optional[str] = None
    evidence_refs: List[str] = Field(default_factory=list)
    as_of: Optional[str] = None


class PerformanceSuggestionProducer:
    """Authoritative producer projecting telemetry and paper outcomes into AdjustmentSuggestions."""

    def __init__(self, store: Optional[PerformanceSuggestionStore] = None) -> None:
        self.store = store or PerformanceSuggestionStore()

    def produce_suggestion_from_outcome(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        evaluation: PerformanceOutcomeEvaluationInput,
        utc_now: Optional[str] = None,
    ) -> AdjustmentSuggestion:
        now_str = utc_now or datetime.now(timezone.utc).isoformat()
        as_of_str = evaluation.as_of or now_str

        seed = {
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "strategy_id": evaluation.strategy_id,
            "period": evaluation.period,
            "outcome_type": evaluation.outcome_type,
            "source_id": evaluation.source_id,
            "as_of": as_of_str,
        }
        digest = hashlib.sha256(json.dumps(seed, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        suggestion_id = f"sug-{evaluation.strategy_id[:8]}-{evaluation.outcome_type[:6]}-{digest}"

        provenance = SuggestionProvenance(
            source_id=evaluation.source_id,
            source_type=evaluation.source_type,
            produced_at=now_str,
            source_version=evaluation.source_version,
            evidence_refs=list(evaluation.evidence_refs),
        )

        suggestion = AdjustmentSuggestion(
            suggestion_id=suggestion_id,
            strategy_id=evaluation.strategy_id,
            period=evaluation.period,
            status="proposed",
            version=1,
            title=evaluation.title,
            rationale=evaluation.rationale,
            expected_effect=evaluation.expected_effect or {
                "metric": evaluation.outcome_type,
                "projected_improvement": evaluation.metrics.get("projected_improvement", "mitigation"),
            },
            expected_risk=evaluation.expected_risk or {
                "risk_type": evaluation.outcome_type,
                "current_level": evaluation.metrics.get("current_level", "elevated"),
            },
            provenance=provenance,
            as_of=as_of_str,
            updated_at=now_str,
            no_order_route_proof="agora_suggestion_state_only",
        )

        self.store.upsert_suggestion(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            suggestion=suggestion,
        )
        return suggestion
