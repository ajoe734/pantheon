"""Real SQLite lifetime/locking regressions, including separate OS processes.

Synthetic local storage tests, not Registry or hosted business acceptance.
"""
from contextlib import closing
import gc
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from services.source_ingestion import distillation_controller as controller
from services.source_ingestion import distillation_worker as storage
from services.source_ingestion.connectors.base import SourceRecord


def _exclusive_shared_range_available(path: Path) -> bool:
    # Independent PID, not a second connection in this process (fcntl locks
    # are process-owned). Do not fork with an inherited SQLite connection.
    probe = """
import fcntl, sys
with open(sys.argv[1], 'rb+') as handle:
    try:
        fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB, 510, 1073741826)
        print('acquired')
    except BlockingIOError:
        print('blocked')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(path)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() in {"acquired", "blocked"}
    return result.stdout.strip() == "acquired"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux POSIX SQLite lock regression")
def test_constructor_does_not_release_another_sqlite_connections_locks(tmp_path):
    path = tmp_path / "queue.jsonl"
    storage.DistillationJobQueue(path)
    with closing(sqlite3.connect(path)) as reader:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM distillation_outbox").fetchall()
        assert not _exclusive_shared_range_available(path)
        storage.DistillationJobQueue(path)
        assert not _exclusive_shared_range_available(path)


@pytest.mark.parametrize("fail_setup", [False, True])
def test_connections_close_without_gc_even_on_setup_failure(tmp_path, monkeypatch, fail_setup):
    connections = []  # Keep strong references: GC cannot hide a leak.
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if fail_setup and sql == "PRAGMA foreign_keys = ON":
                raise sqlite3.OperationalError("injected setup failure")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs, factory=Connection)
        connections.append(conn)
        return conn

    monkeypatch.setattr(storage.sqlite3, "connect", connect)
    try:
        if fail_setup:
            with pytest.raises(sqlite3.OperationalError, match="injected setup failure"):
                storage.DistillationJobQueue(tmp_path / "queue.sqlite3")
        else:
            queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3")
            queue.enqueue("source-1")
            assert queue.count() == 1
            with pytest.raises(RuntimeError, match="rollback"):
                with queue._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("DELETE FROM distillation_outbox")
                    raise RuntimeError("rollback")
            assert queue.count() == 1
        assert connections
        for conn in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                conn.execute("SELECT 1")
    finally:
        for conn in connections:
            conn.close()


def test_quick_check_non_ok_blocks_before_schema_writes(tmp_path, monkeypatch):
    original = sqlite3.connect
    statements = []
    connections = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            statements.append(sql)
            if sql == "PRAGMA quick_check":
                return super().execute("SELECT 'invalid page number'")
            return super().execute(sql, *args, **kwargs)

        def executescript(self, sql):
            pytest.fail("must not bootstrap schema after a failed quick_check")

    def connect(*args, **kwargs):
        conn = original(*args, **kwargs, factory=Connection)
        connections.append(conn)
        return conn

    monkeypatch.setattr(storage.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError, match="offline recovery required"):
        storage.DistillationJobQueue(tmp_path / "queue.sqlite3")
    assert "PRAGMA quick_check" in statements
    assert "PRAGMA journal_mode = WAL" not in statements
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def _concurrent_writer(path: str, prefix: str, barrier, count: int) -> None:
    # Amplify delayed collection deterministically rather than depending on
    # CPython's changing GC thresholds. No queue connection should depend on GC.
    gc.disable()
    try:
        queue = storage.DistillationJobQueue(path)
        barrier.wait(timeout=20)
        for index in range(count):
            # Retain the old controller's repeated-construction pattern as an
            # adversarial regression, even though main now reuses one queue.
            if prefix == "controller":
                queue = storage.DistillationJobQueue(path)
            source = SourceRecord(
                source_id=f"{prefix}-{index}", connector_id="local-regression",
                source_type="paper", status="normalized", title=f"{prefix}-{index}",
                content_ref=f"file:///synthetic/{prefix}/{index}",
                metadata={"body": prefix * 8192},
            )
            job = queue.enqueue_source_record(source)
            assert queue.enqueue_source_record(source).job_id == job.job_id
            if index % 5 == 0:
                for claimed in queue.claim_due(worker_id=prefix, lease_seconds=60, limit=10):
                    snapshot = queue.source_for_job(claimed)
                    assert snapshot.source_id == claimed.source_id
                    assert snapshot.metadata["body"] == claimed.source_id.split("-")[0] * 8192
                    queue.mark_done(claimed.job_id, seed_id=f"synthetic-{claimed.job_id}",
                                    claim_token=claimed.lease_token)
            if prefix == "ingest":
                # Model one process collecting idle connections while its
                # peer still retains its SQLite connections and WAL view.
                gc.collect()
            barrier.wait(timeout=20)
    finally:
        gc.enable()
        gc.collect()


@pytest.mark.parametrize("iteration", range(3))
def test_two_process_queue_preserves_versions_receipts_and_integrity(tmp_path, iteration):
    path = tmp_path / f"shared-{iteration}.jsonl"
    storage.DistillationJobQueue(path)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    count = 40
    processes = [ctx.Process(target=_concurrent_writer, args=(str(path), prefix, barrier, count))
                 for prefix in ("ingest", "controller")]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=60)
            assert process.exitcode == 0, f"worker exit={process.exitcode}, pid={process.pid}"
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
    queue = storage.DistillationJobQueue(path)
    for job in queue.claim_due(worker_id="test-drain", lease_seconds=60, limit=100):
        queue.mark_done(job.job_id, seed_id=f"synthetic-{job.job_id}", claim_token=job.lease_token)
    jobs = queue.list_all()
    assert {job.source_id for job in jobs} == {
        f"{prefix}-{index}" for prefix in ("ingest", "controller") for index in range(count)
    }
    assert len(jobs) == 2 * count
    assert all(job.status == "done" for job in jobs)
    assert queue.list_dead_letters() == []
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert conn.execute("SELECT count(*) FROM distillation_source_versions").fetchone()[0] == 2 * count
        assert conn.execute("SELECT count(*) FROM distillation_inbox WHERE status='applied'").fetchone()[0] == 2 * count
    assert not list(tmp_path.glob("*.corrupt-*"))
    assert not Path(f"{path}.sqlite3").exists()


def _cold_constructor(path: str, barrier, index: int) -> None:
    barrier.wait(timeout=30)
    for round_ in range(5):
        storage.DistillationJobQueue(f"{path}-{round_}")
    storage.DistillationJobQueue(path)


def _cold_round(ctx, root: Path, rounds: int) -> None:
    # Fresh, never-initialized paths: both processes race the first
    # PRAGMA journal_mode = WAL transition. Do not pre-create the queue.
    for round_ in range(rounds):
        barrier = ctx.Barrier(2)
        base = root / f"cold-{round_}.sqlite3"
        procs = [ctx.Process(target=_cold_constructor, args=(str(base), barrier, i))
                 for i in range(2)]
        for process in procs:
            process.start()
        for process in procs:
            process.join(timeout=120)
        for process in procs:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        assert [p.exitcode for p in procs] == [0, 0], f"cold round {round_}"


def test_two_process_cold_start_initializes_fresh_queue(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    _cold_round(ctx, tmp_path, 6)
    paths = sorted(tmp_path.glob("cold-*.sqlite3"))
    assert len(paths) == 6
    for path in paths:
        queue = storage.DistillationJobQueue(path)
        source = SourceRecord(
            source_id="cold", connector_id="local-regression", source_type="paper",
            status="normalized", title="cold", content_ref="file:///synthetic/cold",
            metadata={},
        )
        job = queue.enqueue_source_record(source)
        assert [j.job_id for j in queue.list_all()] == [job.job_id]
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_controller_reuses_one_queue_across_ticks(tmp_path, monkeypatch):
    config = controller.DistillationControllerConfig(
        database_url="unused", registry_url="http://unused.invalid", interval_seconds=1,
        max_ticks=3, state_path=tmp_path / "state.json", alive_path=tmp_path / "alive",
        job_queue_path=tmp_path / "queue.jsonl", seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=tmp_path / "evidence.jsonl", source_dirs=[],
    )
    bootstrap_calls = []
    successes = []
    bootstrap = storage.DistillationJobQueue._bootstrap

    def tracked_bootstrap(queue):
        bootstrap_calls.append(queue)
        return bootstrap(queue)

    async def record_success(**kwargs):
        successes.append(kwargs)

    monkeypatch.setattr(storage.DistillationJobQueue, "_bootstrap", tracked_bootstrap)
    monkeypatch.setattr(controller, "config_from_env", lambda: config)
    monkeypatch.setattr(controller, "build_loop_writer", lambda **_: SimpleNamespace(record_success=record_success))
    monkeypatch.setattr(controller, "read_source_records_for_tenant", lambda **_: [])
    monkeypatch.setattr(controller, "_make_registry_sync", lambda _: lambda request: pytest.fail("no Registry I/O"))
    monkeypatch.setattr(controller.time, "sleep", lambda _: None)
    assert controller.main() == 0
    assert len(successes) == 3
    assert len(bootstrap_calls) == 1
