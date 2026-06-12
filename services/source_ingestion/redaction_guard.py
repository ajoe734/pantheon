"""IDS-002 — Redaction / visibility / scope guard.

Applied at the InteractionSourceRecord boundary before any SeedCandidate can
be created. Strips PII/credentials/capital amounts/broker refs/private notes
from summary and metadata, sets redaction_status, and enforces visibility
scope so private/persona content never leaks into shared consumers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionVisibility,
)


# ---------------------------------------------------------------------------
# Sensitive-content detection patterns (v1 deterministic — no embeddings)
# ---------------------------------------------------------------------------

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    ("phone_us", re.compile(r"\b(\+1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}\b")),
)

_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key_sk", re.compile(r"\bsk[-_][a-zA-Z0-9\-_.]{16,}\b")),
    ("api_key_pk", re.compile(r"\bpk[-_][a-zA-Z0-9\-_.]{16,}\b")),
    ("api_key_generic", re.compile(r"\b[aA][pP][iI][-_]?[kK][eE][yY]\s*[:=]\s*[\w\-./+]{16,}")),
    ("password", re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*\S{4,}")),
    ("token", re.compile(r"\b(?:token|secret|auth_token|access_token)\s*[:=]\s*\S{8,}")),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*")),
)

_CAPITAL_AMOUNT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dollar_amount", re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?(?:[kKmMbB])?")),
    (
        "amount_word",
        re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:million|billion|thousand|k|m|b)\b", re.IGNORECASE),
    ),
)

_BROKER_REF_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("account_id_numeric", re.compile(r"\b[Aa]ccount\s+(?:#\s*)?\d{5,}\b")),
    ("ibkr_id", re.compile(r"\bU\d{7,8}\b")),
    (
        "broker_account_ref",
        re.compile(r"\b(?:broker|brokerage)\s+(?:account|ID|ref)\s*[:=#]?\s*\w{4,}\b", re.IGNORECASE),
    ),
)

# Markers that indicate the summary IS a private note (hard fail).
_PRIVATE_NOTE_MARKERS: frozenset[str] = frozenset(
    {"PRIVATE:", "PERSONAL:", "CONFIDENTIAL:", "DO NOT SHARE:"}
)

# Patterns that indicate raw transcript content was inlined (hard fail — raw prompt).
_RAW_TRANSCRIPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:User|Human|Assistant|AI|System)\s*:", re.MULTILINE),
    re.compile(r'"role"\s*:\s*"(?:user|assistant|system)"'),
    re.compile(r'"content"\s*:\s*"'),
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedactionContext:
    """Scope context supplied by the consumer requesting access to a record."""

    tenant_id: str
    requesting_visibility: InteractionVisibility
    user_id: str | None = None
    persona_id: str | None = None


@dataclass(frozen=True)
class RedactionResult:
    """Outcome of running the redaction guard against one record."""

    record: InteractionSourceRecord
    was_redacted: bool
    findings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.record.redaction_status == InteractionRedactionStatus.PASSED

    @property
    def failed(self) -> bool:
        return self.record.redaction_status == InteractionRedactionStatus.FAILED


class SeedCandidateBlockedError(ValueError):
    """Typed refusal raised when a SeedCandidate cannot be created.

    Raised by ``guard_seed_candidate`` when redaction_status is not PASSED.
    IDS-004 and IDS-005 bridges must call this guard before emitting candidates.
    """

    def __init__(self, interaction_id: str, failure_reason: str) -> None:
        super().__init__(
            f"SeedCandidate blocked for interaction {interaction_id!r}: {failure_reason}"
        )
        self.interaction_id = interaction_id
        self.failure_reason = failure_reason


class VisibilityScopeError(ValueError):
    """Raised when a private/persona record would leak into a broader consumer scope."""

    def __init__(
        self,
        interaction_id: str,
        record_visibility: str,
        requesting_visibility: str,
    ) -> None:
        super().__init__(
            f"Visibility scope violation for {interaction_id!r}: "
            f"record is {record_visibility!r} but consumer requested {requesting_visibility!r}"
        )
        self.interaction_id = interaction_id
        self.record_visibility = record_visibility
        self.requesting_visibility = requesting_visibility


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SCOPE_LEVEL: dict[InteractionVisibility, int] = {
    InteractionVisibility.PRIVATE: 0,
    InteractionVisibility.PERSONA: 1,
    InteractionVisibility.DESK: 2,
    InteractionVisibility.SHARED: 3,
}


def _is_raw_transcript(text: str) -> bool:
    return any(p.search(text) for p in _RAW_TRANSCRIPT_PATTERNS)


def _has_private_note_marker(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in _PRIVATE_NOTE_MARKERS)


def _redact_text(text: str) -> tuple[str, list[str]]:
    findings: list[str] = []
    result = text

    for label, pattern in _PII_PATTERNS:
        if pattern.search(result):
            result = pattern.sub(f"[REDACTED:{label.upper()}]", result)
            findings.append(f"pii:{label}")

    for label, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(result):
            result = pattern.sub("[REDACTED:CREDENTIAL]", result)
            findings.append(f"credential:{label}")

    for label, pattern in _CAPITAL_AMOUNT_PATTERNS:
        if pattern.search(result):
            result = pattern.sub("[REDACTED:AMOUNT]", result)
            findings.append(f"capital_amount:{label}")

    for label, pattern in _BROKER_REF_PATTERNS:
        if pattern.search(result):
            result = pattern.sub("[REDACTED:BROKER_REF]", result)
            findings.append(f"broker_ref:{label}")

    return result, findings


def _redact_metadata(metadata: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    findings: list[str] = []
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, str):
            redacted, sub = _redact_text(value)
            result[key] = redacted
            findings.extend(sub)
        elif isinstance(value, dict):
            redacted_dict, sub = _redact_metadata(value)
            result[key] = redacted_dict
            findings.extend(sub)
        elif isinstance(value, list):
            new_list: list[Any] = []
            for item in value:
                if isinstance(item, str):
                    redacted_item, sub = _redact_text(item)
                    new_list.append(redacted_item)
                    findings.extend(sub)
                elif isinstance(item, dict):
                    redacted_dict, sub = _redact_metadata(item)
                    new_list.append(redacted_dict)
                    findings.extend(sub)
                else:
                    new_list.append(item)
            result[key] = new_list
        else:
            result[key] = value
    return result, findings


def _check_visibility_scope(
    record: InteractionSourceRecord,
    context: RedactionContext,
) -> None:
    record_level = _SCOPE_LEVEL[InteractionVisibility(record.visibility)]
    consumer_level = _SCOPE_LEVEL[context.requesting_visibility]

    if record_level < consumer_level:
        raise VisibilityScopeError(
            interaction_id=record.interaction_id,
            record_visibility=record.visibility.value,
            requesting_visibility=context.requesting_visibility.value,
        )

    if record.visibility == InteractionVisibility.PRIVATE and context.user_id is None:
        raise VisibilityScopeError(
            interaction_id=record.interaction_id,
            record_visibility="private",
            requesting_visibility="anonymous",
        )

    if record.visibility == InteractionVisibility.PERSONA and context.persona_id is None:
        raise VisibilityScopeError(
            interaction_id=record.interaction_id,
            record_visibility="persona",
            requesting_visibility="no_persona_context",
        )


def _v(field: Any) -> Any:
    return field.value if hasattr(field, "value") else field


def _rebuild(record: InteractionSourceRecord, **changes: Any) -> InteractionSourceRecord:
    return InteractionSourceRecord(
        interaction_id=changes.get("interaction_id", record.interaction_id),
        source_surface=_v(changes.get("source_surface", record.source_surface)),
        actor_type=_v(changes.get("actor_type", record.actor_type)),
        persona_refs=list(changes.get("persona_refs", record.persona_refs)),
        session_id=changes.get("session_id", record.session_id),
        raw_ref=changes.get("raw_ref", record.raw_ref),
        summary=changes.get("summary", record.summary),
        evidence_refs=list(changes.get("evidence_refs", record.evidence_refs)),
        visibility=_v(changes.get("visibility", record.visibility)),
        redaction_status=_v(changes.get("redaction_status", record.redaction_status)),
        created_at=changes.get("created_at", record.created_at),
        updated_at=changes.get("updated_at", record.updated_at),
        metadata=dict(changes.get("metadata", record.metadata)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_redaction(
    record: InteractionSourceRecord,
    context: RedactionContext,
) -> RedactionResult:
    """Apply the redaction guard to an InteractionSourceRecord.

    Enforces visibility scope, then strips PII/credentials/capital amounts/
    broker refs/private notes from summary and metadata.  Returns a new
    record whose ``redaction_status`` is set to PASSED or FAILED.

    Raises:
        VisibilityScopeError: if the record's visibility is narrower than the
            consumer's ``requesting_visibility`` (e.g. private record served
            to a shared consumer).
    """
    _check_visibility_scope(record, context)

    # Hard fail: raw transcript detected in summary (raw prompt must never reach seed store).
    if _is_raw_transcript(record.summary):
        return RedactionResult(
            record=_rebuild(record, redaction_status=InteractionRedactionStatus.FAILED),
            was_redacted=False,
            findings=("raw_transcript_detected",),
        )

    # Hard fail: private note marker signals the summary is a personal note.
    if _has_private_note_marker(record.summary):
        return RedactionResult(
            record=_rebuild(record, redaction_status=InteractionRedactionStatus.FAILED),
            was_redacted=False,
            findings=("private_note_marker_detected",),
        )

    # Strip PII/credentials/amounts/broker refs from summary and metadata.
    all_findings: list[str] = []
    redacted_summary, summary_findings = _redact_text(record.summary)
    all_findings.extend(summary_findings)

    redacted_metadata, metadata_findings = _redact_metadata(dict(record.metadata))
    all_findings.extend(metadata_findings)

    return RedactionResult(
        record=_rebuild(
            record,
            summary=redacted_summary,
            metadata=redacted_metadata,
            redaction_status=InteractionRedactionStatus.PASSED,
        ),
        was_redacted=bool(all_findings),
        findings=tuple(all_findings),
    )


def guard_seed_candidate(record: InteractionSourceRecord) -> None:
    """Assert that a record has passed redaction before allowing SeedCandidate creation.

    Must be called by every IDS-004 / IDS-005 bridge before emitting a
    SeedCandidate.

    Raises:
        SeedCandidateBlockedError: typed refusal when redaction_status is not
            PASSED (covers both FAILED and PENDING states).
    """
    if record.redaction_status == InteractionRedactionStatus.FAILED:
        raise SeedCandidateBlockedError(
            interaction_id=record.interaction_id,
            failure_reason=(
                "redaction_status=failed; record must pass redaction "
                "before creating a SeedCandidate"
            ),
        )
    if record.redaction_status == InteractionRedactionStatus.PENDING:
        raise SeedCandidateBlockedError(
            interaction_id=record.interaction_id,
            failure_reason=(
                "redaction_status=pending; apply_redaction must be called "
                "before creating a SeedCandidate"
            ),
        )
