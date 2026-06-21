"""agora-result-synthesis skill — Pantheon-side orchestrator.

Implements the agora-result-synthesis skill per C1 design-closure SPEC:
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/result-synthesis/SPEC.md

Evidence-grounding enforcement (task AG-BE-RS-004 hard rule):
  Every verdict conclusion must be backed by evidence_refs in the output.
  An output with verdict != "insufficient" but empty evidence_refs is
  blocked with INSUFFICIENT_EVIDENCE rather than returned to the caller.

Hard rules (C1 SPEC §Common Hard Rules + result-synthesis §Rules):
  - All conclusions grounded in evidence_refs; no ungrounded claims.
  - Must not represent stub/fixture results as production proof.
  - Must distinguish in-sample / OOS / paper / shadow results.
  - Proposed patches require base version, rationale, predicted effects, re-validation plan.
  - Contradictory evidence preserved as conflict in unresolved_decisions; not suppressed.
  - No direct live enable; output is proposal/candidate only.
  - Must not write RuntimeBinding, capital binding, broker order.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Failure / warning codes from C1 SPEC §5
# ---------------------------------------------------------------------------

INPUT_SCHEMA_INVALID = "INPUT_SCHEMA_INVALID"
SYNTHESIS_ADAPTER_UNAVAILABLE = "SYNTHESIS_ADAPTER_UNAVAILABLE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

STUB_RESULT_NOT_PRODUCTION_PROOF = "STUB_RESULT_NOT_PRODUCTION_PROOF"

# Backend modes that are not production-proof
_NON_PRODUCTION_MODES: frozenset[str] = frozenset({"stub", "fixture"})

# Verdicts that require non-empty evidence_refs in output
_EVIDENCE_REQUIRED_VERDICTS: frozenset[str] = frozenset({
    "promising", "needs_revision", "reject",
})


# ---------------------------------------------------------------------------
# Input / Output models per C1 SPEC
# ---------------------------------------------------------------------------


class ResultSynthesisInput(BaseModel):
    """Input to agora-result-synthesis (C1 SPEC §Input).

    Maps to ResultSynthesisInput TypeScript interface in the SPEC:
      strategySpecRef      → strategy_spec_ref
      baseVersionId        → base_version_id
      researchRunRefs      → research_run_refs
      consultMemoRefs      → consult_memo_refs
      evidenceRefs         → evidence_refs
      userDecisionStyleRef → user_decision_style_ref
    """

    strategy_spec_ref: str = Field(min_length=1, description="StrategySpec ref being synthesized (ref only, never raw content).")
    base_version_id: str = Field(min_length=1, description="Base workshop version ID the synthesis is grounded on.")
    research_run_refs: List[str] = Field(default_factory=list, description="ResearchRun ref IDs providing evidence (at least one required).")
    consult_memo_refs: List[str] = Field(default_factory=list, description="ConsultMemo ref IDs (may be empty).")
    evidence_refs: List[str] = Field(default_factory=list, description="Evidence ref IDs grounding the synthesis (at least one required).")
    user_decision_style_ref: Optional[str] = Field(default=None, description="Optional user decision-style ref for personalization.")


class ResultSynthesisOutput(BaseModel):
    """Output of agora-result-synthesis (C1 SPEC §Output).

    Maps to ResultSynthesisOutput TypeScript interface in the SPEC:
      verdict                  → verdict
      confidence               → confidence
      coreMetrics              → core_metrics
      strengths                → strengths
      weaknesses               → weaknesses
      regimeFindings           → regime_findings
      costCapacityFindings     → cost_capacity_findings
      proposedVersionPatches   → proposed_version_patches
      unresolvedDecisions      → unresolved_decisions
      userFacingDiscussionCard → user_facing_discussion_card
      evidenceRefs             → evidence_refs

    Common result envelope (C1 §3):
      status, blocking_reasons, warnings
    """

    verdict: Literal["promising", "needs_revision", "insufficient", "reject"] = "insufficient"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    core_metrics: Dict[str, float] = Field(default_factory=dict)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    regime_findings: List[str] = Field(default_factory=list)
    cost_capacity_findings: List[str] = Field(default_factory=list)
    proposed_version_patches: List[Any] = Field(default_factory=list)
    unresolved_decisions: List[Any] = Field(default_factory=list)
    user_facing_discussion_card: str = ""
    evidence_refs: List[str] = Field(default_factory=list)

    # Common result envelope fields
    status: str = "completed"
    blocking_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core skill function
# ---------------------------------------------------------------------------


def run_result_synthesis(
    input_data: ResultSynthesisInput,
    *,
    synthesis_adapter: Optional[Any] = None,
    synthesis_id: Optional[str] = None,
) -> ResultSynthesisOutput:
    """Run the agora-result-synthesis skill per C1 SPEC + AG-BE-RS-004 evidence-grounding rules.

    Synthesizes multiple ResearchRunSummary + ConsultMemo inputs into a
    trader-facing verdict, version-patch proposals, and a discussion card.
    Every non-insufficient verdict must be backed by non-empty evidence_refs.

    Degraded mode (golden eval — synthesis adapter unavailable):
      If synthesis_adapter is None, returns blocked with SYNTHESIS_ADAPTER_UNAVAILABLE.
      No verdict is forged; no evidence is fabricated.

    Stub/fixture run detection:
      If the adapter reports any research run with backend mode "stub" or "fixture",
      a STUB_RESULT_NOT_PRODUCTION_PROOF warning is emitted.  A "promising" verdict
      from stub-only runs is downgraded to "needs_revision" because stub/smoke results
      must not be presented as production proof (C1 result-synthesis §Rules).

    Evidence grounding enforcement:
      If the adapter returns a non-insufficient verdict but empty evidence_refs,
      the result is blocked with INSUFFICIENT_EVIDENCE rather than forwarded.

    Conflict preservation (golden eval 3):
      unresolved_decisions from the adapter are passed through verbatim; the skill
      must not suppress disagreements between personas.

    C1 hard rules enforced here:
      - Output is proposal/candidate; never executes governance, capital, or order routes.
      - No live enable path; output never sets lifecycle_state to active or triggers broker.
      - Contradictory evidence preserved; servant does not reconcile without evidence.
    """
    sid = synthesis_id or f"rs-{uuid.uuid4().hex[:12]}"

    # Step 1: Input validation — research_run_refs and evidence_refs must be non-empty
    input_errors: List[str] = []
    if not input_data.research_run_refs:
        input_errors.append("research_run_refs must not be empty")
    if not input_data.evidence_refs:
        input_errors.append("evidence_refs must not be empty")
    if input_errors:
        return ResultSynthesisOutput(
            status="blocked",
            blocking_reasons=[INPUT_SCHEMA_INVALID],
            warnings=input_errors,
        )

    # Step 2: Degraded mode — synthesis adapter unavailable (C1 golden eval degraded)
    if synthesis_adapter is None:
        return ResultSynthesisOutput(
            status="blocked",
            blocking_reasons=[SYNTHESIS_ADAPTER_UNAVAILABLE],
            warnings=[
                f"No synthesis adapter provided (synthesis_id={sid}). "
                "Returning degraded result — no verdict forged, no evidence fabricated. "
                "Caller must supply a synthesis_adapter to produce a real synthesis."
            ],
        )

    # Step 3: Call the synthesis adapter
    # Expected adapter interface:
    #   adapter.synthesize(
    #     strategy_spec_ref, base_version_id, research_run_refs,
    #     consult_memo_refs, evidence_refs, user_decision_style_ref
    #   ) -> {
    #     "verdict": str,
    #     "confidence": float,
    #     "core_metrics": dict,
    #     "strengths": list[str],
    #     "weaknesses": list[str],
    #     "regime_findings": list[str],
    #     "cost_capacity_findings": list[str],
    #     "proposed_patches": list,
    #     "unresolved_decisions": list,
    #     "discussion_card": str,
    #     "evidence_refs": list[str],
    #     "run_modes": list[str],   # backend mode per run: "real"|"fixture"|"stub"
    #   }
    raw = synthesis_adapter.synthesize(
        strategy_spec_ref=input_data.strategy_spec_ref,
        base_version_id=input_data.base_version_id,
        research_run_refs=input_data.research_run_refs,
        consult_memo_refs=input_data.consult_memo_refs,
        evidence_refs=input_data.evidence_refs,
        user_decision_style_ref=input_data.user_decision_style_ref,
    )

    verdict: str = raw.get("verdict", "insufficient")
    confidence: float = float(raw.get("confidence", 0.0))
    core_metrics: Dict[str, float] = raw.get("core_metrics", {})
    strengths: List[str] = raw.get("strengths", [])
    weaknesses: List[str] = raw.get("weaknesses", [])
    regime_findings: List[str] = raw.get("regime_findings", [])
    cost_capacity_findings: List[str] = raw.get("cost_capacity_findings", [])
    proposed_patches: List[Any] = raw.get("proposed_patches", [])
    unresolved_decisions: List[Any] = raw.get("unresolved_decisions", [])
    discussion_card: str = raw.get("discussion_card", "")
    output_evidence_refs: List[str] = raw.get("evidence_refs", [])
    run_modes: List[str] = raw.get("run_modes", [])

    warnings: List[str] = []

    # Step 4: Stub/fixture run detection — must not present as production proof
    non_prod_modes = [m for m in run_modes if m in _NON_PRODUCTION_MODES]
    if non_prod_modes:
        warnings.append(
            f"{STUB_RESULT_NOT_PRODUCTION_PROOF}: "
            f"research run(s) used backend mode(s) {sorted(set(non_prod_modes))} — "
            "stub and fixture results are NOT production proof. "
            "A 'promising' verdict from stub-only runs has been downgraded to 'needs_revision'."
        )
        # Downgrade: stub-only promising verdict is not production proof
        if verdict == "promising" and all(m in _NON_PRODUCTION_MODES for m in run_modes):
            verdict = "needs_revision"

    # Step 5: Evidence grounding check — non-insufficient verdict requires evidence_refs
    if verdict in _EVIDENCE_REQUIRED_VERDICTS and not output_evidence_refs:
        return ResultSynthesisOutput(
            status="blocked",
            blocking_reasons=[INSUFFICIENT_EVIDENCE],
            warnings=[
                f"Synthesis returned verdict='{verdict}' but evidence_refs is empty. "
                "Every non-insufficient verdict must be grounded in evidence_refs. "
                "Refusing to return an ungrounded conclusion."
            ],
        )

    # Step 6: Validate verdict is a recognised enum value
    valid_verdicts = {"promising", "needs_revision", "insufficient", "reject"}
    if verdict not in valid_verdicts:
        verdict = "insufficient"
        warnings.append(
            f"Adapter returned unrecognised verdict; downgraded to 'insufficient'. "
            "Only promising/needs_revision/insufficient/reject are valid."
        )

    return ResultSynthesisOutput(
        verdict=verdict,  # type: ignore[arg-type]
        confidence=min(max(confidence, 0.0), 1.0),
        core_metrics=core_metrics,
        strengths=strengths,
        weaknesses=weaknesses,
        regime_findings=regime_findings,
        cost_capacity_findings=cost_capacity_findings,
        proposed_version_patches=proposed_patches,
        unresolved_decisions=unresolved_decisions,
        user_facing_discussion_card=discussion_card,
        evidence_refs=output_evidence_refs,
        status="completed",
        blocking_reasons=[],
        warnings=warnings,
    )
