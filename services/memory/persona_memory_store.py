"""
Persona memory store for the Pantheon memory plane.

This module mirrors the institutional memory store shape for persona-scoped
entries:
- PersonaMemoryEntry immutable dataclass
- Semantic and JSON-schema validation helpers
- Thread-safe in-memory store with optional JSON persistence
- Retrieval scoped by persona_id with reuse-count writeback
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def _parse_utc_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone information")
    offset = parsed.utcoffset()
    if offset is None or offset != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be expressed in UTC")
    return parsed.astimezone(timezone.utc)


def _entry_sort_key(entry: "PersonaMemoryEntry") -> tuple[int, datetime]:
    return (entry.reuse_count, _parse_utc_timestamp(entry.written_at))


class PersonaMemoryError(ValueError):
    """Raised when persona memory validation or store operations fail."""


class PersonaMemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PREFERENCE = "preference"
    STRATEGY_LESSON = "strategy_lesson"
    RISK_OBSERVATION = "risk_observation"
    CONSULTATION_OUTCOME = "consultation_outcome"


class PersonaSourceEventType(str, Enum):
    SESSION_END = "session_end"
    POSTMORTEM_PUBLISHED = "postmortem_published"
    EVOLUTION_DECISION_APPROVED = "evolution_decision_approved"
    CONSULTATION_CLOSED = "consultation_closed"
    TRAINER_EPOCH_COMPLETE = "trainer_epoch_complete"
    OPERATOR_FEEDBACK = "operator_feedback"
    RESEARCH_TASK_COMPLETED = "research_task_completed"


class PersonaWriteAuthority(str, Enum):
    PERSONA_MEMORY_SVC = "persona-memory-svc"
    INCIDENT_SVC = "incident-svc"
    EVOLUTION_SVC = "evolution-svc"
    TRAINER_SVC = "trainer-svc"
    CONSULTATION_SVC = "consultation-svc"
    RESEARCH_SVC = "research-svc"


class PersonaRelevanceScope(str, Enum):
    PERSONA_PRIVATE = "persona_private"
    PERSONA_AND_COMMITTEE = "persona_and_committee"


_WRITEBACK_RULES: dict[str, set[tuple[str, str]]] = {
    PersonaWriteAuthority.INCIDENT_SVC.value: {
        (
            PersonaSourceEventType.POSTMORTEM_PUBLISHED.value,
            PersonaMemoryType.STRATEGY_LESSON.value,
        ),
    },
    PersonaWriteAuthority.EVOLUTION_SVC.value: {
        (
            PersonaSourceEventType.EVOLUTION_DECISION_APPROVED.value,
            PersonaMemoryType.STRATEGY_LESSON.value,
        ),
    },
    PersonaWriteAuthority.TRAINER_SVC.value: {
        (PersonaSourceEventType.TRAINER_EPOCH_COMPLETE.value, PersonaMemoryType.EPISODIC.value),
        (PersonaSourceEventType.TRAINER_EPOCH_COMPLETE.value, PersonaMemoryType.SEMANTIC.value),
    },
    PersonaWriteAuthority.CONSULTATION_SVC.value: {
        (
            PersonaSourceEventType.CONSULTATION_CLOSED.value,
            PersonaMemoryType.CONSULTATION_OUTCOME.value,
        ),
    },
    PersonaWriteAuthority.RESEARCH_SVC.value: {
        (
            PersonaSourceEventType.RESEARCH_TASK_COMPLETED.value,
            PersonaMemoryType.STRATEGY_LESSON.value,
        ),
    },
}


@dataclass(frozen=True)
class PersonaMemoryEntry:
    """Canonical persona-scoped memory object."""

    memory_id: str
    persona_id: str
    memory_type: str
    content: Dict[str, Any]
    source_event_type: str
    source_event_id: str
    written_at: str
    write_authority: str

    embedding_ref: Optional[str] = None
    superseded_by: Optional[str] = None
    relevance_scope: str = PersonaRelevanceScope.PERSONA_PRIVATE.value
    reuse_count: int = 0

    def __post_init__(self) -> None:
        try:
            PersonaMemoryType(self.memory_type)
        except ValueError as exc:
            raise PersonaMemoryError(
                f"Invalid memory_type: {self.memory_type!r}. "
                f"Must be one of {[e.value for e in PersonaMemoryType]}."
            ) from exc
        try:
            PersonaSourceEventType(self.source_event_type)
        except ValueError as exc:
            raise PersonaMemoryError(
                f"Invalid source_event_type: {self.source_event_type!r}. "
                f"Must be one of {[e.value for e in PersonaSourceEventType]}."
            ) from exc
        try:
            PersonaWriteAuthority(self.write_authority)
        except ValueError as exc:
            raise PersonaMemoryError(
                f"Invalid write_authority: {self.write_authority!r}. "
                f"Must be one of {[e.value for e in PersonaWriteAuthority]}."
            ) from exc
        try:
            PersonaRelevanceScope(self.relevance_scope)
        except ValueError as exc:
            raise PersonaMemoryError(
                f"Invalid relevance_scope: {self.relevance_scope!r}. "
                f"Must be one of {[e.value for e in PersonaRelevanceScope]}."
            ) from exc
        if self.reuse_count < 0:
            raise PersonaMemoryError("reuse_count must be >= 0")
        if not isinstance(self.content, dict):
            raise PersonaMemoryError("content must be an object")
        summary = self.content.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise PersonaMemoryError("content.summary must be a non-empty string")

    @property
    def is_active(self) -> bool:
        return not bool(self.superseded_by)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return {k: v for k, v in payload.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonaMemoryEntry":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, payload: str) -> "PersonaMemoryEntry":
        return cls.from_dict(json.loads(payload))


def validate_persona_memory(entry: PersonaMemoryEntry) -> List[str]:
    """Return semantic validation errors; empty list means valid."""
    errors: List[str] = []

    if not entry.memory_id:
        errors.append("memory_id must not be empty")
    if not entry.persona_id:
        errors.append("persona_id must not be empty")
    if not entry.source_event_id:
        errors.append("source_event_id must not be empty")
    try:
        _parse_utc_timestamp(entry.written_at)
    except ValueError:
        errors.append("written_at must be an ISO-8601 UTC timestamp")

    if entry.write_authority != PersonaWriteAuthority.PERSONA_MEMORY_SVC.value:
        allowed = _WRITEBACK_RULES.get(entry.write_authority, set())
        writeback_pair = (entry.source_event_type, entry.memory_type)
        if writeback_pair not in allowed:
            errors.append(
                "write_authority is not allowed to write this "
                "source_event_type/memory_type pair"
            )

    return errors


def validate_persona_memory_json(data: Dict[str, Any]) -> List[str]:
    """Validate raw dict against the canonical PersonaMemory JSON schema."""
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []

    schema_path = Path(__file__).parent / "persona_memory.schema.json"
    if not schema_path.exists():
        return [f"Schema file not found: {schema_path}"]

    schema = json.loads(schema_path.read_text())
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        errors.append(f"{list(err.path)}: {err.message}")
    return errors


@dataclass(frozen=True)
class PersonaRetrievalHit:
    """Retrieved persona memory plus ranking score."""

    entry: PersonaMemoryEntry
    relevance_score: float


class PersonaMemoryStore:
    """Thread-safe persona memory store with optional JSON persistence."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, PersonaMemoryEntry] = {}
        self._path = path
        if path and path.exists():
            self._load(path)

    def create(self, entry: PersonaMemoryEntry) -> PersonaMemoryEntry:
        errors = validate_persona_memory(entry)
        if errors:
            raise PersonaMemoryError(f"Invalid persona memory entry: {errors}")
        json_errors = validate_persona_memory_json(entry.to_dict())
        if json_errors:
            raise PersonaMemoryError(f"Schema validation failed: {json_errors}")
        with self._lock:
            if entry.memory_id in self._entries:
                raise PersonaMemoryError(f"Entry already exists: {entry.memory_id}")
            self._entries[entry.memory_id] = entry
            self._save()
            return entry

    def get(self, memory_id: str) -> Optional[PersonaMemoryEntry]:
        with self._lock:
            return self._entries.get(memory_id)

    def _require_unlocked(self, memory_id: str) -> PersonaMemoryEntry:
        entry = self._entries.get(memory_id)
        if entry is None:
            raise PersonaMemoryError(f"Entry not found: {memory_id}")
        return entry

    def require(self, memory_id: str) -> PersonaMemoryEntry:
        with self._lock:
            return self._require_unlocked(memory_id)

    def list(
        self,
        *,
        persona_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        relevance_scope: Optional[str] = None,
        active_only: bool = True,
    ) -> List[PersonaMemoryEntry]:
        with self._lock:
            entries = list(self._entries.values())

        if persona_id:
            entries = [entry for entry in entries if entry.persona_id == persona_id]
        if memory_type:
            entries = [entry for entry in entries if entry.memory_type == memory_type]
        if relevance_scope:
            entries = [entry for entry in entries if entry.relevance_scope == relevance_scope]
        if active_only:
            entries = [entry for entry in entries if entry.is_active]
        return sorted(entries, key=_entry_sort_key, reverse=True)

    def mark_reused(self, memory_id: str, count: int = 1) -> PersonaMemoryEntry:
        if count <= 0:
            raise PersonaMemoryError("count must be >= 1")
        with self._lock:
            entry = self._require_unlocked(memory_id)
            updated = PersonaMemoryEntry(
                **{**entry.to_dict(), "reuse_count": entry.reuse_count + count}
            )
            self._entries[memory_id] = updated
            self._save()
            return updated

    def supersede(self, memory_id: str, replacement_memory_id: str) -> PersonaMemoryEntry:
        with self._lock:
            entry = self._require_unlocked(memory_id)
            updated = PersonaMemoryEntry(
                **{**entry.to_dict(), "superseded_by": replacement_memory_id}
            )
            self._entries[memory_id] = updated
            self._save()
            return updated

    def retrieve(
        self,
        *,
        persona_id: str,
        query: str = "",
        memory_type: Optional[str] = None,
        relevance_scope: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        limit: int = 10,
    ) -> List[PersonaRetrievalHit]:
        if limit <= 0:
            return []
        if not persona_id:
            raise PersonaMemoryError("persona_id is required for persona memory retrieval")
        normalized_tags = {tag.strip().lower() for tag in (tags or []) if str(tag).strip()}
        query_terms = {term.strip().lower() for term in query.split() if term.strip()}

        hits: List[PersonaRetrievalHit] = []
        for entry in self.list(
            persona_id=persona_id,
            memory_type=memory_type,
            relevance_scope=relevance_scope,
            active_only=True,
        ):
            haystack_parts = [
                entry.content.get("summary", ""),
                " ".join(entry.content.get("tags", []) or []),
                entry.source_event_type,
                entry.source_event_id,
            ]
            haystack = " ".join(haystack_parts).lower()
            matched_terms = sum(1 for term in query_terms if term in haystack)
            entry_tags = {tag.strip().lower() for tag in (entry.content.get("tags", []) or []) if str(tag).strip()}
            matched_tags = len(normalized_tags.intersection(entry_tags))
            if query_terms or normalized_tags:
                if matched_terms == 0 and matched_tags == 0:
                    continue
            score = float(matched_terms * 10 + matched_tags * 5 + entry.reuse_count)
            hits.append(PersonaRetrievalHit(entry=entry, relevance_score=score))

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
            raise PersonaMemoryError(f"Expected JSON array in {path}")
        for record in data:
            json_errors = validate_persona_memory_json(record)
            if json_errors:
                memory_id = record.get("memory_id", "<unknown>") if isinstance(record, dict) else "<unknown>"
                raise PersonaMemoryError(
                    f"Schema validation failed for persisted entry {memory_id}: {json_errors}"
                )
            entry = PersonaMemoryEntry.from_dict(record)
            errors = validate_persona_memory(entry)
            if errors:
                raise PersonaMemoryError(f"Invalid persisted entry {entry.memory_id}: {errors}")
            self._entries[entry.memory_id] = entry


class PostgresPersonaMemoryStore(PersonaMemoryStore):
    """Postgres owner store for persona memory entries."""

    def __init__(
        self,
        *,
        dsn: str,
        table: str = "memory.persona_memory_entries",
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

    def create(self, entry: PersonaMemoryEntry) -> PersonaMemoryEntry:
        self._refresh_from_postgres()
        return super().create(entry)

    def get(self, memory_id: str) -> Optional[PersonaMemoryEntry]:
        self._refresh_from_postgres()
        return super().get(memory_id)

    def require(self, memory_id: str) -> PersonaMemoryEntry:
        self._refresh_from_postgres()
        return super().require(memory_id)

    def list(
        self,
        *,
        persona_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        relevance_scope: Optional[str] = None,
        active_only: bool = True,
    ) -> List[PersonaMemoryEntry]:
        self._refresh_from_postgres()
        return super().list(
            persona_id=persona_id,
            memory_type=memory_type,
            relevance_scope=relevance_scope,
            active_only=active_only,
        )

    def mark_reused(self, memory_id: str, count: int = 1) -> PersonaMemoryEntry:
        self._refresh_from_postgres()
        return super().mark_reused(memory_id, count=count)

    def supersede(self, memory_id: str, replacement_memory_id: str) -> PersonaMemoryEntry:
        self._refresh_from_postgres()
        return super().supersede(memory_id, replacement_memory_id)

    def _save(self) -> None:
        for entry in self._entries.values():
            self._records.put(entry.memory_id, entry.to_dict())

    def _refresh_from_postgres(self) -> None:
        entries: Dict[str, PersonaMemoryEntry] = {}
        for record in self._records.list_all():
            json_errors = validate_persona_memory_json(record)
            if json_errors:
                memory_id = record.get("memory_id", "<unknown>")
                raise PersonaMemoryError(
                    f"Schema validation failed for persisted entry {memory_id}: {json_errors}"
                )
            entry = PersonaMemoryEntry.from_dict(record)
            errors = validate_persona_memory(entry)
            if errors:
                raise PersonaMemoryError(f"Invalid persisted entry {entry.memory_id}: {errors}")
            entries[entry.memory_id] = entry
        self._entries = entries


def build_persona_memory_store(path: Path) -> PersonaMemoryStore:
    backend = (
        os.getenv("PANTHEON_PERSONA_MEMORY_STORE_BACKEND")
        or os.getenv("PANTHEON_MEMORY_STORE_BACKEND")
        or "json"
    ).strip().lower()
    if backend in ("", "json"):
        return PersonaMemoryStore(path=path)
    if backend != "postgres":
        raise ValueError("PANTHEON_PERSONA_MEMORY_STORE_BACKEND must be json or postgres")
    dsn = (
        os.getenv("PANTHEON_PERSONA_MEMORY_STORE_DSN")
        or os.getenv("PANTHEON_MEMORY_STORE_DSN")
        or os.getenv("DATABASE_URL")
    )
    if not dsn:
        raise ValueError(
            "PANTHEON_PERSONA_MEMORY_STORE_DSN, PANTHEON_MEMORY_STORE_DSN, "
            "or DATABASE_URL is required for Postgres persona memory store"
        )
    bootstrap = os.getenv("PANTHEON_PERSONA_MEMORY_STORE_BOOTSTRAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    table = os.getenv("PANTHEON_PERSONA_MEMORY_STORE_TABLE", "memory.persona_memory_entries")
    return PostgresPersonaMemoryStore(dsn=dsn, table=table, bootstrap=bootstrap)
