"""IDS-005 bridge from governed interaction artifacts to strategy seeds.

Raw debate transcripts and chat logs remain evidence-only through
InteractionSourceRecord.raw_ref.  This bridge only accepts published memo,
verdict, proposal, postmortem, or evolution artifacts, then writes a draft
StrategySpecSeed for the existing seed review inbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.ids_audit import (
    IDSAuditEmitter,
    IDSAuditEventStore,
    IDSAuditOutcome,
    IDSAuditStep,
    make_pipeline_id,
)
from services.source_ingestion.interaction_intent_classifier import (
    IntentClassification,
    InteractionIntentClassifier,
    InteractionPrimaryIntent,
)
from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceRecordError,
    InteractionSourceSurface,
)
from services.source_ingestion.redaction_guard import RedactionContext, apply_redaction, guard_seed_candidate
from services.source_ingestion.strategy_seed_builder import StrategySpecSeed, StrategySpecSeedBuilder, StrategySpecSeedError
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore, StrategySpecSeedStoreError


class AgoraSeedBridgeError(ValueError):
    """Raised when an IDS-005 interaction artifact cannot become a seed."""


class AgoraSeedArtifactKind(str, Enum):
    CONSULT_MEMO = "consult_memo"
    COMMITTEE_VERDICT = "committee_verdict"
    RED_TEAM_MEMO = "red_team_memo"
    SEED_PROPOSAL = "seed_proposal"
    POSTMORTEM = "postmortem"
    EVOLUTION_DECISION = "evolution_decision"


class InteractionSeedKind(str, Enum):
    NEW_STRATEGY = "new_strategy"
    MUTATION = "mutation"
    RISK_CONSTRAINT = "risk_constraint"
    EXECUTION_CONSTRAINT = "execution_constraint"
    NEGATIVE = "negative"
    PERSONA_POLICY = "persona_policy"
    DATA_REQUIREMENT = "data_requirement"


@dataclass(frozen=True)
class AgoraTrustProfile:
    trust_source: str
    source_weight: float
    requires_human_review: bool
    review_bias: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trust_source": self.trust_source,
            "source_weight": self.source_weight,
            "requires_human_review": self.requires_human_review,
            "review_bias": self.review_bias,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AgoraSeedExtractionResult:
    seed: StrategySpecSeed
    interaction_record: InteractionSourceRecord
    classification: IntentClassification
    extraction_ref: Mapping[str, Any]
    stored: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed.to_dict(),
            "interaction_record": self.interaction_record.to_dict(),
            "classification": self.classification.to_dict(),
            "extraction_ref": dict(self.extraction_ref),
            "stored": self.stored,
        }


_ARTIFACT_KIND_ALIASES = {
    "consultmemo": AgoraSeedArtifactKind.CONSULT_MEMO,
    "consult_memo": AgoraSeedArtifactKind.CONSULT_MEMO,
    "memo": AgoraSeedArtifactKind.CONSULT_MEMO,
    "published_consult_memo": AgoraSeedArtifactKind.CONSULT_MEMO,
    "committeeverdict": AgoraSeedArtifactKind.COMMITTEE_VERDICT,
    "committee_verdict": AgoraSeedArtifactKind.COMMITTEE_VERDICT,
    "verdict": AgoraSeedArtifactKind.COMMITTEE_VERDICT,
    "redteammemo": AgoraSeedArtifactKind.RED_TEAM_MEMO,
    "red_team_memo": AgoraSeedArtifactKind.RED_TEAM_MEMO,
    "redteam_memo": AgoraSeedArtifactKind.RED_TEAM_MEMO,
    "seedproposal": AgoraSeedArtifactKind.SEED_PROPOSAL,
    "seed_proposal": AgoraSeedArtifactKind.SEED_PROPOSAL,
    "explicit_seed_proposal": AgoraSeedArtifactKind.SEED_PROPOSAL,
    "postmortem": AgoraSeedArtifactKind.POSTMORTEM,
    "postmortem_report": AgoraSeedArtifactKind.POSTMORTEM,
    "evolution": AgoraSeedArtifactKind.EVOLUTION_DECISION,
    "evolution_decision": AgoraSeedArtifactKind.EVOLUTION_DECISION,
    "evolutiondecision": AgoraSeedArtifactKind.EVOLUTION_DECISION,
}
_FORBIDDEN_TRANSCRIPT_KINDS = frozenset(
    {
        "debate_transcript",
        "raw_debate_transcript",
        "consult_transcript",
        "consultation_transcript",
        "transcript",
        "raw_transcript",
        "chat_transcript",
        "messages",
    }
)
_ARTIFACT_REF_KEYS = (
    "artifact_ref",
    "source_artifact_ref",
    "memo_ref",
    "verdict_ref",
    "proposal_ref",
    "postmortem_ref",
    "evolution_ref",
    "published_ref",
)
_NON_SEED_INTENTS = frozenset(
    {
        InteractionPrimaryIntent.PERSONA_POLICY,
        InteractionPrimaryIntent.PREFERENCE_EXAMPLE,
        InteractionPrimaryIntent.OPERATIONAL_NOTE,
        InteractionPrimaryIntent.NON_STRATEGY,
    }
)


class AgoraSeedBridge:
    """Create draft StrategySpecSeed records from governed IDS-005 artifacts."""

    def __init__(
        self,
        store: StrategySpecSeedStore | None = None,
        *,
        audit_store: IDSAuditEventStore | None = None,
    ) -> None:
        self._store = store
        self._classifier = InteractionIntentClassifier()
        self._builder = StrategySpecSeedBuilder()
        self._audit_store = audit_store

    def extract_seed(
        self,
        record: InteractionSourceRecord | Mapping[str, Any],
        *,
        context: RedactionContext,
        artifact_kind: AgoraSeedArtifactKind | str | None = None,
        artifact_ref: str | None = None,
        seed_kind: InteractionSeedKind | str | None = None,
        negative_memory_records: Sequence[Mapping[str, Any] | Any] = (),
        created_by: str = "Codex",
        created_at: datetime | str | None = None,
    ) -> AgoraSeedExtractionResult:
        interaction = _coerce_record(record)
        kind = _artifact_kind(interaction, artifact_kind)
        source_ref = _artifact_ref(interaction, artifact_ref)
        pipeline_id = make_pipeline_id(interaction.source_surface.value, interaction.session_id, interaction.interaction_id)
        auditor = IDSAuditEmitter(self._audit_store, pipeline_id=pipeline_id)

        auditor.emit(
            IDSAuditStep.RECORD,
            source_surface=interaction.source_surface.value,
            outcome=IDSAuditOutcome.SUCCESS,
            interaction_id=interaction.interaction_id,
            actor=created_by,
            details={
                "artifact_kind": kind.value,
                "raw_ref": interaction.raw_ref,
                "redaction_status": interaction.redaction_status.value,
                "visibility": interaction.visibility.value,
            },
        )

        redaction = apply_redaction(interaction, context)
        guarded_record = redaction.record
        if redaction.failed:
            auditor.emit(
                IDSAuditStep.REDACT,
                source_surface=interaction.source_surface.value,
                outcome=IDSAuditOutcome.FAILED,
                interaction_id=interaction.interaction_id,
                actor=created_by,
                details={"findings": list(redaction.findings), "was_redacted": redaction.was_redacted},
            )
        else:
            auditor.emit(
                IDSAuditStep.REDACT,
                source_surface=interaction.source_surface.value,
                outcome=IDSAuditOutcome.SUCCESS,
                interaction_id=interaction.interaction_id,
                actor=created_by,
                details={"findings": list(redaction.findings), "was_redacted": redaction.was_redacted},
            )
        guard_seed_candidate(guarded_record)

        classification = self._classifier.classify(guarded_record)
        if classification.archive_only or classification.primary_intent in _NON_SEED_INTENTS:
            auditor.emit(
                IDSAuditStep.CLASSIFY,
                source_surface=interaction.source_surface.value,
                outcome=IDSAuditOutcome.ARCHIVE_ONLY,
                interaction_id=interaction.interaction_id,
                actor=created_by,
                details={"primary_intent": classification.primary_intent.value, "archive_only": True},
            )
            raise AgoraSeedBridgeError(
                "interaction intent is not eligible for the IDS-005 seed queue: "
                f"{classification.primary_intent.value}"
            )
        auditor.emit(
            IDSAuditStep.CLASSIFY,
            source_surface=interaction.source_surface.value,
            outcome=IDSAuditOutcome.SUCCESS,
            interaction_id=interaction.interaction_id,
            actor=created_by,
            details={
                "primary_intent": classification.primary_intent.value,
                "confidence": classification.confidence,
                "requires_human_review": classification.requires_human_review,
            },
        )

        effective_seed_kind = _seed_kind(guarded_record, classification, seed_kind, kind)
        trust_profile = _trust_profile(kind, guarded_record.source_surface, classification)
        source_record, evidence_item, bundle = _evidence_objects(
            guarded_record,
            kind,
            source_ref,
            effective_seed_kind,
            trust_profile,
            classification,
            created_by=created_by,
            created_at=created_at,
        )
        extraction_ref = _extraction_ref(
            guarded_record,
            kind,
            source_ref,
            effective_seed_kind,
            trust_profile,
            classification,
            redaction_findings=redaction.findings,
        )

        try:
            seed = self._builder.build_seed(
                bundle,
                source_records=[source_record],
                evidence_items=[evidence_item],
                created_by=created_by,
                created_at=created_at,
                metadata={
                    "source_surface": guarded_record.source_surface.value,
                    "interaction_id": guarded_record.interaction_id,
                    "artifact_kind": kind.value,
                    "artifact_ref": source_ref,
                    "seed_kind": effective_seed_kind.value,
                    "source_trust_profile": trust_profile.to_dict(),
                    "intent_classification": _classification_summary(classification),
                    "redaction_findings": list(redaction.findings),
                    "raw_ref_role": "evidence_only",
                    "review_inbox_target": "strategy_seed_review_inbox",
                    "human_review_required": trust_profile.requires_human_review,
                    "research_only": True,
                    "registry_write_performed": False,
                    "execution_route": "none",
                    **({"negative_memory": True} if effective_seed_kind == InteractionSeedKind.NEGATIVE else {}),
                },
                negative_memory_records=negative_memory_records,
            )
        except StrategySpecSeedError as exc:
            auditor.emit(
                IDSAuditStep.CANDIDATE,
                source_surface=interaction.source_surface.value,
                outcome=IDSAuditOutcome.FAILED,
                interaction_id=interaction.interaction_id,
                actor=created_by,
                details={"reason": str(exc)},
            )
            raise AgoraSeedBridgeError(str(exc)) from exc

        neg_match = seed.negative_memory_match
        neg_match_level = str((neg_match or {}).get("warning_level") or "info")
        auditor.emit(
            IDSAuditStep.NEGATIVE_MATCH,
            source_surface=interaction.source_surface.value,
            outcome=IDSAuditOutcome.BLOCKED if neg_match_level == "blocking" else IDSAuditOutcome.SUCCESS,
            interaction_id=interaction.interaction_id,
            actor=created_by,
            details={
                "warning_level": neg_match_level,
                "similarity": float((neg_match or {}).get("similarity") or 0.0),
                "matched_memory_id": (neg_match or {}).get("matched_memory_id"),
            },
        )

        seed = _attach_extraction_ref(seed, extraction_ref)
        stored = False
        if self._store is not None:
            try:
                self._store.save(seed)
            except StrategySpecSeedStoreError as exc:
                raise AgoraSeedBridgeError(str(exc)) from exc
            stored = True

        auditor.emit(
            IDSAuditStep.CANDIDATE,
            source_surface=interaction.source_surface.value,
            outcome=IDSAuditOutcome.SUCCESS,
            interaction_id=interaction.interaction_id,
            seed_id=seed.seed_id,
            actor=created_by,
            details={
                "seed_id": seed.seed_id,
                "stored": stored,
                "seed_kind": effective_seed_kind.value,
                "artifact_kind": kind.value,
                "raw_ref": interaction.raw_ref,
                "raw_ref_role": "evidence_only",
            },
        )

        return AgoraSeedExtractionResult(
            seed=seed,
            interaction_record=guarded_record,
            classification=classification,
            extraction_ref=extraction_ref,
            stored=stored,
        )


def extract_agora_seed(
    record: InteractionSourceRecord | Mapping[str, Any],
    *,
    context: RedactionContext,
    store: StrategySpecSeedStore | None = None,
    artifact_kind: AgoraSeedArtifactKind | str | None = None,
    artifact_ref: str | None = None,
    seed_kind: InteractionSeedKind | str | None = None,
    negative_memory_records: Sequence[Mapping[str, Any] | Any] = (),
    created_by: str = "Codex",
    created_at: datetime | str | None = None,
    audit_store: IDSAuditEventStore | None = None,
) -> AgoraSeedExtractionResult:
    """One-shot IDS-005 extraction helper."""

    return AgoraSeedBridge(store=store, audit_store=audit_store).extract_seed(
        record,
        context=context,
        artifact_kind=artifact_kind,
        artifact_ref=artifact_ref,
        seed_kind=seed_kind,
        negative_memory_records=negative_memory_records,
        created_by=created_by,
        created_at=created_at,
    )


def _coerce_record(value: InteractionSourceRecord | Mapping[str, Any]) -> InteractionSourceRecord:
    if isinstance(value, InteractionSourceRecord):
        return value
    try:
        return InteractionSourceRecord.from_dict(value)
    except InteractionSourceRecordError:
        raise
    except Exception as exc:  # pragma: no cover - defensive adapter wrapper.
        raise AgoraSeedBridgeError("record must be an InteractionSourceRecord payload") from exc


def _artifact_kind(
    record: InteractionSourceRecord,
    explicit_kind: AgoraSeedArtifactKind | str | None,
) -> AgoraSeedArtifactKind:
    candidates: list[Any] = []
    if explicit_kind is not None:
        candidates.append(explicit_kind)
    metadata = record.metadata
    for key in ("artifact_kind", "interaction_kind", "surface_name", "source_artifact_kind", "document_type"):
        if metadata.get(key):
            candidates.append(metadata[key])
    if not candidates:
        surface = record.source_surface
        if surface == InteractionSourceSurface.POSTMORTEM:
            candidates.append("postmortem")
        elif surface == InteractionSourceSurface.RED_TEAM:
            candidates.append("red_team_memo")
        elif surface == InteractionSourceSurface.COMMITTEE:
            candidates.append("committee_verdict")
        else:
            candidates.append("consult_memo")

    for candidate in candidates:
        if isinstance(candidate, AgoraSeedArtifactKind):
            return candidate
        normalized = _normalize_key(candidate)
        if normalized in _FORBIDDEN_TRANSCRIPT_KINDS:
            raise AgoraSeedBridgeError(
                "raw DebateTranscript/raw transcript artifacts are evidence-only and cannot enter the seed queue"
            )
        kind = _ARTIFACT_KIND_ALIASES.get(normalized)
        if kind is not None:
            return kind

    raise AgoraSeedBridgeError(f"unsupported IDS-005 artifact kind: {candidates[0]!r}")


def _artifact_ref(record: InteractionSourceRecord, explicit_ref: str | None) -> str:
    candidates: list[Any] = []
    if explicit_ref:
        candidates.append(explicit_ref)
    for key in _ARTIFACT_REF_KEYS:
        if record.metadata.get(key):
            candidates.append(record.metadata[key])
    for evidence_ref in record.evidence_refs:
        ref = str(evidence_ref.get("ref") or "").strip()
        kind = str(evidence_ref.get("kind") or "").strip()
        if ref and ref != record.raw_ref and kind != "raw_interaction":
            candidates.append(ref)

    for candidate in candidates:
        ref = str(candidate or "").strip()
        if ref and ref != record.raw_ref:
            return ref

    raise AgoraSeedBridgeError(
        "published memo/verdict/proposal/postmortem artifact_ref is required; raw_ref cannot be used as seed content"
    )


def _seed_kind(
    record: InteractionSourceRecord,
    classification: IntentClassification,
    explicit_seed_kind: InteractionSeedKind | str | None,
    artifact_kind: AgoraSeedArtifactKind,
) -> InteractionSeedKind:
    candidates: list[Any] = []
    if explicit_seed_kind is not None:
        candidates.append(explicit_seed_kind)
    if record.metadata.get("seed_kind"):
        candidates.append(record.metadata["seed_kind"])
    nested = record.metadata.get("strategy_seed")
    if isinstance(nested, Mapping) and nested.get("seed_kind"):
        candidates.append(nested["seed_kind"])

    for candidate in candidates:
        kind = _coerce_seed_kind(candidate)
        if kind is not None:
            return kind

    intent = classification.primary_intent
    if intent == InteractionPrimaryIntent.STRATEGY_HYPOTHESIS:
        return InteractionSeedKind.NEW_STRATEGY
    if intent == InteractionPrimaryIntent.RISK_OVERLAY:
        return InteractionSeedKind.RISK_CONSTRAINT
    if intent == InteractionPrimaryIntent.EXECUTION_POLICY:
        return InteractionSeedKind.EXECUTION_CONSTRAINT
    if intent == InteractionPrimaryIntent.NEGATIVE_MEMORY:
        return InteractionSeedKind.NEGATIVE
    if intent == InteractionPrimaryIntent.PORTFOLIO_ALLOCATION:
        return InteractionSeedKind.RISK_CONSTRAINT
    if artifact_kind == AgoraSeedArtifactKind.POSTMORTEM:
        return InteractionSeedKind.NEGATIVE
    if artifact_kind == AgoraSeedArtifactKind.EVOLUTION_DECISION:
        return InteractionSeedKind.RISK_CONSTRAINT
    raise AgoraSeedBridgeError(f"cannot map intent to seed_kind: {intent.value}")


def _coerce_seed_kind(value: Any) -> InteractionSeedKind | None:
    if isinstance(value, InteractionSeedKind):
        return value
    normalized = _normalize_key(value)
    aliases = {
        "strategy_hypothesis": InteractionSeedKind.NEW_STRATEGY,
        "new_strategy": InteractionSeedKind.NEW_STRATEGY,
        "mutation": InteractionSeedKind.MUTATION,
        "risk_overlay": InteractionSeedKind.RISK_CONSTRAINT,
        "risk_constraint": InteractionSeedKind.RISK_CONSTRAINT,
        "execution_policy": InteractionSeedKind.EXECUTION_CONSTRAINT,
        "execution_constraint": InteractionSeedKind.EXECUTION_CONSTRAINT,
        "negative": InteractionSeedKind.NEGATIVE,
        "negative_memory": InteractionSeedKind.NEGATIVE,
        "persona_policy": InteractionSeedKind.PERSONA_POLICY,
        "data_requirement": InteractionSeedKind.DATA_REQUIREMENT,
    }
    return aliases.get(normalized)


def _trust_profile(
    kind: AgoraSeedArtifactKind,
    source_surface: InteractionSourceSurface,
    classification: IntentClassification,
) -> AgoraTrustProfile:
    base_review = bool(classification.requires_human_review)
    if kind == AgoraSeedArtifactKind.COMMITTEE_VERDICT:
        return AgoraTrustProfile(
            trust_source="committee_verdict",
            source_weight=0.90,
            requires_human_review=base_review,
            review_bias="committee_prioritized",
            reason="committee verdict is a reviewed governance artifact, not an ordinary memo",
        )
    if kind == AgoraSeedArtifactKind.RED_TEAM_MEMO:
        return AgoraTrustProfile(
            trust_source="red_team_memo",
            source_weight=0.88,
            requires_human_review=True,
            review_bias="safety_first",
            reason="red-team memo is trusted as critique evidence but remains review-required for seed admission",
        )
    if kind == AgoraSeedArtifactKind.SEED_PROPOSAL:
        return AgoraTrustProfile(
            trust_source="explicit_seed_proposal",
            source_weight=0.78,
            requires_human_review=True,
            review_bias="explicit_submission",
            reason="explicit seed proposal can enter the inbox but still requires seed review",
        )
    if kind == AgoraSeedArtifactKind.POSTMORTEM:
        return AgoraTrustProfile(
            trust_source="postmortem",
            source_weight=0.92,
            requires_human_review=base_review,
            review_bias="postincident_safety",
            reason="postmortem evidence is authoritative for risk, execution, and negative seeds",
        )
    if kind == AgoraSeedArtifactKind.EVOLUTION_DECISION:
        return AgoraTrustProfile(
            trust_source="evolution_decision",
            source_weight=0.90,
            requires_human_review=base_review,
            review_bias="governed_evolution",
            reason="evolution decision is governed follow-up evidence",
        )
    return AgoraTrustProfile(
        trust_source=f"ordinary_{source_surface.value}_memo",
        source_weight=0.65,
        requires_human_review=True,
        review_bias="ordinary_memo_requires_review",
        reason="ordinary consult memo has weaker trust than committee or red-team artifacts",
    )


def _evidence_objects(
    record: InteractionSourceRecord,
    artifact_kind: AgoraSeedArtifactKind,
    artifact_ref: str,
    seed_kind: InteractionSeedKind,
    trust_profile: AgoraTrustProfile,
    classification: IntentClassification,
    *,
    created_by: str,
    created_at: datetime | str | None,
) -> tuple[SourceRecord, EvidenceItem, EvidenceBundle]:
    key = [record.interaction_id, artifact_kind.value, artifact_ref, seed_kind.value]
    suffix = _short_hash(key)
    source_id = f"src-ids005-{suffix}"
    evidence_item_id = f"evi-ids005-{suffix}"
    bundle_id = f"evbundle-ids005-{suffix}"
    citation_ref = f"{artifact_kind.value}#{record.interaction_id}"
    timestamp = created_at or _utc_now()
    strategy_metadata = _strategy_seed_metadata(record, seed_kind, artifact_kind)
    confidence = round(max(0.20, min(0.95, classification.confidence * trust_profile.source_weight)), 4)

    source_record = SourceRecord(
        source_id=source_id,
        connector_id="interaction-derived-seeds",
        source_type="internal_note",
        title=f"{artifact_kind.value} for {record.interaction_id}",
        content_ref=artifact_ref,
        trace_id=f"trace-ids005-{suffix}",
        metadata={
            "strategy_seed": strategy_metadata,
            "interaction_id": record.interaction_id,
            "source_surface": record.source_surface.value,
            "artifact_kind": artifact_kind.value,
            "artifact_ref": artifact_ref,
            "raw_ref": record.raw_ref,
            "raw_ref_role": "evidence_only",
            "seed_kind": seed_kind.value,
            "trust_score": trust_profile.source_weight,
            "trust_profile": trust_profile.to_dict(),
            "allowed_use": ["research", "strategy_seed"],
            "governance": {
                "direct_execution_allowed": False,
                "canonical_sink": "InteractionSourceRecord/EvidenceBundle/StrategySpecSeed",
            },
        },
    )
    evidence_item = EvidenceItem(
        evidence_item_id=evidence_item_id,
        source_id=source_id,
        item_type=artifact_kind.value,
        content_ref=artifact_ref,
        citation_label=citation_ref,
        body=record.summary,
        confidence=confidence,
        access_scope=["research", "strategy_seed"],
        trace_refs=[source_record.trace_id],
        metadata={
            "strategy_seed": strategy_metadata,
            "seed_kind": seed_kind.value,
            "interaction_id": record.interaction_id,
            "source_surface": record.source_surface.value,
            "artifact_kind": artifact_kind.value,
        },
    )
    bundle = EvidenceBundle(
        evidence_bundle_id=bundle_id,
        source_ids=[source_id],
        evidence_item_ids=[evidence_item_id],
        summary=record.summary,
        citation_refs=[citation_ref],
        confidence=confidence,
        license_scope="internal",
        access_scope=["research", "strategy_seed"],
        created_by=created_by,
        created_at=timestamp,
        trace_refs=[source_record.trace_id],
        metadata={
            "strategy_seed": strategy_metadata,
            "source_surface": record.source_surface.value,
            "artifact_kind": artifact_kind.value,
            "seed_kind": seed_kind.value,
            "intent_classification": _classification_summary(classification),
            "trust_profile": trust_profile.to_dict(),
            "raw_ref_role": "evidence_only",
        },
    )
    return source_record, evidence_item, bundle


def _strategy_seed_metadata(
    record: InteractionSourceRecord,
    seed_kind: InteractionSeedKind,
    artifact_kind: AgoraSeedArtifactKind,
) -> dict[str, Any]:
    nested = record.metadata.get("strategy_seed")
    strategy_seed = dict(nested) if isinstance(nested, Mapping) else {}
    strategy_seed.setdefault("hypothesis", _default_hypothesis(record.summary, seed_kind))
    strategy_seed.setdefault("asset_class", _strings(record.metadata.get("asset_class")) or ["unspecified"])
    strategy_seed.setdefault("market_scope", _strings(record.metadata.get("market_scope")) or ["unspecified"])
    strategy_seed.setdefault("required_data", ["governed interaction artifact"])
    strategy_seed.setdefault("feature_hints", _strings(record.metadata.get("feature_hints")))
    strategy_seed.setdefault("label_hints", _strings(record.metadata.get("label_hints")))
    risk_notes = list(_strings(strategy_seed.get("risk_notes")))
    if not risk_notes:
        risk_notes = [
            "interaction_derived_seed_requires_review",
            "raw_transcript_is_evidence_only",
        ]
    if seed_kind == InteractionSeedKind.NEGATIVE:
        risk_notes.append("negative_memory_seed")
    if seed_kind == InteractionSeedKind.EXECUTION_CONSTRAINT:
        risk_notes.append("execution_constraint_review_required")
    if artifact_kind in {AgoraSeedArtifactKind.RED_TEAM_MEMO, AgoraSeedArtifactKind.POSTMORTEM}:
        risk_notes.append(f"safety_source:{artifact_kind.value}")
    strategy_seed["risk_notes"] = _unique_strings(risk_notes)
    strategy_seed["seed_kind"] = seed_kind.value
    strategy_seed["execution_route"] = "none"
    return strategy_seed


def _default_hypothesis(summary: str, seed_kind: InteractionSeedKind) -> str:
    prefix = {
        InteractionSeedKind.RISK_CONSTRAINT: "Risk seed",
        InteractionSeedKind.EXECUTION_CONSTRAINT: "Execution seed",
        InteractionSeedKind.NEGATIVE: "Negative-memory seed",
        InteractionSeedKind.MUTATION: "Mutation seed",
        InteractionSeedKind.DATA_REQUIREMENT: "Data-requirement seed",
        InteractionSeedKind.PERSONA_POLICY: "Persona-policy seed",
    }.get(seed_kind, "Strategy seed")
    return f"{prefix}: {summary}"


def _extraction_ref(
    record: InteractionSourceRecord,
    artifact_kind: AgoraSeedArtifactKind,
    artifact_ref: str,
    seed_kind: InteractionSeedKind,
    trust_profile: AgoraTrustProfile,
    classification: IntentClassification,
    *,
    redaction_findings: Sequence[str],
) -> dict[str, Any]:
    return {
        "ref_type": "AgoraSeedExtractionRef",
        "interaction_id": record.interaction_id,
        "source_surface": record.source_surface.value,
        "session_id": record.session_id,
        "artifact_kind": artifact_kind.value,
        "artifact_ref": artifact_ref,
        "raw_ref": record.raw_ref,
        "raw_ref_role": "evidence_only",
        "evidence_refs": [dict(ref) for ref in record.evidence_refs],
        "seed_kind": seed_kind.value,
        "intent_classification": _classification_summary(classification),
        "trust_profile": trust_profile.to_dict(),
        "redaction_status": record.redaction_status.value,
        "redaction_findings": list(redaction_findings),
    }


def _attach_extraction_ref(seed: StrategySpecSeed, extraction_ref: Mapping[str, Any]) -> StrategySpecSeed:
    payload = seed.to_dict()
    lineage = dict(payload.get("lineage") or {})
    lineage["AgoraSeedExtractionRef"] = dict(extraction_ref)
    lineage["agora_seed_extraction_ref"] = dict(extraction_ref)
    lineage["raw_ref_role"] = "evidence_only"
    promotion_requires = list(lineage.get("promotion_requires") or [])
    if "seed_review_inbox" not in promotion_requires:
        promotion_requires.append("seed_review_inbox")
    lineage["promotion_requires"] = promotion_requires
    payload["lineage"] = lineage

    metadata = dict(payload.get("metadata") or {})
    metadata["AgoraSeedExtractionRef"] = dict(extraction_ref)
    metadata["raw_ref_role"] = "evidence_only"
    payload["metadata"] = metadata
    return StrategySpecSeed.from_dict(payload)


def _classification_summary(classification: IntentClassification) -> dict[str, Any]:
    return {
        "interaction_id": classification.interaction_id,
        "primary_intent": classification.primary_intent.value,
        "confidence": classification.confidence,
        "requires_human_review": classification.requires_human_review,
        "archive_only": classification.archive_only,
        "matched_signals": list(classification.matched_signals),
        "secondary_intents": [intent.value for intent in classification.secondary_intents],
    }


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _strings(values: Sequence[Any] | Any | None) -> list[str]:
    if values in (None, "", [], ()):
        return []
    if isinstance(values, (str, int, float)):
        values = (values,)
    return [str(value).strip() for value in values if str(value).strip()]


def _unique_strings(values: Sequence[Any] | Any | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _strings(values):
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _short_hash(values: Sequence[str]) -> str:
    payload = json.dumps(list(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


__all__ = [
    "AgoraSeedArtifactKind",
    "AgoraSeedBridge",
    "AgoraSeedBridgeError",
    "AgoraSeedExtractionResult",
    "AgoraTrustProfile",
    "InteractionSeedKind",
    "extract_agora_seed",
]
