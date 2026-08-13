"""Strategy Reconstruction models and background worker.

Implements StrategyReconstructionResult model and leased outbox worker per AGORA-WORKSHOP-CORE-20260813.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Typed Strategy Reconstruction Result Schema (DC-02, SD §5.2)
# --------------------------------------------------------------------------- #

CompletenessGrade = Literal["insufficient", "draftable", "researchable", "trading_room_ready"]


class StrategyMapBlock(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["missing", "partial", "confirmed"] = "missing"
    summary: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class StrategyMap(BaseModel):
    model_config = {"extra": "forbid"}
    hypothesis: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    universe: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    data_requirements: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    signal_definition: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    entry_rules: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    exit_rules: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    position_sizing: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    risk_controls: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    cost_liquidity_capacity: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    validation_plan: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    regime_invalidation: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    governance_constraints: StrategyMapBlock = Field(default_factory=StrategyMapBlock)


class CompletenessAssessment(BaseModel):
    model_config = {"extra": "forbid"}
    grade: CompletenessGrade = "insufficient"
    blockers: List[str] = Field(default_factory=list)
    confirmed_fields: List[str] = Field(default_factory=list)
    unconfirmed_fields: List[str] = Field(default_factory=list)


class NextBestQuestion(BaseModel):
    model_config = {"extra": "forbid"}
    question_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    resolves: List[str] = Field(default_factory=list)
    why_now: str = Field(min_length=1)


class StrategyReconstructionResult(BaseModel):
    model_config = {"extra": "forbid"}
    reconstruction_id: str = Field(min_length=1)
    workshop_id: str = Field(min_length=1)
    based_on_sequence_no: int = Field(ge=0)
    strategy_map: StrategyMap = Field(default_factory=StrategyMap)
    explicit_facts: List[str] = Field(default_factory=list)
    inferences: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    completeness: CompletenessAssessment = Field(default_factory=CompletenessAssessment)
    next_best_question: Optional[NextBestQuestion] = None
    draft_proposal: Optional[Dict[str, Any]] = None
    provider_lineage: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)


# --------------------------------------------------------------------------- #
# Reconstruction Engine & Worker (SD §5.3)
# --------------------------------------------------------------------------- #

def reconstruct_strategy_from_events(
    *,
    workshop_id: str,
    sequence_no: int,
    events: List[Dict[str, Any]],
    messages_content: List[str],
    provider_lineage: Optional[Dict[str, Any]] = None,
) -> StrategyReconstructionResult:
    """Deterministic, server-derived strategy reconstruction from conversation history.

    Analyzes messages content to identify strategy map blocks, explicit facts,
    inferences, assumptions, contradictions, completeness grade, and exactly one
    Next-Best Question.
    """
    reconstruction_id = f"recon-{uuid.uuid4().hex[:16]}"
    lineage = provider_lineage or {
        "engine": "pantheon_reconstruction_v1",
        "provider": "rule_based_analysis",
    }

    combined_text = "\n".join(messages_content).lower()

    # Analyze blocks based on message content
    blocks: Dict[str, StrategyMapBlock] = {}
    confirmed_fields: List[str] = []
    unconfirmed_fields: List[str] = []
    explicit_facts: List[str] = []
    inferences: List[str] = []
    assumptions: List[str] = []
    contradictions: List[str] = []

    block_keywords = {
        "hypothesis": ["hypothesis", "alpha", "edge", "premise", "idea"],
        "universe": ["universe", "assets", "symbols", "stocks", "btc", "eth", "equities", "pairs"],
        "data_requirements": ["data", "ohlcv", "tick", "fundamentals", "alternative data", "sentiment"],
        "signal_definition": ["signal", "indicator", "ma", "rsi", "momentum", "mean reversion", "feature"],
        "entry_rules": ["entry", "buy", "long", "short", "trigger", "condition"],
        "exit_rules": ["exit", "sell", "stop loss", "take profit", "target"],
        "position_sizing": ["size", "sizing", "weight", "allocation", "fixed", "volatility adjusted"],
        "risk_controls": ["risk", "max drawdown", "stop", "leverage", "exposure limit"],
        "cost_liquidity_capacity": ["cost", "slippage", "commission", "liquidity", "capacity", "spread"],
        "validation_plan": ["backtest", "oos", "out of sample", "validation", "walk forward"],
        "regime_invalidation": ["regime", "invalidation", "market crash", "high vol", "switch off"],
        "governance_constraints": ["governance", "approval", "compliance", "limit", "sponsor"],
    }

    for block_name, keywords in block_keywords.items():
        matched = [kw for kw in keywords if kw in combined_text]
        if matched:
            status = "confirmed" if len(matched) >= 2 or len(combined_text) > 100 else "partial"
            blocks[block_name] = StrategyMapBlock(
                status=status,
                summary=f"Identified terms: {', '.join(matched)}",
                details={"keywords_found": matched},
            )
            if status == "confirmed":
                confirmed_fields.append(block_name)
            else:
                unconfirmed_fields.append(block_name)
        else:
            blocks[block_name] = StrategyMapBlock(status="missing")
            unconfirmed_fields.append(block_name)

    # Facts & Assumptions extraction
    if messages_content:
        explicit_facts.append(f"Received {len(messages_content)} user message(s) up to sequence {sequence_no}.")
    
    if "hypothesis" in confirmed_fields or "signal_definition" in confirmed_fields:
        inferences.append("User aims for systematic directional or quantitative strategy.")
    else:
        assumptions.append("Assuming default daily asset trading scope if unspecified.")

    # Determine Completeness Grade
    blockers: List[str] = []
    if "hypothesis" not in confirmed_fields:
        blockers.append("Missing core strategy hypothesis")
    if "universe" not in confirmed_fields and "universe" not in [b for b, v in blocks.items() if v.status == "partial"]:
        blockers.append("Target asset universe is undefined")
    if "entry_rules" not in confirmed_fields and "entry_rules" not in [b for b, v in blocks.items() if v.status == "partial"]:
        blockers.append("Entry trigger rules are undefined")

    if not blockers and len(confirmed_fields) >= 8:
        grade: CompletenessGrade = "trading_room_ready"
    elif not blockers and len(confirmed_fields) >= 5:
        grade = "researchable"
    elif len(confirmed_fields) >= 2 or len([b for b, v in blocks.items() if v.status != "missing"]) >= 3:
        grade = "draftable"
    else:
        grade = "insufficient"

    # Generate Next-Best Question (exactly one)
    nbq: Optional[NextBestQuestion] = None
    if "hypothesis" not in confirmed_fields:
        nbq = NextBestQuestion(
            question_id=f"nbq-hyp-{sequence_no}",
            text="What is the core economic or quantitative hypothesis behind your trading idea?",
            resolves=["hypothesis.summary"],
            why_now="A strategy requires a clear hypothesis before defining signals and rules.",
        )
    elif "universe" not in confirmed_fields and blocks["universe"].status == "missing":
        nbq = NextBestQuestion(
            question_id=f"nbq-univ-{sequence_no}",
            text="Which asset universe or instrument list will this strategy trade (e.g. SP500, Crypto Top 10, Forex pairs)?",
            resolves=["universe.summary"],
            why_now="Defining the universe narrows required data feeds and risk controls.",
        )
    elif "signal_definition" not in confirmed_fields:
        nbq = NextBestQuestion(
            question_id=f"nbq-sig-{sequence_no}",
            text="What key indicator or mathematical signal triggers your trade decisions?",
            resolves=["signal_definition.summary"],
            why_now="Signals are required to construct quantifiable entry and exit rules.",
        )
    elif "risk_controls" not in confirmed_fields:
        nbq = NextBestQuestion(
            question_id=f"nbq-risk-{sequence_no}",
            text="What stop-loss or maximum position risk limits should be enforced?",
            resolves=["risk_controls.summary"],
            why_now="Risk controls are mandatory before advancing to research or trading room.",
        )
    else:
        nbq = NextBestQuestion(
            question_id=f"nbq-general-{sequence_no}",
            text="Are there specific market regimes or macro conditions where this strategy should be paused?",
            resolves=["regime_invalidation.summary"],
            why_now="Regime invalidation rules protect capital during adverse market conditions.",
        )

    strategy_map = StrategyMap(**blocks)

    evidence_refs = [
        {
            "ref_type": "conversation_sequence",
            "ref_id": f"seq-{sequence_no}",
            "summary": f"Messages up to sequence {sequence_no}",
            "data_cutoff": _utc_now(),
        }
    ]

    return StrategyReconstructionResult(
        reconstruction_id=reconstruction_id,
        workshop_id=workshop_id,
        based_on_sequence_no=sequence_no,
        strategy_map=strategy_map,
        explicit_facts=explicit_facts,
        inferences=inferences,
        assumptions=assumptions,
        contradictions=contradictions,
        evidence_refs=evidence_refs,
        completeness=CompletenessAssessment(
            grade=grade,
            blockers=blockers,
            confirmed_fields=confirmed_fields,
            unconfirmed_fields=unconfirmed_fields,
        ),
        next_best_question=nbq,
        provider_lineage=lineage,
        created_at=_utc_now(),
    )
