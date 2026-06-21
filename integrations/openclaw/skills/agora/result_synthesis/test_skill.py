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
  - Proposed patches validated against v1.3 VersionPatchProposal schema; invalid → blocked
  - Unknown verdict enum downgraded to insufficient
  - Invented output evidence_refs (not in input scope) filtered out; warning emitted
  - All output refs invented + non-insufficient verdict → blocked INSUFFICIENT_EVIDENCE
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from integrations.openclaw.skills.agora.result_synthesis.skill import (
    INPUT_SCHEMA_INVALID,
    INSUFFICIENT_EVIDENCE,
    INVENTED_EVIDENCE_REF,
    PATCH_SCHEMA_INVALID,
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


def _valid_patch(**overrides: Any) -> Dict[str, Any]:
    """Return a minimal valid v1.3 VersionPatchProposal for use in tests."""
    base: Dict[str, Any] = {
        "spec_version": "1.0",
        "proposal_id": "vpp_test001",
        "workshop_id": "ws-test001",
        "strategy_id": "strat-momentum-v4",
        "base_workshop_version_id": "wv-001",
        "base_strategy_spec_registry_id": "reg-strat-001",
        # 64-char hex string (required pattern)
        "base_document_sha256": "a" * 64,
        "proposed_by": {"actor_type": "agora_servant", "actor_ref": "servant-synthesis"},
        "source_event_ids": ["evt-001"],
        "patch_format": "rfc6902-restricted-v1",
        "operations": [
            {"op": "replace", "path": "/title", "value": "Updated strategy title"},
        ],
        "rationale": "Raise entry threshold to improve liquidity profile",
        "status": "draft",
        "created_at": "2026-06-21T00:00:00Z",
    }
    base.update(overrides)
    return base


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
        _valid_patch(
            proposal_id="vpp_eval1_001",
            base_workshop_version_id="wv-001",
            rationale="Raise entry threshold 0.03→0.05 to improve liquidity profile",
            predicted_effects=[
                {
                    "metric": "liquidity_score",
                    "direction": "increase",
                    "basis": "prior_backtest",
                    "confidence": 0.75,
                },
                {
                    "metric": "trade_count",
                    "direction": "decrease",
                    "basis": "heuristic",
                    "confidence": 0.80,
                },
            ],
            operations=[
                {
                    "op": "replace",
                    "path": "/execution_profile",
                    "value": {"entry_threshold": 0.05},
                },
            ],
            evidence_refs=[
                {"ref_type": "research_run", "ref_id": "ev-backtest-v4-001"},
                {"ref_type": "research_run", "ref_id": "ev-oos-v4-002"},
            ],
        )
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

    # Proposed patch has required v1.3 schema fields
    assert len(result.proposed_version_patches) == 1
    patch = result.proposed_version_patches[0]
    assert "base_workshop_version_id" in patch
    assert "rationale" in patch
    assert "predicted_effects" in patch
    assert "operations" in patch

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
    # evidence_refs matches input scope so scope filtering does not block
    adapter = _mock_adapter(
        verdict="promising",
        evidence_refs=["ev-001"],
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
    # evidence_refs matches input scope so scope filtering does not block
    adapter = _mock_adapter(
        verdict="promising",
        evidence_refs=["ev-001"],
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
# Invented evidence ref filtering (scope enforcement)
# ---------------------------------------------------------------------------

def test_invented_evidence_ref_filtered_warning_emitted():
    """Adapter returns a ref not in the input scope: it is filtered and an INVENTED_EVIDENCE_REF warning is emitted."""
    adapter = _mock_adapter(
        verdict="promising",
        # "ev-invented-001" is NOT in the input scope ["ev-001"]
        evidence_refs=["ev-001", "ev-invented-001"],
        run_modes=["real"],
    )

    result = run_result_synthesis(
        _valid_input(evidence_refs=["ev-001"]),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    assert result.verdict == "promising"
    # Invented ref filtered out; only "ev-001" remains
    assert result.evidence_refs == ["ev-001"]
    assert any(INVENTED_EVIDENCE_REF in w for w in result.warnings)


def test_invented_evidence_ref_all_invented_non_insufficient_blocked():
    """All output evidence_refs are invented (not in input scope) + non-insufficient verdict → BLOCKED."""
    adapter = _mock_adapter(
        verdict="promising",
        # Neither ref is in the input scope ["ev-001"]
        evidence_refs=["ev-invented-a", "ev-invented-b"],
        run_modes=["real"],
    )

    result = run_result_synthesis(
        _valid_input(evidence_refs=["ev-001"]),
        synthesis_adapter=adapter,
    )

    assert result.status == "blocked"
    assert INSUFFICIENT_EVIDENCE in result.blocking_reasons


def test_invented_evidence_ref_all_invented_insufficient_allowed():
    """All output evidence_refs are invented but verdict is insufficient → allowed (no grounding needed)."""
    adapter = _mock_adapter(
        verdict="insufficient",
        evidence_refs=["ev-invented-x"],
        run_modes=["real"],
    )

    result = run_result_synthesis(
        _valid_input(evidence_refs=["ev-001"]),
        synthesis_adapter=adapter,
    )

    # insufficient does not require evidence; filtered refs are empty but that is fine
    assert result.status == "completed"
    assert result.verdict == "insufficient"
    assert result.evidence_refs == []
    assert any(INVENTED_EVIDENCE_REF in w for w in result.warnings)


# ---------------------------------------------------------------------------
# VersionPatchProposal schema validation
# ---------------------------------------------------------------------------

def test_invalid_patch_schema_blocked():
    """Adapter returns a patch that does not conform to v1.3 VersionPatchProposal schema → BLOCKED."""
    bad_patch = {
        # Missing many required fields (workshop_id, strategy_id, source_event_ids, etc.)
        "proposal_id": "vpp_bad",
        "rationale": "Some rationale",
    }
    adapter = _mock_adapter(
        verdict="promising",
        proposed_patches=[bad_patch],
        evidence_refs=["ev-001"],
    )

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "blocked"
    assert PATCH_SCHEMA_INVALID in result.blocking_reasons
    # Warnings should describe the schema violations
    assert len(result.warnings) >= 1
    assert any("patch[0]" in w for w in result.warnings)


def test_invalid_patch_extra_field_blocked():
    """Patch with additionalProperties (not in schema) is rejected."""
    bad_patch = {
        **_valid_patch(),
        "revalidation_plan": "Re-run OOS next quarter",  # not in v1.3 schema
    }
    adapter = _mock_adapter(
        verdict="promising",
        proposed_patches=[bad_patch],
        evidence_refs=["ev-001"],
    )

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "blocked"
    assert PATCH_SCHEMA_INVALID in result.blocking_reasons


def test_valid_patch_passes_schema_validation():
    """A fully-valid v1.3 VersionPatchProposal passes schema validation and is forwarded."""
    patch = _valid_patch()
    adapter = _mock_adapter(
        verdict="promising",
        proposed_patches=[patch],
        evidence_refs=["ev-001"],
    )

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    assert result.proposed_version_patches == [patch]
    assert PATCH_SCHEMA_INVALID not in result.blocking_reasons


def test_no_patches_skips_schema_validation():
    """Empty proposed_patches list: schema validation is skipped; result is completed."""
    adapter = _mock_adapter(verdict="promising", proposed_patches=[], evidence_refs=["ev-001"])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    assert result.proposed_version_patches == []


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
# Proposed patch forwarding (valid schema)
# ---------------------------------------------------------------------------

def test_proposed_patches_forwarded():
    """Proposed patches conforming to v1.3 schema are forwarded unchanged."""
    patch = _valid_patch(
        proposal_id="vpp_fwd001",
        rationale="Adjust stop-loss threshold",
        predicted_effects=[
            {"metric": "max_drawdown", "direction": "decrease", "basis": "prior_backtest", "confidence": 0.7},
        ],
        operations=[
            {"op": "replace", "path": "/execution_profile", "value": {"stop_loss": 0.05}},
        ],
    )

    adapter = _mock_adapter(proposed_patches=[patch], evidence_refs=["ev-001"])

    result = run_result_synthesis(
        _valid_input(),
        synthesis_adapter=adapter,
    )

    assert result.status == "completed"
    assert result.proposed_version_patches == [patch]


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
