"""Tests for agora-expert-consult skill — golden evals per C1 SPEC §6.

Golden evals:
  1. Winner branch: five-path consult (chips/stats/compliance/risk/red-team)
  2. Privacy: ContextBundle contains no raw_prompt/user_identity
  3. Expert unavailable: return degraded, no memo forged

Additional tests:
  - B1 policy enforcement: forbidden assertion terms suppressed
  - B1 disclaimer present in suppressed warning
  - Input validation: invalid mode rejected
  - Disagreements preserved in output
  - RAW_PRIVATE_CONTENT_FORBIDDEN on forbidden extra_fields
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from integrations.openclaw.skills.agora.expert_consult.skill import (
    B1PolicyViolationError,
    B1_REQUIRED_DISCLAIMER,
    ExpertConsultInput,
    ExpertConsultOutput,
    build_expert_consult_bundle,
    check_b1_policy,
    run_expert_consult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_input(
    mode: str = "consult",
    required_expertise: Optional[List[str]] = None,
    private_fields_allowed: Optional[List[str]] = None,
) -> ExpertConsultInput:
    return ExpertConsultInput(
        strategy_spec_ref="spec-ref-momentum-001",
        question="Is this momentum strategy overfit to the 2022 macro regime?",
        relevant_symbols=["TSLA", "NVDA", "AMD"],
        evidence_refs=["ev-backtest-001", "ev-oos-002"],
        data_cutoff="2026-06-01T00:00:00Z",
        required_expertise=required_expertise or ["risk", "stats"],
        mode=mode,
        private_fields_allowed=private_fields_allowed or [],
    )


def _mock_adapter(
    *,
    memos: Optional[List[Dict[str, Any]]] = None,
    session_refs: Optional[List[str]] = None,
    disagreements: Optional[List[Any]] = None,
    missing_evidence: Optional[List[str]] = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.submit_consult_bundle.return_value = {
        "session_refs": session_refs or ["sess-001"],
        "raw_memos": memos or [],
        "disagreements": disagreements or [],
        "missing_evidence": missing_evidence or [],
    }
    return adapter


# ---------------------------------------------------------------------------
# Golden eval 1 — Winner branch: five-path consult
# ---------------------------------------------------------------------------

def test_golden_eval_1_winner_branch_five_path_consult():
    """Five expertise paths each produce a memo; privacy manifest clean; disagreements preserved."""
    expertise = ["chips_analyst", "stats_reviewer", "compliance", "risk_reviewer", "red_team"]
    raw_memos = [
        {
            "persona_id": expert,
            "memo_ref": f"memo-{expert}-001",
            "conclusion": f"公開資料顯示統計上的事件領先關聯 — {expert} review.",
            "confidence": 0.82,
            "evidence_refs": ["ev-001"],
        }
        for expert in expertise
    ]
    disagreements_fixture = [
        {"from": "red_team", "claim": "Strategy may underperform in high-VIX regimes"},
    ]

    adapter = _mock_adapter(
        memos=raw_memos,
        session_refs=["sess-wbranch-001"],
        disagreements=disagreements_fixture,
    )

    result = run_expert_consult(
        _valid_input(mode="committee", required_expertise=expertise),
        session_adapter=adapter,
        consult_group_id="cg-golden-1",
    )

    assert result.status == "completed"
    assert result.consult_group_id == "cg-golden-1"
    assert len(result.memos) == 5
    assert result.session_refs == ["sess-wbranch-001"]

    # Privacy manifest must always be clean
    assert result.privacy_manifest.raw_prompt_included is False
    assert result.privacy_manifest.user_identity_included is False

    # Disagreements must be preserved, not suppressed
    assert result.disagreements == disagreements_fixture

    # No B1 violations in clean memos
    assert result.warnings == []
    assert result.blocking_reasons == []


# ---------------------------------------------------------------------------
# Golden eval 2 — Privacy: ContextBundle contains no raw_prompt/user_identity
# ---------------------------------------------------------------------------

def test_golden_eval_2_privacy_no_raw_prompt():
    """ContextBundle built from valid input never includes raw_prompt or user_identity."""
    bundle = build_expert_consult_bundle(_valid_input())

    assert bundle.privacy_manifest.raw_prompt_included is False
    assert bundle.privacy_manifest.user_identity_included is False
    assert "strategy_spec_draft_ref" in bundle.privacy_manifest.fields_shared
    assert "question" in bundle.privacy_manifest.fields_shared
    assert "data_cutoff" in bundle.privacy_manifest.fields_shared


def test_golden_eval_2_forbidden_field_raw_prompt_blocked():
    """Passing raw_prompt via private_fields_allowed triggers RAW_PRIVATE_CONTENT_FORBIDDEN."""
    result = run_expert_consult(
        _valid_input(private_fields_allowed=["raw_prompt"]),
        session_adapter=_mock_adapter(),
        consult_group_id="cg-privacy-test",
    )

    assert result.status == "blocked"
    assert "RAW_PRIVATE_CONTENT_FORBIDDEN" in result.blocking_reasons
    assert result.memos == []


def test_golden_eval_2_forbidden_field_user_identity_blocked():
    """Passing user_identity via private_fields_allowed triggers RAW_PRIVATE_CONTENT_FORBIDDEN."""
    result = run_expert_consult(
        _valid_input(private_fields_allowed=["user_identity"]),
        session_adapter=_mock_adapter(),
        consult_group_id="cg-privacy-user",
    )

    assert result.status == "blocked"
    assert "RAW_PRIVATE_CONTENT_FORBIDDEN" in result.blocking_reasons
    assert result.memos == []


def test_golden_eval_2_privacy_manifest_always_false_on_output():
    """ExpertConsultOutput.privacy_manifest always has raw_prompt_included=False."""
    adapter = _mock_adapter(memos=[{
        "persona_id": "stats-reviewer",
        "memo_ref": "memo-001",
        "conclusion": "公開資料顯示統計上的事件領先關聯.",
        "confidence": 0.9,
        "evidence_refs": [],
    }])

    result = run_expert_consult(
        _valid_input(),
        session_adapter=adapter,
    )

    assert result.privacy_manifest.raw_prompt_included is False
    assert result.privacy_manifest.user_identity_included is False


# ---------------------------------------------------------------------------
# Golden eval 3 — Expert unavailable: degraded, no memo forged
# ---------------------------------------------------------------------------

def test_golden_eval_3_expert_unavailable_no_memo_forged():
    """With no session_adapter: blocked, EXPERT_UNAVAILABLE, no memos, missing_evidence filled."""
    expertise = ["risk", "compliance", "stats"]

    result = run_expert_consult(
        _valid_input(required_expertise=expertise),
        session_adapter=None,
        consult_group_id="cg-degraded-001",
    )

    assert result.status == "blocked"
    assert "EXPERT_UNAVAILABLE" in result.blocking_reasons
    assert result.memos == []
    assert result.disagreements == []
    assert set(result.missing_evidence) == set(expertise)
    assert result.privacy_manifest.raw_prompt_included is False
    assert result.privacy_manifest.user_identity_included is False
    # Warnings explain why degraded, not an error raised
    assert len(result.warnings) >= 1
    assert "EXPERT_UNAVAILABLE" in result.warnings[0] or "degraded" in result.warnings[0].lower()


def test_golden_eval_3_mode_committee_degraded():
    """committee mode also degrades cleanly without forging memos."""
    result = run_expert_consult(
        _valid_input(mode="committee"),
        session_adapter=None,
    )
    assert result.status == "blocked"
    assert result.memos == []


def test_golden_eval_3_mode_red_team_degraded():
    """red_team mode also degrades cleanly without forging memos."""
    result = run_expert_consult(
        _valid_input(mode="red_team"),
        session_adapter=None,
    )
    assert result.status == "blocked"
    assert result.memos == []


# ---------------------------------------------------------------------------
# B1 policy enforcement
# ---------------------------------------------------------------------------

def test_b1_policy_check_clean_text_passes():
    """B1-compliant text (statistical proxy language) passes without error."""
    check_b1_policy("公開資料顯示統計上的事件領先關聯")
    check_b1_policy("可能存在資金遷移或關聯分點反向流")
    check_b1_policy("樣本呈現異常活動，但不能據此判定違法或主觀知情")


def test_b1_policy_check_forbidden_term_raises():
    """Text asserting insider trading triggers B1PolicyViolationError."""
    with pytest.raises(B1PolicyViolationError) as exc_info:
        check_b1_policy("This branch is confirmed insider trading activity.")
    assert "insider" in exc_info.value.violated_terms


def test_b1_policy_check_manipulation_raises():
    """Text asserting market manipulation triggers B1PolicyViolationError."""
    with pytest.raises(B1PolicyViolationError) as exc_info:
        check_b1_policy("Evidence points to stock manipulation by this broker.")
    assert "manipulation" in exc_info.value.violated_terms


def test_b1_policy_suppressed_memo_goes_to_warnings():
    """Memo with B1-violating conclusion is suppressed; appears in warnings, not memos."""
    adapter = _mock_adapter(memos=[
        {
            "persona_id": "risk-analyst",
            "memo_ref": "memo-b1-001",
            "conclusion": "This branch is confirmed insider trading activity.",
            "confidence": 0.95,
            "evidence_refs": ["ev-001"],
        },
        {
            "persona_id": "stats-reviewer",
            "memo_ref": "memo-clean-001",
            "conclusion": "公開資料顯示統計上的事件領先關聯.",
            "confidence": 0.80,
            "evidence_refs": [],
        },
    ])

    result = run_expert_consult(
        _valid_input(),
        session_adapter=adapter,
    )

    # The B1-violating memo is suppressed
    assert len(result.memos) == 1
    assert result.memos[0].persona_id == "stats-reviewer"

    # Warning contains suppression message and B1 disclaimer
    assert len(result.warnings) == 1
    assert "POLICY_SUPPRESSED" in result.warnings[0]
    assert B1_REQUIRED_DISCLAIMER in result.warnings[0]


def test_b1_policy_all_memos_suppressed_gives_needs_user_status():
    """If all memos are suppressed by B1, status is needs_user not completed."""
    adapter = _mock_adapter(memos=[
        {
            "persona_id": "bad-actor-persona",
            "memo_ref": "memo-bad-001",
            "conclusion": "confirmed insider trading manipulation.",
            "confidence": 1.0,
            "evidence_refs": [],
        },
    ])

    result = run_expert_consult(
        _valid_input(),
        session_adapter=adapter,
    )

    assert result.memos == []
    assert result.status == "needs_user"
    assert len(result.warnings) == 1


# ---------------------------------------------------------------------------
# Disagreement preservation
# ---------------------------------------------------------------------------

def test_disagreements_preserved_verbatim():
    """Disagreements from adapter are passed through unchanged; servant must not suppress."""
    disagreements = [
        {"from": "red_team", "claim": "Strategy tail risk underestimated"},
        {"from": "compliance", "claim": "Concentration limit may be breached"},
    ]

    adapter = _mock_adapter(
        memos=[{
            "persona_id": "stats",
            "memo_ref": "memo-001",
            "conclusion": "公開資料顯示統計上的事件領先關聯.",
            "confidence": 0.75,
            "evidence_refs": [],
        }],
        disagreements=disagreements,
    )

    result = run_expert_consult(_valid_input(), session_adapter=adapter)

    assert result.disagreements == disagreements


# ---------------------------------------------------------------------------
# Session mode routing
# ---------------------------------------------------------------------------

def test_mode_consult_passed_to_adapter():
    """mode='consult' is passed as-is to the session adapter."""
    adapter = _mock_adapter()
    run_expert_consult(_valid_input(mode="consult"), session_adapter=adapter)

    call_kwargs = adapter.submit_consult_bundle.call_args.kwargs
    assert call_kwargs["mode"] == "consult"


def test_mode_committee_passed_to_adapter():
    """mode='committee' is passed as-is to the session adapter."""
    adapter = _mock_adapter()
    run_expert_consult(_valid_input(mode="committee"), session_adapter=adapter)

    call_kwargs = adapter.submit_consult_bundle.call_args.kwargs
    assert call_kwargs["mode"] == "committee"


def test_mode_red_team_passed_to_adapter():
    """mode='red_team' is passed as-is to the session adapter."""
    adapter = _mock_adapter()
    run_expert_consult(_valid_input(mode="red_team"), session_adapter=adapter)

    call_kwargs = adapter.submit_consult_bundle.call_args.kwargs
    assert call_kwargs["mode"] == "red_team"
