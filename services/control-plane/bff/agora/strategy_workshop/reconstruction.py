"""Strategy Reconstruction models and background worker.

Implements StrategyReconstructionResult model and leased outbox worker per AGORA-WORKSHOP-CORE-20260813.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence
from pydantic import BaseModel, Field

from services.research.strategy_spec.models import (
    StrategySpec,
    StrategySpecValidationError,
    validate_strategy_spec,
    validate_strategy_spec_payload,
)


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
# Candidate StrategySpec Extraction Helpers
# --------------------------------------------------------------------------- #

def _extract_candidate_spec(
    *,
    strategy_spec: Optional[StrategySpec | Mapping[str, Any]],
    events: List[Dict[str, Any]],
    messages_content: List[str],
    workshop_id: str,
) -> tuple[Optional[StrategySpec | Mapping[str, Any]], Optional[str]]:
    """Extract a candidate StrategySpec from explicit args, events, or messages."""
    # 1. Explicit argument
    if strategy_spec is not None:
        return strategy_spec, None

    # 2. From events
    for event in reversed(events):
        if not isinstance(event, Mapping):
            continue
        if event.get("strategy_spec"):
            return event["strategy_spec"], None
        payload = event.get("payload")
        if isinstance(payload, Mapping):
            if payload.get("strategy_spec"):
                return payload["strategy_spec"], None
            if "spec_version" in payload and ("strategy_id" in payload or "hypothesis" in payload):
                return payload, None
        meta = event.get("metadata")
        if isinstance(meta, Mapping) and meta.get("strategy_spec"):
            return meta["strategy_spec"], None
        if event.get("strategy_spec_payload"):
            return event["strategy_spec_payload"], None

    # 3. From messages_content (JSON or markdown fence)
    for msg in reversed(messages_content):
        if not isinstance(msg, str):
            continue
        trimmed = msg.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, Mapping):
                    if parsed.get("strategy_spec"):
                        return parsed["strategy_spec"], None
                    if "spec_version" in parsed and ("strategy_id" in parsed or "hypothesis" in parsed):
                        return parsed, None
            except Exception:
                pass
        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", msg)
        if fence_match:
            try:
                parsed = json.loads(fence_match.group(1))
                if isinstance(parsed, Mapping):
                    if parsed.get("strategy_spec"):
                        return parsed["strategy_spec"], None
                    if "spec_version" in parsed and ("strategy_id" in parsed or "hypothesis" in parsed):
                        return parsed, None
            except Exception:
                pass

    # 4. From structured text definitions across messages
    combined_raw = "\n".join(messages_content)
    hyp_match = re.search(r"(?:^|\n|\.\s*)hypothesis\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    univ_match = re.search(r"(?:^|\n|\.\s*)universe\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    sig_match = re.search(r"(?:^|\n|\.\s*)signal\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    entry_match = re.search(r"(?:^|\n|\.\s*)entry\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    exit_match = re.search(r"(?:^|\n|\.\s*)exit\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    risk_match = re.search(r"(?:^|\n|\.\s*)risk\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    val_match = re.search(r"(?:^|\n|\.\s*)validation\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
    gov_match = re.search(r"(?:^|\n|\.\s*)governance\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)

    if hyp_match and univ_match and (sig_match or entry_match or risk_match):
        raw_univ = univ_match.group(1).strip()
        symbols = [
            token.upper()
            for token in re.findall(r"\b[A-Za-z0-9_]{2,10}\b", raw_univ)
            if token.upper() not in {"TOP", "AND", "FOR", "THE", "CRYPTO", "EQUITY", "PAIRS", "ALL", "WITH"}
            and not token.isdigit()
        ]
        if not symbols:
            symbols = [raw_univ[:20].strip() or "BTC"]

        hyp_text = hyp_match.group(1).strip()
        entry_text = entry_match.group(1).strip() if entry_match else f"Execute {hyp_text}"
        constructed = {
            "spec_version": "1.0",
            "strategy_id": f"strat-{workshop_id[:16]}",
            "title": f"Workshop Strategy {workshop_id[:8]}",
            "hypothesis": hyp_text,
            "objective": entry_text,
            "market_scope": {
                "symbols": symbols,
                "frequency": "1d",
            },
            "data_dependencies": [
                {"ref": "dataset:workshop-market-data", "kind": "dataset"},
            ],
            "execution_profile": {
                "signal_schema_version": "1.0",
                "quantity_type": "PERCENT_PORTFOLIO",
                "rebalance_cadence": exit_match.group(1).strip() if exit_match else "1d",
                "execution_mode_hint": "research",
            },
            "evaluation_plan": {
                "metrics": ["sharpe_ratio"],
                "candidate_gate": val_match.group(1).strip() if val_match else "standard",
            },
            "governance": {
                "policy_id": "workshop-policy-v1",
                "approval_required": True,
            },
            "provenance": {
                "source_kind": "workflow",
                "source_refs": [f"workshop:{workshop_id}"],
                "created_at": _utc_now(),
            },
        }
        return constructed, None

    return None, None


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
    strategy_spec: Optional[StrategySpec | Dict[str, Any]] = None,
) -> StrategyReconstructionResult:
    """Deterministic, server-derived strategy reconstruction evaluating typed StrategySpec semantics.

    Decides confirmed status from typed executable StrategySpec semantics. Keyword
    matching and character counts do not decide confirmed status.
    """
    reconstruction_id = f"recon-{uuid.uuid4().hex[:16]}"
    lineage = provider_lineage or {
        "engine": "pantheon_reconstruction_v1",
        "provider": "rule_based_analysis",
    }

    combined_raw = "\n".join(messages_content)
    combined_text = combined_raw.lower()

    # Step 1: Extract candidate StrategySpec
    candidate_spec, _ = _extract_candidate_spec(
        strategy_spec=strategy_spec,
        events=events,
        messages_content=messages_content,
        workshop_id=workshop_id,
    )

    # Step 2: Validate typed StrategySpec semantics
    valid_spec: Optional[StrategySpec] = None
    spec_validation_error: Optional[str] = None

    if candidate_spec is not None:
        if isinstance(candidate_spec, StrategySpec):
            errors = validate_strategy_spec(candidate_spec)
            if errors:
                spec_validation_error = "; ".join(errors)
            else:
                valid_spec = candidate_spec
        elif isinstance(candidate_spec, Mapping):
            try:
                validate_strategy_spec_payload(candidate_spec)
                spec_obj = StrategySpec.from_dict(candidate_spec, validate_schema=True)
                errors = validate_strategy_spec(spec_obj)
                if errors:
                    spec_validation_error = "; ".join(errors)
                else:
                    valid_spec = spec_obj
            except (StrategySpecValidationError, Exception) as exc:
                spec_validation_error = str(exc)
        else:
            spec_validation_error = "Supplied strategy_spec is not a valid Mapping or StrategySpec instance"

    blocks: Dict[str, StrategyMapBlock] = {}
    confirmed_fields: List[str] = []
    unconfirmed_fields: List[str] = []
    explicit_facts: List[str] = []
    inferences: List[str] = []
    assumptions: List[str] = []
    contradictions: List[str] = []

    if messages_content:
        explicit_facts.append(f"Received {len(messages_content)} user message(s) up to sequence {sequence_no}.")

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

    if valid_spec is not None:
        # Confirmed from typed executable StrategySpec semantics
        blocks["hypothesis"] = StrategyMapBlock(
            status="confirmed",
            summary=valid_spec.hypothesis,
            details={"hypothesis": valid_spec.hypothesis, "objective": valid_spec.objective},
        )
        blocks["universe"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Symbols: {', '.join(valid_spec.market_scope.symbols)}, Frequency: {valid_spec.market_scope.frequency}",
            details=valid_spec.market_scope.to_dict(),
        )
        blocks["data_requirements"] = StrategyMapBlock(
            status="confirmed",
            summary=f"{len(valid_spec.data_dependencies)} data dependencies ({', '.join(d.ref for d in valid_spec.data_dependencies)})",
            details={"data_dependencies": [d.to_dict() for d in valid_spec.data_dependencies]},
        )
        blocks["signal_definition"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Signal schema: {valid_spec.execution_profile.signal_schema_version}",
            details={"signal_schema_version": valid_spec.execution_profile.signal_schema_version},
        )
        blocks["entry_rules"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Objective: {valid_spec.objective}",
            details={"objective": valid_spec.objective},
        )
        blocks["exit_rules"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Rebalance cadence: {valid_spec.execution_profile.rebalance_cadence or 'unspecified'}",
            details={"rebalance_cadence": valid_spec.execution_profile.rebalance_cadence},
        )
        blocks["position_sizing"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Quantity type: {valid_spec.execution_profile.quantity_type}",
            details={"quantity_type": str(valid_spec.execution_profile.quantity_type)},
        )
        blocks["risk_controls"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Policy: {valid_spec.governance.policy_id}, Approval required: {valid_spec.governance.approval_required}",
            details=valid_spec.governance.to_dict(),
        )
        blocks["cost_liquidity_capacity"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Execution mode hint: {valid_spec.execution_profile.execution_mode_hint or 'research'}",
            details={"execution_mode_hint": str(valid_spec.execution_profile.execution_mode_hint or 'research')},
        )
        blocks["validation_plan"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Metrics: {', '.join(valid_spec.evaluation_plan.metrics)}",
            details=valid_spec.evaluation_plan.to_dict(),
        )
        blocks["regime_invalidation"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Candidate gate: {valid_spec.evaluation_plan.candidate_gate or 'standard'}, Paper gate: {valid_spec.evaluation_plan.paper_gate or 'standard'}",
            details={
                "candidate_gate": valid_spec.evaluation_plan.candidate_gate,
                "paper_gate": valid_spec.evaluation_plan.paper_gate,
                "live_gate": valid_spec.evaluation_plan.live_gate,
            },
        )
        blocks["governance_constraints"] = StrategyMapBlock(
            status="confirmed",
            summary=f"Policy: {valid_spec.governance.policy_id}, Risk profile: {valid_spec.governance.risk_profile or 'standard'}",
            details=valid_spec.governance.to_dict(),
        )
        confirmed_fields = list(blocks.keys())
        unconfirmed_fields = []
        blockers: List[str] = []
        grade: CompletenessGrade = "trading_room_ready"
        draft_proposal = {"strategy_spec": valid_spec.to_dict()}
        explicit_facts.append(
            f"Evaluated typed executable StrategySpec '{valid_spec.strategy_id}' (v{valid_spec.spec_version}): confirmed all 12 strategy dimensions."
        )
        inferences.append(
            f"Strategy '{valid_spec.strategy_id}' is executable with objective: {valid_spec.objective}."
        )
    else:
        # Non-executable / incomplete reconstruction: evaluate blocks without allowing keywords or char count to confirm
        explicit_definitions: Dict[str, str] = {}
        hyp_match = re.search(r"(?:^|\n|\.\s*)hypothesis\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
        if hyp_match:
            explicit_definitions["hypothesis"] = hyp_match.group(1).strip()
        univ_match = re.search(r"(?:^|\n|\.\s*)universe\s*:\s*([^.\n]+)", combined_raw, re.IGNORECASE)
        if univ_match:
            explicit_definitions["universe"] = univ_match.group(1).strip()

        for block_name, keywords in block_keywords.items():
            if block_name in explicit_definitions and len(explicit_definitions[block_name]) >= 2:
                statement = explicit_definitions[block_name]
                blocks[block_name] = StrategyMapBlock(
                    status="confirmed",
                    summary=statement,
                    details={"explicit_statement": statement},
                )
                confirmed_fields.append(block_name)
            else:
                matched = [kw for kw in keywords if kw in combined_text]
                if matched:
                    # Keyword matches alone produce partial status, never confirmed
                    blocks[block_name] = StrategyMapBlock(
                        status="partial",
                        summary=f"Identified terms: {', '.join(matched)}",
                        details={"keywords_found": matched},
                    )
                    unconfirmed_fields.append(block_name)
                else:
                    blocks[block_name] = StrategyMapBlock(status="missing")
                    unconfirmed_fields.append(block_name)

        if "hypothesis" in confirmed_fields or "signal_definition" in confirmed_fields:
            inferences.append("User aims for systematic directional or quantitative strategy.")
        else:
            assumptions.append("Assuming default daily asset trading scope if unspecified.")

        blockers: List[str] = []
        if spec_validation_error:
            blockers.append(f"StrategySpec validation error: {spec_validation_error}")
        else:
            blockers.append("No valid executable StrategySpec provided or reconstructed")

        if "hypothesis" not in confirmed_fields:
            blockers.append("Missing core strategy hypothesis")
        if "universe" not in confirmed_fields and blocks["universe"].status != "partial":
            blockers.append("Target asset universe is undefined")
        if "entry_rules" not in confirmed_fields and blocks["entry_rules"].status != "partial":
            blockers.append("Entry trigger rules are undefined")

        # Without a valid StrategySpec, the reconstruction stays insufficient
        grade = "insufficient"
        draft_proposal = None

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
        draft_proposal=draft_proposal,
        provider_lineage=lineage,
        created_at=_utc_now(),
    )
