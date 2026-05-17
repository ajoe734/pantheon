"""Trainer commit lineage edges for persona policy artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


SCHEMA_VERSION = "trainer_policy_lineage_edge.v1"
SEMANTIC_EDGE_ID = "persona_policy_artifact.trainer_commit"
SOURCE_TYPE = "trainer_session"
TARGET_TYPE = "persona_policy_artifact"
DEFAULT_STORE_FILENAME = "policy_lineage_edges.jsonl"


class PolicyLineageError(ValueError):
    """Raised when a trainer commit lineage edge cannot be recorded."""


class UnknownPersonaPolicyError(PolicyLineageError):
    """Raised when no persona policy artifact can be resolved for a persona."""


class LineageReadStore(Protocol):
    """Minimal append surface needed by the lineage-read store."""

    def append_edge(self, edge: Mapping[str, Any]) -> Mapping[str, Any]:
        """Persist one normalized lineage edge and return the stored record."""


class PersonaPolicyResolver(Protocol):
    """Resolves the persona policy artifact that a trainer commit mutates."""

    def resolve_persona_policy_artifact(self, persona_id: str, session_id: str) -> str | None:
        """Return the persona policy artifact id for a persona/session pair."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(payload), ensure_ascii=True, sort_keys=True))


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PolicyLineageError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def _normalize_event_ids(event_ids: Sequence[str] | Any) -> tuple[str, ...]:
    if isinstance(event_ids, (str, bytes)) or not isinstance(event_ids, Sequence):
        raise PolicyLineageError("event_ids must be a non-empty sequence")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in event_ids:
        event_id = _require_text(item, "event_ids[]")
        if event_id in seen:
            raise PolicyLineageError(f"duplicate teaching event id: {event_id}")
        seen.add(event_id)
        normalized.append(event_id)

    if not normalized:
        raise PolicyLineageError("event_ids must be a non-empty sequence")
    return tuple(normalized)


def _edge_id(session_id: str, target_id: str, event_id: str) -> str:
    digest = sha256(f"{session_id}\0{target_id}\0{event_id}".encode("utf-8")).hexdigest()[:16]
    return f"lin-trainer-commit-{digest}"


def default_lineage_store_path(data_dir: str | Path | None = None) -> Path:
    explicit = os.getenv("TRAINING_SESSION_LINEAGE_READ_STORE_PATH")
    if explicit:
        return Path(explicit)
    root = Path(data_dir or os.getenv("TRAINING_SESSION_DATA_DIR", "/tmp/pantheon/training-session"))
    return root / DEFAULT_STORE_FILENAME


@dataclass(frozen=True)
class ConventionalPersonaPolicyResolver:
    """Resolves policy artifacts using the TRN-004 persona policy ref convention."""

    def resolve_persona_policy_artifact(self, persona_id: str, session_id: str) -> str | None:
        if not persona_id:
            return None
        return f"persona:{persona_id}:policy:{session_id}"


@dataclass(frozen=True)
class StaticPersonaPolicyResolver:
    """Deterministic resolver for tests and callers with a known persona map."""

    persona_policy_artifacts: Mapping[str, str]

    def resolve_persona_policy_artifact(self, persona_id: str, session_id: str) -> str | None:
        del session_id
        artifact_id = self.persona_policy_artifacts.get(persona_id)
        return str(artifact_id).strip() if artifact_id else None


class InMemoryLineageReadStore:
    """Append/replay lineage-read store used by focused contract tests."""

    def __init__(self, edges: Sequence[Mapping[str, Any]] | None = None) -> None:
        self._edges_by_id: dict[str, dict[str, Any]] = {}
        for edge in edges or ():
            self.append_edge(edge)

    def append_edge(self, edge: Mapping[str, Any]) -> dict[str, Any]:
        record = _json_copy(edge)
        edge_id = _require_text(record.get("edge_id"), "edge_id")
        existing = self._edges_by_id.get(edge_id)
        if existing is not None:
            if existing == record:
                return _json_copy(existing)
            raise PolicyLineageError(f"lineage edge {edge_id!r} already exists with different payload")
        self._edges_by_id[edge_id] = record
        return _json_copy(record)

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        record = self._edges_by_id.get(edge_id)
        return _json_copy(record) if record is not None else None

    def list_edges(self) -> list[dict[str, Any]]:
        return [_json_copy(record) for record in self._edges_by_id.values()]


class JsonlLineageReadStore(InMemoryLineageReadStore):
    """Append-only JSONL lineage-read store for local training-session commits."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__()
        self.reload()

    def reload(self) -> None:
        self._edges_by_id.clear()
        if not self.path.exists():
            return
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                record = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise PolicyLineageError(
                    f"Invalid lineage edge JSONL at {self.path}:{line_number}: {exc.msg}"
                ) from exc
            super().append_edge(record)

    def append_edge(self, edge: Mapping[str, Any]) -> dict[str, Any]:
        record = _json_copy(edge)
        edge_id = _require_text(record.get("edge_id"), "edge_id")
        existing = self.get_edge(edge_id)
        if existing is not None:
            if existing == record:
                return existing
            raise PolicyLineageError(f"lineage edge {edge_id!r} already exists with different payload")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        self._edges_by_id[edge_id] = record
        return _json_copy(record)


def build_lineage_read_store(data_dir: str | Path | None = None) -> JsonlLineageReadStore:
    return JsonlLineageReadStore(default_lineage_store_path(data_dir))


@dataclass
class PolicyLineageRecorder:
    """Records trainer commit lineage edges against a lineage-read store."""

    store: LineageReadStore
    persona_policy_resolver: PersonaPolicyResolver = ConventionalPersonaPolicyResolver()

    def record_commit(
        self,
        session_id: str,
        event_ids: Sequence[str],
        persona_id: str,
        *,
        commit_at: str | None = None,
        policy_artifact_id: str | None = None,
    ) -> list[dict[str, Any]]:
        session_id = _require_text(session_id, "session_id")
        persona_id = _require_text(persona_id, "persona_id")
        event_ids = _normalize_event_ids(event_ids)
        recorded_at = _optional_text(commit_at) or utc_now()

        target_id = _optional_text(policy_artifact_id)
        if target_id is None:
            target_id = self.persona_policy_resolver.resolve_persona_policy_artifact(persona_id, session_id)
        target_id = _optional_text(target_id)
        if target_id is None:
            raise UnknownPersonaPolicyError(f"unknown persona policy artifact for persona_id {persona_id!r}")

        written: list[dict[str, Any]] = []
        for event_id in event_ids:
            edge = {
                "schema_version": SCHEMA_VERSION,
                "edge_id": _edge_id(session_id, target_id, event_id),
                "semantic_edge_id": SEMANTIC_EDGE_ID,
                "edge_type": SEMANTIC_EDGE_ID,
                "source": SOURCE_TYPE,
                "source_type": SOURCE_TYPE,
                "source_id": session_id,
                "source_ref": {"type": SOURCE_TYPE, "id": session_id},
                "producer": session_id,
                "producer_type": SOURCE_TYPE,
                "producer_id": session_id,
                "target": TARGET_TYPE,
                "target_type": TARGET_TYPE,
                "target_id": target_id,
                "target_ref": {"type": TARGET_TYPE, "id": target_id},
                "persona_id": persona_id,
                "session_id": session_id,
                "teaching_event_id": event_id,
                "teaching_event_ids": list(event_ids),
                "commit_at": recorded_at,
                "recorded_at": recorded_at,
            }
            written.append(_json_copy(self.store.append_edge(edge)))
        return written


def record_commit(
    session_id: str,
    event_ids: Sequence[str],
    persona_id: str,
    *,
    store: LineageReadStore | None = None,
    persona_policy_resolver: PersonaPolicyResolver | None = None,
    commit_at: str | None = None,
    policy_artifact_id: str | None = None,
) -> list[dict[str, Any]]:
    """Record trainer commit lineage edges for one persona policy artifact."""

    recorder = PolicyLineageRecorder(
        store=store or build_lineage_read_store(),
        persona_policy_resolver=persona_policy_resolver or ConventionalPersonaPolicyResolver(),
    )
    return recorder.record_commit(
        session_id,
        event_ids,
        persona_id,
        commit_at=commit_at,
        policy_artifact_id=policy_artifact_id,
    )
