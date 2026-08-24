"""
Institutional memory backbone store for the Pantheon memory plane.

This module provides:
- InstitutionalMemoryEntry immutable dataclass
- Semantic and JSON-schema validation helpers
- Thread-safe in-memory store with optional JSON persistence
- A simple retrieval helper that respects scope and supersession semantics
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone information")
    offset = parsed.utcoffset()
    if offset is None or offset != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be expressed in UTC")
    return parsed.astimezone(timezone.utc)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _entry_sort_key(entry: "InstitutionalMemoryEntry") -> tuple[int, datetime]:
    return (entry.reuse_count, _parse_utc_timestamp(entry.written_at))


class InstitutionalMemoryError(ValueError):
    """Raised when entry validation or store operations fail."""


class KnowledgeType(str, Enum):
    INCIDENT_LESSON = "incident_lesson"
    REGIME_PATTERN = "regime_pattern"
    POLICY_PRECEDENT = "policy_precedent"
    RESEARCH_FINDING = "research_finding"
    EVOLUTION_RATIONALE = "evolution_rationale"
    CROSS_PERSONA_OBSERVATION = "cross_persona_observation"


class SourceEventType(str, Enum):
    RUNTIME_TELEMETRY_OUTCOME = "runtime_telemetry_outcome"
    POSTMORTEM_PUBLISHED = "postmortem_published"
    EVOLUTION_DECISION_APPROVED = "evolution_decision_approved"
    RESEARCH_TASK_COMPLETED = "research_task_completed"
    RESEARCH_FINDING_PUBLISHED = "research_finding_published"
    GOVERNANCE_REVIEW_CLOSED = "governance_review_closed"
    COMMITTEE_RESOLUTION_PUBLISHED = "committee_resolution_published"
    CONSULTATION_CLOSED = "consultation_closed"


class WriteAuthority(str, Enum):
    TELEMETRY_SVC = "telemetry-svc"
    INCIDENT_SVC = "incident-svc"
    EVOLUTION_SVC = "evolution-svc"
    RESEARCH_SVC = "research-svc"
    GOVERNANCE_SVC = "governance-svc"
    CONSULTATION_SVC = "consultation-svc"


class Scope(str, Enum):
    SYSTEM_WIDE = "system_wide"
    STRATEGY_FAMILY = "strategy_family"
    INSTRUMENT_CLASS = "instrument_class"


@dataclass(frozen=True)
class InstitutionalMemoryEntry:
    """Canonical institutional memory object."""

    entry_id: str
    knowledge_type: str
    content: Dict[str, Any]
    source_event_type: str
    source_event_id: str
    written_at: str
    write_authority: str
    scope: str

    contributing_persona_ids: List[str] = field(default_factory=list)
    scope_filter: Optional[str] = None
    embedding_ref: Optional[str] = None
    superseded_by: Optional[str] = None
    expires_at: Optional[str] = None
    archived_at: Optional[str] = None
    archived_reason: Optional[str] = None
    reuse_count: int = 0

    def __post_init__(self) -> None:
        try:
            KnowledgeType(self.knowledge_type)
        except ValueError as exc:
            raise InstitutionalMemoryError(
                f"Invalid knowledge_type: {self.knowledge_type!r}. "
                f"Must be one of {[e.value for e in KnowledgeType]}."
            ) from exc
        try:
            SourceEventType(self.source_event_type)
        except ValueError as exc:
            raise InstitutionalMemoryError(
                f"Invalid source_event_type: {self.source_event_type!r}. "
                f"Must be one of {[e.value for e in SourceEventType]}."
            ) from exc
        try:
            WriteAuthority(self.write_authority)
        except ValueError as exc:
            raise InstitutionalMemoryError(
                f"Invalid write_authority: {self.write_authority!r}. "
                f"Must be one of {[e.value for e in WriteAuthority]}."
            ) from exc
        try:
            Scope(self.scope)
        except ValueError as exc:
            raise InstitutionalMemoryError(
                f"Invalid scope: {self.scope!r}. Must be one of {[e.value for e in Scope]}."
            ) from exc
        if self.reuse_count < 0:
            raise InstitutionalMemoryError("reuse_count must be >= 0")
        if not isinstance(self.content, dict):
            raise InstitutionalMemoryError("content must be an object")
        headline = self.content.get("headline")
        body = self.content.get("body")
        if not isinstance(headline, str) or not headline.strip():
            raise InstitutionalMemoryError("content.headline must be a non-empty string")
        if not isinstance(body, str) or not body.strip():
            raise InstitutionalMemoryError("content.body must be a non-empty string")

    @property
    def is_active(self) -> bool:
        return not bool(self.superseded_by or self.archived_at)

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        current = now or datetime.now(timezone.utc)
        return _parse_utc_timestamp(self.expires_at) <= current.astimezone(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return {k: v for k, v in payload.items() if v is not None and v != []}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstitutionalMemoryEntry":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "InstitutionalMemoryEntry":
        return cls.from_dict(json.loads(payload))


def validate_institutional_memory(entry: InstitutionalMemoryEntry) -> List[str]:
    """Return semantic validation errors; empty list means valid."""
    errors: List[str] = []

    if not entry.entry_id:
        errors.append("entry_id must not be empty")
    if not entry.source_event_id:
        errors.append("source_event_id must not be empty")
    try:
        _parse_utc_timestamp(entry.written_at)
    except ValueError:
        errors.append("written_at must be an ISO-8601 UTC timestamp")
    if entry.expires_at:
        try:
            _parse_utc_timestamp(entry.expires_at)
        except ValueError:
            errors.append("expires_at must be an ISO-8601 UTC timestamp")
    if entry.archived_at:
        try:
            _parse_utc_timestamp(entry.archived_at)
        except ValueError:
            errors.append("archived_at must be an ISO-8601 UTC timestamp")
        if not entry.archived_reason:
            errors.append("archived_reason is required when archived_at is set")
    if entry.scope == Scope.SYSTEM_WIDE.value and entry.scope_filter is not None:
        errors.append("scope_filter must be null/omitted when scope is 'system_wide'")
    if entry.scope in {Scope.STRATEGY_FAMILY.value, Scope.INSTRUMENT_CLASS.value} and not entry.scope_filter:
        errors.append("scope_filter is required when scope is strategy_family or instrument_class")
    if any(not str(persona_id).strip() for persona_id in entry.contributing_persona_ids):
        errors.append("contributing_persona_ids must not contain blank values")
    return errors


def validate_institutional_memory_json(data: Dict[str, Any]) -> List[str]:
    """Validate raw dict against the canonical JSON schema."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []

    schema_path = Path(__file__).parent / "institutional_memory_entry.schema.json"
    if not schema_path.exists():
        return [f"Schema file not found: {schema_path}"]

    schema = json.loads(schema_path.read_text())
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        errors.append(f"{list(err.path)}: {err.message}")
    return errors


@dataclass(frozen=True)
class RetrievalHit:
    """Retrieved entry plus ranking score."""

    entry: InstitutionalMemoryEntry
    relevance_score: float


@dataclass(frozen=True)
class InstitutionalRetentionPolicy:
    """TTL policy for new institutional memory entries."""

    ttl_days: Optional[int] = 365
    archive_reason: str = "retention_ttl_expired"

    @classmethod
    def from_env(cls) -> "InstitutionalRetentionPolicy":
        raw = os.getenv("PANTHEON_MEMORY_RETENTION_DAYS", "365").strip()
        if raw.lower() in {"", "none", "never", "indefinite"}:
            ttl_days: Optional[int] = None
        else:
            try:
                parsed = int(raw)
            except ValueError as exc:
                raise InstitutionalMemoryError("PANTHEON_MEMORY_RETENTION_DAYS must be an integer or 'indefinite'") from exc
            ttl_days = parsed if parsed > 0 else None
        reason = os.getenv("PANTHEON_MEMORY_RETENTION_ARCHIVE_REASON", "retention_ttl_expired").strip()
        return cls(ttl_days=ttl_days, archive_reason=reason or "retention_ttl_expired")

    def apply_to_new_entry(self, entry: InstitutionalMemoryEntry) -> InstitutionalMemoryEntry:
        if entry.expires_at or self.ttl_days is None:
            return entry
        try:
            written_at = _parse_utc_timestamp(entry.written_at)
        except ValueError as exc:
            raise InstitutionalMemoryError("written_at must be an ISO-8601 UTC timestamp") from exc
        expires_at = _format_utc_timestamp(written_at + timedelta(days=self.ttl_days))
        return InstitutionalMemoryEntry(**{**entry.to_dict(), "expires_at": expires_at})


class InstitutionalMemoryStore:
    """Thread-safe institutional memory store with optional JSON persistence."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, InstitutionalMemoryEntry] = {}
        self._path = path
        if path and path.exists():
            self._load(path)

    def create(self, entry: InstitutionalMemoryEntry) -> InstitutionalMemoryEntry:
        entry = InstitutionalRetentionPolicy.from_env().apply_to_new_entry(entry)
        errors = validate_institutional_memory(entry)
        if errors:
            raise InstitutionalMemoryError(f"Invalid institutional memory entry: {errors}")
        json_errors = validate_institutional_memory_json(entry.to_dict())
        if json_errors:
            raise InstitutionalMemoryError(f"Schema validation failed: {json_errors}")
        with self._lock:
            if entry.entry_id in self._entries:
                raise InstitutionalMemoryError(f"Entry already exists: {entry.entry_id}")
            self._entries[entry.entry_id] = entry
            self._save()
            return entry

    def get(self, entry_id: str) -> Optional[InstitutionalMemoryEntry]:
        with self._lock:
            return self._entries.get(entry_id)

    def _require_unlocked(self, entry_id: str) -> InstitutionalMemoryEntry:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise InstitutionalMemoryError(f"Entry not found: {entry_id}")
        return entry

    def require(self, entry_id: str) -> InstitutionalMemoryEntry:
        with self._lock:
            return self._require_unlocked(entry_id)

    def list(
        self,
        *,
        knowledge_type: Optional[str] = None,
        scope: Optional[str] = None,
        scope_filter: Optional[str] = None,
        contributing_persona_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[InstitutionalMemoryEntry]:
        if active_only:
            self.archive_expired()
        with self._lock:
            entries = list(self._entries.values())

        if knowledge_type:
            entries = [entry for entry in entries if entry.knowledge_type == knowledge_type]
        if scope:
            entries = [entry for entry in entries if entry.scope == scope]
        if scope_filter is not None:
            entries = [entry for entry in entries if entry.scope_filter == scope_filter]
        if contributing_persona_id:
            entries = [
                entry
                for entry in entries
                if contributing_persona_id in entry.contributing_persona_ids
            ]
        if active_only:
            entries = [entry for entry in entries if entry.is_active]
        return sorted(entries, key=_entry_sort_key, reverse=True)

    def find_by_source_event(
        self,
        *,
        source_event_type: str,
        source_event_id: str,
        write_authority: Optional[str] = None,
        active_only: bool = False,
    ) -> List[InstitutionalMemoryEntry]:
        entries = self.list(active_only=active_only)
        matches = [
            entry
            for entry in entries
            if entry.source_event_type == source_event_type
            and entry.source_event_id == source_event_id
            and (write_authority is None or entry.write_authority == write_authority)
        ]
        return sorted(matches, key=lambda entry: entry.entry_id)

    def mark_reused(self, entry_id: str, count: int = 1) -> InstitutionalMemoryEntry:
        if count <= 0:
            raise InstitutionalMemoryError("count must be >= 1")
        with self._lock:
            entry = self._require_unlocked(entry_id)
            updated = InstitutionalMemoryEntry(
                **{**entry.to_dict(), "reuse_count": entry.reuse_count + count}
            )
            self._entries[entry_id] = updated
            self._save()
            return updated

    def supersede(self, entry_id: str, replacement_entry_id: str) -> InstitutionalMemoryEntry:
        with self._lock:
            entry = self._require_unlocked(entry_id)
            updated = InstitutionalMemoryEntry(
                **{**entry.to_dict(), "superseded_by": replacement_entry_id}
            )
            self._entries[entry_id] = updated
            self._save()
            return updated

    def archive_expired(
        self,
        *,
        now: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> List[InstitutionalMemoryEntry]:
        policy = InstitutionalRetentionPolicy.from_env()
        archive_time = _parse_utc_timestamp(now) if now else datetime.now(timezone.utc)
        archived: List[InstitutionalMemoryEntry] = []
        with self._lock:
            for entry_id, entry in list(self._entries.items()):
                if not entry.is_active or not entry.is_expired(now=archive_time):
                    continue
                updated = InstitutionalMemoryEntry(
                    **{
                        **entry.to_dict(),
                        "archived_at": _format_utc_timestamp(archive_time),
                        "archived_reason": reason or policy.archive_reason,
                    }
                )
                self._entries[entry_id] = updated
                archived.append(updated)
            if archived:
                self._save()
        return archived

    def retrieve(
        self,
        *,
        query: str = "",
        knowledge_type: Optional[str] = None,
        scope: Optional[str] = None,
        scope_filter: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        limit: int = 10,
    ) -> List[RetrievalHit]:
        if limit <= 0:
            return []
        normalized_tags = {tag.strip().lower() for tag in (tags or []) if str(tag).strip()}
        query_terms = {term.strip().lower() for term in query.split() if term.strip()}

        hits: List[RetrievalHit] = []
        for entry in self.list(
            knowledge_type=knowledge_type,
            scope=scope,
            scope_filter=scope_filter,
            active_only=True,
        ):
            haystack_parts = [
                entry.content.get("headline", ""),
                entry.content.get("body", ""),
                " ".join(entry.content.get("tags", []) or []),
                " ".join(entry.contributing_persona_ids),
            ]
            haystack = " ".join(haystack_parts).lower()
            matched_terms = sum(1 for term in query_terms if term in haystack)
            entry_tags = {tag.strip().lower() for tag in (entry.content.get("tags", []) or []) if str(tag).strip()}
            matched_tags = len(normalized_tags.intersection(entry_tags))
            if query_terms or normalized_tags:
                if matched_terms == 0 and matched_tags == 0:
                    continue
            score = float(matched_terms * 10 + matched_tags * 5 + entry.reuse_count)
            hits.append(RetrievalHit(entry=entry, relevance_score=score))

        hits.sort(
            key=lambda hit: (
                hit.relevance_score,
                hit.entry.reuse_count,
                _parse_utc_timestamp(hit.entry.written_at),
            ),
            reverse=True,
        )
        return hits[:limit]

    def _save(self) -> None:
        if not self._path:
            return
        records = [entry.to_dict() for entry in self._entries.values()]
        self._path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise InstitutionalMemoryError(f"Expected JSON array in {path}")
        for record in data:
            json_errors = validate_institutional_memory_json(record)
            if json_errors:
                entry_id = record.get("entry_id", "<unknown>") if isinstance(record, dict) else "<unknown>"
                raise InstitutionalMemoryError(
                    f"Schema validation failed for persisted entry {entry_id}: {json_errors}"
                )
            entry = InstitutionalMemoryEntry.from_dict(record)
            errors = validate_institutional_memory(entry)
            if errors:
                raise InstitutionalMemoryError(f"Invalid persisted entry {entry.entry_id}: {errors}")
            self._entries[entry.entry_id] = entry


class PostgresInstitutionalMemoryStore(InstitutionalMemoryStore):
    """Postgres owner store for institutional memory entries."""

    def __init__(
        self,
        *,
        dsn: str,
        table: str = "memory.institutional_memory_entries",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="memory-svc",
            bootstrap=bootstrap,
        )
        super().__init__(path=None)
        self._refresh_from_postgres()

    def create(self, entry: InstitutionalMemoryEntry) -> InstitutionalMemoryEntry:
        self._refresh_from_postgres()
        return super().create(entry)

    def get(self, entry_id: str) -> Optional[InstitutionalMemoryEntry]:
        self._refresh_from_postgres()
        return super().get(entry_id)

    def require(self, entry_id: str) -> InstitutionalMemoryEntry:
        self._refresh_from_postgres()
        return super().require(entry_id)

    def list(
        self,
        *,
        knowledge_type: Optional[str] = None,
        scope: Optional[str] = None,
        scope_filter: Optional[str] = None,
        contributing_persona_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[InstitutionalMemoryEntry]:
        self._refresh_from_postgres()
        return super().list(
            knowledge_type=knowledge_type,
            scope=scope,
            scope_filter=scope_filter,
            contributing_persona_id=contributing_persona_id,
            active_only=active_only,
        )

    def mark_reused(self, entry_id: str, count: int = 1) -> InstitutionalMemoryEntry:
        self._refresh_from_postgres()
        return super().mark_reused(entry_id, count=count)

    def supersede(self, entry_id: str, replacement_entry_id: str) -> InstitutionalMemoryEntry:
        self._refresh_from_postgres()
        return super().supersede(entry_id, replacement_entry_id)

    def _save(self) -> None:
        for entry in self._entries.values():
            self._records.put(entry.entry_id, entry.to_dict())

    def _refresh_from_postgres(self) -> None:
        entries: Dict[str, InstitutionalMemoryEntry] = {}
        for record in self._records.list_all():
            json_errors = validate_institutional_memory_json(record)
            if json_errors:
                entry_id = record.get("entry_id", "<unknown>")
                raise InstitutionalMemoryError(
                    f"Schema validation failed for persisted entry {entry_id}: {json_errors}"
                )
            entry = InstitutionalMemoryEntry.from_dict(record)
            errors = validate_institutional_memory(entry)
            if errors:
                raise InstitutionalMemoryError(f"Invalid persisted entry {entry.entry_id}: {errors}")
            entries[entry.entry_id] = entry
        self._entries = entries


def build_institutional_memory_store(path: Path) -> InstitutionalMemoryStore:
    backend = os.getenv("PANTHEON_MEMORY_STORE_BACKEND", "json").strip().lower()
    if backend in ("", "json"):
        return InstitutionalMemoryStore(path=path)
    if backend != "postgres":
        raise ValueError("PANTHEON_MEMORY_STORE_BACKEND must be json or postgres")
    dsn = os.getenv("PANTHEON_MEMORY_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("PANTHEON_MEMORY_STORE_DSN or DATABASE_URL is required for Postgres memory store")
    bootstrap = os.getenv("PANTHEON_MEMORY_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    table = os.getenv("PANTHEON_MEMORY_STORE_TABLE", "memory.institutional_memory_entries")
    return PostgresInstitutionalMemoryStore(dsn=dsn, table=table, bootstrap=bootstrap)


_default_store: Optional[InstitutionalMemoryStore] = None
_store_lock = threading.Lock()


def get_store() -> InstitutionalMemoryStore:
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = InstitutionalMemoryStore()
        return _default_store


def reset_store() -> None:
    global _default_store
    with _store_lock:
        _default_store = None
