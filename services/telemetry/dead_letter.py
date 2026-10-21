"""
Dead-letter queue for telemetry ingest.

TEL-002: Events that fail validation, exceed retry limits, or are poisoned
are routed here with diagnostic tags for incident investigation and potential replay.

Per TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §8.2:
- transient DB/network errors: retry with exponential backoff (handled by batch_writer)
- poison events: enter dead-letter queue
- malformed schema: reject + audit + incident threshold accumulation
"""

from __future__ import annotations

from collections import deque
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Diagnostic tag vocabulary
TAG_SCHEMA_VIOLATION = "schema_violation"
TAG_BINDING_MISMATCH = "binding_mismatch"
TAG_TEMPORAL_VIOLATION = "temporal_violation"
TAG_BUFFER_OVERFLOW = "buffer_overflow"
TAG_RETRY_EXHAUSTED = "retry_exhausted"
TAG_POISON_EVENT = "poison_event"
TAG_WRITER_ERROR = "writer_error"


class DeadLetterEntry:
    """A single dead-letter entry with diagnostic metadata."""

    __slots__ = (
        "event",
        "tags",
        "reason",
        "rejected_at",
        "retry_count",
        "original_attempt_count",
    )

    def __init__(
        self,
        event: dict[str, Any],
        tags: list[str],
        reason: str,
        retry_count: int = 0,
        original_attempt_count: int = 0,
    ):
        self.event = event
        self.tags = tags
        self.reason = reason
        self.rejected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.retry_count = retry_count
        self.original_attempt_count = original_attempt_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "tags": self.tags,
            "reason": self.reason,
            "rejected_at": self.rejected_at,
            "retry_count": self.retry_count,
            "original_attempt_count": self.original_attempt_count,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


class DeadLetterQueue:
    """
    Dead-letter queue with JSONL persistence and diagnostic tagging.

    Features:
    - In-memory buffer for fast access
    - JSONL file persistence for crash recovery
    - Diagnostic tags for incident categorization
    - Replay support (re-inject entries back into the buffer)
    - Threshold monitoring (alerts when rejection rate exceeds configured limit)
    """

    DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600.0

    def __init__(
        self,
        spill_path: Optional[str] = None,
        max_memory_entries: int = 10_000,
        incident_threshold: int = 100,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        replayable_tags: tuple[str, ...] = (TAG_WRITER_ERROR, TAG_RETRY_EXHAUSTED),
    ):
        """
        Parameters
        ----------
        spill_path : str, optional
            Path to JSONL file for persistent dead-letter storage.
        max_memory_entries : int
            Max entries kept in memory. Older entries are still written to
            spill_path but evicted from memory.
        incident_threshold : int
            Number of dead-letter entries that triggers an incident warning.
        retention_seconds : float
            Age cap for spill entries. Entries carrying any of
            ``replayable_tags`` are exempt, because startup replay may still
            retry them.
        replayable_tags : tuple[str, ...]
            Tags whose entries are kept regardless of age.
        """
        self._entries: list[DeadLetterEntry] = []
        self._spill_path = Path(spill_path) if spill_path else None
        self._max_memory_entries = max_memory_entries
        self._incident_threshold = incident_threshold
        self._total_rejected = 0
        self._incident_fired = False
        self._retention_seconds = max(1.0, float(retention_seconds))
        self._replayable_tags = frozenset(replayable_tags)
        self._spill_lock = threading.Lock()
        self._spill_expired_dropped = 0

        if self._spill_path:
            self._spill_path.parent.mkdir(parents=True, exist_ok=True)

    def reject(
        self,
        event: dict[str, Any],
        tags: list[str],
        reason: str,
        retry_count: int = 0,
        original_attempt_count: int = 0,
    ) -> bool:
        """
        Route an event to the dead-letter queue with diagnostic tags.

        Parameters
        ----------
        event : dict
            The failed event envelope.
        tags : list[str]
            Diagnostic tags (e.g., TAG_SCHEMA_VIOLATION, TAG_RETRY_EXHAUSTED).
        reason : str
            Human-readable rejection reason.
        retry_count : int
            Number of retries attempted before giving up.
        original_attempt_count : int
            Original write attempt count (for batch writer context).
        """
        entry = DeadLetterEntry(
            event=event,
            tags=tags,
            reason=reason,
            retry_count=retry_count,
            original_attempt_count=original_attempt_count,
        )
        self._entries.append(entry)
        self._total_rejected += 1

        # Evict old entries from memory if over limit
        if len(self._entries) > self._max_memory_entries:
            self._entries = self._entries[-self._max_memory_entries:]

        # Persist to spill file. A durable buffer may be acknowledged only
        # after this append is fsynced, otherwise a writer failure followed by
        # process death could lose both the broker receipt and its replay copy.
        persisted = False
        if self._spill_path:
            try:
                with self._spill_lock, open(self._spill_path, "a") as f:
                    f.write(entry.to_json() + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                persisted = True
            except Exception as e:
                log.error(f"DeadLetterQueue spill write failed: {e}")

        # Incident threshold check
        if self._total_rejected >= self._incident_threshold and not self._incident_fired:
            self._incident_fired = True
            log.error(
                f"DEAD_LETTER INCIDENT THRESHOLD REACHED: "
                f"{self._total_rejected} rejections. "
                f"Check {self._spill_path} for details."
            )

        event_id = event.get("event_id", "unknown")
        log.warning(
            f"DeadLetterQueue: event {event_id} rejected. "
            f"tags={tags}, reason={reason}, total_rejected={self._total_rejected}"
        )
        return persisted

    def get_entries(
        self,
        tag_filter: Optional[str] = None,
        limit: int = 100,
    ) -> list[DeadLetterEntry]:
        """
        Get dead-letter entries, optionally filtered by tag.

        Parameters
        ----------
        tag_filter : str, optional
            If provided, only return entries containing this tag.
        limit : int
            Maximum number of entries to return (most recent first).

        Returns
        -------
        list[DeadLetterEntry]
        """
        entries = self._entries
        if tag_filter:
            entries = [e for e in entries if tag_filter in e.tags]
        return entries[-limit:]

    def get_entries_as_dicts(
        self,
        tag_filter: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get dead-letter entries as dictionaries."""
        return [e.to_dict() for e in self.get_entries(tag_filter=tag_filter, limit=limit)]

    def replay_entries(self, tag_filter: Optional[str] = None) -> list[dict[str, Any]]:
        """
        Get events suitable for replay back into the ingest buffer.
        Returns the raw event dicts (not the full DLQ entry).
        """
        entries = self.get_entries(tag_filter=tag_filter, limit=10_000)
        return [e.event for e in entries if e.event is not None]

    def _spill_line_expired(self, line: str, cutoff: datetime) -> bool:
        """Whether a spill line is past retention and not replayable."""
        try:
            data = json.loads(line)
        except ValueError:
            return True
        if not isinstance(data, dict):
            return True
        tags = data.get("tags")
        if isinstance(tags, list) and self._replayable_tags.intersection(tags):
            return False
        try:
            rejected = datetime.fromisoformat(str(data.get("rejected_at", "")).replace("Z", "+00:00"))
        except ValueError:
            return True
        if rejected.tzinfo is None:
            rejected = rejected.replace(tzinfo=timezone.utc)
        return rejected < cutoff

    def compact_spill(self) -> int:
        """Drop spill entries past retention, keeping every replayable one.

        Streams the file (it may be far larger than memory) into a temp file and
        replaces the spill atomically. Returns the number of entries dropped.
        """
        if not self._spill_path or not self._spill_path.exists():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._retention_seconds)
        tmp_path = self._spill_path.with_name(self._spill_path.name + ".compact.tmp")
        dropped = 0
        kept = 0
        with self._spill_lock:
            try:
                with open(self._spill_path, "r") as src, open(tmp_path, "w") as dst:
                    for raw in src:
                        line = raw.strip()
                        if not line:
                            continue
                        if self._spill_line_expired(line, cutoff):
                            dropped += 1
                            continue
                        dst.write(line + "\n")
                        kept += 1
                    dst.flush()
                    os.fsync(dst.fileno())
                if dropped:
                    os.replace(tmp_path, self._spill_path)
            except Exception as e:
                log.error(f"DeadLetterQueue.compact_spill failed: {e}")
                return 0
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()
        self._spill_expired_dropped += dropped
        if dropped:
            log.info(
                "DeadLetterQueue spill retention dropped %s expired entries, kept %s",
                dropped,
                kept,
            )
        return dropped

    def load_from_spill(self) -> int:
        """
        Load dead-letter entries from the spill file into memory.

        The spill is first compacted to its retention window, so only retained
        entries are read. Returns the number of entries loaded.
        """
        if not self._spill_path or not self._spill_path.exists():
            return 0
        self.compact_spill()

        observed_count = 0
        retained_lines: deque[str] = deque(maxlen=self._max_memory_entries)
        try:
            with open(self._spill_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    observed_count += 1
                    retained_lines.append(line)
        except Exception as e:
            log.error(f"DeadLetterQueue.load_from_spill failed: {e}")
            return 0

        invalid_retained_count = 0
        retained_count = 0
        for line in retained_lines:
            try:
                data = json.loads(line)
                entry = DeadLetterEntry(
                    event=data.get("event", {}),
                    tags=data.get("tags", []),
                    reason=data.get("reason", ""),
                    retry_count=data.get("retry_count", 0),
                    original_attempt_count=data.get("original_attempt_count", 0),
                )
                entry.rejected_at = data.get("rejected_at", entry.rejected_at)
                self._entries.append(entry)
                retained_count += 1
            except json.JSONDecodeError:
                invalid_retained_count += 1
                log.warning("DeadLetterQueue skipped invalid retained spill line")

        # Trim to max memory entries
        if len(self._entries) > self._max_memory_entries:
            self._entries = self._entries[-self._max_memory_entries:]
        loaded_count = max(0, observed_count - invalid_retained_count)
        self._total_rejected += loaded_count
        if self._total_rejected >= self._incident_threshold:
            self._incident_fired = True

        log.info(
            "DeadLetterQueue observed %s persisted spill entries and retained %s in memory",
            loaded_count,
            retained_count,
        )
        return loaded_count

    def stats(self) -> dict[str, Any]:
        """Return DLQ statistics for monitoring."""
        tag_counts: dict[str, int] = {}
        for entry in self._entries:
            for tag in entry.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return {
            "memory_entries": len(self._entries),
            "total_rejected": self._total_rejected,
            "incident_threshold": self._incident_threshold,
            "incident_fired": self._incident_fired,
            "spill_path": str(self._spill_path) if self._spill_path else None,
            "tag_counts": tag_counts,
            "retention_seconds": self._retention_seconds,
            "spill_expired_dropped": self._spill_expired_dropped,
        }

    def reset_incident_flag(self) -> None:
        """Reset the incident flag (e.g., after incident resolution)."""
        self._incident_fired = False
