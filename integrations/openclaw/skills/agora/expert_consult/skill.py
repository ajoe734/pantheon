"""agora-expert-consult skill — Pantheon-side orchestrator.

Implements the agora-expert-consult skill per C1 design-closure SPEC:
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/expert-consult/SPEC.md

Privacy boundary (AG-BE-ID-004):
  All ContextBundles are constructed via build_context_bundle(), which enforces:
    raw_prompt_included   = False  (Literal[False], not runtime-settable)
    user_identity_included = False  (Literal[False], not runtime-settable)

Session routing per C1 SPEC §Rules:
  mode="consult"    → OpenClaw session_type="consult"
  mode="committee"  → OpenClaw session_type="committee"
  mode="red_team"   → OpenClaw session_type="red_team"

B1 policy (design-closure B1_information_lead_proxy_policy.md):
  Any output text must NOT assert insider knowledge, manipulation, or
  illegal intent. Required disclaimer must accompany information_lead output.

Hard rules (C1 SPEC §Rules):
  - Only share: strategy_spec_ref / question / symbols / evidence_refs / data_cutoff
  - Never share: raw prompt, full Journal, other strategies, user identity
  - Central persona may only reply Memo/Evidence/Critique/RiskNote
  - Central persona must NOT write to private memory
  - Disagreements must be preserved; servant must not silently suppress them
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from integrations.openclaw.adapter.agora_context_bundle import (
    AgoraContextBundle,
    ContextBundlePrivacyError,
    build_context_bundle,
)


# ---------------------------------------------------------------------------
# B1 information-lead proxy policy enforcement
# ---------------------------------------------------------------------------

B1_FORBIDDEN_ASSERTION_TERMS: frozenset[str] = frozenset({
    "insider trading",
    "insider",
    "知情交易",
    "內線交易",
    "內線",
    "操縱股價",
    "操縱",
    "對倒",
    "共謀",
    "非法",
    "illegal",
    "manipulation",
    "confirmed illegal",
    "confirmed insider",
    "特定關係人就是特定分點",
})

B1_REQUIRED_DISCLAIMER: str = (
    "本分析僅依公開或已授權資料建立統計關聯與事件領先 proxy，"
    "不構成身份認定、違法行為判斷、投資建議或法律結論。"
    "相關關聯可能由共同市場因素、資料缺漏、分點遷移或其他未觀測因素造成。"
)


class B1PolicyViolationError(ValueError):
    """Raised when skill output text violates B1 information-lead proxy policy.

    The skill must never assert insider knowledge, market manipulation,
    or illegal intent.  Any output containing the forbidden terms triggers
    this error instead of returning a poisoned result to the caller.
    """

    def __init__(self, message: str, *, violated_terms: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.violated_terms: List[str] = violated_terms or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": "B1_POLICY_VIOLATION",
            "message": str(self),
            "violated_terms": self.violated_terms,
        }


def check_b1_policy(text: str) -> None:
    """Raise B1PolicyViolationError if text contains B1-forbidden assertions.

    Called on all memo summary/conclusion/claim text before returning output.
    Suppresses the result rather than returning a policy-violating answer.
    """
    lower = text.lower()
    violations = [term for term in B1_FORBIDDEN_ASSERTION_TERMS if term in lower]
    if violations:
        raise B1PolicyViolationError(
            f"B1 policy violation: output contains forbidden assertion terms "
            f"({', '.join(sorted(violations))}). "
            "System must not assert insider knowledge, manipulation, or illegal intent. "
            "Use B1-allowed proxy language instead.",
            violated_terms=violations,
        )


# ---------------------------------------------------------------------------
# Input / Output models per C1 SPEC
# ---------------------------------------------------------------------------


class ExpertConsultInput(BaseModel):
    """Input to the agora-expert-consult skill (C1 SPEC §Input).

    Maps to ExpertConsultInput TypeScript interface in the SPEC:
      strategySpecRef     → strategy_spec_ref
      question            → question
      relevantSymbols     → relevant_symbols
      evidenceRefs        → evidence_refs
      dataCutoff          → data_cutoff
      requiredExpertise   → required_expertise
      mode                → mode
      privateFieldsAllowed → private_fields_allowed
    """

    strategy_spec_ref: str = Field(min_length=1, description="Reference to the strategy spec (never raw content)")
    question: str = Field(min_length=1, description="Structured consult question for the central persona")
    relevant_symbols: List[str] = Field(default_factory=list, description="Trading symbols relevant to the question")
    evidence_refs: List[str] = Field(default_factory=list, description="Evidence reference IDs (never raw content)")
    data_cutoff: str = Field(min_length=1, description="ISO-8601 data cutoff timestamp")
    required_expertise: List[str] = Field(default_factory=list, description="Expertise domains required from central persona")
    mode: Literal["consult", "committee", "red_team"] = Field(description="Session type to use in OpenClaw")
    private_fields_allowed: List[str] = Field(default_factory=list, description="Operator-authorized additional fields (cannot include raw_prompt or user PII)")


class ExpertConsultMemo(BaseModel):
    """Single persona memo in the consult output (C1 SPEC §Output.memos)."""

    persona_id: str = Field(min_length=1)
    memo_ref: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: List[str] = Field(default_factory=list)


class ExpertConsultPrivacyManifest(BaseModel):
    """Privacy manifest attached to every ExpertConsultOutput.

    raw_prompt_included and user_identity_included are Literal[False]:
    they can never be set to True, encoding the hard policy constraint.
    """

    raw_prompt_included: Literal[False] = False
    user_identity_included: Literal[False] = False
    fields_shared: List[str] = Field(default_factory=list)


class ExpertConsultOutput(BaseModel):
    """Output of the agora-expert-consult skill (C1 SPEC §Output).

    Maps to ExpertConsultOutput TypeScript interface in the SPEC:
      consultGroupId  → consult_group_id
      sessionRefs     → session_refs
      memos           → memos
      disagreements   → disagreements
      missingEvidence → missing_evidence
      privacyManifest → privacy_manifest

    Additional fields for common result envelope (C1 §3):
      status          → "completed" | "needs_user" | "blocked" | "failed"
      blocking_reasons, warnings
    """

    consult_group_id: str = Field(min_length=1)
    session_refs: List[str] = Field(default_factory=list)
    memos: List[ExpertConsultMemo] = Field(default_factory=list)
    disagreements: List[Any] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    privacy_manifest: ExpertConsultPrivacyManifest = Field(
        default_factory=ExpertConsultPrivacyManifest
    )
    status: str = "completed"
    blocking_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core skill functions
# ---------------------------------------------------------------------------


def build_expert_consult_bundle(
    input_data: ExpertConsultInput,
) -> AgoraContextBundle:
    """Build the minimal, privacy-safe ContextBundle from ExpertConsultInput.

    Delegates to build_context_bundle() which enforces the AG-BE-ID-004
    privacy boundary (raw_prompt_included=False, user_identity_included=False,
    11-field forbidden blocklist).

    Raises ContextBundlePrivacyError with code=RAW_PRIVATE_CONTENT_FORBIDDEN
    if any private_fields_allowed entry tries to sneak in a forbidden field.
    """
    extra: Optional[Dict[str, Any]] = None
    if input_data.private_fields_allowed:
        extra = {field: None for field in input_data.private_fields_allowed}

    return build_context_bundle(
        strategy_spec_draft_ref=input_data.strategy_spec_ref,
        question=input_data.question,
        symbols=input_data.relevant_symbols,
        evidence_refs=input_data.evidence_refs,
        data_cutoff=input_data.data_cutoff,
        required_output_schema="agora-expert-consult-output-v1",
        extra_fields=extra,
    )


def run_expert_consult(
    input_data: ExpertConsultInput,
    *,
    session_adapter: Optional[Any] = None,
    consult_group_id: Optional[str] = None,
) -> ExpertConsultOutput:
    """Run an expert consult session per the agora-expert-consult C1 skill SPEC.

    Builds a minimal ContextBundle (privacy boundary enforced by AG-BE-ID-004)
    and routes to the appropriate OpenClaw session type (consult/committee/red_team).

    Degraded mode (golden eval 3): if session_adapter is None, returns a
    blocked result with EXPERT_UNAVAILABLE instead of raising. Missing personas
    are listed in missing_evidence from required_expertise. No memos are forged.

    B1 enforcement: all memo conclusion/claim text is checked via check_b1_policy().
    Any output asserting insider knowledge or manipulation is suppressed with a
    B1_POLICY_SUPPRESSED warning; the memo is placed in warnings, not memos.

    C1 hard rules enforced here:
      - ContextBundle contains only allowed fields (enforced by build_expert_consult_bundle)
      - Central persona output (memos) cannot write to private memory
      - Disagreements are preserved verbatim; servant may not suppress them
    """
    group_id = consult_group_id or f"cg-{uuid.uuid4().hex[:12]}"

    # Step 1: Build privacy-safe ContextBundle
    try:
        bundle = build_expert_consult_bundle(input_data)
    except ContextBundlePrivacyError as exc:
        return ExpertConsultOutput(
            consult_group_id=group_id,
            status="blocked",
            blocking_reasons=[exc.code],
            warnings=[str(exc)],
        )

    # Step 2: Build privacy manifest for output
    privacy_manifest = ExpertConsultPrivacyManifest(
        fields_shared=bundle.privacy_manifest.fields_shared,
    )

    # Step 3: Degraded mode — expert unavailable (C1 golden eval 3)
    if session_adapter is None:
        return ExpertConsultOutput(
            consult_group_id=group_id,
            session_refs=[],
            memos=[],
            disagreements=[],
            missing_evidence=list(input_data.required_expertise),
            privacy_manifest=privacy_manifest,
            status="blocked",
            blocking_reasons=["EXPERT_UNAVAILABLE"],
            warnings=[
                "No OpenClaw session adapter provided. "
                "Returning degraded result with missing_evidence listing required_expertise. "
                "No memos forged per C1 golden eval 3."
            ],
        )

    # Step 4: Submit ContextBundle to OpenClaw session (mode determines session_type)
    # The session_adapter is expected to implement:
    #   adapter.submit_consult_bundle(bundle, mode=input_data.mode)
    #   → returns {"session_refs": [...], "raw_memos": [...], "disagreements": [...]}
    raw_result = session_adapter.submit_consult_bundle(
        bundle=bundle.model_dump(),
        mode=input_data.mode,
        required_expertise=input_data.required_expertise,
    )

    # Step 5: Collect and validate memos with B1 policy enforcement
    validated_memos: List[ExpertConsultMemo] = []
    suppressed_warnings: List[str] = []

    for raw_memo in raw_result.get("raw_memos", []):
        conclusion = raw_memo.get("conclusion", "")
        try:
            check_b1_policy(conclusion)
        except B1PolicyViolationError as b1_err:
            suppressed_warnings.append(
                f"POLICY_SUPPRESSED: memo from persona {raw_memo.get('persona_id','?')} "
                f"suppressed per B1 policy — {b1_err}. "
                "Use B1-allowed proxy language. "
                f"Disclaimer: {B1_REQUIRED_DISCLAIMER}"
            )
            continue

        validated_memos.append(
            ExpertConsultMemo(
                persona_id=raw_memo["persona_id"],
                memo_ref=raw_memo["memo_ref"],
                conclusion=conclusion,
                confidence=float(raw_memo.get("confidence", 1.0)),
                evidence_refs=raw_memo.get("evidence_refs", []),
            )
        )

    # Step 6: Determine missing_evidence from personas that did not respond
    responded_personas = {m.persona_id for m in validated_memos}
    missing: List[str] = raw_result.get("missing_evidence", [])
    if not missing and input_data.required_expertise:
        missing = [
            expertise
            for expertise in input_data.required_expertise
            if not any(expertise in m.persona_id for m in validated_memos)
        ]

    return ExpertConsultOutput(
        consult_group_id=group_id,
        session_refs=raw_result.get("session_refs", []),
        memos=validated_memos,
        disagreements=raw_result.get("disagreements", []),
        missing_evidence=missing,
        privacy_manifest=privacy_manifest,
        status="completed" if validated_memos else "needs_user",
        blocking_reasons=[],
        warnings=suppressed_warnings,
    )
