"""Incremental search indexing pipeline with schema-versioned snapshots and freshness SLA."""

from __future__ import annotations

import contextlib
import dataclasses
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
    # Streaming-mode checkpoint (bounded, independent of row count). ``indexed_append_cursor`` is the
    # append_id below which every knowledge object is indexed; ``updates_*`` is the in-place-update
    # sweep position; ``audit_cursor`` is the rolling backend-presence audit position.
    indexed_append_cursor: int = 0
    updates_cursor: int = 0
    updates_since: str | None = None
    audit_cursor: int = 0

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
            "indexed_append_cursor": self.indexed_append_cursor,
            "updates_cursor": self.updates_cursor,
            "updates_since": self.updates_since,
            "audit_cursor": self.audit_cursor,
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
            indexed_append_cursor=int(data.get("indexed_append_cursor") or 0),
            updates_cursor=int(data.get("updates_cursor") or 0),
            updates_since=data.get("updates_since"),
            audit_cursor=int(data.get("audit_cursor") or 0),
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
        """Stream the JSONL one line at a time, compacting each run as the next one arrives.

        Every run line persists the full ``indexed_object_ids`` checkpoint (~28k ids at the hosted
        object count, ~1-2 MB/line, up to ``max_retention`` lines). Reading the whole file and
        parsing every line first made each refresh/source-completion/freshness call allocate
        several GB transiently; here only the line in flight and the latest run's ids are alive.
        """
        with self._lock:
            runs: list[IndexPipelineSnapshot] = []
            if self.path.exists():
                with self._open_lines() as handle:
                    for line in handle:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            run = IndexPipelineSnapshot.from_dict(json.loads(stripped))
                        except (json.JSONDecodeError, KeyError):
                            continue
                        del stripped, line
                        if runs and runs[-1].indexed_object_ids:
                            runs[-1] = dataclasses.replace(runs[-1], indexed_object_ids=[])
                        runs.append(run)
            self._runs = self._compact(runs)

    def _open_lines(self):
        """Line iterator over the JSONL. Streams via open(); if ``Path.read_text`` has been replaced
        (test instrumentation that synchronizes on reads) go through it so that hook still fires."""
        if not str(getattr(Path.read_text, "__module__", "pathlib")).startswith("pathlib"):
            return contextlib.nullcontext(self.path.read_text(encoding="utf-8").splitlines())
        return self.path.open("r", encoding="utf-8")

    @staticmethod
    def _compact(runs: list[IndexPipelineSnapshot]) -> list[IndexPipelineSnapshot]:
        """Only the latest run needs its checkpoint id list (it is the next run's baseline).

        Older runs keep the bounded summary (counts, watermarks, timestamps) without the full
        indexed_object_ids list. The persisted JSONL format is unchanged.
        """
        return [
            run if index == len(runs) - 1 or not run.indexed_object_ids
            else dataclasses.replace(run, indexed_object_ids=[])
            for index, run in enumerate(runs)
        ]

    def record_run(self, snapshot: IndexPipelineSnapshot) -> IndexPipelineSnapshot:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
            self._runs = self._compact([*self._runs, snapshot])
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
        pushed_down = getattr(self.repository, "source_watermarks", None)
        if pushed_down is not None:
            return dict(pushed_down())
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
        # Incremental by append_id; only an explicit force_full pays for a whole-table resync.
        refresh = getattr(self.repository, "refresh", None)
        if force_full or refresh is None:
            self.repository.reload()
        else:
            refresh()
        self.pipeline_store.reload()

        if hasattr(self.repository, "fetch_knowledge_object_page"):
            return self._run_streaming(triggered_by, trigger_ref, force_full)

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

    @staticmethod
    def _indexable(obj: Any) -> bool:
        # Numeric Collector bundles are provenance refs, not semantic documents.
        return not (obj.source_type == "market" and obj.metadata.get("collector_market_bundle"))

    def _index_chunk(self, chunk: list[Any]) -> None:
        if self.retrieval_backend is not None:
            self.retrieval_backend.index_documents(
                [_knowledge_object_to_retrieval_record(obj, self.repository) for obj in chunk]
            )

    def _run_streaming(self, triggered_by: str, trigger_ref: str | None, force_full: bool) -> IndexPipelineSnapshot:
        """Cursor-based run: memory is O(page + batch), never O(total rows).

        Phases share one batch/time budget: (A) knowledge objects above ``indexed_append_cursor``;
        (B) a sweep for objects updated in place (append_id is preserved by upserts), bounded by
        ``updates_cursor``; (C) a rolling page audit that re-indexes objects the retrieval backend
        lacks. Legacy checkpoints (id lists, no cursor) are honoured once as a skip-set while the
        cursor catches up, then the id list is dropped.
        """
        repo = self.repository
        previous = self.pipeline_store.get_latest()
        fresh = force_full or previous is None
        is_full_rebuild = fresh
        legacy_ids: set[str] = set() if fresh else set(previous.indexed_object_ids)
        old_cursor = 0 if fresh else previous.indexed_append_cursor
        updates_cursor = 0 if fresh else previous.updates_cursor
        updates_since = None if fresh else (previous.updates_since or previous.indexed_at)
        audit_cursor = 0 if fresh else previous.audit_cursor
        sweep_started_at = _now_iso_precise()
        since_dt = _parse_time(updates_since)

        deadline = time.monotonic() + self.time_budget_seconds
        processed_count = 0

        def budget_left() -> int:
            if processed_count and time.monotonic() >= deadline:
                return 0
            return self.max_batch_size - processed_count

        def drain(scan: Any) -> bool:
            """Index chunks from ``scan(n) -> [(append_id, obj)]`` until budget or source is spent.

            Returns False when an index error stopped it after earlier progress (the failed chunk is
            retried next run); raises when nothing at all was processed.
            """
            nonlocal processed_count
            while budget_left() > 0:
                chunk = scan(min(self.chunk_size, budget_left()))
                if not chunk:
                    return True
                try:
                    self._index_chunk([obj for _, obj in chunk])
                except Exception:  # noqa: BLE001
                    if not processed_count:
                        raise
                    return False
                processed_count += len(chunk)
            return True

        # (A) new objects above the cursor
        committed = old_cursor
        scan_pos = old_cursor

        def scan_new(n: int) -> list[tuple[int, Any]]:
            nonlocal scan_pos
            scan_pos = committed
            chunk: list[tuple[int, Any]] = []
            while len(chunk) < n:
                page = repo.fetch_knowledge_object_page(scan_pos)
                if not page:
                    break
                for append_id, obj in page:
                    scan_pos = append_id
                    if not self._indexable(obj):
                        continue
                    if obj.knowledge_object_id in legacy_ids:
                        effective = self._object_effective_time(obj)
                        if not (effective is None or since_dt is None or effective >= since_dt):
                            continue  # already indexed by the legacy checkpoint and untouched since
                    chunk.append((append_id, obj))
                    if len(chunk) >= n:
                        break
            return chunk

        def drain_new() -> None:
            nonlocal committed, processed_count
            while budget_left() > 0:
                chunk = scan_new(min(self.chunk_size, budget_left()))
                if chunk:
                    try:
                        self._index_chunk([obj for _, obj in chunk])
                    except Exception:  # noqa: BLE001
                        if not processed_count:
                            raise
                        return
                    processed_count += len(chunk)
                committed = scan_pos
                if not chunk:
                    return

        drain_new()
        new_cursor = committed

        # (B) in-place update sweep over rows indexed before this run
        sweep_done = True
        if updates_since is not None and old_cursor > 0:
            if budget_left() <= 0:
                sweep_done = False
            else:
                sweep_pos = updates_cursor
                sweep_exhausted = False

                def scan_updates(n: int) -> list[tuple[int, Any]]:
                    nonlocal sweep_pos, sweep_exhausted
                    chunk: list[tuple[int, Any]] = []
                    while len(chunk) < n:
                        page = repo.fetch_knowledge_object_page(
                            sweep_pos, max_append_id=old_cursor, touched_since=updates_since
                        )
                        if not page:
                            sweep_exhausted = True
                            break
                        for append_id, obj in page:
                            sweep_pos = append_id
                            if not self._indexable(obj):
                                continue
                            effective = self._object_effective_time(obj)
                            if effective is None or since_dt is None or effective >= since_dt:
                                chunk.append((append_id, obj))
                                if len(chunk) >= n:
                                    break
                    return chunk

                clean = drain(scan_updates)
                if clean:
                    updates_cursor = sweep_pos
                sweep_done = clean and sweep_exhausted
        if sweep_done:
            updates_since = sweep_started_at
            updates_cursor = 0

        # (C) rolling presence audit (only when the backend can report presence)
        probe = getattr(self.retrieval_backend, "indexed_document_states", None)
        if probe is not None and budget_left() > 0 and new_cursor > 0:
            page = repo.fetch_knowledge_object_page(audit_cursor, max_append_id=new_cursor)
            if not page:
                audit_cursor = 0
            else:
                by_tenant: dict[str, list[tuple[int, Any]]] = {}
                for append_id, obj in page:
                    if self._indexable(obj):
                        tenant = str((dict(obj.metadata) if obj.metadata else {}).get("tenant_id") or "default")
                        by_tenant.setdefault(tenant, []).append((append_id, obj))
                missing: list[tuple[int, Any]] = []
                supported = True
                for tenant, rows in by_tenant.items():
                    states = probe(tenant, [o.knowledge_object_id for _, o in rows])
                    if not isinstance(states, dict):
                        supported = False
                        break
                    missing.extend((a, o) for a, o in rows if states.get(o.knowledge_object_id) is not _desired_active(o))
                if supported:
                    missing.sort(key=lambda pair: pair[0])
                    audit_cursor = page[-1][0]
                    fixed = 0
                    while fixed < len(missing) and budget_left() > 0:
                        part = missing[fixed:fixed + min(self.chunk_size, budget_left())]
                        try:
                            self._index_chunk([o for _, o in part])
                        except Exception:  # noqa: BLE001
                            if not processed_count:
                                raise
                            audit_cursor = missing[fixed][0] - 1
                            break
                        fixed += len(part)
                        processed_count += len(part)
                    else:
                        if fixed < len(missing):
                            audit_cursor = missing[fixed][0] - 1

        backlog_remaining = repo.count_knowledge_objects(new_cursor, indexable_only=True)
        indexed_total = repo.count_knowledge_objects(0, max_append_id=new_cursor, indexable_only=True)

        snapshot = IndexPipelineSnapshot(
            pipeline_run_id=str(uuid.uuid4()),
            schema_version=PIPELINE_SCHEMA_VERSION,
            triggered_by=triggered_by,
            trigger_ref=trigger_ref,
            indexed_count=indexed_total,
            incremental_count=processed_count,
            removed_count=0,
            indexed_watermarks=self._source_watermarks(),
            indexed_at=_now_iso_precise(),
            is_full_rebuild=is_full_rebuild,
            freshness_sla_seconds=self.freshness_sla_seconds,
            # Legacy id checkpoint is carried (one-time, bounded by its old size) only until the cursor
            # has caught up with the table; afterwards the run line holds no id list at all.
            indexed_object_ids=sorted(legacy_ids) if backlog_remaining > 0 else [],
            backlog_remaining=backlog_remaining,
            indexed_append_cursor=new_cursor,
            updates_cursor=updates_cursor,
            updates_since=updates_since,
            audit_cursor=audit_cursor,
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
