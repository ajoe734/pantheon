"""InteractionSourceRecord model and JSONL dev store.

InteractionSourceRecord is the first governed boundary for interaction-derived
strategy seeds. Raw chat, transcripts, prompts, and notebooks remain evidence
artifacts addressed by ``raw_ref``; this store persists only the wrapper,
summary, visibility, redaction state, and evidence pointers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.source_ingestion.registry.jsonl_store import JsonlRegistryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InteractionSourceRecordError(f"{name} is required")
    return text


def _require_ref(value: Any, name: str) -> str:
    text = _require(value, name)
    if "\n" in text or "\r" in text:
        raise InteractionSourceRecordError(f"{name} must be a single reference, not inline text")
    if len(text) > 1024:
        raise InteractionSourceRecordError(f"{name} is too long to be a reference")
    return text


class InteractionSourceRecordError(ValueError):
    """Raised when InteractionSourceRecord invariants are violated."""


class InteractionSourceSurface(str, Enum):
    TRAINER = "trainer"
    ASK_PERSONAS = "ask_personas"
    COMMITTEE = "committee"
    DECISION_JOURNAL = "decision_journal"
    NOTEBOOK = "notebook"
    POSTMORTEM = "postmortem"
    AGORA = "agora"
    RED_TEAM = "red_team"
    MANAGEMENT_AI = "management_ai"
    OPERATOR_CONSOLE = "operator_console"
    RESEARCH_CHAT = "research_chat"
    OTHER = "other"


class InteractionActorType(str, Enum):
    USER = "user"
    OPERATOR = "operator"
    PERSONA = "persona"
    AGENT = "agent"
    SYSTEM = "system"
    REVIEWER = "reviewer"
    RESEARCHER = "researcher"


class InteractionVisibility(str, Enum):
    PRIVATE = "private"
    PERSONA = "persona"
    DESK = "desk"
    SHARED = "shared"


class InteractionRedactionStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


_SCHEMA_VERSION = "interaction_source_record.v1"
_RECORD_KIND = "interaction_source_record"
_FORBIDDEN_INLINE_RAW_KEYS = frozenset(
    {
        "raw_text",
        "raw_content",
        "raw_prompt",
        "prompt",
        "transcript",
        "messages",
        "message",
        "body",
        "content",
    }
)
_EVIDENCE_REF_KINDS = frozenset(
    {
        "raw_interaction",
        "committed_event",
        "memo",
        "verdict",
        "postmortem",
        "notebook_entry",
        "source_record",
        "evidence_bundle",
        "audit_event",
        "artifact",
    }
)


def _reject_inline_raw_keys(value: Any, *, path: str = "record") -> None:
    """Reject common raw-content containers anywhere callers can attach metadata."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_INLINE_RAW_KEYS:
                raise InteractionSourceRecordError(
                    f"{path}.{key_text} must not inline raw interaction content; use raw_ref/evidence_refs"
                )
            _reject_inline_raw_keys(nested, path=f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_inline_raw_keys(nested, path=f"{path}[{index}]")


def _coerce_evidence_refs(raw_refs: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    refs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_refs):
        if not isinstance(raw, Mapping):
            raise InteractionSourceRecordError(f"evidence_refs[{index}] must be an object")
        _reject_inline_raw_keys(raw, path=f"evidence_refs[{index}]")
        ref = _require_ref(raw.get("ref"), f"evidence_refs[{index}].ref")
        kind = _require(raw.get("kind"), f"evidence_refs[{index}].kind")
        if kind not in _EVIDENCE_REF_KINDS:
            raise InteractionSourceRecordError(
                f"evidence_refs[{index}].kind must be one of: {sorted(_EVIDENCE_REF_KINDS)}"
            )
        coerced: dict[str, Any] = {
            "ref": ref,
            "kind": kind,
        }
        if "content_hash" in raw:
            coerced["content_hash"] = raw.get("content_hash")
        if "created_at" in raw:
            coerced["created_at"] = raw.get("created_at")
        if "metadata" in raw:
            metadata = dict(raw.get("metadata") or {})
            _reject_inline_raw_keys(metadata, path=f"evidence_refs[{index}].metadata")
            coerced["metadata"] = metadata
        refs.append(coerced)
    if not refs:
        raise InteractionSourceRecordError("evidence_refs must not be empty")
    return tuple(refs)


@dataclass(frozen=True)
class InteractionSourceRecord:
    """Governed wrapper around raw interaction evidence.

    The record is intentionally not a seed candidate. IDS-002/003/007 perform
    redaction, classification, and negative-memory matching before any seed can
    enter the review inbox.
    """

    interaction_id: str
    source_surface: InteractionSourceSurface | str
    actor_type: InteractionActorType | str
    persona_refs: Sequence[str]
    session_id: str
    raw_ref: str
    summary: str
    evidence_refs: Sequence[Mapping[str, Any]]
    visibility: InteractionVisibility | str
    redaction_status: InteractionRedactionStatus | str
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    record_kind: str = field(default=_RECORD_KIND, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_kind", _RECORD_KIND)
        object.__setattr__(self, "interaction_id", _require(self.interaction_id, "interaction_id"))
        object.__setattr__(self, "session_id", _require(self.session_id, "session_id"))
        object.__setattr__(self, "raw_ref", _require_ref(self.raw_ref, "raw_ref"))
        object.__setattr__(self, "summary", _require(self.summary, "summary"))

        try:
            source_surface = InteractionSourceSurface(str(self.source_surface))
        except ValueError as exc:
            allowed = ", ".join(s.value for s in InteractionSourceSurface)
            raise InteractionSourceRecordError(f"source_surface must be one of: {allowed}") from exc
        object.__setattr__(self, "source_surface", source_surface)

        try:
            actor_type = InteractionActorType(str(self.actor_type))
        except ValueError as exc:
            allowed = ", ".join(a.value for a in InteractionActorType)
            raise InteractionSourceRecordError(f"actor_type must be one of: {allowed}") from exc
        object.__setattr__(self, "actor_type", actor_type)

        try:
            visibility = InteractionVisibility(str(self.visibility))
        except ValueError as exc:
            allowed = ", ".join(v.value for v in InteractionVisibility)
            raise InteractionSourceRecordError(f"visibility must be one of: {allowed}") from exc
        object.__setattr__(self, "visibility", visibility)

        try:
            redaction_status = InteractionRedactionStatus(str(self.redaction_status))
        except ValueError as exc:
            allowed = ", ".join(s.value for s in InteractionRedactionStatus)
            raise InteractionSourceRecordError(f"redaction_status must be one of: {allowed}") from exc
        object.__setattr__(self, "redaction_status", redaction_status)

        persona_refs = tuple(str(ref).strip() for ref in self.persona_refs if str(ref).strip())
        object.__setattr__(self, "persona_refs", persona_refs)

        evidence_refs = _coerce_evidence_refs(self.evidence_refs)
        if self.raw_ref not in {ref["ref"] for ref in evidence_refs}:
            raise InteractionSourceRecordError("raw_ref must be present in evidence_refs")
        object.__setattr__(self, "evidence_refs", evidence_refs)

        metadata = dict(self.metadata or {})
        _reject_inline_raw_keys(metadata, path="metadata")
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "interaction_id": self.interaction_id,
            "record_kind": self.record_kind,
            "source_surface": self.source_surface.value,
            "actor_type": self.actor_type.value,
            "persona_refs": list(self.persona_refs),
            "session_id": self.session_id,
            "raw_ref": self.raw_ref,
            "summary": self.summary,
            "evidence_refs": [dict(ref) for ref in self.evidence_refs],
            "visibility": self.visibility.value,
            "redaction_status": self.redaction_status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InteractionSourceRecord":
        _reject_inline_raw_keys(data, path="record")
        if str(data.get("record_kind", "")) != _RECORD_KIND:
            raise InteractionSourceRecordError(
                f"record_kind must be '{_RECORD_KIND}'; got '{data.get('record_kind')}'"
            )
        if str(data.get("schema_version", "")) != _SCHEMA_VERSION:
            raise InteractionSourceRecordError(
                f"schema_version must be '{_SCHEMA_VERSION}'; got '{data.get('schema_version')}'"
            )
        return cls(
            interaction_id=str(data["interaction_id"]),
            source_surface=str(data["source_surface"]),
            actor_type=str(data["actor_type"]),
            persona_refs=list(data.get("persona_refs") or []),
            session_id=str(data["session_id"]),
            raw_ref=str(data["raw_ref"]),
            summary=str(data["summary"]),
            evidence_refs=list(data.get("evidence_refs") or []),
            visibility=str(data["visibility"]),
            redaction_status=str(data["redaction_status"]),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            metadata=dict(data.get("metadata") or {}),
        )


class InteractionSourceRecordStore:
    """JSONL-backed dev store for InteractionSourceRecord objects."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path:
            resolved = Path(path)
        else:
            data_dir = Path(os.environ.get("SOURCE_INGEST_DATA_DIR", "data/source_ingestion"))
            resolved = Path(
                os.environ.get(
                    "INTERACTION_SOURCE_STORE_PATH",
                    str(data_dir / "interaction_source_records.jsonl"),
                )
            )
        self._store = JsonlRegistryStore(resolved, id_field="interaction_id")

    @property
    def path(self) -> Path:
        return self._store.path

    def save(self, record: InteractionSourceRecord) -> None:
        """Persist a record with upsert semantics after invariant validation."""
        self._store.upsert(record.to_dict())

    def add(self, record: InteractionSourceRecord) -> InteractionSourceRecord:
        if self.get(record.interaction_id) is not None:
            raise InteractionSourceRecordError(f"Interaction source record already exists: {record.interaction_id}")
        self.save(record)
        return record

    def get(self, interaction_id: str) -> InteractionSourceRecord | None:
        raw = self._store.read_by_id(interaction_id)
        if raw is None:
            return None
        return InteractionSourceRecord.from_dict(raw)

    def list_all(self) -> list[InteractionSourceRecord]:
        return [InteractionSourceRecord.from_dict(raw) for raw in self._store.read_all()]

    def list_by_surface(self, surface: InteractionSourceSurface | str) -> list[InteractionSourceRecord]:
        target = InteractionSourceSurface(str(surface)).value
        return [
            InteractionSourceRecord.from_dict(raw)
            for raw in self._store.read_all()
            if raw.get("source_surface") == target
        ]

    def list_by_visibility(self, visibility: InteractionVisibility | str) -> list[InteractionSourceRecord]:
        target = InteractionVisibility(str(visibility)).value
        return [
            InteractionSourceRecord.from_dict(raw)
            for raw in self._store.read_all()
            if raw.get("visibility") == target
        ]

    def list_by_redaction_status(
        self,
        redaction_status: InteractionRedactionStatus | str,
    ) -> list[InteractionSourceRecord]:
        target = InteractionRedactionStatus(str(redaction_status)).value
        return [
            InteractionSourceRecord.from_dict(raw)
            for raw in self._store.read_all()
            if raw.get("redaction_status") == target
        ]

    def count(self) -> int:
        return len(self._store.read_all())
