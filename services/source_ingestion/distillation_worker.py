"""Source-to-strategy distillation worker.

Listens for normalized SourceRecord events and enqueues idempotent distillation
jobs that produce or refresh StrategySpecSeed drafts.

Pipeline:
    SourceRecord (normalized) -> DistillationJob (pending)
        -> EvidenceBundle (synthesized) -> StrategySpecSeed (draft)

Invariants:
    - Only DRAFT and NEEDS_MORE_EVIDENCE seeds are ever refreshed.
    - Seeds in accepted, promoted, rejected, or other terminal states are
      skipped; the job is marked SKIPPED without touching the seed.
    - All paths (initial distill, catch-up, manual redispatch) are idempotent:
      re-running with the same source_id produces the same outcome.
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

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem
from services.knowledge.evidence.repository import InMemoryEvidenceRepository
from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.registry.jsonl_store import JsonlRegistryStore
from services.source_ingestion.seed_materializer import (
    MaterializationMode,
    SeedMaterializationError,
    SeedMaterializationService,
)
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedStatus
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


# Statuses where re-distillation is allowed (seed is still a mutable draft).
_MUTABLE_SEED_STATUSES: frozenset[str] = frozenset(
    {
        StrategySpecSeedStatus.DRAFT.value,
        StrategySpecSeedStatus.NEEDS_MORE_EVIDENCE.value,
    }
)

# Statuses that block re-distillation (seed has advanced past draft).
_IMMUTABLE_SEED_STATUSES: frozenset[str] = frozenset(
    {
        StrategySpecSeedStatus.ACCEPTED.value,
        StrategySpecSeedStatus.PROMOTED_TO_STRATEGY_SPEC.value,
        StrategySpecSeedStatus.CONVERTED_TO_RISK_CONSTRAINT.value,
        StrategySpecSeedStatus.CONVERTED_TO_NEGATIVE.value,
        StrategySpecSeedStatus.MERGED.value,
        StrategySpecSeedStatus.ARCHIVED_AS_INSIGHT.value,
        StrategySpecSeedStatus.REJECTED.value,
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_job_id(source_id: str) -> str:
    return "distill-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]


def _stable_bundle_id(source_id: str) -> str:
    return "evbundle-distill-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]


def _stable_item_id(source_id: str) -> str:
    return "evi-distill-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]


class DistillationJobStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class DistillationError(ValueError):
    """Raised when a distillation invariant is violated."""


@dataclass(frozen=True)
class DistillationJob:
    """Persisted record of one source-to-strategy distillation job."""

    job_id: str
    source_id: str
    idempotency_key: str
    status: DistillationJobStatus | str
    enqueued_at: str
    processed_at: str | None = None
    seed_id: str | None = None
    error: str | None = None
    skip_reason: str | None = None
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value if isinstance(self.status, DistillationJobStatus) else str(self.status)
        return {
            "job_id": self.job_id,
            "source_id": self.source_id,
            "idempotency_key": self.idempotency_key,
            "status": status,
            "enqueued_at": self.enqueued_at,
            "processed_at": self.processed_at,
            "seed_id": self.seed_id,
            "error": self.error,
            "skip_reason": self.skip_reason,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DistillationJob":
        raw_status = str(data.get("status") or DistillationJobStatus.PENDING.value)
        try:
            status = DistillationJobStatus(raw_status)
        except ValueError:
            status = raw_status  # type: ignore[assignment]
        return cls(
            job_id=str(data["job_id"]),
            source_id=str(data["source_id"]),
            idempotency_key=str(data.get("idempotency_key") or ""),
            status=status,
            enqueued_at=str(data.get("enqueued_at") or ""),
            processed_at=data.get("processed_at") or None,
            seed_id=data.get("seed_id") or None,
            error=data.get("error") or None,
            skip_reason=data.get("skip_reason") or None,
            attempts=int(data.get("attempts") or 0),
        )

    @property
    def is_pending(self) -> bool:
        status = self.status.value if isinstance(self.status, DistillationJobStatus) else str(self.status)
        return status == DistillationJobStatus.PENDING.value

    @property
    def is_terminal(self) -> bool:
        status = self.status.value if isinstance(self.status, DistillationJobStatus) else str(self.status)
        return status in (
            DistillationJobStatus.DONE.value,
            DistillationJobStatus.FAILED.value,
            DistillationJobStatus.SKIPPED.value,
        )


@dataclass(frozen=True)
class DistillationRunResult:
    """Summary of a run_pending or catch_up pass."""

    processed: int
    created: int
    refreshed: int
    skipped: int
    failed: int
    enqueued: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "created": self.created,
            "refreshed": self.refreshed,
            "skipped": self.skipped,
            "failed": self.failed,
            "enqueued": self.enqueued,
        }


class DistillationJobQueue:
    """JSONL-backed job queue with one record per source_id (upsert by job_id).

    Idempotency: enqueue() returns the existing job when the source already has
    a queued or completed record.  Only PENDING jobs are returned by
    list_pending().
    """

    def __init__(self, path: str | Path | None = None) -> None:
        resolved = (
            Path(path)
            if path
            else Path(
                os.environ.get(
                    "DISTILLATION_JOB_QUEUE_PATH",
                    "data/distillation/job_queue.jsonl",
                )
            )
        )
        self._store = JsonlRegistryStore(resolved, id_field="job_id")

    @property
    def path(self) -> Path:
        return self._store.path

    def enqueue(
        self,
        source_id: str,
        *,
        requested_by: str = "distillation_worker",
        enqueued_at: str | None = None,
    ) -> DistillationJob:
        """Enqueue a distillation job for source_id. Returns existing job if already present."""
        job_id = _stable_job_id(source_id)
        existing_raw = self._store.read_by_id(job_id)
        if existing_raw is not None:
            return DistillationJob.from_dict(existing_raw)
        idempotency_key = f"distill:{source_id}:{requested_by}"
        job = DistillationJob(
            job_id=job_id,
            source_id=source_id,
            idempotency_key=idempotency_key,
            status=DistillationJobStatus.PENDING,
            enqueued_at=enqueued_at or _utc_now(),
        )
        self._store.upsert(job.to_dict())
        return job

    def get(self, source_id: str) -> DistillationJob | None:
        job_id = _stable_job_id(source_id)
        raw = self._store.read_by_id(job_id)
        return DistillationJob.from_dict(raw) if raw is not None else None

    def list_pending(self) -> list[DistillationJob]:
        return [
            DistillationJob.from_dict(r)
            for r in self._store.read_all()
            if r.get("status") == DistillationJobStatus.PENDING.value
        ]

    def list_all(self) -> list[DistillationJob]:
        return [DistillationJob.from_dict(r) for r in self._store.read_all()]

    def mark_done(self, job_id: str, *, seed_id: str, processed_at: str | None = None) -> None:
        raw = self._store.read_by_id(job_id)
        if raw is None:
            raise DistillationError(f"DistillationJob not found: {job_id}")
        job = DistillationJob.from_dict(raw)
        updated = DistillationJob(
            job_id=job.job_id,
            source_id=job.source_id,
            idempotency_key=job.idempotency_key,
            status=DistillationJobStatus.DONE,
            enqueued_at=job.enqueued_at,
            processed_at=processed_at or _utc_now(),
            seed_id=seed_id,
            attempts=job.attempts + 1,
        )
        self._store.upsert(updated.to_dict())

    def mark_failed(self, job_id: str, *, error: str, processed_at: str | None = None) -> None:
        raw = self._store.read_by_id(job_id)
        if raw is None:
            raise DistillationError(f"DistillationJob not found: {job_id}")
        job = DistillationJob.from_dict(raw)
        updated = DistillationJob(
            job_id=job.job_id,
            source_id=job.source_id,
            idempotency_key=job.idempotency_key,
            status=DistillationJobStatus.FAILED,
            enqueued_at=job.enqueued_at,
            processed_at=processed_at or _utc_now(),
            error=error,
            attempts=job.attempts + 1,
        )
        self._store.upsert(updated.to_dict())

    def mark_skipped(self, job_id: str, *, reason: str, processed_at: str | None = None) -> None:
        raw = self._store.read_by_id(job_id)
        if raw is None:
            raise DistillationError(f"DistillationJob not found: {job_id}")
        job = DistillationJob.from_dict(raw)
        updated = DistillationJob(
            job_id=job.job_id,
            source_id=job.source_id,
            idempotency_key=job.idempotency_key,
            status=DistillationJobStatus.SKIPPED,
            enqueued_at=job.enqueued_at,
            processed_at=processed_at or _utc_now(),
            skip_reason=reason,
            attempts=job.attempts + 1,
        )
        self._store.upsert(updated.to_dict())

    def reset_to_pending(self, source_id: str, *, requested_by: str = "redispatch") -> DistillationJob:
        """Reset a job to pending for manual re-distillation. Idempotent if already pending."""
        job_id = _stable_job_id(source_id)
        raw = self._store.read_by_id(job_id)
        if raw is None:
            return self.enqueue(source_id, requested_by=requested_by)
        existing = DistillationJob.from_dict(raw)
        if existing.is_pending:
            return existing
        idempotency_key = f"distill:{source_id}:{requested_by}:redispatch"
        reset = DistillationJob(
            job_id=existing.job_id,
            source_id=existing.source_id,
            idempotency_key=idempotency_key,
            status=DistillationJobStatus.PENDING,
            enqueued_at=existing.enqueued_at,
            attempts=existing.attempts,
        )
        self._store.upsert(reset.to_dict())
        return reset

    def count(self) -> int:
        return len(self._store.read_all())


def _synthesize_evidence_item(source: SourceRecord) -> EvidenceItem:
    """Create a minimal EvidenceItem for a normalized SourceRecord.

    The citation_label doubles as the stable dedupe key across distillation runs.
    """
    item_id = _stable_item_id(source.source_id)
    body = source.title
    for key in ("body", "excerpt", "abstract", "summary"):
        candidate = source.metadata.get(key)
        if isinstance(candidate, str) and candidate.strip():
            body = candidate.strip()
            break
    return EvidenceItem(
        evidence_item_id=item_id,
        source_id=source.source_id,
        item_type=source.source_type.value,
        content_ref=source.content_ref,
        citation_label=f"{source.source_id}#normalized",
        body=body,
        confidence=float(source.metadata.get("trust_score") or 0.7),
        access_scope=list(source.metadata.get("access_scope") or ("public",)),
        metadata={"evidence_dedupe_key": item_id, **dict(source.metadata)},
    )


def _synthesize_evidence_bundle(source: SourceRecord, item: EvidenceItem) -> EvidenceBundle:
    """Create a minimal EvidenceBundle from a SourceRecord + its synthetic EvidenceItem."""
    bundle_id = _stable_bundle_id(source.source_id)
    summary = source.title
    license_scope = str(source.metadata.get("license_scope") or "internal").strip() or "internal"
    access_scope = list(source.metadata.get("access_scope") or ("public",))
    confidence = item.confidence
    trace_refs: list[str] = [source.trace_id] if source.trace_id else []
    return EvidenceBundle(
        evidence_bundle_id=bundle_id,
        source_ids=[source.source_id],
        evidence_item_ids=[item.evidence_item_id],
        summary=summary,
        citation_refs=[item.citation_label],
        confidence=confidence,
        license_scope=license_scope,
        access_scope=access_scope,
        created_by="distillation_worker",
        trace_refs=trace_refs,
        metadata={
            "distillation_source": "source_record",
            "source_type": source.source_type.value,
        },
    )


class DistillationWorker:
    """Converts normalized SourceRecords into StrategySpecSeed drafts.

    The worker has three entry points:
    - enqueue_from_source_record: trigger from the ingest pipeline on normalization
    - run_pending: process the current pending queue (called by the scheduler)
    - catch_up: idempotent batch-enqueue all normalized sources that lack seeds
    - redispatch: manual reset of a specific source for re-processing
    """

    def __init__(
        self,
        job_queue: DistillationJobQueue | None = None,
        seed_store: StrategySpecSeedStore | None = None,
        *,
        created_by: str = "distillation_worker",
    ) -> None:
        self._queue = job_queue or DistillationJobQueue()
        self._seed_store = seed_store or StrategySpecSeedStore()
        self._materializer = SeedMaterializationService(
            store=self._seed_store,
            created_by=created_by,
        )
        self._created_by = created_by

    def enqueue_from_source_record(self, source: SourceRecord) -> DistillationJob:
        """Enqueue a distillation job when a source record becomes normalized.

        Idempotent: calling this multiple times for the same source_id returns
        the same job without creating duplicates.
        """
        if source.status == SourceRecordStatus.REJECTED:
            raise DistillationError(
                f"Rejected source cannot be enqueued for distillation: {source.source_id}"
            )
        return self._queue.enqueue(source.source_id, requested_by=self._created_by)

    def run_pending(
        self,
        source_records: Mapping[str, SourceRecord],
        *,
        limit: int = 100,
        now: str | None = None,
    ) -> DistillationRunResult:
        """Process pending distillation jobs.

        Args:
            source_records: mapping of source_id -> SourceRecord for lookup.
                Typically the normalized records from the evidence repository.
            limit: maximum number of jobs to process in this pass.
            now: override for the current timestamp (used in tests).

        Returns:
            A DistillationRunResult summarising what happened.
        """
        pending = self._queue.list_pending()[:limit]
        processed = created = refreshed = skipped = failed = 0
        for job in pending:
            source = source_records.get(job.source_id)
            if source is None:
                self._queue.mark_failed(
                    job.job_id,
                    error=f"SourceRecord not found: {job.source_id}",
                    processed_at=now or _utc_now(),
                )
                failed += 1
                processed += 1
                continue
            outcome = self._distill_one(job, source, now=now)
            processed += 1
            if outcome == "created":
                created += 1
            elif outcome == "refreshed":
                refreshed += 1
            elif outcome == "skipped":
                skipped += 1
            elif outcome == "failed":
                failed += 1
        return DistillationRunResult(
            processed=processed,
            created=created,
            refreshed=refreshed,
            skipped=skipped,
            failed=failed,
        )

    def catch_up(
        self,
        source_records: Sequence[SourceRecord],
        *,
        limit: int = 500,
        now: str | None = None,
    ) -> DistillationRunResult:
        """Idempotently enqueue and process all normalized sources without done seeds.

        Sources with existing done/skipped jobs are not re-processed unless
        redispatch() is called explicitly.

        Args:
            source_records: all known normalized source records.
            limit: maximum number of sources to enqueue in this pass.
            now: timestamp override for tests.

        Returns:
            A DistillationRunResult including the enqueued count.
        """
        normalized = [
            s for s in source_records if s.status != SourceRecordStatus.REJECTED
        ][:limit]

        enqueued = 0
        records_map: dict[str, SourceRecord] = {}
        for source in normalized:
            records_map[source.source_id] = source
            existing = self._queue.get(source.source_id)
            if existing is None or existing.is_pending:
                job = self._queue.enqueue(
                    source.source_id,
                    requested_by=self._created_by,
                    enqueued_at=now or _utc_now(),
                )
                if existing is None:
                    enqueued += 1

        run_result = self.run_pending(records_map, now=now)
        return DistillationRunResult(
            processed=run_result.processed,
            created=run_result.created,
            refreshed=run_result.refreshed,
            skipped=run_result.skipped,
            failed=run_result.failed,
            enqueued=enqueued,
        )

    def redispatch(self, source_id: str, source: SourceRecord) -> DistillationJob:
        """Manually reset a source for re-distillation.

        Resets the job to PENDING and returns it. Idempotent if already pending.
        Re-distillation will only proceed if the seed is still a mutable draft;
        advanced seeds are skipped.

        Args:
            source_id: the source to redispatch.
            source: the SourceRecord for the source (must not be rejected).
        """
        if source.status == SourceRecordStatus.REJECTED:
            raise DistillationError(
                f"Rejected source cannot be redispatched: {source_id}"
            )
        return self._queue.reset_to_pending(source_id, requested_by="redispatch")

    def _distill_one(
        self,
        job: DistillationJob,
        source: SourceRecord,
        *,
        now: str | None = None,
    ) -> str:
        """Run distillation for one job. Returns outcome string for accounting.

        Returns one of: 'created', 'refreshed', 'skipped', 'failed'.
        """
        ts = now or _utc_now()

        if source.status == SourceRecordStatus.REJECTED:
            self._queue.mark_skipped(
                job.job_id,
                reason=f"source {source.source_id} is rejected",
                processed_at=ts,
            )
            return "skipped"

        # Check existing seed status to enforce mutable-draft-only invariant.
        bundle_id = _stable_bundle_id(source.source_id)
        existing_seeds = self._seed_store.list_by_bundle(bundle_id)
        if existing_seeds:
            seed = existing_seeds[0]
            seed_status = seed.status.value if isinstance(seed.status, StrategySpecSeedStatus) else str(seed.status)
            if seed_status in _IMMUTABLE_SEED_STATUSES:
                self._queue.mark_skipped(
                    job.job_id,
                    reason=f"seed {seed.seed_id} is in immutable status {seed_status!r}",
                    processed_at=ts,
                )
                return "skipped"
            mode = MaterializationMode.REFRESH
        else:
            mode = MaterializationMode.CREATE_IF_ABSENT

        is_refresh = mode == MaterializationMode.REFRESH

        try:
            item = _synthesize_evidence_item(source)
            bundle = _synthesize_evidence_bundle(source, item)
            result = self._materializer.materialize(
                bundle,
                source_records=[source],
                evidence_items=[item],
                mode=mode,
                requested_by=self._created_by,
                idempotency_key=job.idempotency_key,
            )
        except (SeedMaterializationError, ValueError, Exception) as exc:
            self._queue.mark_failed(
                job.job_id,
                error=str(exc),
                processed_at=ts,
            )
            return "failed"

        self._queue.mark_done(
            job.job_id,
            seed_id=result.seed.seed_id,
            processed_at=ts,
        )
        return "refreshed" if is_refresh else "created"


def make_distillation_worker(
    *,
    queue_path: str | Path | None = None,
    seed_store_path: str | Path | None = None,
    created_by: str = "distillation_worker",
) -> DistillationWorker:
    """Factory for a default-configured DistillationWorker."""
    queue = DistillationJobQueue(queue_path)
    seed_store = StrategySpecSeedStore(seed_store_path)
    return DistillationWorker(queue, seed_store, created_by=created_by)


__all__ = [
    "DistillationError",
    "DistillationJob",
    "DistillationJobQueue",
    "DistillationJobStatus",
    "DistillationRunResult",
    "DistillationWorker",
    "make_distillation_worker",
]
