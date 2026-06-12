"""IDS-004 Trainer committed-event to seed bridge.

The bridge accepts only committed Trainer events, wraps them as governed
InteractionSourceRecord objects, runs IDS safety steps, and writes draft
StrategySpecSeed candidates into the existing seed review inbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.source_ingestion.interaction_intent_classifier import (
    IntentClassification,
    InteractionPrimaryIntent,
    classify_interaction_intent,
)
from services.source_ingestion.interaction_source_store import (
    InteractionActorType,
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceRecordStore,
    InteractionSourceSurface,
    InteractionVisibility,
)
from services.source_ingestion.redaction_guard import (
    RedactionContext,
    SeedCandidateBlockedError,
    apply_redaction,
    guard_seed_candidate,
)
from services.source_ingestion.seed_materializer import (
    MaterializationMode,
    SeedMaterializationError,
    SeedMaterializationService,
)
from services.source_ingestion.strategy_seed_builder import (
    StrategySpecSeed,
    StrategySpecSeedStatus,
)
from services.source_ingestion.strategy_seed_store import (
    StrategySpecSeedStore,
    StrategySpecSeedStoreError,
)


class TrainerSeedKind(str, Enum):
    NEW_STRATEGY = "new_strategy"
    MUTATION = "mutation"
    RISK_CONSTRAINT = "risk_constraint"
    EXECUTION_CONSTRAINT = "execution_constraint"
    NEGATIVE = "negative"
    PERSONA_POLICY = "persona_policy"
    DATA_REQUIREMENT = "data_requirement"


class TrainerSeedBridgeError(ValueError):
    """Raised when a Trainer event cannot enter the seed review inbox."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrainerSeedExtractionRef:
    """Lineage ref from a reviewable seed back to the committed Trainer event."""

    extraction_id: str
    event_type: str
    event_id: str
    session_id: str
    seed_kind: str
    interaction_id: str
    evidence_bundle_id: str
    seed_id: str
    raw_ref: str
    committed_at: str
    committed_by: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_kind": "trainer_seed_extraction_ref",
            "schema_version": "trainer_seed_extraction_ref.v1",
            "extraction_id": self.extraction_id,
            "source_surface": InteractionSourceSurface.TRAINER.value,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "seed_kind": self.seed_kind,
            "interaction_id": self.interaction_id,
            "evidence_bundle_id": self.evidence_bundle_id,
            "seed_id": self.seed_id,
            "raw_ref": self.raw_ref,
            "committed_at": self.committed_at,
            "committed_by": self.committed_by,
            "created_at": self.created_at,
            "registry_write_performed": False,
            "execution_route": "none",
        }


@dataclass(frozen=True)
class TrainerSeedBridgeResult:
    interaction_record: InteractionSourceRecord
    classification: IntentClassification
    seed: StrategySpecSeed
    extraction_ref: TrainerSeedExtractionRef
    was_created: bool
    redaction_findings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_record": self.interaction_record.to_dict(),
            "classification": self.classification.to_dict(),
            "seed": self.seed.to_dict(),
            "trainer_seed_extraction_ref": self.extraction_ref.to_dict(),
            "was_created": self.was_created,
            "redaction_findings": list(self.redaction_findings),
            "research_only": True,
            "execution_route": "none",
        }


_ALLOWED_EVENT_TYPES = frozenset({"trainer_commit", "explicit_seed_submission"})
_RAW_TEACHING_LOG_KEYS = frozenset(
    {
        "raw_text",
        "raw_content",
        "raw_prompt",
        "prompt",
        "transcript",
        "messages",
        "message_body",
        "teaching_log",
        "dialogue",
        "conversation",
        "events",
    }
)
_SEED_KIND_TO_ALLOWED_INTENTS = {
    TrainerSeedKind.NEW_STRATEGY: {InteractionPrimaryIntent.STRATEGY_HYPOTHESIS},
    TrainerSeedKind.MUTATION: {InteractionPrimaryIntent.STRATEGY_HYPOTHESIS},
    TrainerSeedKind.RISK_CONSTRAINT: {InteractionPrimaryIntent.RISK_OVERLAY},
    TrainerSeedKind.EXECUTION_CONSTRAINT: {InteractionPrimaryIntent.EXECUTION_POLICY},
    TrainerSeedKind.NEGATIVE: {InteractionPrimaryIntent.NEGATIVE_MEMORY},
    TrainerSeedKind.PERSONA_POLICY: {InteractionPrimaryIntent.PERSONA_POLICY},
    TrainerSeedKind.DATA_REQUIREMENT: {InteractionPrimaryIntent.STRATEGY_HYPOTHESIS},
}
_SEED_KIND_SUMMARY_PREFIX = {
    TrainerSeedKind.NEW_STRATEGY: "Investable hypothesis",
    TrainerSeedKind.MUTATION: "Strategy mutation",
    TrainerSeedKind.RISK_CONSTRAINT: "Risk constraint",
    TrainerSeedKind.EXECUTION_CONSTRAINT: "Execution policy",
    TrainerSeedKind.NEGATIVE: "Negative memory",
    TrainerSeedKind.PERSONA_POLICY: "Persona policy",
    TrainerSeedKind.DATA_REQUIREMENT: "Data requirement for a strategy seed",
}


class TrainerSeedBridge:
    """Convert committed Trainer events into reviewable StrategySpecSeed drafts."""

    def __init__(
        self,
        *,
        interaction_store: InteractionSourceRecordStore | None = None,
        seed_store: StrategySpecSeedStore | None = None,
        created_by: str = "Codex",
    ) -> None:
        self._interaction_store = interaction_store or InteractionSourceRecordStore()
        self._seed_store = seed_store or StrategySpecSeedStore()
        self._created_by = created_by

    def ingest_event(
        self,
        event: Mapping[str, Any],
        *,
        tenant_id: str = "pantheon",
        requested_by: str | None = None,
        created_at: datetime | str | None = None,
        negative_memory_records: Sequence[Mapping[str, Any] | Any] = (),
    ) -> TrainerSeedBridgeResult:
        """Ingest one committed Trainer event and persist a reviewable seed.

        Raw teaching logs, raw transcripts, message events, and uncommitted
        Trainer replay state are refused before any store write.
        """

        payload = dict(event or {})
        _reject_raw_teaching_log(payload)
        event_type = _event_type(payload)
        seed_kind = _seed_kind(payload)
        event_id = _require_text(payload.get("event_id"), "event_id")
        session_id = _require_text(payload.get("session_id"), "session_id")
        committed_at = _event_time(payload, created_at)
        committed_by = _committed_by(payload, requested_by, self._created_by)
        summary = _summary(payload, seed_kind)
        persona_refs = _persona_refs(payload)
        raw_ref = _raw_ref(payload, session_id=session_id, event_id=event_id)
        source_id = _source_id(session_id, event_id)
        evidence_item_id = f"evi-{source_id}"
        evidence_bundle_id = f"evbundle-{source_id}"
        interaction_id = str(
            payload.get("interaction_id")
            or f"interaction-{_short_hash([session_id, event_id, seed_kind.value])}"
        )

        pending_record = InteractionSourceRecord(
            interaction_id=interaction_id,
            source_surface=InteractionSourceSurface.TRAINER.value,
            actor_type=_actor_type(payload),
            persona_refs=persona_refs,
            session_id=session_id,
            raw_ref=raw_ref,
            summary=summary,
            evidence_refs=_evidence_refs(payload, raw_ref=raw_ref),
            visibility=_visibility(payload, persona_refs),
            redaction_status=InteractionRedactionStatus.PENDING.value,
            created_at=committed_at,
            updated_at=committed_at,
            metadata=_interaction_metadata(
                payload,
                event_type=event_type,
                seed_kind=seed_kind,
                committed_by=committed_by,
                committed_at=committed_at,
            ),
        )

        classification = classify_interaction_intent(pending_record)
        if classification.archive_only:
            raise TrainerSeedBridgeError(
                "archive_only_intent",
                (
                    "Committed Trainer event classified as non_strategy; "
                    "no SeedCandidate should enter the review inbox."
                ),
            )
        expected_intents = _SEED_KIND_TO_ALLOWED_INTENTS[seed_kind]
        if classification.primary_intent not in expected_intents:
            raise TrainerSeedBridgeError(
                "intent_seed_kind_mismatch",
                (
                    f"Committed Trainer event seed_kind={seed_kind.value!r} "
                    f"classified as {classification.primary_intent.value!r}."
                ),
            )

        redaction = apply_redaction(
            pending_record,
            RedactionContext(
                tenant_id=tenant_id,
                requesting_visibility=InteractionVisibility(pending_record.visibility),
                user_id=committed_by,
                persona_id=persona_refs[0] if persona_refs else None,
            ),
        )
        if redaction.failed:
            raise TrainerSeedBridgeError(
                "redaction_failed",
                (
                    "Committed Trainer event failed redaction and cannot create "
                    f"a SeedCandidate: {', '.join(redaction.findings)}"
                ),
            )
        try:
            guard_seed_candidate(redaction.record)
        except SeedCandidateBlockedError as exc:
            raise TrainerSeedBridgeError("seed_candidate_blocked", str(exc)) from exc

        self._interaction_store.save(redaction.record)

        materializer = SeedMaterializationService(
            store=self._seed_store,
            created_by=committed_by,
        )
        try:
            materialized = materializer.materialize(
                EvidenceBundle(
                    evidence_bundle_id=evidence_bundle_id,
                    source_ids=[source_id],
                    evidence_item_ids=[evidence_item_id],
                    summary=redaction.record.summary,
                    citation_refs=[raw_ref],
                    confidence=_seed_confidence(classification),
                    license_scope=str(payload.get("license_scope") or "internal"),
                    access_scope=["research", "strategy_seed"],
                    created_by=committed_by,
                    created_at=committed_at,
                    available_time=committed_at,
                    trace_refs=[interaction_id],
                    metadata=_bundle_metadata(
                        payload,
                        seed_kind=seed_kind,
                        classification=classification,
                        interaction_id=interaction_id,
                        summary=redaction.record.summary,
                    ),
                ),
                evidence_items=[
                    EvidenceItem(
                        evidence_item_id=evidence_item_id,
                        source_id=source_id,
                        item_type="trainer_committed_summary",
                        content_ref=raw_ref,
                        citation_label=f"trainer:{session_id}:{event_id}",
                        body=redaction.record.summary,
                        event_time=committed_at,
                        available_time=committed_at,
                        confidence=_seed_confidence(classification),
                        access_scope=["research", "strategy_seed"],
                        trace_refs=[interaction_id],
                        metadata={
                            "source_kind": "trainer",
                            "seed_kind": seed_kind.value,
                            "interaction_id": interaction_id,
                            "event_type": event_type,
                            "strategy_seed": _strategy_seed_metadata(
                                payload,
                                seed_kind=seed_kind,
                                summary=redaction.record.summary,
                            ),
                        },
                    )
                ],
                mode=MaterializationMode.CREATE_IF_ABSENT,
                requested_by=committed_by,
                idempotency_key=f"trainer-seed:{session_id}:{event_id}:{seed_kind.value}",
                created_at=committed_at,
                metadata={
                    "source_kind": "trainer",
                    "source_surface": InteractionSourceSurface.TRAINER.value,
                    "seed_kind": seed_kind.value,
                    "interaction_source_record_id": interaction_id,
                    "intent_primary": classification.primary_intent.value,
                    "research_only": True,
                    "registry_write_performed": False,
                    "execution_route": "none",
                },
                negative_memory_records=negative_memory_records,
            )
        except SeedMaterializationError as exc:
            raise TrainerSeedBridgeError("materialization_failed", str(exc)) from exc

        extraction_ref = TrainerSeedExtractionRef(
            extraction_id=f"trainer-seed-extraction-{_short_hash([session_id, event_id, materialized.seed.seed_id])}",
            event_type=event_type,
            event_id=event_id,
            session_id=session_id,
            seed_kind=seed_kind.value,
            interaction_id=interaction_id,
            evidence_bundle_id=materialized.seed.evidence_bundle_id,
            seed_id=materialized.seed.seed_id,
            raw_ref=raw_ref,
            committed_at=committed_at,
            committed_by=committed_by,
            created_at=_iso(created_at) or _utc_now_iso(),
        )
        updated_seed = _attach_trainer_lineage(
            materialized.seed,
            extraction_ref=extraction_ref,
            classification=classification,
            redaction_findings=redaction.findings,
            seed_kind=seed_kind,
        )
        try:
            self._seed_store.save(updated_seed)
        except StrategySpecSeedStoreError as exc:
            raise TrainerSeedBridgeError("seed_store_error", str(exc)) from exc

        return TrainerSeedBridgeResult(
            interaction_record=redaction.record,
            classification=classification,
            seed=updated_seed,
            extraction_ref=extraction_ref,
            was_created=materialized.was_created,
            redaction_findings=tuple(redaction.findings),
        )


def _event_type(event: Mapping[str, Any]) -> str:
    event_type = _require_text(event.get("event_type"), "event_type")
    normalized = event_type.strip().lower()
    if normalized not in _ALLOWED_EVENT_TYPES:
        raise TrainerSeedBridgeError(
            "unsupported_event_type",
            (
                "Trainer seed bridge accepts only committed Trainer events "
                "trainer_commit or explicit_seed_submission."
            ),
        )
    return normalized


def _seed_kind(event: Mapping[str, Any]) -> TrainerSeedKind:
    raw = (
        event.get("seed_kind")
        or (event.get("metadata") or {}).get("seed_kind")
        or (event.get("strategy_seed") or {}).get("seed_kind")
        or TrainerSeedKind.MUTATION.value
    )
    normalized = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return TrainerSeedKind(normalized)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in TrainerSeedKind)
        raise TrainerSeedBridgeError("unsupported_seed_kind", f"seed_kind must be one of: {allowed}") from exc


def _reject_raw_teaching_log(value: Any, *, path: str = "event") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text in _RAW_TEACHING_LOG_KEYS and _has_payload(nested):
                raise TrainerSeedBridgeError(
                    "raw_teaching_log_refused",
                    (
                        f"{path}.{key_text} contains raw or uncommitted teaching "
                        "content; pass a committed event reference and summary instead."
                    ),
                )
            _reject_raw_teaching_log(nested, path=f"{path}.{key_text}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _reject_raw_teaching_log(nested, path=f"{path}[{index}]")


def _has_payload(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return bool(value)
    return True


def _summary(event: Mapping[str, Any], seed_kind: TrainerSeedKind) -> str:
    text = _require_text(
        event.get("summary")
        or event.get("candidate_summary")
        or event.get("committed_summary")
        or (event.get("strategy_seed") or {}).get("hypothesis"),
        "summary",
    )
    prefix = _SEED_KIND_SUMMARY_PREFIX[seed_kind]
    if text.lower().startswith(prefix.lower()):
        return text
    return f"{prefix}: {text}"


def _persona_refs(event: Mapping[str, Any]) -> tuple[str, ...]:
    raw = event.get("persona_refs") or event.get("persona_ids") or ()
    if isinstance(raw, str):
        raw = (raw,)
    refs = [str(item).strip() for item in raw if str(item).strip()]
    persona_id = str(event.get("persona_id") or "").strip()
    if persona_id and persona_id not in refs:
        refs.append(persona_id)
    return tuple(refs)


def _raw_ref(event: Mapping[str, Any], *, session_id: str, event_id: str) -> str:
    explicit = str(
        event.get("raw_ref")
        or event.get("committed_event_ref")
        or event.get("event_ref")
        or ""
    ).strip()
    if explicit:
        return explicit
    return f"evidence://trainer/{session_id}/{event_id}"


def _evidence_refs(event: Mapping[str, Any], *, raw_ref: str) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = [{"ref": raw_ref, "kind": "committed_event"}]
    for raw in event.get("evidence_refs") or ():
        if isinstance(raw, Mapping):
            ref = str(raw.get("ref") or "").strip()
            kind = str(raw.get("kind") or "artifact").strip()
            if ref and ref != raw_ref:
                refs.append({"ref": ref, "kind": kind})

    artifact_refs = event.get("artifact_refs")
    if isinstance(artifact_refs, Mapping):
        for value in artifact_refs.values():
            ref = str(value or "").strip()
            if ref:
                refs.append({"ref": ref, "kind": "artifact"})
    for key in ("candidate_artifact_ref", "after_artifact_ref", "before_artifact_ref"):
        ref = str(event.get(key) or "").strip()
        if ref:
            refs.append({"ref": ref, "kind": "artifact"})

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref["ref"], ref["kind"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return tuple(unique)


def _visibility(event: Mapping[str, Any], persona_refs: Sequence[str]) -> str:
    raw = str(event.get("visibility") or "").strip().lower()
    if raw:
        return InteractionVisibility(raw).value
    return InteractionVisibility.PERSONA.value if persona_refs else InteractionVisibility.DESK.value


def _actor_type(event: Mapping[str, Any]) -> str:
    raw = str(event.get("actor_type") or "").strip().lower()
    if raw:
        return InteractionActorType(raw).value
    if event.get("persona_id") or event.get("persona_refs"):
        return InteractionActorType.OPERATOR.value
    return InteractionActorType.USER.value


def _committed_by(event: Mapping[str, Any], requested_by: str | None, fallback: str) -> str:
    return _require_text(
        event.get("committed_by")
        or event.get("actor_id")
        or event.get("operator_id")
        or requested_by
        or fallback,
        "committed_by",
    )


def _event_time(event: Mapping[str, Any], created_at: datetime | str | None) -> str:
    return _require_text(
        _iso(created_at)
        or event.get("committed_at")
        or event.get("emitted_at")
        or event.get("created_at")
        or _utc_now_iso(),
        "committed_at",
    )


def _interaction_metadata(
    event: Mapping[str, Any],
    *,
    event_type: str,
    seed_kind: TrainerSeedKind,
    committed_by: str,
    committed_at: str,
) -> dict[str, Any]:
    return {
        "surface_name": event_type,
        "source_kind": "trainer",
        "seed_kind": seed_kind.value,
        "intent_hint": _intent_hint(seed_kind),
        "trainer_event_id": event.get("event_id"),
        "committed_by": committed_by,
        "committed_at": committed_at,
        "artifact_refs": dict(event.get("artifact_refs") or {}),
        "research_only": True,
        "registry_write_performed": False,
        "execution_route": "none",
    }


def _bundle_metadata(
    event: Mapping[str, Any],
    *,
    seed_kind: TrainerSeedKind,
    classification: IntentClassification,
    interaction_id: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "source_kind": "trainer",
        "source_surface": InteractionSourceSurface.TRAINER.value,
        "seed_kind": seed_kind.value,
        "interaction_source_record_id": interaction_id,
        "intent_classification": classification.to_dict(),
        "strategy_seed": _strategy_seed_metadata(event, seed_kind=seed_kind, summary=summary),
        "research_only": True,
        "registry_write_performed": False,
        "execution_route": "none",
    }


def _strategy_seed_metadata(
    event: Mapping[str, Any],
    *,
    seed_kind: TrainerSeedKind,
    summary: str,
) -> dict[str, Any]:
    metadata = dict(event.get("strategy_seed") or {})
    metadata.setdefault("hypothesis", summary)
    metadata.setdefault("seed_kind", seed_kind.value)
    metadata.setdefault("risk_notes", ["trainer_seed_requires_review"])
    metadata.setdefault("required_data", ["governed trainer commit evidence"])
    if seed_kind == TrainerSeedKind.DATA_REQUIREMENT:
        metadata["required_data"] = _strings(
            event.get("required_data")
            or metadata.get("required_data")
            or ["operator supplied data requirement"]
        )
    if seed_kind == TrainerSeedKind.RISK_CONSTRAINT:
        metadata.setdefault("risk_constraints", [summary])
    if seed_kind == TrainerSeedKind.EXECUTION_CONSTRAINT:
        metadata.setdefault("backend_hint", "policy_review")
    if seed_kind == TrainerSeedKind.PERSONA_POLICY:
        metadata.setdefault("strategy_family", "persona_policy")
    metadata["research_only"] = True
    metadata["registry_write_performed"] = False
    metadata["execution_route"] = "none"
    return metadata


def _intent_hint(seed_kind: TrainerSeedKind) -> str:
    if seed_kind == TrainerSeedKind.DATA_REQUIREMENT:
        return InteractionPrimaryIntent.STRATEGY_HYPOTHESIS.value
    return {
        TrainerSeedKind.NEW_STRATEGY: InteractionPrimaryIntent.STRATEGY_HYPOTHESIS.value,
        TrainerSeedKind.MUTATION: InteractionPrimaryIntent.STRATEGY_HYPOTHESIS.value,
        TrainerSeedKind.RISK_CONSTRAINT: InteractionPrimaryIntent.RISK_OVERLAY.value,
        TrainerSeedKind.EXECUTION_CONSTRAINT: InteractionPrimaryIntent.EXECUTION_POLICY.value,
        TrainerSeedKind.NEGATIVE: InteractionPrimaryIntent.NEGATIVE_MEMORY.value,
        TrainerSeedKind.PERSONA_POLICY: InteractionPrimaryIntent.PERSONA_POLICY.value,
    }[seed_kind]


def _attach_trainer_lineage(
    seed: StrategySpecSeed,
    *,
    extraction_ref: TrainerSeedExtractionRef,
    classification: IntentClassification,
    redaction_findings: Sequence[str],
    seed_kind: TrainerSeedKind,
) -> StrategySpecSeed:
    payload = seed.to_dict()
    lineage = dict(payload.get("lineage") or {})
    lineage["created_from"] = "trainer_seed_bridge"
    lineage["trainer_seed_extraction_ref"] = extraction_ref.to_dict()
    lineage["interaction_source_record_id"] = extraction_ref.interaction_id
    lineage["intent_classification"] = classification.to_dict()
    lineage["redaction_findings"] = list(redaction_findings)
    lineage["seed_review_inbox_status"] = StrategySpecSeedStatus.DRAFT.value
    lineage["registry_write_performed"] = False
    lineage["execution_route"] = "none"
    payload["lineage"] = lineage

    metadata = dict(payload.get("metadata") or {})
    metadata["source_kind"] = "trainer"
    metadata["source_surface"] = InteractionSourceSurface.TRAINER.value
    metadata["seed_kind"] = seed_kind.value
    metadata["trainer_seed_extraction_ref_id"] = extraction_ref.extraction_id
    metadata["interaction_source_record_id"] = extraction_ref.interaction_id
    metadata["research_only"] = True
    metadata["registry_write_performed"] = False
    metadata["execution_route"] = "none"
    payload["metadata"] = metadata
    return StrategySpecSeed.from_dict(payload)


def _seed_confidence(classification: IntentClassification) -> float:
    if classification.requires_human_review:
        return round(max(0.1, min(classification.confidence, 0.75)), 2)
    return classification.confidence


def _source_id(session_id: str, event_id: str) -> str:
    return f"src-trainer-{_short_hash([session_id, event_id])}"


def _short_hash(parts: Sequence[Any]) -> str:
    encoded = json.dumps([str(part) for part in parts], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TrainerSeedBridgeError("missing_field", f"{field_name} is required")
    return text


def _strings(value: Any) -> list[str]:
    if value in (None, "", [], ()):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Mapping):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def trainer_seed_kind_from_text(summary: str) -> TrainerSeedKind:
    """Best-effort BFF helper for committed Trainer replay events."""
    text = str(summary or "").lower()
    if re.search(r"\b(data|dataset|ohlcv|fundamental|feature|label)\b", text):
        return TrainerSeedKind.DATA_REQUIREMENT
    if re.search(r"\b(order|routing|slippage|fill|broker|execution)\b", text):
        return TrainerSeedKind.EXECUTION_CONSTRAINT
    if re.search(r"\b(drawdown|risk|exposure|leverage|limit|constraint|stop loss)\b", text):
        return TrainerSeedKind.RISK_CONSTRAINT
    if re.search(r"\b(never|avoid|do not repeat|retired|failed|negative)\b", text):
        return TrainerSeedKind.NEGATIVE
    if re.search(r"\b(persona|style|coaching|tone|response)\b", text):
        return TrainerSeedKind.PERSONA_POLICY
    if re.search(r"\b(new strategy|hypothesis|alpha|factor|signal)\b", text):
        return TrainerSeedKind.NEW_STRATEGY
    return TrainerSeedKind.MUTATION


__all__ = [
    "TrainerSeedBridge",
    "TrainerSeedBridgeError",
    "TrainerSeedBridgeResult",
    "TrainerSeedExtractionRef",
    "TrainerSeedKind",
    "trainer_seed_kind_from_text",
]
