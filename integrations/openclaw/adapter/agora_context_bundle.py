"""Agora ContextBundle builder — privacy boundary for central persona consult.

Central persona consult calls (agora-expert-consult, committee, red-team) must
only receive a minimal, redacted bundle. This module enforces that boundary.

Allowed fields per C1 design-closure / agora-expert-consult SPEC.md:
  strategy_spec_draft_ref — reference to the strategy spec (never raw content)
  question               — the structured consult question
  symbols                — relevant trading symbols
  evidence_refs          — reference IDs only (never raw evidence content)
  data_cutoff            — ISO-8601 data cutoff
  required_output_schema — output schema reference

Forbidden content:
  raw private user messages, user PII, private journal entries,
  conversation history, capital binding details.

Violation raises ContextBundlePrivacyError with code RAW_PRIVATE_CONTENT_FORBIDDEN.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


RAW_PRIVATE_CONTENT_FORBIDDEN = "RAW_PRIVATE_CONTENT_FORBIDDEN"

_FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset({
    "raw_prompt",
    "user_identity",
    "user_id",
    "user_email",
    "user_name",
    "private_journal",
    "journal_entries",
    "session_history",
    "conversation_history",
    "capital_binding",
    "pii",
})


class ContextBundlePrivacyError(ValueError):
    """Raised when a context bundle build is rejected due to privacy violations.

    Always carries code=RAW_PRIVATE_CONTENT_FORBIDDEN. The violated_fields
    list names every forbidden field that was found in the request.
    """

    def __init__(
        self,
        message: str,
        *,
        violated_fields: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.code = RAW_PRIVATE_CONTENT_FORBIDDEN
        self.violated_fields: List[str] = violated_fields or []

    def to_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "violated_fields": self.violated_fields,
        }


class ContextBundlePrivacyManifest(BaseModel):
    """Privacy manifest attached to every AgoraContextBundle.

    Confirms the bundle was constructed without raw private content.
    explicit_user_authorization records whether the operator granted consent
    for an expanded field set (consent does NOT allow raw_prompt or user PII).
    """

    raw_prompt_included: Literal[False] = False
    user_identity_included: Literal[False] = False
    fields_shared: List[str] = Field(default_factory=list)
    explicit_user_authorization: bool = False


class AgoraContextBundle(BaseModel):
    """Minimal, privacy-safe ContextBundle for central persona consult calls.

    Central persona (agora-expert-consult / committee / red-team) may only
    receive these fields. No raw private content may be embedded.
    """

    strategy_spec_draft_ref: str = Field(min_length=1)
    question: str = Field(min_length=1)
    symbols: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    data_cutoff: str = Field(min_length=1)
    required_output_schema: str = Field(default="")
    privacy_manifest: ContextBundlePrivacyManifest = Field(
        default_factory=ContextBundlePrivacyManifest
    )


def build_context_bundle(
    *,
    strategy_spec_draft_ref: str,
    question: str,
    symbols: Optional[List[str]] = None,
    evidence_refs: Optional[List[str]] = None,
    data_cutoff: str,
    required_output_schema: str = "",
    explicit_user_authorization: bool = False,
    extra_fields: Optional[Dict[str, object]] = None,
) -> AgoraContextBundle:
    """Build a privacy-safe AgoraContextBundle for central persona consult.

    If extra_fields contains any name from _FORBIDDEN_FIELD_NAMES, raises
    ContextBundlePrivacyError(code=RAW_PRIVATE_CONTENT_FORBIDDEN) before
    constructing the bundle.
    """
    violations: List[str] = []
    if extra_fields:
        for field_name in extra_fields:
            if field_name.lower() in _FORBIDDEN_FIELD_NAMES:
                violations.append(field_name)
    if violations:
        raise ContextBundlePrivacyError(
            f"Context bundle rejected: forbidden private fields detected "
            f"({', '.join(sorted(violations))}). "
            "Central persona must never receive raw private content.",
            violated_fields=violations,
        )

    shared: List[str] = ["strategy_spec_draft_ref", "question", "data_cutoff"]
    if symbols:
        shared.append("symbols")
    if evidence_refs:
        shared.append("evidence_refs")
    if required_output_schema:
        shared.append("required_output_schema")

    manifest = ContextBundlePrivacyManifest(
        raw_prompt_included=False,
        user_identity_included=False,
        fields_shared=shared,
        explicit_user_authorization=explicit_user_authorization,
    )

    return AgoraContextBundle(
        strategy_spec_draft_ref=strategy_spec_draft_ref,
        question=question,
        symbols=symbols or [],
        evidence_refs=evidence_refs or [],
        data_cutoff=data_cutoff,
        required_output_schema=required_output_schema,
        privacy_manifest=manifest,
    )


__all__ = [
    "RAW_PRIVATE_CONTENT_FORBIDDEN",
    "AgoraContextBundle",
    "ContextBundlePrivacyError",
    "ContextBundlePrivacyManifest",
    "build_context_bundle",
]
