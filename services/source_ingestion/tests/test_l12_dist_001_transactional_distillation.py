"""L12-DIST-001 acceptance proof: transactional, replayable distillation.

Each test maps to one acceptance criterion on the task packet:

  AC-1  A committed normalized SourceRecord transactionally enqueues exactly
        one versioned distillation job.
  AC-2  Concurrent workers claim under lease, and revised content is a
        distinct event resolved by source digest.
  AC-3  Registry failure records controller failure plus durable retry or DLQ;
        it is never recorded as success.
  AC-4  Approved (immutable) Registry artifacts are never rewritten.
  AC-5  A crash before or after the Registry write replays to exactly one
        terminal draft.

The two-worker proof uses genuinely independent OS processes against one
shared ledger. Same-process thread tests are kept only as regressions and are
named ``*_threads_*``; they are not the concurrency acceptance proof.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import socket
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
import uvicorn

from services.source_ingestion.connectors.base import SourceRecord
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    DistillationControllerError,
    run_controller_tick,
)
from services.source_ingestion.distillation_worker import (
    DistillationError,
    DistillationJobQueue,
    DistillationJobStatus,
    RegistrySyncRequest,
    RegistrySyncResult,
    make_distillation_worker,
    source_version_digest,
)
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _normalized_source(
    source_id: str = "src-dist-001",
    *,
    title: str = "LightGBM TW equity momentum factor paper",
    **metadata_overrides: Any,
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector_id="conn-papers",
        source_type="paper",
        title=title,
        content_ref=f"https://doi.org/10.1000/{source_id}",
        status="normalized",
        metadata={
            "trust_score": 0.8,
            "access_scope": ["research"],
            "license_scope": "internal",
            "keywords": ["momentum", "lightgbm", "equity"],
            "strategy_seed": {
                "hypothesis": f"{title} carries a tradable momentum signal",
                "asset_class": ["equity"],
                "market_scope": ["Taiwan"],
                "holding_period": "5 days",
                "required_data": ["OHLCV"],
                "backend_hint": "qlib",
                "feature_hints": ["momentum"],
                "label_hints": ["return"],
                "risk_notes": ["none"],
            },
            **metadata_overrides,
        },
    )


class _RecordingRegistry:
    """In-test Registry sink that records every write and readback."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}
        self.writes: list[str] = []
        self.readbacks: list[str] = []
        self.fail_writes_until: int = 0
        self.drop_ack_after_write: bool = False

    def get(self, registry_id: str) -> dict[str, Any] | None:
        self.readbacks.append(registry_id)
        return self.entries.get(registry_id)

    def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        registry_id = payload["registry_id"]
        if len(self.writes) < self.fail_writes_until:
            self.writes.append(registry_id)
            raise RuntimeError("registry outage: connection refused")
        self.writes.append(registry_id)
        entry = {"registry_id": registry_id, "artifact_state": "draft"}
        self.entries[registry_id] = {"entry": entry}
        if self.drop_ack_after_write:
            # The write landed but the worker never sees the acknowledgement.
            raise RuntimeError("registry ack lost after durable write")
        return {"entry": entry}

    def approve(self, registry_id: str) -> None:
        self.entries[registry_id] = {
            "entry": {"registry_id": registry_id, "artifact_state": "approved"}
        }


@pytest.fixture()
def registry(monkeypatch: pytest.MonkeyPatch) -> _RecordingRegistry:
    fake = _RecordingRegistry()
    monkeypatch.setattr(
        "services.source_ingestion.distillation_controller._get_registry_entry",
        lambda url, registry_id: fake.get(registry_id),
    )
    monkeypatch.setattr(
        "services.source_ingestion.distillation_controller._register_strategy_spec",
        lambda url, payload: fake.write(payload),
    )
    return fake


class _DummyLoopWriter:
    def __init__(self) -> None:
        self.successes: list[dict[str, Any]] = []
        self.ticks: list[dict[str, Any]] = []

    async def record_success(self, **kwargs: Any) -> None:
        self.successes.append(kwargs)

    async def record_tick(self, **kwargs: Any) -> None:
        self.ticks.append(kwargs)


def _controller_config(
    tmp_path: Path,
    *,
    registry_url: str = "http://mock-registry:8087",
    max_attempts: int = 3,
    retry_base_seconds: int = 5,
) -> DistillationControllerConfig:
    return DistillationControllerConfig(
        database_url="postgresql://test:test@localhost:5432/test",
        registry_url=registry_url,
        interval_seconds=60,
        max_ticks=1,
        state_path=tmp_path / "controller_state.json",
        alive_path=tmp_path / "controller_alive",
        job_queue_path=tmp_path / "job_queue.sqlite3",
        seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=tmp_path / "source_evidence.jsonl",
        source_dirs=[tmp_path],
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
    )


def _write_evidence(config: DistillationControllerConfig, *records: SourceRecord) -> None:
    with open(config.evidence_store_path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n"
            )


def _fresh_state() -> ControllerState:
    return ControllerState(
        controller_id="test-distill-controller",
        controller_name="test-distillation-controller",
        environment="test",
        tenant_id="test",
        deployment={"git_sha": "test-sha"},
    )


def _run_tick(
    config: DistillationControllerConfig,
    writer: _DummyLoopWriter,
    state: ControllerState | None = None,
) -> tuple[dict[str, Any] | None, Exception | None, ControllerState]:
    state = state or _fresh_state()
    store = ControllerStateStore(config.state_path)
    store.save(state)
    try:
        return run_controller_tick(config=config, state=state, store=store, writer=writer), None, state
    except Exception as exc:  # noqa: BLE001 - the controller failure is the assertion subject
        return None, exc, state


def _registry_id_for(source: SourceRecord) -> str:
    digest = source_version_digest(source).removeprefix("sha256:")
    return f"reg-strategy-spec-{source.source_id}-{digest[:12]}"


# ---------------------------------------------------------------------------
# AC-1: one committed normalized version -> exactly one versioned job
# ---------------------------------------------------------------------------

class TestVersionedAdmission:
    def test_committed_source_enqueues_exactly_one_versioned_job(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        source = _normalized_source()

        first = queue.enqueue_source_record(source)
        second = queue.enqueue_source_record(source)

        assert first.job_id == second.job_id
        assert queue.count() == 1
        assert queue.version_count(source.source_id) == 1
        assert first.source_digest == source_version_digest(source)
        assert first.event_version == "source_record.normalized.v1"
        assert first.status == DistillationJobStatus.PENDING

    def test_source_snapshot_is_committed_with_the_job(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        source = _normalized_source()

        job = queue.enqueue_source_record(source)
        replayed = queue.source_for_job(job)

        # The job carries its own committed snapshot; a later in-memory
        # revision cannot retroactively change what this event meant.
        assert replayed is not None
        assert replayed.to_dict() == source.to_dict()

    def test_only_normalized_versions_are_admitted(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        rejected = SourceRecord(
            source_id="src-rejected-001",
            connector_id="conn-papers",
            source_type="paper",
            title="Rejected paper",
            content_ref="https://doi.org/rejected",
            status="rejected",
        )

        with pytest.raises(DistillationError, match="Only normalized SourceRecord"):
            queue.enqueue_source_record(rejected)
        assert queue.count() == 0

    def test_identity_collision_rolls_back_the_whole_admission(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        source = _normalized_source()
        queue.enqueue_source_record(source)
        digest = source_version_digest(source)

        # Same version identity, contradicting payload: the transaction must
        # abort rather than leave a half-written version/outbox pair.
        with pytest.raises(DistillationError, match="identity collision"):
            queue._insert_source_version_and_job(
                source_id=source.source_id,
                source_digest=digest,
                payload_json='{"source_id":"tampered"}',
                requested_by="test",
                enqueued_at=time.time(),
                event_version="source_record.normalized.v1",
                versioned=True,
            )

        assert queue.count() == 1
        assert queue.version_count(source.source_id) == 1
        assert queue.source_for_job(queue.get(source.source_id)).to_dict() == source.to_dict()


# ---------------------------------------------------------------------------
# AC-2: concurrent workers under lease; revised content is a distinct version
# ---------------------------------------------------------------------------

def _claim_in_child(queue_path: str, worker_id: str, out: Any) -> None:
    """Child-process entry point: claim due jobs and report the ids."""
    from services.source_ingestion.distillation_worker import DistillationJobQueue as Q

    queue = Q(Path(queue_path))
    claimed = queue.claim_due(worker_id=worker_id, lease_seconds=60, limit=100)
    out.put([job.job_id for job in claimed])


def _distill_in_child(
    queue_path: str,
    seed_path: str,
    worker_id: str,
    out: Any,
    barrier: Any = None,
) -> None:
    """Child-process entry point: contend for jobs one at a time.

    Claiming a single job per pass (behind a shared barrier) keeps both
    processes writing to the shared seed store at the same time, which is what
    makes this a concurrency proof rather than a drain-by-the-first-starter.
    """
    from services.source_ingestion.distillation_worker import make_distillation_worker as factory

    worker = factory(
        queue_path=Path(queue_path),
        seed_store_path=Path(seed_path),
        created_by=worker_id,
        worker_id=worker_id,
        lease_seconds=60,
    )
    if barrier is not None:
        barrier.wait(timeout=120)

    totals = {"processed": 0, "created": 0, "failed": 0, "skipped": 0}
    empty_passes = 0
    while empty_passes < 20:
        result = worker.run_pending(None, limit=1)
        if result.processed == 0:
            empty_passes += 1
            time.sleep(0.05)
            continue
        empty_passes = 0
        totals["processed"] += result.processed
        totals["created"] += result.created
        totals["failed"] += result.failed
        totals["skipped"] += result.skipped
    out.put(totals)


class TestConcurrentWorkers:
    def test_two_processes_claim_disjoint_jobs(self, tmp_path: Path) -> None:
        queue_path = tmp_path / "queue.sqlite3"
        queue = DistillationJobQueue(queue_path)
        expected = set()
        for index in range(12):
            job = queue.enqueue_source_record(_normalized_source(f"src-conc-{index:03d}"))
            expected.add(job.job_id)

        ctx = mp.get_context("spawn")
        out: Any = ctx.Queue()
        children = [
            ctx.Process(target=_claim_in_child, args=(str(queue_path), f"worker-{n}", out))
            for n in range(2)
        ]
        for child in children:
            child.start()
        batches = [out.get(timeout=120) for _ in children]
        for child in children:
            child.join(timeout=120)
            assert child.exitcode == 0

        claimed = [job_id for batch in batches for job_id in batch]
        # A lease is exclusive: every job is claimed by exactly one process.
        assert sorted(claimed) == sorted(expected)
        assert len(claimed) == len(set(claimed)) == 12
        assert queue.metrics()["leased"] == 12

    def test_two_processes_produce_one_terminal_draft_per_source(self, tmp_path: Path) -> None:
        total_sources = 40
        queue_path = tmp_path / "queue.sqlite3"
        seed_path = tmp_path / "seeds.jsonl"
        queue = DistillationJobQueue(queue_path)
        for index in range(total_sources):
            queue.enqueue_source_record(_normalized_source(f"src-mp-{index:03d}"))

        ctx = mp.get_context("spawn")
        out: Any = ctx.Queue()
        barrier = ctx.Barrier(2)
        children = [
            ctx.Process(
                target=_distill_in_child,
                args=(str(queue_path), str(seed_path), f"worker-{n}", out, barrier),
            )
            for n in range(2)
        ]
        for child in children:
            child.start()
        results = [out.get(timeout=300) for _ in children]
        for child in children:
            child.join(timeout=300)
            assert child.exitcode == 0

        # Both processes must have genuinely contended, or this proves nothing.
        assert all(r["processed"] > 0 for r in results), results
        assert sum(r["processed"] for r in results) == total_sources
        assert sum(r["created"] for r in results) == total_sources
        assert sum(r["failed"] for r in results) == 0

        jobs = queue.list_all()
        assert len(jobs) == total_sources
        assert all(job.status == DistillationJobStatus.DONE.value for job in jobs)
        # No lost update: the shared JSONL seed store kept every writer's seed.
        seeds = StrategySpecSeedStore(seed_path).list_all()
        assert len(seeds) == total_sources
        assert len({seed.seed_id for seed in seeds}) == total_sources

    def test_stale_lease_completion_is_rejected(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        job = queue.enqueue_source_record(_normalized_source())

        first = queue.claim_due(worker_id="worker-a", lease_seconds=1, limit=10)[0]
        # Lease expires; a second worker re-claims and fences the first token.
        second = queue.claim_due(
            worker_id="worker-b", lease_seconds=60, now=time.time() + 5, limit=10
        )[0]
        assert second.job_id == job.job_id
        assert second.lease_token != first.lease_token

        with pytest.raises(DistillationError, match="stale distillation lease"):
            queue.mark_done(job.job_id, seed_id="seed-stale", claim_token=first.lease_token)

        queue.mark_done(job.job_id, seed_id="seed-fresh", claim_token=second.lease_token)
        assert queue.get(job.source_id).seed_id == "seed-fresh"

    def test_revised_content_is_a_distinct_versioned_job(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        original = _normalized_source("src-rev-001", title="Momentum factor v1")
        revised = _normalized_source("src-rev-001", title="Momentum factor v2 (revised)")

        first = queue.enqueue_source_record(original)
        second = queue.enqueue_source_record(revised)

        assert first.job_id != second.job_id
        assert first.source_digest != second.source_digest
        assert queue.version_count("src-rev-001") == 2
        assert queue.count() == 2

    def test_identical_recrawl_does_not_create_a_second_version(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        first_run = _normalized_source("src-dup-001", ingest_run_id="run-a", trace_id="t-a")
        second_run = _normalized_source("src-dup-001", ingest_run_id="run-b", trace_id="t-b")

        first = queue.enqueue_source_record(first_run)
        second = queue.enqueue_source_record(second_run)

        # Run-local correlation fields are not content: same content, one job.
        assert first.job_id == second.job_id
        assert queue.version_count("src-dup-001") == 1

    def test_revised_content_syncs_two_distinct_registry_drafts(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        config = _controller_config(tmp_path)
        original = _normalized_source("src-rev-002", title="Cross-sectional factor v1")
        _write_evidence(config, original)
        result, error, _ = _run_tick(config, _DummyLoopWriter())
        assert error is None and result["actual"]["synced_count"] == 1

        revised = _normalized_source("src-rev-002", title="Cross-sectional factor v2")
        _write_evidence(config, revised)
        result, error, _ = _run_tick(config, _DummyLoopWriter())
        assert error is None and result["actual"]["synced_count"] == 1

        assert registry.writes == [_registry_id_for(original), _registry_id_for(revised)]
        assert len(registry.entries) == 2


# ---------------------------------------------------------------------------
# AC-3: Registry failure is truthful and durable
# ---------------------------------------------------------------------------

class TestRegistryFailureIsTruthful:
    def test_registry_outage_fails_the_tick_and_parks_a_durable_retry(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        registry.fail_writes_until = 99
        config = _controller_config(tmp_path)
        source = _normalized_source("src-outage-001")
        _write_evidence(config, source)
        writer = _DummyLoopWriter()

        result, error, state = _run_tick(config, writer)

        # The controller must not record a success it did not achieve.
        assert result is None
        assert isinstance(error, DistillationControllerError)
        assert error.stage == "registry_sync"
        assert writer.successes == []
        assert len(writer.ticks) == 1
        assert "registry_sync" in json.dumps(writer.ticks[0])
        assert state.last_success_at is None
        assert state.last_failure_stage == "registry_sync"
        assert state.consecutive_failures == 1

        job = DistillationJobQueue(config.job_queue_path).get(source.source_id)
        assert job.status == DistillationJobStatus.RETRY_WAIT.value
        assert "registry outage" in job.error
        assert job.next_attempt_at is not None
        assert registry.entries == {}

    def test_exhausted_attempts_dead_letter_durably(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        registry.fail_writes_until = 99
        config = _controller_config(tmp_path, max_attempts=2, retry_base_seconds=0)
        source = _normalized_source("src-dlq-001")
        _write_evidence(config, source)

        for _ in range(2):
            _, error, _ = _run_tick(config, _DummyLoopWriter())
            assert isinstance(error, DistillationControllerError)

        queue = DistillationJobQueue(config.job_queue_path, default_max_attempts=2)
        job = queue.get(source.source_id)
        assert job.status == DistillationJobStatus.DEAD_LETTER.value
        dead_letters = queue.list_dead_letters()
        assert len(dead_letters) == 1
        assert dead_letters[0]["source_id"] == source.source_id
        # The dead letter carries the committed payload, so it stays redrivable.
        assert json.loads(dead_letters[0]["payload_json"])["source_id"] == source.source_id

    def test_registry_recovery_replays_the_parked_job_to_done(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        registry.fail_writes_until = 1
        config = _controller_config(tmp_path, retry_base_seconds=0)
        source = _normalized_source("src-recover-001")
        _write_evidence(config, source)

        _, error, _ = _run_tick(config, _DummyLoopWriter())
        assert isinstance(error, DistillationControllerError)

        result, error, _ = _run_tick(config, _DummyLoopWriter())
        assert error is None
        assert result["actual"]["synced_count"] == 1

        job = DistillationJobQueue(config.job_queue_path).get(source.source_id)
        assert job.status == DistillationJobStatus.DONE.value
        assert job.registry_id == _registry_id_for(source)
        assert len(registry.entries) == 1


# ---------------------------------------------------------------------------
# AC-4: approved artifacts are immutable
# ---------------------------------------------------------------------------

class TestApprovedArtifactsAreImmutable:
    def test_approved_registry_entry_is_never_rewritten(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        config = _controller_config(tmp_path)
        source = _normalized_source("src-approved-001")
        _write_evidence(config, source)
        registry.approve(_registry_id_for(source))
        approved_before = dict(registry.entries[_registry_id_for(source)])

        result, error, _ = _run_tick(config, _DummyLoopWriter())

        assert error is None
        assert result["actual"]["synced_count"] == 0
        assert result["actual"]["skipped_immutable_count"] == 1
        assert registry.writes == []
        assert registry.entries[_registry_id_for(source)] == approved_before

        job = DistillationJobQueue(config.job_queue_path).get(source.source_id)
        assert job.status == DistillationJobStatus.SKIPPED.value
        assert "immutable" in job.skip_reason

    def test_approval_between_probe_and_write_still_blocks_mutation(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        config = _controller_config(tmp_path)
        source = _normalized_source("src-approved-002")
        _write_evidence(config, source)
        registry_id = _registry_id_for(source)

        original_write = registry.write

        def approve_on_write(payload: dict[str, Any]) -> dict[str, Any]:
            response = original_write(payload)
            registry.approve(registry_id)  # approved before our readback lands
            return response

        registry.write = approve_on_write  # type: ignore[method-assign]

        result, error, _ = _run_tick(config, _DummyLoopWriter())

        # The terminal readback, not the write response, decides the outcome.
        assert error is None
        assert result["actual"]["skipped_immutable_count"] == 1
        assert result["actual"]["synced_count"] == 0
        assert registry.entries[registry_id]["entry"]["artifact_state"] == "approved"

    def test_accepted_seed_draft_is_not_overwritten(self, tmp_path: Path) -> None:
        seed_path = tmp_path / "seeds.jsonl"
        worker = make_distillation_worker(
            queue_path=tmp_path / "queue.sqlite3",
            seed_store_path=seed_path,
            created_by="test-worker",
        )
        source = _normalized_source("src-seed-immutable-001")
        worker.enqueue_from_source_record(source)
        worker.run_pending(None)

        store = StrategySpecSeedStore(seed_path)
        seed = store.list_all()[0]
        store.record_review_decision(
            seed.seed_id, decision="accept", reviewer_id="test-reviewer", reason="good"
        )

        worker.redispatch(source.source_id, source)
        result = worker.run_pending(None)

        assert result.skipped == 1
        assert result.created == 0
        assert store.get(seed.seed_id).status.value == "accepted"


# ---------------------------------------------------------------------------
# AC-5: crash before or after the Registry write replays to one terminal draft
# ---------------------------------------------------------------------------

class TestCrashReplay:
    def test_crash_before_registry_write_replays_to_one_terminal_draft(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        config = _controller_config(tmp_path)
        source = _normalized_source("src-crash-before-001")
        _write_evidence(config, source)

        # Worker A claims the job and dies without ever reaching the Registry.
        queue = DistillationJobQueue(config.job_queue_path)
        queue.enqueue_source_record(source)
        crashed = queue.claim_due(worker_id="worker-crashed", lease_seconds=1, limit=10)
        assert len(crashed) == 1
        assert registry.writes == []

        time.sleep(1.1)  # the lease expires; the event is recoverable

        result, error, _ = _run_tick(config, _DummyLoopWriter())

        assert error is None
        assert result["actual"]["synced_count"] == 1
        assert registry.writes == [_registry_id_for(source)]
        jobs = DistillationJobQueue(config.job_queue_path).list_all()
        assert len(jobs) == 1
        assert jobs[0].status == DistillationJobStatus.DONE.value
        assert len(StrategySpecSeedStore(config.seed_store_path).list_all()) == 1

    def test_crash_after_registry_write_replays_without_duplicating(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        config = _controller_config(tmp_path, retry_base_seconds=0)
        source = _normalized_source("src-crash-after-001")
        _write_evidence(config, source)

        # The Registry write lands but the acknowledgement is lost.
        registry.drop_ack_after_write = True
        _, error, _ = _run_tick(config, _DummyLoopWriter())
        assert isinstance(error, DistillationControllerError)
        assert registry.writes == [_registry_id_for(source)]

        registry.drop_ack_after_write = False
        result, error, _ = _run_tick(config, _DummyLoopWriter())

        # Replay reads back the versioned draft instead of writing a second one.
        assert error is None
        assert result["actual"]["synced_count"] == 1
        assert registry.writes == [_registry_id_for(source)]
        assert len(registry.entries) == 1
        jobs = DistillationJobQueue(config.job_queue_path).list_all()
        assert len(jobs) == 1
        assert jobs[0].status == DistillationJobStatus.DONE.value
        assert jobs[0].registry_id == _registry_id_for(source)
        assert len(StrategySpecSeedStore(config.seed_store_path).list_all()) == 1

    def test_repeated_ticks_are_idempotent_after_terminal_success(
        self, tmp_path: Path, registry: _RecordingRegistry
    ) -> None:
        config = _controller_config(tmp_path)
        source = _normalized_source("src-idem-001")
        _write_evidence(config, source)

        for _ in range(3):
            _, error, _ = _run_tick(config, _DummyLoopWriter())
            assert error is None

        assert registry.writes == [_registry_id_for(source)]
        assert len(StrategySpecSeedStore(config.seed_store_path).list_all()) == 1
        assert DistillationJobQueue(config.job_queue_path).count() == 1


# ---------------------------------------------------------------------------
# Real source-to-Registry service proof (no HTTP mock in this section)
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _registry_service() -> Iterator[str]:
    """Serve the real registry FastAPI app over real HTTP on a real port."""
    from services.registry import service as registry_service_module
    from services.registry.storage import reset_store

    reset_store()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            registry_service_module.app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() >= deadline:
            raise RuntimeError("registry test server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        reset_store()


def _http_get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


class TestRealSourceToRegistryService:
    def test_normalized_source_reaches_the_real_registry_service(self, tmp_path: Path) -> None:
        source = _normalized_source("src-live-001", title="TW momentum live service proof")
        with _registry_service() as registry_url:
            config = _controller_config(tmp_path, registry_url=registry_url)
            _write_evidence(config, source)
            writer = _DummyLoopWriter()

            result, error, _ = _run_tick(config, writer)

            assert error is None, f"controller tick failed: {error}"
            assert result["actual"]["synced_count"] == 1
            assert len(writer.successes) == 1

            registry_id = _registry_id_for(source)
            entry = _http_get_json(
                f"{registry_url}/api/registry/strategy-specs/{registry_id}"
            )
            payload = entry.get("entry") or entry
            assert payload["registry_id"] == registry_id
            assert payload["artifact_state"] == "draft"

            # Source lineage survives the real HTTP round trip.
            distillation = (payload.get("metadata") or {})["distillation"]
            assert distillation["source_id"] == source.source_id
            assert distillation["source_digest"] == source_version_digest(source)
            assert distillation["source_event_version"] == "source_record.normalized.v1"

        job = DistillationJobQueue(config.job_queue_path).get(source.source_id)
        assert job.status == DistillationJobStatus.DONE.value
        assert job.registry_id == registry_id

    def test_real_service_outage_then_recovery_replays_once(self, tmp_path: Path) -> None:
        source = _normalized_source("src-live-002", title="TW momentum outage replay proof")
        # The service is not listening yet: a real connection failure.
        config = _controller_config(
            tmp_path, registry_url=f"http://127.0.0.1:{_free_port()}", retry_base_seconds=0
        )
        _write_evidence(config, source)

        _, error, _ = _run_tick(config, _DummyLoopWriter())
        assert isinstance(error, DistillationControllerError)
        assert error.stage == "registry_sync"
        job = DistillationJobQueue(config.job_queue_path).get(source.source_id)
        assert job.status == DistillationJobStatus.RETRY_WAIT.value

        with _registry_service() as registry_url:
            recovered = _controller_config(
                tmp_path, registry_url=registry_url, retry_base_seconds=0
            )
            result, error, _ = _run_tick(recovered, _DummyLoopWriter())

            assert error is None
            assert result["actual"]["synced_count"] == 1
            registry_id = _registry_id_for(source)
            entry = _http_get_json(
                f"{registry_url}/api/registry/strategy-specs/{registry_id}"
            )
            assert (entry.get("entry") or entry)["registry_id"] == registry_id

        assert DistillationJobQueue(config.job_queue_path).count() == 1


# ---------------------------------------------------------------------------
# Same-process regressions (NOT the concurrency acceptance proof)
# ---------------------------------------------------------------------------

class TestThreadRegressions:
    def test_threads_never_double_claim_one_job(self, tmp_path: Path) -> None:
        queue = DistillationJobQueue(tmp_path / "queue.sqlite3")
        for index in range(20):
            queue.enqueue_source_record(_normalized_source(f"src-thread-{index:03d}"))

        claimed: list[str] = []
        guard = threading.Lock()

        def claim(worker_id: str) -> None:
            jobs = queue.claim_due(worker_id=worker_id, lease_seconds=60, limit=20)
            with guard:
                claimed.extend(job.job_id for job in jobs)

        threads = [threading.Thread(target=claim, args=(f"t-{n}",)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert len(claimed) == 20
        assert len(set(claimed)) == 20
