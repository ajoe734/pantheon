"""Shared dead-letter queue primitives and JSONL persistence helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .audit import AuditAction
from .exceptions import FoundationValidationError
from .outbox import EventEnvelope
from .serialization import drop_none, ensure_utc, foundation_id

DEAD_LETTER_ENTRY_SCHEMA_VERSION = "dead_letter_entry.v1"


class DeadLetterStatus(str, Enum):
    PENDING = "pending"
    REPLAYED = "replayed"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    REPLAY_FAILED = "replay_failed"
    SCHEMA_REJECTED = "schema_rejected"


@dataclass(frozen=True)
class DeadLetterEntry:
    entry_id: str
    event: EventEnvelope
    reason: str
    tags: tuple[str, ...] | list[str]
    rejected_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    status: DeadLetterStatus | str = DeadLetterStatus.PENDING
    source_ref: str | None = None
    replay_attempts: int = 0
    last_replay_at: datetime | str | None = None
    audit_action_ref: str | None = None
    last_error: str | None = None
    schema_version: str = DEAD_LETTER_ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("entry_id", "reason", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"dead_letter.{field_name} is required")
        if not isinstance(self.event, EventEnvelope):
            raise FoundationValidationError("dead_letter.event must be an EventEnvelope")
        tags = tuple(str(tag).strip() for tag in self.tags if str(tag).strip())
        if not tags:
            raise FoundationValidationError("dead_letter.tags requires at least one tag")
        try:
            status = self.status if isinstance(self.status, DeadLetterStatus) else DeadLetterStatus(str(self.status))
        except ValueError as exc:
            allowed = ", ".join(item.value for item in DeadLetterStatus)
            raise FoundationValidationError(f"dead_letter.status must be one of: {allowed}") from exc
        if int(self.replay_attempts) < 0:
            raise FoundationValidationError("dead_letter.replay_attempts must be >= 0")
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "replay_attempts", int(self.replay_attempts))
        object.__setattr__(self, "rejected_at", ensure_utc(self.rejected_at))
        if self.last_replay_at is not None:
            object.__setattr__(self, "last_replay_at", ensure_utc(self.last_replay_at))

    @classmethod
    def new(
        cls,
        *,
        event: EventEnvelope,
        reason: str,
        tags: tuple[str, ...] | list[str],
        source_ref: str | None = None,
    ) -> "DeadLetterEntry":
        return cls(
            entry_id=foundation_id("dlq"),
            event=event,
            reason=reason,
            tags=tags,
            source_ref=source_ref,
        )

    def with_replay_result(
        self,
        *,
        status: DeadLetterStatus,
        audit_action: AuditAction,
        error: str | None = None,
    ) -> "DeadLetterEntry":
        return replace(
            self,
            status=status,
            replay_attempts=self.replay_attempts + 1,
            last_replay_at=ensure_utc(None),
            audit_action_ref=audit_action.action_id,
            last_error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "entry_id": self.entry_id,
                "event": self.event.to_dict(),
                "reason": self.reason,
                "tags": list(self.tags),
                "rejected_at": ensure_utc(self.rejected_at).isoformat().replace("+00:00", "Z"),
                "status": self.status.value,
                "source_ref": self.source_ref,
                "replay_attempts": self.replay_attempts,
                "last_replay_at": ensure_utc(self.last_replay_at).isoformat().replace("+00:00", "Z")
                if self.last_replay_at
                else None,
                "audit_action_ref": self.audit_action_ref,
                "last_error": self.last_error,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeadLetterEntry":
        return cls(
            entry_id=str(data["entry_id"]),
            event=EventEnvelope.from_dict(data["event"]),
            reason=str(data["reason"]),
            tags=tuple(data.get("tags", ())),
            rejected_at=str(data["rejected_at"]),
            status=str(data.get("status", DeadLetterStatus.PENDING.value)),
            source_ref=data.get("source_ref"),
            replay_attempts=int(data.get("replay_attempts", 0)),
            last_replay_at=data.get("last_replay_at"),
            audit_action_ref=data.get("audit_action_ref"),
            last_error=data.get("last_error"),
            schema_version=str(data.get("schema_version", DEAD_LETTER_ENTRY_SCHEMA_VERSION)),
        )


class DeadLetterQueue:
    """Dead-letter collection with optional append-only JSONL spill."""

    def __init__(self, spill_path: str | Path | None = None, entries: list[DeadLetterEntry] | None = None):
        self._entries: list[DeadLetterEntry] = list(entries or [])
        self._spill_path = Path(spill_path) if spill_path else None
        if self._spill_path:
            self._spill_path.parent.mkdir(parents=True, exist_ok=True)

    def reject(
        self,
        event: EventEnvelope,
        *,
        reason: str,
        tags: tuple[str, ...] | list[str],
        source_ref: str | None = None,
    ) -> DeadLetterEntry:
        entry = DeadLetterEntry.new(event=event, reason=reason, tags=tags, source_ref=source_ref)
        self._entries.append(entry)
        self._append_to_spill(entry)
        return entry

    def entries(self, *, status: DeadLetterStatus | str | None = None, tag_filter: str | None = None) -> list[DeadLetterEntry]:
        entries = self._entries
        if status is not None:
            effective_status = status if isinstance(status, DeadLetterStatus) else DeadLetterStatus(str(status))
            entries = [entry for entry in entries if entry.status == effective_status]
        if tag_filter:
            entries = [entry for entry in entries if tag_filter in entry.tags]
        return list(entries)

    def pending_entries(self, *, tag_filter: str | None = None) -> list[DeadLetterEntry]:
        return self.entries(status=DeadLetterStatus.PENDING, tag_filter=tag_filter)

    def replace_entry(self, updated: DeadLetterEntry) -> None:
        for index, entry in enumerate(self._entries):
            if entry.entry_id == updated.entry_id:
                self._entries[index] = updated
                self._append_to_spill(updated)
                return
        raise FoundationValidationError(f"dead-letter entry not found: {updated.entry_id}")

    def load_from_spill(self) -> int:
        if not self._spill_path or not self._spill_path.exists():
            return 0
        loaded_by_id: dict[str, DeadLetterEntry] = {}
        order: list[str] = []
        with self._spill_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entry = DeadLetterEntry.from_dict(json.loads(line))
                    if entry.entry_id not in loaded_by_id:
                        order.append(entry.entry_id)
                    loaded_by_id[entry.entry_id] = entry
        self._entries = [loaded_by_id[entry_id] for entry_id in order]
        return len(self._entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spill_path": str(self._spill_path) if self._spill_path else None,
            "entries": [entry.to_dict() for entry in self._entries],
        }

    def _append_to_spill(self, entry: DeadLetterEntry) -> None:
        if not self._spill_path:
            return
        with self._spill_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
