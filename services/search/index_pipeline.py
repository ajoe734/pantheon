"""Incremental search indexing pipeline with schema-versioned snapshots and freshness SLA."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.knowledge.evidence.repository import InMemoryEvidenceRepository

from .index_adapter import KeywordIndexAdapter, _parse_time


PIPELINE_SCHEMA_VERSION = "index_pipeline_snapshot.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class IndexPipelineSnapshot:
    """Immutable record of a single pipeline run."""

    pipeline_run_id: str
    schema_version: str
    triggered_by: str
    trigger_ref: str | None
    indexed_count: int
    incremental_count: int
    removed_count: int
    indexed_watermarks: dict[str, str | None]
    indexed_at: str
    is_full_rebuild: bool
    freshness_sla_seconds: int
    indexed_object_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pipeline_run_id": self.pipeline_run_id,
            "triggered_by": self.triggered_by,
            "trigger_ref": self.trigger_ref,
            "indexed_count": self.indexed_count,
            "incremental_count": self.incremental_count,
            "removed_count": self.removed_count,
            "indexed_watermarks": dict(self.indexed_watermarks),
            "indexed_at": self.indexed_at,
            "is_full_rebuild": self.is_full_rebuild,
            "freshness_sla_seconds": self.freshness_sla_seconds,
            "indexed_object_ids": list(self.indexed_object_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexPipelineSnapshot":
        return cls(
            pipeline_run_id=str(data.get("pipeline_run_id") or ""),
            schema_version=str(data.get("schema_version") or PIPELINE_SCHEMA_VERSION),
            triggered_by=str(data.get("triggered_by") or "unknown"),
            trigger_ref=data.get("trigger_ref"),
            indexed_count=int(data.get("indexed_count") or 0),
            incremental_count=int(data.get("incremental_count") or 0),
            removed_count=int(data.get("removed_count") or 0),
            indexed_watermarks=dict(data.get("indexed_watermarks") or {}),
            indexed_at=str(data.get("indexed_at") or ""),
            is_full_rebuild=bool(data.get("is_full_rebuild", False)),
            freshness_sla_seconds=int(data.get("freshness_sla_seconds") or 3600),
            indexed_object_ids=list(data.get("indexed_object_ids") or []),
        )


class JsonlIndexPipelineStore:
    """Append-only JSONL store for pipeline run snapshots with configurable retention."""

    def __init__(self, path: Path, max_retention: int = 500) -> None:
        self.path = path
        self.max_retention = max(1, int(max_retention))
        self._runs: list[IndexPipelineSnapshot] = []
        self.reload()

    def reload(self) -> None:
        self._runs = []
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
                self._runs.append(IndexPipelineSnapshot.from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue

    def record_run(self, snapshot: IndexPipelineSnapshot) -> IndexPipelineSnapshot:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        self._runs.append(snapshot)
        if len(self._runs) > self.max_retention:
            self.prune(self.max_retention)
        return snapshot

    def prune(self, max_keep: int) -> int:
        """Compact store to last max_keep runs. Returns number of entries pruned."""
        self.reload()
        if len(self._runs) <= max_keep:
            return 0
        kept = self._runs[-max_keep:]
        removed = len(self._runs) - len(kept)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for run in kept:
                handle.write(json.dumps(run.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        self._runs = kept
        return removed

    def get_latest(self) -> IndexPipelineSnapshot | None:
        return self._runs[-1] if self._runs else None

    def count_runs(self) -> int:
        return len(self._runs)

    def list_runs(self, limit: int = 50) -> list[IndexPipelineSnapshot]:
        if limit <= 0:
            return []
        return list(self._runs[-limit:])


class IncrementalIndexPipeline:
    """Drives incremental or full index refresh against the evidence repository."""

    def __init__(
        self,
        repository: InMemoryEvidenceRepository,
        pipeline_store: JsonlIndexPipelineStore,
        *,
        freshness_sla_seconds: int = 3600,
    ) -> None:
        self.repository = repository
        self.pipeline_store = pipeline_store
        self.freshness_sla_seconds = freshness_sla_seconds

    def _source_watermarks(self) -> dict[str, str | None]:
        watermarks: dict[str, str | None] = {}
        for source in self.repository.list_source_records():
            source_type = source.source_type.value if hasattr(source.source_type, "value") else str(source.source_type)
            created_at = str(source.created_at or "")
            if source_type not in watermarks or created_at > str(watermarks[source_type] or ""):
                watermarks[source_type] = created_at or None
        return watermarks

    def _object_effective_time(self, knowledge_object: Any) -> datetime | None:
        meta = dict(knowledge_object.metadata) if knowledge_object.metadata else {}
        for key in ("updated_at", "indexed_at", "created_at"):
            t = _parse_time(meta.get(key))
            if t is not None:
                return t
        return _parse_time(knowledge_object.created_at)

    def run(
        self,
        *,
        triggered_by: str = "manual",
        trigger_ref: str | None = None,
        force_full: bool = False,
    ) -> IndexPipelineSnapshot:
        """Run incremental (or full) index refresh. Records and returns the pipeline snapshot."""
        self.repository.reload()
        self.pipeline_store.reload()

        previous = self.pipeline_store.get_latest()
        previous_ids: set[str] = set(previous.indexed_object_ids) if previous else set()
        last_indexed_at: datetime | None = _parse_time(previous.indexed_at) if previous else None

        all_objects = self.repository.list_knowledge_objects()
        current_ids = {obj.knowledge_object_id for obj in all_objects}
        removed_count = len(previous_ids - current_ids)

        is_full_rebuild = force_full or (previous is None)

        if is_full_rebuild:
            new_objects = list(all_objects)
        else:
            # Incremental: index brand-new ids plus objects created/updated since the last pipeline run.
            new_objects = []
            for obj in all_objects:
                effective_time = self._object_effective_time(obj)
                if (
                    obj.knowledge_object_id not in previous_ids
                    or effective_time is None
                    or last_indexed_at is None
                    or effective_time >= last_indexed_at
                ):
                    new_objects.append(obj)

        adapter = KeywordIndexAdapter(
            self.repository,
            source_watermarks=self._source_watermarks(),
            adapter_state="incremental" if not is_full_rebuild else "full_rebuild",
        )
        # Build documents for all new/changed objects (validates adapter can process them)
        adapter.documents_for(new_objects)

        now = _now_iso()
        snapshot = IndexPipelineSnapshot(
            pipeline_run_id=str(uuid.uuid4()),
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by=triggered_by,
            trigger_ref=trigger_ref,
            indexed_count=len(all_objects),
            incremental_count=len(new_objects),
            removed_count=removed_count,
            indexed_watermarks=self._source_watermarks(),
            indexed_at=now,
            is_full_rebuild=is_full_rebuild,
            freshness_sla_seconds=self.freshness_sla_seconds,
            indexed_object_ids=sorted(current_ids),
        )
        return self.pipeline_store.record_run(snapshot)

    def freshness_status(self) -> dict[str, Any]:
        """Return freshness SLA status based on the latest pipeline run."""
        latest = self.pipeline_store.get_latest()
        if latest is None:
            return {
                "status": "never_indexed",
                "last_indexed_at": None,
                "staleness_seconds": None,
                "sla_seconds": self.freshness_sla_seconds,
                "is_fresh": False,
                "within_sla": False,
                "last_run_at": None,
                "seconds_since_last_run": None,
            }
        now = datetime.now(timezone.utc)
        last_dt = _parse_time(latest.indexed_at)
        staleness = (now - last_dt).total_seconds() if last_dt else None
        is_fresh = staleness is not None and staleness <= self.freshness_sla_seconds
        staleness_rounded = round(staleness, 1) if staleness is not None else None
        return {
            "status": "fresh" if is_fresh else "stale",
            "last_indexed_at": latest.indexed_at,
            "staleness_seconds": staleness_rounded,
            "sla_seconds": self.freshness_sla_seconds,
            "is_fresh": is_fresh,
            "within_sla": is_fresh,
            "last_run_at": latest.indexed_at,
            "seconds_since_last_run": staleness_rounded,
            "last_pipeline_run_id": latest.pipeline_run_id,
            "last_indexed_count": latest.indexed_count,
            "last_triggered_by": latest.triggered_by,
        }
