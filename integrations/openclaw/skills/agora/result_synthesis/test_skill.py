"""Tests for agora-result-synthesis skill — golden evals per C1 SPEC §6.

Golden evals:
  1. V3→V4 threshold/liquidity change: output quantitative comparison, verdict=promising
  2. OOS failure with IS pass: verdict=needs_revision or reject; IS alone must not yield promising
  3. Consult disagreement: risk vs alpha persona positions preserved verbatim in unresolved_decisions

Additional tests:
  - Degraded mode (no synthesis_adapter): blocked, SYNTHESIS_ADAPTER_UNAVAILABLE, no verdict forged
  - Empty research_run_refs: blocked, INPUT_SCHEMA_INVALID
  - Empty input evidence_refs: blocked, INPUT_SCHEMA_INVALID
  - Stub-only run: STUB_RESULT_NOT_PRODUCTION_PROOF warning; promising downgraded to needs_revision
  - Mixed real+stub run: warning emitted but verdict not downgraded (real run present)
  - Ungrounded non-insufficient verdict: blocked, INSUFFICIENT_EVIDENCE
  - Conflicts preserved verbatim in unresolved_decisions
  - Proposed patches forwarded with base_version, rationale, revalidation_plan
  - Unknown verdict enum downgraded to insufficient
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from integrations.openclaw.skills.agora.result_synthesis.skill import (
    INPUT_SCHEMA_INVALID,
    INSUFFICIENT_EVIDENCE,
    STUB_RESULT_NOT_PRODUCTION_PROOF,
    SYNTHESIS_ADAPTER_UNAVAILABLE,
    ResultSynthesisInput,
    ResultSynthesisOutput,
    run_result_synthesis,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_input(
    research_run_refs: Optional[List[str]] = None,
    consult_memo_refs: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    user_decision_style_ref: Optional[str] = None,
) -> ResultSynthesisInput:
    return ResultSynthesisInput(
        strategy_spec_ref="spec-ref-momentum-v4",
        base_version_id="wv-001",
        research_run_refs=research_run_refs if research_run_refs is not None else ["run-001"],
        consult_memo_refs=consult_memo_refs if consult_memo_refs is not None else [],
        evidence_refs=evidence_refs if evidence_refs is not None else ["ev-001"],
        user_decision_style_ref=user_decision_style_ref,
    )


def _mock_adapter(
    *,
    verdict: str = "promising",
    confidence: float = 0.8,
    core_metrics: Optional[Dict[str, float]] = None,
    strengths: Optional[List[str]] = None,
    weaknesses: Optional[List[str]] = None,
    regime_findings: Optional[List[str]] = None,
    cost_capacity_findings: Optional[List[str]] = None,
    proposed_patches: Optional[List[Any]] = None,
    unresolved_decisions: Optional[List[Any]] = None,
    discussion_card: str = "Strategy looks promising based on evidence.",
    evidence_refs: Optional[List[str]] = None,
    run_modes: Optional[List[str]] = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.synthesize.return_value = {
        "verdict": verdict,
        "confidence": confidence,
        "core_metrics": core_metrics or {},
        "strengths": strengths or [],
        "weaknesses": weaknesses or [],
        "regime_findings": regime_findings or [],
        "cost_capacity_findings": cost_capacity_findings or [],
        "proposed_patches": proposed_patches or [],
        "unresolved_decisions": unresolved_decisions or [],
        "discussion_card": discussion_card,
        "evidence_refs": evidence_refs if evidence_refs is not None else ["ev-001"],
        "run_modes": run_modes if run_modes is not None else ["real"],
    }
    return adapter


# ---------------------------------------------------------------------------
# Golden eval 1 — V3→V4 threshold/liquidity change (quantitative comparison)
# ---------------------------------------------------------------------------

def test_golden_eval_1_v3_v4_threshold_liquidity():
    """V3→V4 patch: quantitative before/after in core_metrics; verdict=promising; evidence grounded."""
    core_metrics = {
        "sharpe_v3": 1.2,
        "sharpe_v4": 1.8,
        "liquidity_score_v3": 0.55,
        "liquidity_score_v4": 0.72,
        "max_drawdown_v4": -0.12,
    }
    proposed_patches = [
        {
            "proposal_id": "vpp_test001",
            "base_version_id": "wv-001",
            "rationale": "Raise entry threshold 0.03→0.05 to improve liquidity profile",
            "predicted_effects": ["reduce trade count by ~20%", "improve liquidity_score by ~0.15"],
            "revalidation_plan": "Re-run rolling OOS on 2024–2026 with new threshold",
            "evidence_refs": ["ev-backtest-v4-001", "ev-oos-v4-002"],
        }
    ]

    adapter = _mock_adapter(
        verdict="promising",
        confidence=0.82,
        core_metrics=core_metrics,
        strengths=["[OOS] V4 Sharpe 1.8 vs V3 1.2 — improvement grounded in rolling OOS"],
        weaknesses=["[IS] V4 max drawdown -12% requires monitoring in high-VIX regimes"],
        proposed_patches=proposed_patches,
        evidence_refs=["ev-backtest-v4-001", "ev-oos-v4-002"],
        run_modes=["real", "real"],
    )

    result = run_result_synthesis(
        _valid_input(
            research_run_refs=["run-v3-001", "run-v4-001"],
            evidence_refs=["ev-backtest-v4-001", "ev-oos-v4-002"],
        ),
        synthesis_adapter=adapter,
        synthesis_id="rs-golden-1",
    )

    assert result.status == "completed"
    assert result.verdict == "promising"
    assert result.confidence == pytest.approx(0.82)

    # Quantitative before/after metrics must be present
    assert "sharpe_v3" in result.core_metrics
    assert "sharpe_v4" in result.core_metrics
    assert result.core_metrics["sharpe_v4"] > result.core_metrics["sharpe_v3"]
    assert "liquidity_score_v3" in result.core_metrics
    assert "liquidity_score_v4" in result.core_metrics

    # Evidence grounded
    assert len(result.evidence_refs) > 0

    # Proposed patch has required fields
    assert len(result.proposed_version_patches) == 1
    patch = result.proposed_version_patches[0]
    assert "base_version_id" in patch
    assert "rationale" in patch
    assert "predicted_effects" in patch
    assert "revalidation_plan" in patch

    assert result.blocking_reasons == []
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Golden eval 2 — OOS failure, IS pass → needs_revision or reject
# ---------------------------------------------------------------------------

def test_golden_eval_2_oos_fail_is_pass_needs_revision():
    """OOS failure with IS pass: verdict must be needs_revision or reject; IS alone not promising."""
    adapter = _mock_adapter(
        verdict="needs_revision",
        confidence=0.60,
        core_metrics={
            "sharpe_is": 2.1,
            "sharpe_oos": 0.3,
            "max_drawdown_oos": -0.28,
        },
        strengths=["[IS] Sharpe 2.1 — strong in-sample fit"],
        weaknesses=[
            "[OOS] Sharpe 0.3 — strategy underperforms out-of-sample; possible overfit",
            "[OOS] Max drawdown -28% exceeds risk threshold",
        ],
        evidence_refs=["ev-is-001", "ev-oos-fail-001"],
        run_modes=["real"],
    )

    result = run_result_synthesis(
        _valid_input(
            research_run_refs=["run-is-001", "run-oos-001"],
            evidence_refs=["ev-is-001", "ev-oos-fail-001"],
        ),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    assert result.verdict in ("needs_revision", "reject")

    # OOS failure must appear in weaknesses
    oos_weakness = any("OOS" in w or "oos" in w.lower() for w in result.weaknesses)
    assert oos_weakness, "OOS failure must be reflected in weaknesses"

    # Evidence grounded
    assert len(result.evidence_refs) > 0

    # IS result alone must not produce promising
    assert result.verdict != "promising"
    assert result.blocking_reasons == []


def test_golden_eval_2_strong_oos_fail_reject():
    """Severe OOS degradation results in reject."""
    adapter = _mock_adapter(
        verdict="reject",
        confidence=0.85,
        core_metrics={"sharpe_is": 3.0, "sharpe_oos": -0.5},
        weaknesses=["[OOS] Negative Sharpe — strategy failed OOS validation entirely"],
        evidence_refs=["ev-oos-reject-001"],
        run_modes=["real"],
    )

    result = run_result_synthesis(
        _valid_input(evidence_refs=["ev-oos-reject-001"]),
        synthesis_adapter=adapter,
    )

    assert result.verdict == "reject"
    assert result.status == "completed"
    assert result.evidence_refs


# ---------------------------------------------------------------------------
# Golden eval 3 — Consult disagreement (risk vs alpha persona)
# ---------------------------------------------------------------------------

def test_golden_eval_3_consult_disagreement_preserved():
    """Risk and alpha persona disagreement preserved verbatim; servant does not suppress."""
    risk_position = {
        "persona": "risk_reviewer",
        "position": "Strategy concentration risk is unacceptable in current regime",
        "evidence_refs": ["ev-risk-001"],
    }
    alpha_position = {
        "persona": "alpha_analyst",
        "position": "Alpha signal is statistically significant; concentration is justified",
        "evidence_refs": ["ev-alpha-001"],
    }
    unresolved = [
        {
            "conflict_id": "conflict-concentration-001",
            "topic": "concentration risk vs alpha quality",
            "positions": [risk_position, alpha_position],
        }
    ]

    adapter = _mock_adapter(
        verdict="needs_revision",
        confidence=0.55,
        unresolved_decisions=unresolved,
        discussion_card=(
            "Risk reviewer: concentration risk is unacceptable. "
            "Alpha analyst: alpha signal justifies concentration. "
            "Both positions preserved for trader decision."
        ),
        evidence_refs=["ev-risk-001", "ev-alpha-001"],
        run_modes=["real"],
    )

    result = run_result_synthesis(
        _valid_input(
            consult_memo_refs=["memo-risk-001", "memo-alpha-001"],
            evidence_refs=["ev-risk-001", "ev-alpha-001"],
        ),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"

    # Both positions must be preserved verbatim
    assert len(result.unresolved_decisions) == 1
    conflict = result.unresolved_decisions[0]
    assert len(conflict["positions"]) == 2

    personas = {p["persona"] for p in conflict["positions"]}
    assert "risk_reviewer" in personas
    assert "alpha_analyst" in personas

    # Discussion card must describe both perspectives
    assert "risk" in result.user_facing_discussion_card.lower() or "risk_reviewer" in result.user_facing_discussion_card
    assert "alpha" in result.user_facing_discussion_card.lower() or "alpha_analyst" in result.user_facing_discussion_card

    # Evidence grounded
    assert len(result.evidence_refs) > 0
    assert result.blocking_reasons == []
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Degraded mode — no synthesis adapter
# ---------------------------------------------------------------------------

def test_no_synthesis_adapter_returns_blocked():
    """With no synthesis_adapter: blocked, SYNTHESIS_ADAPTER_UNAVAILABLE, no verdict forged."""
    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=None,
        synthesis_id="rs-degraded-001",
    )

    assert result.status == "blocked"
    assert SYNTHESIS_ADAPTER_UNAVAILABLE in result.blocking_reasons
    assert result.verdict == "insufficient"
    assert result.evidence_refs == []
    assert result.proposed_version_patches == []
    assert len(result.warnings) >= 1
    assert "no verdict forged" in result.warnings[0].lower() or "adapter" in result.warnings[0].lower()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_empty_research_run_refs_blocked():
    """Empty research_run_refs: blocked with INPUT_SCHEMA_INVALID."""
    result = run_result_synthesis(
        _valid_input(research_run_refs=[]),
        synthesis_adapter=_mock_adapter(),
    )

    assert result.status == "blocked"
    assert INPUT_SCHEMA_INVALID in result.blocking_reasons
    assert result.verdict == "insufficient"


def test_empty_input_evidence_refs_blocked():
    """Empty input evidence_refs: blocked with INPUT_SCHEMA_INVALID."""
    result = run_result_synthesis(
        _valid_input(evidence_refs=[]),
        synthesis_adapter=_mock_adapter(),
    )

    assert result.status == "blocked"
    assert INPUT_SCHEMA_INVALID in result.blocking_reasons
    assert result.verdict == "insufficient"


def test_both_empty_fields_blocked():
    """Both research_run_refs and evidence_refs empty: blocked."""
    result = run_result_synthesis(
        _valid_input(research_run_refs=[], evidence_refs=[]),
        synthesis_adapter=_mock_adapter(),
    )

    assert result.status == "blocked"
    assert INPUT_SCHEMA_INVALID in result.blocking_reasons


# ---------------------------------------------------------------------------
# Evidence grounding enforcement
# ---------------------------------------------------------------------------

def test_non_insufficient_verdict_empty_output_evidence_refs_blocked():
    """Adapter returns promising verdict but empty output evidence_refs: blocked INSUFFICIENT_EVIDENCE."""
    adapter = _mock_adapter(verdict="promising", evidence_refs=[])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "blocked"
    assert INSUFFICIENT_EVIDENCE in result.blocking_reasons


def test_needs_revision_verdict_empty_output_evidence_refs_blocked():
    """needs_revision verdict with empty evidence_refs is also blocked."""
    adapter = _mock_adapter(verdict="needs_revision", evidence_refs=[])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "blocked"
    assert INSUFFICIENT_EVIDENCE in result.blocking_reasons


def test_reject_verdict_empty_output_evidence_refs_blocked():
    """reject verdict with empty evidence_refs is also blocked."""
    adapter = _mock_adapter(verdict="reject", evidence_refs=[])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "blocked"
    assert INSUFFICIENT_EVIDENCE in result.blocking_reasons


def test_insufficient_verdict_empty_evidence_refs_allowed():
    """insufficient verdict with empty evidence_refs is allowed (no evidence needed to say insufficient)."""
    adapter = _mock_adapter(verdict="insufficient", confidence=0.0, evidence_refs=[])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    assert result.verdict == "insufficient"
    assert result.blocking_reasons == []


# ---------------------------------------------------------------------------
# Stub / fixture run detection
# ---------------------------------------------------------------------------

def test_stub_only_run_emits_warning_and_downgrades_promising():
    """All runs in stub mode: STUB_RESULT_NOT_PRODUCTION_PROOF warning; promising → needs_revision."""
    adapter = _mock_adapter(
        verdict="promising",
        evidence_refs=["ev-stub-001"],
        run_modes=["stub"],
    )

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    # Verdict downgraded from promising to needs_revision
    assert result.verdict == "needs_revision"
    assert len(result.warnings) == 1
    assert STUB_RESULT_NOT_PRODUCTION_PROOF in result.warnings[0]


def test_fixture_only_run_emits_warning_and_downgrades_promising():
    """All runs in fixture mode: warning emitted; promising downgraded."""
    adapter = _mock_adapter(
        verdict="promising",
        evidence_refs=["ev-fixture-001"],
        run_modes=["fixture"],
    )

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.verdict == "needs_revision"
    assert any(STUB_RESULT_NOT_PRODUCTION_PROOF in w for w in result.warnings)


def test_stub_run_with_needs_revision_verdict_not_changed():
    """Stub run with already-degraded needs_revision verdict: warning emitted but verdict unchanged."""
    adapter = _mock_adapter(
        verdict="needs_revision",
        evidence_refs=["ev-001"],
        run_modes=["stub"],
    )

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    # Already needs_revision; not further downgraded
    assert result.verdict == "needs_revision"
    assert any(STUB_RESULT_NOT_PRODUCTION_PROOF in w for w in result.warnings)


def test_mixed_real_and_stub_run_warning_emitted_verdict_preserved():
    """Mixed real+stub: warning emitted but promising verdict preserved (real run present)."""
    adapter = _mock_adapter(
        verdict="promising",
        evidence_refs=["ev-001"],
        run_modes=["real", "stub"],
    )

    result = run_result_synthesis(
        _valid_input(research_run_refs=["run-real-001", "run-stub-001"]),
        synthesis_adapter=adapter,
    )

    # real run present so verdict is NOT downgraded
    assert result.verdict == "promising"
    # Warning still emitted for the stub run
    assert any(STUB_RESULT_NOT_PRODUCTION_PROOF in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Conflict preservation
# ---------------------------------------------------------------------------

def test_unresolved_decisions_passed_through_verbatim():
    """unresolved_decisions from adapter passed through unchanged; servant must not suppress."""
    conflicts = [
        {"conflict_id": "c1", "topic": "regime risk", "positions": [{"a": 1}, {"b": 2}]},
        {"conflict_id": "c2", "topic": "capacity", "positions": [{"x": "high"}, {"y": "low"}]},
    ]

    adapter = _mock_adapter(unresolved_decisions=conflicts, evidence_refs=["ev-001"])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.unresolved_decisions == conflicts


# ---------------------------------------------------------------------------
# Proposed patch forwarding
# ---------------------------------------------------------------------------

def test_proposed_patches_forwarded():
    """Proposed patches from adapter are forwarded unchanged in proposed_version_patches."""
    patches = [
        {
            "proposal_id": "vpp_001",
            "base_version_id": "wv-001",
            "rationale": "Adjust stop-loss threshold",
            "predicted_effects": ["reduce max_drawdown by 5%"],
            "revalidation_plan": "Re-run OOS on 2025 data",
            "evidence_refs": ["ev-001"],
        }
    ]

    adapter = _mock_adapter(proposed_patches=patches, evidence_refs=["ev-001"])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.proposed_version_patches == patches


# ---------------------------------------------------------------------------
# Unknown verdict enum handling
# ---------------------------------------------------------------------------

def test_unknown_verdict_downgraded_to_insufficient():
    """Adapter returns unrecognised verdict: downgraded to insufficient, warning added."""
    adapter = _mock_adapter(verdict="super_promising", evidence_refs=["ev-001"])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.verdict == "insufficient"
    assert len(result.warnings) >= 1
    assert any("unrecognised" in w.lower() or "downgraded" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Confidence clamping
# ---------------------------------------------------------------------------

def test_confidence_clamped_to_0_1():
    """Confidence values outside [0,1] are clamped."""
    adapter_high = _mock_adapter(verdict="promising", confidence=2.5, evidence_refs=["ev-001"])
    result_high = run_result_synthesis(_valid_input(), synthesis_adapter=adapter_high)
    assert result_high.confidence == pytest.approx(1.0)

    adapter_low = _mock_adapter(verdict="insufficient", confidence=-0.5, evidence_refs=[])
    result_low = run_result_synthesis(_valid_input(), synthesis_adapter=adapter_low)
    assert result_low.confidence == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Adapter call arguments verified
# ---------------------------------------------------------------------------

def test_adapter_receives_correct_arguments():
    """Adapter is called with strategy_spec_ref, base_version_id, and all input refs."""
    adapter = _mock_adapter(evidence_refs=["ev-001"])
    inp = _valid_input(
        research_run_refs=["run-a", "run-b"],
        consult_memo_refs=["memo-x"],
        evidence_refs=["ev-001", "ev-002"],
        user_decision_style_ref="style-conservative",
    )

    run_result_synthesis(inp, synthesis_adapter=adapter)

    call_kwargs = adapter.synthesize.call_args.kwargs
    assert call_kwargs["strategy_spec_ref"] == "spec-ref-momentum-v4"
    assert call_kwargs["base_version_id"] == "wv-001"
    assert call_kwargs["research_run_refs"] == ["run-a", "run-b"]
    assert call_kwargs["consult_memo_refs"] == ["memo-x"]
    assert call_kwargs["evidence_refs"] == ["ev-001", "ev-002"]
    assert call_kwargs["user_decision_style_ref"] == "style-conservative"
