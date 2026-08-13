"""Pydantic data models for owner-scoped decision event projection."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


def compute_event_digest(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class DecisionEventFreshness(BaseModel):
    model_config = {"extra": "forbid"}

    evaluated_at: str = Field(min_length=1)
    signal_as_of: str = Field(min_length=1)
    risk_as_of: str = Field(min_length=1)
    max_staleness_sec: float = 300.0
    is_fresh: bool = True


class DecisionEventEvidenceRef(BaseModel):
    model_config = {"extra": "forbid"}

    ref_type: str = Field(min_length=1)
    ref_id: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    as_of: str = Field(min_length=1)


class DecisionEventRecord(BaseModel):
    model_config = {"extra": "forbid"}

    decision_event_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    owner_scope: Literal["user_private"] = "user_private"
    strategy_id: str = Field(min_length=1)
    persona_id: Optional[str] = None
    event_type: str = Field(min_length=1)
    probability: float = Field(ge=0.0, le=1.0)
    expected_value: float = 0.0
    risk: Dict[str, Any] = Field(default_factory=dict)
    invalidation_conditions: List[str] = Field(default_factory=list)
    freshness: DecisionEventFreshness
    evidence_refs: List[DecisionEventEvidenceRef] = Field(default_factory=list)
    status: Literal["projected", "stale", "invalidated", "retained"] = "projected"
    created_at: str = Field(min_length=1)
    has_broker_authority: Literal[False] = False


class DecisionProjectionCommand(BaseModel):
    model_config = {"extra": "forbid"}

    idempotency_key: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    persona_id: Optional[str] = None
    event_type: str = Field(min_length=1)
    signal_data: Dict[str, Any] = Field(default_factory=dict)
    risk_data: Dict[str, Any] = Field(default_factory=dict)
    signal_as_of: str = Field(min_length=1)
    risk_as_of: str = Field(min_length=1)
    max_staleness_sec: float = 300.0
    evidence_refs: List[DecisionEventEvidenceRef] = Field(default_factory=list)
