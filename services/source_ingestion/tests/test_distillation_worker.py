"""Tests for the source-to-strategy distillation worker.

Covers the three acceptance criteria from LOOP-AUTO-KNOW-001:
  AC-1: New normalized sources enqueue distillation jobs.
  AC-2: Distillation updates mutable draft only.
  AC-3: Manual re-distill and catch-up paths are idempotent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.connectors.base import SourceRecord, SourceRecordStatus
from services.source_ingestion.distillation_worker import (
    DistillationError,
    DistillationJob,
    DistillationJobQueue,
    DistillationJobStatus,
    DistillationRunResult,
    DistillationWorker,
    _stable_bundle_id,
    _stable_item_id,
    _stable_job_id,
    _synthesize_evidence_bundle,
    _synthesize_evidence_item,
    source_version_digest,
)
from services.source_ingestion.strategy_seed_builder import StrategySpecSeedStatus
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalized_source(
    source_id: str = "src-tw-001",
    title: str = "LightGBM TW equity momentum factor paper",
    source_type: str = "paper",
    trust_score: float = 0.8,
    **metadata_overrides: Any,
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id="conn-papers",
        source_type=source_type,
        title=title,
        content_ref=f"https://doi.org/10.1000/{source_id}",
        status="normalized",
        metadata={
            "trust_score": trust_score,
            "access_scope": ["research"],
            "license_scope": "internal",
            "keywords": ["momentum", "lightgbm", "equity"],
            **metadata_overrides,
        },
    )


def _rejected_source(source_id: str = "src-rejected-001") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id="conn-papers",
        source_type="paper",
        title="Rejected source",
        content_ref="https://doi.org/rejected",
        status="rejected",
    )


def _make_worker(tmp_path: Path) -> tuple[DistillationWorker, DistillationJobQueue, StrategySpecSeedStore]:
    queue = DistillationJobQueue(tmp_path / "queue.jsonl")
    seed_store = StrategySpecSeedStore(tmp_path / "seeds.jsonl")
    worker = DistillationWorker(queue, seed_store, created_by="test_worker")
    return worker, queue, seed_store


def _versioned_bundle_id(source: SourceRecord) -> str:
    return _stable_bundle_id(source.source_id, source_version_digest(source))


# ---------------------------------------------------------------------------
# AC-1: New normalized sources enqueue distillation jobs
# ---------------------------------------------------------------------------

class TestEnqueueFromSourceRecord:
    def test_enqueue_normalized_source_creates_pending_job(self, tmp_path: Path) -> None:
        worker, queue, _ = _make_worker(tmp_path)
        source = _normalized_source()

        job = worker.enqueue_from_source_record(source)

        assert job.status == DistillationJobStatus.PENDING
        assert job.source_id == "src-tw-001"
        # Job identity is per source *version*, not per source id.
        digest = source_version_digest(source)
        assert job.source_digest == digest
        assert job.job_id == _stable_job_id("src-tw-001", digest)

    def test_enqueue_twice_returns_same_job(self, tmp_path: Path) -> None:
        worker, queue, _ = _make_worker(tmp_path)
        source = _normalized_source()

        job_a = worker.enqueue_from_source_record(source)
        job_b = worker.enqueue_from_source_record(source)

        assert job_a.job_id == job_b.job_id
        assert queue.count() == 1

    def test_rejected_source_cannot_be_enqueued(self, tmp_path: Path) -> None:
        worker, _, _ = _make_worker(tmp_path)
        source = _rejected_source()

        with pytest.raises(DistillationError, match="Only normalized source"):
            worker.enqueue_from_source_record(source)

    def test_run_pending_processes_enqueued_job(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()
        worker.enqueue_from_source_record(source)

        result = worker.run_pending({source.source_id: source})

        assert result.processed == 1
        assert result.created == 1
        assert result.failed == 0
        assert queue.list_pending() == []
        done_jobs = [j for j in queue.list_all() if j.status == DistillationJobStatus.DONE]
        assert len(done_jobs) == 1
        assert done_jobs[0].seed_id is not None

    def test_run_pending_creates_seed_for_source(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()
        worker.enqueue_from_source_record(source)

        worker.run_pending({source.source_id: source})

        bundle_id = _versioned_bundle_id(source)
        seeds = seed_store.list_by_bundle(bundle_id)
        assert len(seeds) == 1
        seed = seeds[0]
        assert seed.status == StrategySpecSeedStatus.DRAFT
        assert "momentum" in seed.feature_hints or "lightgbm" in " ".join(seed.feature_hints).lower()

    def test_legacy_run_pending_missing_source_marks_failed(self, tmp_path: Path) -> None:
        worker, queue, _ = _make_worker(tmp_path)
        source = _normalized_source()
        queue.enqueue(source.source_id)

        result = worker.run_pending({})  # legacy jobs still require caller lookup

        assert result.processed == 1
        assert result.failed == 1
        failed_jobs = [j for j in queue.list_all() if j.status == DistillationJobStatus.FAILED]
        assert len(failed_jobs) == 1
        assert "not found" in failed_jobs[0].error.lower()

    def test_run_pending_does_not_process_beyond_limit(self, tmp_path: Path) -> None:
        worker, queue, _ = _make_worker(tmp_path)
        sources = [_normalized_source(source_id=f"src-{i:03d}") for i in range(5)]
        records_map = {s.source_id: s for s in sources}
        for s in sources:
            worker.enqueue_from_source_record(s)

        result = worker.run_pending(records_map, limit=3)

        assert result.processed == 3
        assert len(queue.list_pending()) == 2


# ---------------------------------------------------------------------------
# AC-2: Distillation updates mutable draft only
# ---------------------------------------------------------------------------

class TestMutableDraftOnly:
    def test_existing_draft_seed_is_refreshed(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()
        records_map = {source.source_id: source}

        # First pass: creates the seed
        worker.enqueue_from_source_record(source)
        result1 = worker.run_pending(records_map)
        assert result1.created == 1

        # Second pass after redispatch: refreshes the draft seed
        worker.redispatch(source.source_id, source)
        result2 = worker.run_pending(records_map)

        assert result2.processed == 1
        assert result2.refreshed == 1
        bundle_id = _versioned_bundle_id(source)
        seeds = seed_store.list_by_bundle(bundle_id)
        assert len(seeds) == 1
        assert seeds[0].status == StrategySpecSeedStatus.DRAFT

    def test_accepted_seed_is_not_overwritten(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()
        records_map = {source.source_id: source}

        # First pass: creates seed
        worker.enqueue_from_source_record(source)
        worker.run_pending(records_map)

        # Advance the seed to ACCEPTED
        bundle_id = _versioned_bundle_id(source)
        seeds = seed_store.list_by_bundle(bundle_id)
        seed = seeds[0]
        seed_store.record_review_decision(
            seed.seed_id,
            decision="accept",
            reviewer_id="test_reviewer",
            reason="evidence looks good",
        )

        # Redispatch and re-run: should be skipped, not overwritten
        worker.redispatch(source.source_id, source)
        result = worker.run_pending(records_map)

        assert result.skipped == 1
        assert result.created == 0
        assert result.refreshed == 0
        # Seed must still be ACCEPTED
        refreshed_seeds = seed_store.list_by_bundle(bundle_id)
        assert refreshed_seeds[0].status == StrategySpecSeedStatus.ACCEPTED

    def test_rejected_seed_is_not_overwritten(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()
        records_map = {source.source_id: source}

        worker.enqueue_from_source_record(source)
        worker.run_pending(records_map)

        bundle_id = _versioned_bundle_id(source)
        seeds = seed_store.list_by_bundle(bundle_id)
        seed_store.record_review_decision(
            seeds[0].seed_id,
            decision="reject",
            reviewer_id="test_reviewer",
            reason="evidence insufficient",
        )

        worker.redispatch(source.source_id, source)
        result = worker.run_pending(records_map)

        assert result.skipped == 1
        refreshed_seeds = seed_store.list_by_bundle(bundle_id)
        assert refreshed_seeds[0].status == StrategySpecSeedStatus.REJECTED

    def test_rejected_source_record_is_skipped_in_run(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        rejected = _rejected_source()

        # Manually inject a pending job as if it was created before the source became rejected
        queue.enqueue("src-rejected-001", requested_by="test")
        result = worker.run_pending({rejected.source_id: rejected})

        assert result.skipped == 1
        assert result.created == 0


# ---------------------------------------------------------------------------
# AC-3: Manual re-distill and catch-up paths are idempotent
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_run_pending_twice_does_not_duplicate_seeds(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()
        records_map = {source.source_id: source}

        worker.enqueue_from_source_record(source)
        worker.run_pending(records_map)
        # Second run_pending with no pending jobs is a no-op
        result2 = worker.run_pending(records_map)

        assert result2.processed == 0
        bundle_id = _versioned_bundle_id(source)
        assert len(seed_store.list_by_bundle(bundle_id)) == 1

    def test_catch_up_is_idempotent(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        sources = [_normalized_source(source_id=f"src-cu-{i:03d}") for i in range(3)]

        result_a = worker.catch_up(sources)
        result_b = worker.catch_up(sources)

        # Only creates seeds once
        assert result_a.created == 3
        # Second catch_up: no new pending jobs (all done), no re-processing
        assert result_b.processed == 0
        assert result_b.enqueued == 0

    def test_catch_up_enqueues_new_sources_only(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        sources = [_normalized_source(source_id=f"src-new-{i:03d}") for i in range(2)]

        result_a = worker.catch_up(sources)
        assert result_a.enqueued == 2

        extra = _normalized_source(source_id="src-new-extra")
        result_b = worker.catch_up([*sources, extra])
        assert result_b.enqueued == 1
        assert result_b.created == 1

    def test_redispatch_is_idempotent_when_already_pending(self, tmp_path: Path) -> None:
        worker, queue, _ = _make_worker(tmp_path)
        source = _normalized_source()

        worker.enqueue_from_source_record(source)
        job_a = worker.redispatch(source.source_id, source)
        job_b = worker.redispatch(source.source_id, source)

        assert job_a.job_id == job_b.job_id
        assert queue.count() == 1

    def test_redispatch_allows_re_processing_failed_job(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        source = _normalized_source()

        # A legacy unversioned job still depends on the caller map.
        queue.enqueue(source.source_id)
        worker.run_pending({})  # fails

        failed = [j for j in queue.list_all() if j.status == DistillationJobStatus.FAILED]
        assert len(failed) == 1

        # Redispatch and re-run with the correct map
        worker.redispatch(source.source_id, source)
        result = worker.run_pending({source.source_id: source})

        assert result.created == 1
        done = [j for j in queue.list_all() if j.status == DistillationJobStatus.DONE]
        assert len(done) == 1

    def test_catch_up_skips_rejected_sources(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        sources: list[SourceRecord] = [
            _normalized_source(source_id="src-good-001"),
            _rejected_source("src-bad-001"),
        ]

        result = worker.catch_up(sources)

        # Only the normalized source should have been processed
        assert result.enqueued == 1
        assert result.created == 1

    def test_multiple_sources_independent(self, tmp_path: Path) -> None:
        worker, queue, seed_store = _make_worker(tmp_path)
        sources = [
            _normalized_source(source_id="src-a", title="Taiwan equity momentum research"),
            _normalized_source(source_id="src-b", title="Cross-sectional factor analysis TWSE"),
        ]
        result = worker.catch_up(sources)

        assert result.created == 2
        assert result.failed == 0
        all_seeds = seed_store.list_all()
        assert len(all_seeds) == 2
        seed_source_ids: set[str] = set()
        for seed in all_seeds:
            seed_source_ids.update(seed.source_ids)
        assert "src-a" in seed_source_ids
        assert "src-b" in seed_source_ids


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_stable_job_id_is_deterministic(self) -> None:
        assert _stable_job_id("src-001") == _stable_job_id("src-001")
        assert _stable_job_id("src-001") != _stable_job_id("src-002")

    def test_stable_bundle_id_is_deterministic(self) -> None:
        assert _stable_bundle_id("src-001") == _stable_bundle_id("src-001")
        assert _stable_bundle_id("src-001") != _stable_bundle_id("src-002")

    def test_synthesize_evidence_item_uses_body_from_metadata(self) -> None:
        source = _normalized_source(abstract="This is the abstract text.")
        item = _synthesize_evidence_item(source)
        assert item.body == "This is the abstract text."

    def test_synthesize_evidence_item_falls_back_to_title(self) -> None:
        source = _normalized_source(title="Fallback title")
        item = _synthesize_evidence_item(source)
        assert item.body == "Fallback title"

    def test_synthesize_evidence_item_confidence_from_trust_score(self) -> None:
        source = _normalized_source(trust_score=0.65)
        item = _synthesize_evidence_item(source)
        assert item.confidence == pytest.approx(0.65)

    def test_synthesize_evidence_bundle_links_source_and_item(self) -> None:
        source = _normalized_source()
        item = _synthesize_evidence_item(source)
        bundle = _synthesize_evidence_bundle(source, item)

        assert source.source_id in bundle.source_ids
        assert item.evidence_item_id in bundle.evidence_item_ids
        assert item.citation_label in bundle.citation_refs

    def test_distillation_job_roundtrip(self) -> None:
        job = DistillationJob(
            job_id="distill-abc123",
            source_id="src-001",
            idempotency_key="distill:src-001:worker",
            status=DistillationJobStatus.PENDING,
            enqueued_at="2026-06-27T10:00:00Z",
        )
        restored = DistillationJob.from_dict(job.to_dict())
        assert restored.job_id == job.job_id
        assert restored.source_id == job.source_id
        assert restored.status == job.status

    def test_distillation_job_is_pending(self) -> None:
        job = DistillationJob(
            job_id="j", source_id="s", idempotency_key="k",
            status=DistillationJobStatus.PENDING, enqueued_at="2026-06-27T10:00:00Z",
        )
        assert job.is_pending is True
        assert job.is_terminal is False

    def test_distillation_job_done_is_terminal(self) -> None:
        job = DistillationJob(
            job_id="j", source_id="s", idempotency_key="k",
            status=DistillationJobStatus.DONE, enqueued_at="2026-06-27T10:00:00Z",
        )
        assert job.is_terminal is True
        assert job.is_pending is False


# ---------------------------------------------------------------------------
# DistillationJobQueue unit tests
# ---------------------------------------------------------------------------

class TestDistillationJobQueue:
    def test_enqueue_creates_pending_job(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        job = q.enqueue("src-001")
        assert job.status == DistillationJobStatus.PENDING
        assert job.source_id == "src-001"

    def test_enqueue_idempotent(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        j1 = q.enqueue("src-001")
        j2 = q.enqueue("src-001")
        assert j1.job_id == j2.job_id
        assert q.count() == 1

    def test_mark_done_transitions_status(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        job = q.enqueue("src-001")
        q.mark_done(job.job_id, seed_id="seed-abc")
        stored = q.get("src-001")
        assert stored.status == DistillationJobStatus.DONE
        assert stored.seed_id == "seed-abc"

    def test_mark_failed_transitions_status(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        job = q.enqueue("src-001")
        q.mark_failed(job.job_id, error="something went wrong")
        stored = q.get("src-001")
        assert stored.status == DistillationJobStatus.FAILED
        assert "wrong" in stored.error

    def test_mark_skipped_transitions_status(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        job = q.enqueue("src-001")
        q.mark_skipped(job.job_id, reason="seed is immutable")
        stored = q.get("src-001")
        assert stored.status == DistillationJobStatus.SKIPPED
        assert "immutable" in stored.skip_reason

    def test_list_pending_returns_only_pending(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        j1 = q.enqueue("src-001")
        j2 = q.enqueue("src-002")
        q.mark_done(j1.job_id, seed_id="seed-1")

        pending = q.list_pending()
        assert len(pending) == 1
        assert pending[0].source_id == "src-002"

    def test_reset_to_pending_from_done(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        job = q.enqueue("src-001")
        q.mark_done(job.job_id, seed_id="seed-1")
        reset = q.reset_to_pending("src-001")
        assert reset.status == DistillationJobStatus.PENDING
        assert q.list_pending()[0].source_id == "src-001"

    def test_reset_to_pending_idempotent_when_already_pending(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        q.enqueue("src-001")
        q.reset_to_pending("src-001")
        q.reset_to_pending("src-001")
        assert q.count() == 1
        assert len(q.list_pending()) == 1

    def test_reset_to_pending_creates_new_job_if_none_exists(self, tmp_path: Path) -> None:
        q = DistillationJobQueue(tmp_path / "q.jsonl")
        job = q.reset_to_pending("src-fresh")
        assert job.status == DistillationJobStatus.PENDING
        assert q.count() == 1

    def test_corrupt_queue_file_self_heals_instead_of_crash_looping(self, tmp_path: Path) -> None:
        """A corrupted sqlite file must not wedge the controller permanently.

        Regression test for a live incident: strategy-distillation-worker
        crash-looped on every tick with sqlite3.DatabaseError("database disk
        image is malformed") because the queue file was corrupted, and
        nothing recovered it. The fix quarantines the corrupt file and starts
        a fresh queue instead of propagating the error.
        """
        path = tmp_path / "q.jsonl"
        path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)

        q = DistillationJobQueue(path)

        job = q.enqueue("src-001")
        assert job.status == DistillationJobStatus.PENDING
        quarantined = list(tmp_path.glob("q.jsonl.corrupt-*"))
        assert len(quarantined) == 1

    def test_non_corruption_database_error_is_not_swallowed(self, tmp_path: Path) -> None:
        """Only genuine corruption should trigger quarantine-and-recreate;
        other DatabaseErrors must still propagate."""
        path = tmp_path / "q.jsonl"
        path.mkdir()

        with pytest.raises(Exception):
            DistillationJobQueue(path)


class TestEventAdmissionSharesQueueWithCatchUp:
    """L12-GAP-F02: a commit-time enqueue and the controller's catch_up poll
    must resolve to the same durable queue, not two independent mechanisms."""

    def test_commit_time_admission_is_visible_without_a_catch_up_poll(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "shared_job_queue.sqlite3"
        source = _normalized_source(source_id="src-event-001")

        # Simulate the ingest-side admission: a fresh queue handle opened on
        # the same durable path as the distillation controller would use.
        ingest_side_queue = DistillationJobQueue(queue_path)
        admitted = ingest_side_queue.enqueue_source_record(source, requested_by="source-ingest")

        # A second, independent handle on the same path is what the
        # distillation controller opens on its own tick; it must already see
        # the admitted job without running catch_up's enqueue step first.
        controller_side_queue = DistillationJobQueue(queue_path)
        pending = controller_side_queue.list_pending()
        assert len(pending) == 1
        assert pending[0].job_id == admitted.job_id
        assert pending[0].source_digest == source_version_digest(source)

    def test_catch_up_does_not_duplicate_an_already_admitted_job(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "shared_job_queue.sqlite3"
        source = _normalized_source(source_id="src-event-002")

        ingest_side_queue = DistillationJobQueue(queue_path)
        admitted = ingest_side_queue.enqueue_source_record(source, requested_by="source-ingest")

        with tempfile.TemporaryDirectory() as seed_dir:
            worker = DistillationWorker(
                DistillationJobQueue(queue_path),
                StrategySpecSeedStore(Path(seed_dir) / "seeds.jsonl"),
            )
            result = worker.catch_up([source])

        # catch_up's own enqueue step is a no-op recovery path here: the job
        # was already admitted at commit time, so nothing new is enqueued,
        # and the exact same job_id is what gets processed.
        assert result.enqueued == 0
        assert result.processed == 1
        assert DistillationJobQueue(queue_path).get(source.source_id, source_version_digest(source)).job_id == (
            admitted.job_id
        )
