"""MGMT-OPS-001 shared operations read model: identity and source-confidence contract.

Persona Fleet, Portfolio Book, Performance Attribution, Persona League,
Quarterly Ranking, and Human Review must agree on one vocabulary for
identity, source status, and data confidence instead of each page inventing
an incompatible local fallback. This module defines that vocabulary and the
pure helpers used to build it; composition against live BFF read sources
lives in main.py, which has the read_store and route wiring this module
intentionally stays decoupled from so it can be unit tested in isolation.

See docs/04/pantheon_management_console_operations_workflow_2026-07-07/
MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md, "Read Model Contract".
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Any, Iterable, List, Optional

from pydantic import BaseModel, Field


class DataConfidence(str, Enum):
    """Ladder of trust for a represented row, formal down to unavailable."""

    FORMAL = "formal"
    PARTIAL = "partial"
    FALLBACK = "fallback"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SourceState(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SourceStatus(BaseModel):
    source_name: str
    source_status: SourceState
    source_freshness: Optional[str] = None
    source_row_count: Optional[int] = None
    source_error: Optional[str] = None
    coverage_ratio: Optional[float] = None


class SourceDiagnostic(BaseModel):
    """A missing join or degraded source, surfaced explicitly instead of a
    dropped row or a `nan` metric."""

    source_name: str
    code: str
    message: str


class OperationsIdentity(BaseModel):
    persona_id: str
    persona_label: Optional[str] = None
    stage: Optional[str] = None
    runtime_ids: List[str] = Field(default_factory=list)
    paper_ledger_ids: List[str] = Field(default_factory=list)
    capital_pool_ids: List[str] = Field(default_factory=list)
    sleeve_ids: List[str] = Field(default_factory=list)
    strategy_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    broker_ids: List[str] = Field(default_factory=list)
    period: str
    as_of: str


class OperationsPerformance(BaseModel):
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    drawdown_pct: Optional[float] = None
    risk_pct: Optional[float] = None
    sharpe: Optional[float] = None
    rank: Optional[int] = None
    score: Optional[float] = None
    performance_delta: Optional[float] = None
    source_contribution: Optional[float] = None


class OperationsReadModelEntry(BaseModel):
    identity: OperationsIdentity
    data_confidence: DataConfidence
    performance: OperationsPerformance
    sources: List[SourceStatus] = Field(default_factory=list)
    diagnostics: List[SourceDiagnostic] = Field(default_factory=list)


def sanitize_metric(value: Any) -> Optional[float]:
    """Coerce a raw metric to a finite float, or None.

    Operators must never see `nan`/`inf` rendered as a number; missing or
    non-finite evidence becomes an explicit None so the caller can attach a
    diagnostic instead of a silently wrong value.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def dedupe_ids(values: Iterable[Optional[str]]) -> List[str]:
    seen: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def build_operations_identity(
    *,
    persona_id: str,
    persona_label: Optional[str] = None,
    stage: Optional[str] = None,
    runtime_ids: Iterable[Optional[str]] = (),
    paper_ledger_ids: Iterable[Optional[str]] = (),
    capital_pool_ids: Iterable[Optional[str]] = (),
    sleeve_ids: Iterable[Optional[str]] = (),
    strategy_ids: Iterable[Optional[str]] = (),
    artifact_ids: Iterable[Optional[str]] = (),
    broker_ids: Iterable[Optional[str]] = (),
    period: str,
    as_of: str,
) -> OperationsIdentity:
    return OperationsIdentity(
        persona_id=str(persona_id),
        persona_label=(str(persona_label).strip() or None) if persona_label else None,
        stage=(str(stage).strip() or None) if stage else None,
        runtime_ids=dedupe_ids(runtime_ids),
        paper_ledger_ids=dedupe_ids(paper_ledger_ids),
        capital_pool_ids=dedupe_ids(capital_pool_ids),
        sleeve_ids=dedupe_ids(sleeve_ids),
        strategy_ids=dedupe_ids(strategy_ids),
        artifact_ids=dedupe_ids(artifact_ids),
        broker_ids=dedupe_ids(broker_ids),
        period=str(period),
        as_of=str(as_of),
    )


def diagnostic(source_name: str, code: str, message: str) -> SourceDiagnostic:
    return SourceDiagnostic(source_name=source_name, code=code, message=message)


def classify_confidence(
    *,
    has_formal_match: bool,
    has_partial_evidence: bool,
    is_fallback: bool,
    has_degraded_source: bool,
    has_unavailable_source: bool,
) -> DataConfidence:
    """Rank confidence in the represented performance evidence.

    A formal match wins outright unless a source it depends on is degraded,
    in which case the row is only as trustworthy as its weakest joined
    source ("Show mismatches as first-class incidents instead of quietly
    dropping them"). Fallback (synthesized from another operator source,
    e.g. persona-fleet summary standing in for missing formal attribution)
    ranks below partial but above a bare unavailable read.
    """
    if has_formal_match:
        return DataConfidence.DEGRADED if has_degraded_source else DataConfidence.FORMAL
    if has_partial_evidence:
        return DataConfidence.DEGRADED if has_degraded_source else DataConfidence.PARTIAL
    if is_fallback:
        return DataConfidence.FALLBACK
    if has_unavailable_source:
        return DataConfidence.UNAVAILABLE
    return DataConfidence.UNAVAILABLE
