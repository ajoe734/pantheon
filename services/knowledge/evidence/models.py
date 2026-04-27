"""Evidence and knowledge-object domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise EvidenceValidationError(f"{field_name} is required")
    return normalized


def normalize_strings(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in (values or ()) if str(value).strip())


def validate_confidence(value: float, field_name: str = "confidence") -> float:
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        raise EvidenceValidationError(f"{field_name} must be between 0.0 and 1.0")
    return confidence


class EvidenceValidationError(ValueError):
    """Raised when evidence-plane invariants are violated."""


@dataclass(frozen=True)
class EvidenceItem:
    evidence_item_id: str
    source_id: str
    item_type: str
    content_ref: str
    citation_label: str
    body: str = ""
    event_time: datetime | str | None = None
    available_time: datetime | str | None = None
    confidence: float = 1.0
    access_scope: Sequence[str] = field(default_factory=lambda: ("public",))
    trace_refs: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_item_id", require_text(self.evidence_item_id, "evidence_item_id"))
        object.__setattr__(self, "source_id", require_text(self.source_id, "source_id"))
        object.__setattr__(self, "item_type", require_text(self.item_type, "item_type"))
        object.__setattr__(self, "content_ref", require_text(self.content_ref, "content_ref"))
        object.__setattr__(self, "citation_label", require_text(self.citation_label, "citation_label"))
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "access_scope", normalize_strings(self.access_scope) or ("public",))
        object.__setattr__(self, "trace_refs", normalize_strings(self.trace_refs))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_item_id": self.evidence_item_id,
            "source_id": self.source_id,
            "item_type": self.item_type,
            "content_ref": self.content_ref,
            "citation_label": self.citation_label,
            "body": self.body,
            "event_time": iso(self.event_time),
            "available_time": iso(self.available_time),
            "confidence": self.confidence,
            "access_scope": list(self.access_scope),
            "trace_refs": list(self.trace_refs),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvidenceBundle:
    evidence_bundle_id: str
    source_ids: Sequence[str]
    evidence_item_ids: Sequence[str]
    summary: str
    citation_refs: Sequence[str]
    confidence: float
    license_scope: str
    access_scope: Sequence[str]
    created_by: str
    created_at: datetime | str = field(default_factory=utc_now)
    trace_refs: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_bundle_id", require_text(self.evidence_bundle_id, "evidence_bundle_id"))
        object.__setattr__(self, "source_ids", normalize_strings(self.source_ids))
        object.__setattr__(self, "evidence_item_ids", normalize_strings(self.evidence_item_ids))
        if not self.source_ids:
            raise EvidenceValidationError("EvidenceBundle requires at least one source_id")
        if not self.evidence_item_ids:
            raise EvidenceValidationError("EvidenceBundle requires at least one evidence_item_id")
        object.__setattr__(self, "summary", require_text(self.summary, "summary"))
        object.__setattr__(self, "citation_refs", normalize_strings(self.citation_refs))
        if not self.citation_refs:
            raise EvidenceValidationError("EvidenceBundle requires citation_refs")
        object.__setattr__(self, "confidence", validate_confidence(self.confidence))
        object.__setattr__(self, "license_scope", require_text(self.license_scope, "license_scope"))
        object.__setattr__(self, "access_scope", normalize_strings(self.access_scope) or ("public",))
        object.__setattr__(self, "created_by", require_text(self.created_by, "created_by"))
        object.__setattr__(self, "trace_refs", normalize_strings(self.trace_refs))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_bundle_id": self.evidence_bundle_id,
            "source_ids": list(self.source_ids),
            "evidence_item_ids": list(self.evidence_item_ids),
            "summary": self.summary,
            "citation_refs": list(self.citation_refs),
            "confidence": self.confidence,
            "license_scope": self.license_scope,
            "access_scope": list(self.access_scope),
            "created_by": self.created_by,
            "created_at": iso(self.created_at),
            "trace_refs": list(self.trace_refs),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    source_id: str
    evidence_item_id: str
    chunk_index: int
    text: str
    token_count: int
    embedding_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    access_scope: Sequence[str] = field(default_factory=lambda: ("public",))

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_id", require_text(self.chunk_id, "chunk_id"))
        object.__setattr__(self, "source_id", require_text(self.source_id, "source_id"))
        object.__setattr__(self, "evidence_item_id", require_text(self.evidence_item_id, "evidence_item_id"))
        if int(self.chunk_index) < 0:
            raise EvidenceValidationError("chunk_index must be non-negative")
        object.__setattr__(self, "chunk_index", int(self.chunk_index))
        object.__setattr__(self, "text", require_text(self.text, "text"))
        if int(self.token_count) <= 0:
            raise EvidenceValidationError("token_count must be positive")
        object.__setattr__(self, "token_count", int(self.token_count))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "access_scope", normalize_strings(self.access_scope) or ("public",))

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "evidence_item_id": self.evidence_item_id,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "token_count": self.token_count,
            "embedding_ref": self.embedding_ref,
            "metadata": dict(self.metadata),
            "access_scope": list(self.access_scope),
        }


@dataclass(frozen=True)
class KnowledgeObject:
    knowledge_object_id: str
    source_id: str
    evidence_item_id: str
    evidence_bundle_id: str
    title: str
    text: str
    source_type: str
    license_scope: str
    access_scope: Sequence[str]
    environment_scope: Sequence[str] = field(default_factory=lambda: ("dev", "sandbox", "paper", "canary", "live"))
    persona_scope: Sequence[str] = field(default_factory=tuple)
    workspace_scope: Sequence[str] = field(default_factory=tuple)
    keywords: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for field_name in (
            "knowledge_object_id",
            "source_id",
            "evidence_item_id",
            "evidence_bundle_id",
            "title",
            "text",
            "source_type",
            "license_scope",
        ):
            object.__setattr__(self, field_name, require_text(getattr(self, field_name), field_name))
        object.__setattr__(self, "access_scope", normalize_strings(self.access_scope) or ("public",))
        object.__setattr__(self, "environment_scope", normalize_strings(self.environment_scope))
        object.__setattr__(self, "persona_scope", normalize_strings(self.persona_scope))
        object.__setattr__(self, "workspace_scope", normalize_strings(self.workspace_scope))
        object.__setattr__(self, "keywords", normalize_strings(self.keywords))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_object_id": self.knowledge_object_id,
            "source_id": self.source_id,
            "evidence_item_id": self.evidence_item_id,
            "evidence_bundle_id": self.evidence_bundle_id,
            "title": self.title,
            "text": self.text,
            "source_type": self.source_type,
            "license_scope": self.license_scope,
            "access_scope": list(self.access_scope),
            "environment_scope": list(self.environment_scope),
            "persona_scope": list(self.persona_scope),
            "workspace_scope": list(self.workspace_scope),
            "keywords": list(self.keywords),
            "metadata": dict(self.metadata),
            "created_at": iso(self.created_at),
        }
