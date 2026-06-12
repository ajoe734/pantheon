"""IDS-008 — Audit event emitter for the interaction-derived seed pipeline.

Every pipeline step (record / redact / classify / negative_match / candidate /
accept / reject) emits one ``IDSAuditEvent``.  The events are written to a
JSONL store and carry enough lineage to replay or query the full pipeline
trace for any seed candidate.

Privacy guarantee: raw prompt / transcript content never appears in an audit
event.  The audit records only references (``raw_ref``, ``interaction_id``,
``seed_id``) and structured outcome metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.source_ingestion.registry.jsonl_store import JsonlRegistryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short_id(parts: Sequence[str]) -> str:
    encoded = json.dumps(list(parts), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


class IDSAuditStep(str, Enum):
    RECORD = "record"
    REDACT = "redact"
    CLASSIFY = "classify"
    NEGATIVE_MATCH = "negative_match"
    CANDIDATE = "candidate"
    ACCEPT = "accept"
    REJECT = "reject"


class IDSAuditOutcome(str, Enum):
    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"
    WARNING = "warning"
    ARCHIVE_ONLY = "archive_only"


_RAW_CONTENT_KEYS = frozenset(
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
        "teaching_log",
        "dialogue",
        "conversation",
    }
)


def _sanitize_details(value: Any, *, path: str = "details") -> Any:
    """Recursively strip keys that could inline raw prompt/transcript content."""
    if isinstance(value, Mapping):
        return {
            k: _sanitize_details(v, path=f"{path}.{k}")
            for k, v in value.items()
            if str(k) not in _RAW_CONTENT_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_details(item, path=f"{path}[]") for item in value]
    return value


@dataclass(frozen=True)
class IDSAuditEvent:
    """One audit event for a single step in the IDS pipeline.

    ``pipeline_id`` ties together all audit events that belong to a single
    extraction run (e.g. one ``TrainerSeedBridge.ingest_event`` call).
    """

    event_id: str
    step: IDSAuditStep
    pipeline_id: str
    source_surface: str
    interaction_id: str | None
    seed_id: str | None
    outcome: IDSAuditOutcome
    details: Mapping[str, Any]
    timestamp: str
    actor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_kind": "ids_audit_event",
            "schema_version": "ids_audit_event.v1",
            "event_id": self.event_id,
            "step": self.step.value,
            "pipeline_id": self.pipeline_id,
            "source_surface": self.source_surface,
            "interaction_id": self.interaction_id,
            "seed_id": self.seed_id,
            "outcome": self.outcome.value,
            "details": dict(self.details),
            "timestamp": self.timestamp,
            "actor": self.actor,
        }


class IDSAuditEventStore:
    """JSONL-backed append-only store for IDS audit events."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path:
            resolved = Path(path)
        else:
            data_dir = Path(os.environ.get("SOURCE_INGEST_DATA_DIR", "data/source_ingestion"))
            resolved = Path(
                os.environ.get(
                    "IDS_AUDIT_EVENT_STORE_PATH",
                    str(data_dir / "ids_audit_events.jsonl"),
                )
            )
        self._store = JsonlRegistryStore(resolved, id_field="event_id")

    @property
    def path(self) -> Path:
        return self._store.path

    def append(self, event: IDSAuditEvent) -> None:
        self._store.upsert(event.to_dict())

    def list_by_pipeline(self, pipeline_id: str) -> list[IDSAuditEvent]:
        return [
            _event_from_dict(r)
            for r in self._store.read_all()
            if r.get("pipeline_id") == pipeline_id
        ]

    def list_by_interaction(self, interaction_id: str) -> list[IDSAuditEvent]:
        return [
            _event_from_dict(r)
            for r in self._store.read_all()
            if r.get("interaction_id") == interaction_id
        ]

    def list_by_seed(self, seed_id: str) -> list[IDSAuditEvent]:
        return [
            _event_from_dict(r)
            for r in self._store.read_all()
            if r.get("seed_id") == seed_id
        ]

    def list_all(self) -> list[IDSAuditEvent]:
        return [_event_from_dict(r) for r in self._store.read_all()]


def _event_from_dict(data: Mapping[str, Any]) -> IDSAuditEvent:
    return IDSAuditEvent(
        event_id=str(data.get("event_id") or ""),
        step=IDSAuditStep(str(data.get("step") or IDSAuditStep.RECORD.value)),
        pipeline_id=str(data.get("pipeline_id") or ""),
        source_surface=str(data.get("source_surface") or ""),
        interaction_id=data.get("interaction_id"),
        seed_id=data.get("seed_id"),
        outcome=IDSAuditOutcome(str(data.get("outcome") or IDSAuditOutcome.SUCCESS.value)),
        details=dict(data.get("details") or {}),
        timestamp=str(data.get("timestamp") or ""),
        actor=data.get("actor"),
    )


class IDSAuditEmitter:
    """Convenience emitter: builds ``IDSAuditEvent`` objects and appends them to a store.

    Pass ``None`` as the store to produce a no-op emitter (used when audit is
    not configured for a bridge instance).
    """

    def __init__(self, store: IDSAuditEventStore | None, *, pipeline_id: str) -> None:
        self._store = store
        self._pipeline_id = pipeline_id

    def emit(
        self,
        step: IDSAuditStep,
        *,
        source_surface: str,
        outcome: IDSAuditOutcome,
        interaction_id: str | None = None,
        seed_id: str | None = None,
        actor: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> IDSAuditEvent | None:
        if self._store is None:
            return None
        safe_details = _sanitize_details(dict(details or {}))
        event_id = f"ids-audit-{step.value}-{_short_id([self._pipeline_id, step.value, interaction_id or '', seed_id or ''])}"
        event = IDSAuditEvent(
            event_id=event_id,
            step=step,
            pipeline_id=self._pipeline_id,
            source_surface=source_surface,
            interaction_id=interaction_id,
            seed_id=seed_id,
            outcome=outcome,
            details=safe_details,
            timestamp=_utc_now(),
            actor=actor,
        )
        self._store.append(event)
        return event


def make_pipeline_id(surface: str, session_id: str, event_id: str) -> str:
    """Stable pipeline_id for one extraction run."""
    return f"ids-pipeline-{_short_id([surface, session_id, event_id])}"


__all__ = [
    "IDSAuditEmitter",
    "IDSAuditEvent",
    "IDSAuditEventStore",
    "IDSAuditOutcome",
    "IDSAuditStep",
    "make_pipeline_id",
]
