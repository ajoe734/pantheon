"""Unit tests for services/evaluation/critic.py and CriticResult models."""
from __future__ import annotations

import pytest

from services.evaluation.critic import critique
from services.evaluation.models import (
    CriticResult,
    DecisionGuidance,
    Finding,
    KeyRisk,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_finding(fid="f1", category="execution_fidelity", severity="low"):
    return Finding(
        finding_id=fid,
        category=category,
        severity=severity,
        description="Test finding",
        evidence=["evidence A"],
        impact="Low implementation risk",
        recommendation="Monitor only",
    )


def make_guidance(action="approve_candidate_to_paper", confidence=0.85):
    return DecisionGuidance(
        recommended_action=action,
        confidence_in_recommendation=confidence,
        rationale_summary="Score above threshold, proceed.",
    )


def make_risk(rank=1, risk_type="market_regime_sensitivity", risk_level="low"):
    return KeyRisk(
        rank=rank,
        risk_type=risk_type,
        risk_level=risk_level,
        description="Performance depends on elevated volatility",
        likelihood="low",
        impact="low",
        mitigation="Standard paper period sufficient",
    )


# ---------------------------------------------------------------------------
# Finding validation
# ---------------------------------------------------------------------------

def test_finding_valid():
    f = make_finding()
    assert f.finding_id == "f1"


def test_finding_invalid_category():
    with pytest.raises(ValueError, match="category"):
        Finding(finding_id="f1", category="unknown_cat", severity="low", description="x")


def test_finding_invalid_severity():
    with pytest.raises(ValueError, match="severity"):
        Finding(finding_id="f1", category="execution_fidelity", severity="extreme", description="x")


# ---------------------------------------------------------------------------
# KeyRisk validation
# ---------------------------------------------------------------------------

def test_key_risk_valid():
    r = make_risk()
    assert r.rank == 1


def test_key_risk_invalid_type():
    with pytest.raises(ValueError, match="risk_type"):
        KeyRisk(rank=1, risk_type="unknown", risk_level="low", description="x")


def test_key_risk_invalid_level():
    with pytest.raises(ValueError, match="risk_level"):
        KeyRisk(rank=1, risk_type="other", risk_level="extreme", description="x")


# ---------------------------------------------------------------------------
# DecisionGuidance validation
# ---------------------------------------------------------------------------

def test_guidance_valid():
    g = make_guidance()
    assert g.confidence_in_recommendation == 0.85


def test_guidance_invalid_action():
    with pytest.raises(ValueError, match="recommended_action"):
        DecisionGuidance(
            recommended_action="do_nothing",
            confidence_in_recommendation=0.5,
            rationale_summary="x",
        )


def test_guidance_invalid_confidence():
    with pytest.raises(ValueError, match="confidence_in_recommendation"):
        DecisionGuidance(
            recommended_action="approve_candidate_to_paper",
            confidence_in_recommendation=1.5,
            rationale_summary="x",
        )


# ---------------------------------------------------------------------------
# critique() integration
# ---------------------------------------------------------------------------

def test_critique_returns_critic_result():
    result = critique(
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        critique_trigger="risk_flagged",
        findings=[make_finding()],
        key_risks=[make_risk()],
        decision_guidance=make_guidance(),
    )
    assert isinstance(result, CriticResult)
    assert result.artifact_type == "critique_result"
    assert result.strategy_id == "strat_xyz"


def test_critique_registry_id_auto_generated():
    r = critique(
        strategy_id="strat_a",
        target_artifact_id="strat_a_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        critique_trigger="borderline_score",
        findings=[make_finding()],
        key_risks=[],
        decision_guidance=make_guidance(),
    )
    assert r.registry_id.startswith("crit-strat_a-")


def test_critique_explicit_registry_id():
    r = critique(
        strategy_id="s",
        target_artifact_id="s_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        critique_trigger="borderline_score",
        findings=[make_finding()],
        key_risks=[],
        decision_guidance=make_guidance(),
        registry_id="my-crit-id",
    )
    assert r.registry_id == "my-crit-id"


def test_critique_empty_findings_raises():
    with pytest.raises(ValueError, match="findings"):
        critique(
            strategy_id="s",
            target_artifact_id="s_v1",
            target_artifact_type="strategy_spec",
            target_promotion_state="candidate",
            critique_trigger="borderline_score",
            findings=[],
            key_risks=[],
            decision_guidance=make_guidance(),
        )


def test_critique_invalid_trigger_raises():
    with pytest.raises(ValueError, match="critique_trigger"):
        critique(
            strategy_id="s",
            target_artifact_id="s_v1",
            target_artifact_type="strategy_spec",
            target_promotion_state="candidate",
            critique_trigger="bad_trigger",
            findings=[make_finding()],
            key_risks=[],
            decision_guidance=make_guidance(),
        )


def test_critique_to_dict_round_trip():
    r = critique(
        strategy_id="strat_xyz",
        target_artifact_id="strat_xyz_v1.3.0",
        target_artifact_type="strategy_spec",
        target_promotion_state="candidate",
        critique_trigger="risk_flagged",
        findings=[make_finding()],
        key_risks=[make_risk()],
        decision_guidance=make_guidance(),
        evaluation_context={"referenced_evaluation_id": "eval-xyz-v1", "evaluator_overall_score": 0.83},
        rationale="Score above threshold with manageable risks.",
    )
    d = r.to_dict()
    assert d["artifact_type"] == "critique_result"
    assert len(d["findings"]) == 1
    assert d["findings"][0]["finding_id"] == "f1"
    assert len(d["key_risks"]) == 1
    assert d["decision_guidance"]["recommended_action"] == "approve_candidate_to_paper"
    assert d["evaluation_context"]["evaluator_overall_score"] == 0.83
    assert d["rationale"] == "Score above threshold with manageable risks."


def test_critique_empty_key_risks_allowed():
    r = critique(
        strategy_id="s",
        target_artifact_id="s_v1",
        target_artifact_type="strategy_spec",
        target_promotion_state="paper",
        critique_trigger="failure_forensics",
        findings=[make_finding()],
        key_risks=[],
        decision_guidance=make_guidance(action="approve_paper_to_live"),
    )
    assert r.key_risks == []
    d = r.to_dict()
    assert d["key_risks"] == []
