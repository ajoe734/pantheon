"""Incremental search indexing pipeline with schema-versioned snapshots and freshness SLA."""

from __future__ import annotations

import json
import os
import threading
import time
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


def _now_iso_precise() -> str:
    # Checkpoint time keeps microseconds: with second truncation, objects indexed by a bounded
    # batch in the same second would satisfy "effective_time >= last_indexed_at" and be re-embedded.
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    backlog_remaining: int = 0

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
            "backlog_remaining": self.backlog_remaining,
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
            backlog_remaining=int(data.get("backlog_remaining") or 0),
        )


class JsonlIndexPipelineStore:
    """Append-only JSONL store for pipeline run snapshots with configurable retention."""

    def __init__(self, path: Path, max_retention: int = 500) -> None:
        self.path = path
        self.max_retention = max(1, int(max_retention))
        self._runs: list[IndexPipelineSnapshot] = []
        # File mutation and the in-memory swap are serialized so a concurrent readback never
        # sees a half-written file; reload publishes a complete list in one assignment so
        # readers (get_latest/list_runs) never observe a transiently empty store.
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            runs: list[IndexPipelineSnapshot] = []
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        data = json.loads(stripped)
                        runs.append(IndexPipelineSnapshot.from_dict(data))
                    except (json.JSONDecodeError, KeyError):
                        continue
            self._runs = runs

    def record_run(self, snapshot: IndexPipelineSnapshot) -> IndexPipelineSnapshot:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
            self._runs = [*self._runs, snapshot]
            if len(self._runs) > self.max_retention:
                self.prune(self.max_retention)
        return snapshot

    def prune(self, max_keep: int) -> int:
        """Compact store to last max_keep runs. Returns number of entries pruned."""
        with self._lock:
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
        runs = self._runs
        return runs[-1] if runs else None

    def count_runs(self) -> int:
        return len(self._runs)

    def list_runs(self, limit: int = 50) -> list[IndexPipelineSnapshot]:
        if limit <= 0:
            return []
        return list(self._runs[-limit:])


def _knowledge_object_to_retrieval_record(ko: Any, repository: InMemoryEvidenceRepository) -> Any:
    from services.search.pg_retrieval import RetrievalIndexRecord
    evidence_item = repository.get_evidence_item(ko.evidence_item_id)
    bundle = repository.get_bundle(ko.evidence_bundle_id)
    metadata = dict(ko.metadata) if ko.metadata else {}

    search_parts = [
        ko.title,
        ko.text,
        " ".join(ko.keywords),
        metadata.get("search_text"),
    ]
    if evidence_item is not None:
        search_parts.extend([evidence_item.body, evidence_item.citation_label])
    if bundle is not None:
        search_parts.extend([bundle.summary, " ".join(bundle.citation_refs)])
    search_text = " ".join(str(p or "") for p in search_parts).strip()

    tenant_id = str(metadata.get("tenant_id") or "default")
    persona_id = (ko.persona_scope[0] if ko.persona_scope else None) or metadata.get("persona_id")
    workspace_id = (ko.workspace_scope[0] if ko.workspace_scope else None) or metadata.get("workspace_id")

    event_time = None
    available_time = None
    if evidence_item:
        if evidence_item.event_time:
            event_time = evidence_item.event_time.isoformat() if hasattr(evidence_item.event_time, "isoformat") else str(evidence_item.event_time)
        if evidence_item.available_time:
            available_time = evidence_item.available_time.isoformat() if hasattr(evidence_item.available_time, "isoformat") else str(evidence_item.available_time)
    if not event_time and metadata.get("event_time"):
        event_time = str(metadata["event_time"])
    if not available_time and metadata.get("available_time"):
        available_time = str(metadata["available_time"])

    return RetrievalIndexRecord(
        id=ko.knowledge_object_id,
        record_kind="knowledge_object",
        tenant_id=tenant_id,
        persona_id=persona_id,
        workspace_id=workspace_id,
        environment_scope=list(ko.environment_scope or ["paper"]),
        access_scope=list(ko.access_scope or ["operator"]),
        license_scope=str(ko.license_scope or "internal"),
        role_scope=list(metadata.get("role_scope") or []),
        sensitivity=str(metadata.get("sensitivity") or "internal"),
        capital_pool_scope=list(metadata.get("capital_pool_scope") or []),
        source_type=str(ko.source_type or "internal_note"),
        asset_class=list(metadata.get("asset_class") or []),
        strategy_id=metadata.get("strategy_id"),
        title=ko.title,
        search_text=search_text or ko.text,
        content_ref=evidence_item.content_ref if evidence_item else f"/docs/{ko.knowledge_object_id}",
        citation_label=evidence_item.citation_label if evidence_item else f"doc:{ko.knowledge_object_id}",
        evidence_bundle_id=ko.evidence_bundle_id,
        evidence_item_id=ko.evidence_item_id,
        event_time=event_time,
        available_time=available_time,
        relevance_score=float(metadata.get("relevance_score") or 0.0),
        metadata=metadata,
        is_active=bool(metadata.get("is_active", True)),
    )


def _desired_active(obj: Any) -> bool:
    return bool(dict(obj.metadata).get("is_active", True)) if obj.metadata else True


DEFAULT_REFRESH_BATCH_SIZE = 16
DEFAULT_REFRESH_CHUNK_SIZE = 4
DEFAULT_REFRESH_TIME_BUDGET_SECONDS = 15.0


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


class IncrementalIndexPipeline:
    """Drives incremental or full index refresh against the evidence repository.

    One run embeds at most ``max_batch_size`` objects (and stops starting new chunks once
    ``time_budget_seconds`` is spent), then records a checkpoint containing only what was
    really indexed. Unprocessed objects stay absent from the checkpoint, so repeated runs drain
    the backlog without embedding anything twice. ``backlog_remaining`` reports what is left.
    """

    def __init__(
        self,
        repository: InMemoryEvidenceRepository,
        pipeline_store: JsonlIndexPipelineStore,
        *,
        freshness_sla_seconds: int = 3600,
        retrieval_backend: Any | None = None,
        max_batch_size: int | None = None,
        chunk_size: int | None = None,
        time_budget_seconds: float | None = None,
    ) -> None:
        self.max_batch_size = max(1, int(max_batch_size if max_batch_size is not None else _env_number("SEARCH_INDEX_REFRESH_BATCH_SIZE", DEFAULT_REFRESH_BATCH_SIZE)))
        self.chunk_size = max(1, int(chunk_size if chunk_size is not None else _env_number("SEARCH_INDEX_REFRESH_CHUNK_SIZE", DEFAULT_REFRESH_CHUNK_SIZE)))
        self.time_budget_seconds = float(time_budget_seconds if time_budget_seconds is not None else _env_number("SEARCH_INDEX_REFRESH_TIME_BUDGET_SECONDS", DEFAULT_REFRESH_TIME_BUDGET_SECONDS))
        self.repository = repository
        self.pipeline_store = pipeline_store
        self.freshness_sla_seconds = freshness_sla_seconds
        self.retrieval_backend = retrieval_backend

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

    def _objects_missing_from_backend(self, all_objects: list[Any], selected: list[Any]) -> list[Any]:
        """Objects the retained checkpoint claims are indexed but the retrieval backend lacks.

        The checkpoint is JSONL while the hybrid index is a separate store; an empty or
        underfilled backend (new backend, wiped volume) otherwise never receives pre-existing
        objects because incremental runs only select changed ones.
        """
        probe = getattr(self.retrieval_backend, "indexed_document_states", None)
        if probe is None:
            return []
        selected_ids = {obj.knowledge_object_id for obj in selected}
        by_tenant: dict[str, list[Any]] = {}
        for obj in all_objects:
            if obj.knowledge_object_id in selected_ids:
                continue
            tenant = str((dict(obj.metadata) if obj.metadata else {}).get("tenant_id") or "default")
            by_tenant.setdefault(tenant, []).append(obj)
        missing: list[Any] = []
        for tenant, objects in by_tenant.items():
            states = probe(tenant, [o.knowledge_object_id for o in objects])
            if not isinstance(states, dict):
                return []  # backend cannot report presence; keep checkpoint-only behaviour
            # In sync = row exists with the canonical desired active flag (inactive stays inactive).
            missing.extend(
                o
                for o in objects
                if states.get(o.knowledge_object_id) is not _desired_active(o)
            )
        return missing

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

        # Numeric Collector bundles are discoverable provenance/partition refs,
        # not semantic documents. Partial deliveries update their row counts;
        # embedding those updates would reintroduce work proportional to rows.
        all_objects = [obj for obj in self.repository.list_knowledge_objects()
                       if not (obj.source_type == "market" and obj.metadata.get("collector_market_bundle"))]
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

        if not is_full_rebuild:
            new_objects.extend(self._objects_missing_from_backend(all_objects, new_objects))

        # De-duplicate (backend-missing probe may repeat ids) and bound the batch.
        seen: set[str] = set()
        candidates: list[Any] = []
        for obj in new_objects:
            if obj.knowledge_object_id not in seen:
                seen.add(obj.knowledge_object_id)
                candidates.append(obj)

        processed: list[Any] = []
        deadline = time.monotonic() + self.time_budget_seconds
        while len(processed) < self.max_batch_size and len(processed) < len(candidates):
            if processed and time.monotonic() >= deadline:
                break
            take = min(self.chunk_size, self.max_batch_size - len(processed))
            chunk = candidates[len(processed):len(processed) + take]
            try:
                if self.retrieval_backend is not None:
                    self.retrieval_backend.index_documents(
                        [_knowledge_object_to_retrieval_record(obj, self.repository) for obj in chunk]
                    )
            except Exception:  # noqa: BLE001 - keep progress already made, surface if none
                if not processed:
                    raise
                break  # partial progress is checkpointed; the next run retries this chunk
            processed.extend(chunk)

        processed_ids = {obj.knowledge_object_id for obj in processed}
        backlog_remaining = len(candidates) - len(processed)

        adapter = KeywordIndexAdapter(
            self.repository,
            source_watermarks=self._source_watermarks(),
            adapter_state="incremental" if not is_full_rebuild else "full_rebuild",
        )
        adapter.documents_for(processed)

        if self.retrieval_backend is not None and removed_count > 0:
            for removed_id in (previous_ids - current_ids):
                try:
                    self.retrieval_backend.delete_document(removed_id)
                except Exception:
                    pass

        if backlog_remaining > 0:
            # Checkpoint = previously indexed, still-current ids that were NOT re-selected, plus
            # what this run really indexed. Selected-but-unprocessed ids are left out so the next
            # run selects them again even though indexed_at advanced.
            pending_ids = {obj.knowledge_object_id for obj in candidates} - processed_ids
            checkpoint_ids = ((previous_ids & current_ids) - pending_ids) | processed_ids
        else:
            checkpoint_ids = current_ids

        now = _now_iso_precise()
        snapshot = IndexPipelineSnapshot(
            pipeline_run_id=str(uuid.uuid4()),
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by=triggered_by,
            trigger_ref=trigger_ref,
            indexed_count=len(checkpoint_ids),
            incremental_count=len(processed),
            removed_count=removed_count,
            indexed_watermarks=self._source_watermarks(),
            indexed_at=now,
            is_full_rebuild=is_full_rebuild,
            freshness_sla_seconds=self.freshness_sla_seconds,
            indexed_object_ids=sorted(checkpoint_ids),
            backlog_remaining=backlog_remaining,
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
