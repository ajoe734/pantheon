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
                source_type="internal_note", status="normalized", title=f"{prefix}-{index}",
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
            source_id="cold", connector_id="local-regression", source_type="internal_note",
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


def test_controller_reuses_queue_across_ticks_with_market_sources_and_zero_registry_io(
    tmp_path, monkeypatch
):
    from services.source_ingestion.distillation_worker import MARKET_SOURCE_SKIP_REASON

    config = controller.DistillationControllerConfig(
        database_url="unused",
        registry_url="http://unused.invalid",
        interval_seconds=1,
        max_ticks=2,
        state_path=tmp_path / "state.json",
        alive_path=tmp_path / "alive",
        job_queue_path=tmp_path / "queue.jsonl",
        seed_store_path=tmp_path / "seeds.jsonl",
        evidence_store_path=tmp_path / "evidence.jsonl",
        source_dirs=[],
    )
    market_source = SourceRecord(
        source_id="tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
        connector_id="tw-official",
        source_type="market",
        status="normalized",
        title="TWSE 2330 daily bar",
        content_ref="https://www.twse.com.tw/exchangeReport/STOCK_DAY?stockNo=2330",
        metadata={
            "tenant_id": "default",
            "trust_score": 0.9,
            "access_scope": ["research"],
            "license_scope": "public",
            "keywords": ["2330", "TWSE"],
            "strategy_seed": {
                "hypothesis": "TWSE 2330 bar observation",
                "asset_class": ["equity"],
                "market_scope": ["Taiwan"],
                "holding_period": "5 days",
                "required_data": ["OHLCV"],
                "backend_hint": "qlib",
                "feature_hints": ["close"],
                "label_hints": ["return"],
                "risk_notes": ["none"],
            },
        },
    )
    successes = []

    async def record_success(**kwargs):
        successes.append(kwargs)

    monkeypatch.setattr(controller, "config_from_env", lambda: config)
    monkeypatch.setattr(
        controller,
        "build_loop_writer",
        lambda **_: SimpleNamespace(record_success=record_success),
    )
    monkeypatch.setattr(
        controller,
        "read_source_records_for_tenant",
        lambda **_: [market_source],
    )
    monkeypatch.setattr(
        controller,
        "_get_registry_entry",
        lambda *args, **kwargs: pytest.fail("no Registry read allowed for market source"),
    )
    monkeypatch.setattr(
        controller,
        "_register_strategy_spec_if_absent",
        lambda *args, **kwargs: pytest.fail("no Registry write allowed for market source"),
    )
    monkeypatch.setattr(controller.time, "sleep", lambda _: None)

    assert controller.main() == 0
    assert len(successes) == 2
    assert successes[0]["downstream_actual_state"]["status"] == "ready"
    assert successes[0]["downstream_actual_state"]["summary"] == "0 terminal drafts; 0 dead letters"
    queue = storage.DistillationJobQueue(config.job_queue_path)
    jobs = queue.list_all()
    assert len(jobs) == 1
    assert jobs[0].status == "skipped"
    assert jobs[0].skip_reason == MARKET_SOURCE_SKIP_REASON
    assert jobs[0].registry_id is None



# ---------------------------------------------------------------------------
# DISTILLATION-LEASE-PER-JOB-AND-BOUNDED-CLAIM-20261117
# ---------------------------------------------------------------------------
import json  # noqa: E402

from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore  # noqa: E402


def _src(source_id: str) -> SourceRecord:
    return SourceRecord(
        source_id=source_id, connector_id="conn-papers", source_type="internal_note",
        title=f"title {source_id}", content_ref=f"https://doi.org/{source_id}",
        status="normalized",
        metadata={"trust_score": 0.8, "access_scope": ["research"], "license_scope": "internal"},
    )


def _worker(tmp_path, clock, *, sync_seconds=0.0, on_sync=None, lease_seconds=30.0, **queue_kwargs):
    queue = storage.DistillationJobQueue(
        tmp_path / "queue.sqlite3", now=lambda: clock[0], **queue_kwargs,
    )

    def registry_sync(request):
        clock[0] += sync_seconds
        if on_sync is not None:
            on_sync(queue, request)
        return storage.RegistrySyncResult(registry_id="reg-" + request.job.job_id, status="synced")

    worker = storage.DistillationWorker(
        queue, StrategySpecSeedStore(tmp_path / "seeds.jsonl"), created_by="t",
        worker_id="w", lease_seconds=lease_seconds, registry_sync=registry_sync,
    )
    return worker, queue


def test_batch_of_100_jobs_of_1_5_seconds_has_no_expired_lease(tmp_path):
    clock = [1_000_000.0]
    worker, queue = _worker(tmp_path, clock, sync_seconds=1.5, lease_seconds=30.0)
    for i in range(100):
        queue.enqueue_source_record(_src(f"s{i:03d}"))
    result = worker.run_pending(limit=100)
    assert result.processed == 100 and result.lease_lost == ()
    assert queue.metrics()["done"] == 100
    # 150 s of work against a 30 s lease: every job got its own window.
    assert clock[0] - 1_000_000.0 >= 150


def test_superseded_token_on_one_job_does_not_abort_the_tick(tmp_path):
    clock = [1_000_000.0]
    victim = {}

    def steal(queue, request):
        if request.source.source_id == "s002":
            victim["id"] = request.job.job_id
            with queue._connect() as conn:
                conn.execute("UPDATE distillation_outbox SET lease_token='other' WHERE job_id=?",
                             (request.job.job_id,))

    worker, queue = _worker(tmp_path, clock, on_sync=steal)
    for i in range(5):
        queue.enqueue_source_record(_src(f"s{i:03d}"))
    result = worker.run_pending(limit=10)
    assert result.lease_lost == (victim["id"],)
    assert result.processed == 4 and queue.metrics()["done"] == 4


def test_repeated_lease_expiry_is_backed_off_and_dead_lettered(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(
        tmp_path / "queue.sqlite3", now=lambda: clock[0], max_lease_expiries=3,
        lease_backoff_base_seconds=30.0,
    )
    queue.enqueue_source_record(_src("stuck"))
    available = []
    for _ in range(3):
        job = queue.claim_due(worker_id="w", lease_seconds=10, limit=5)[0]
        queue.begin_job(job, lease_seconds=10)
        clock[0] += 11  # work outlives the lease and nobody completes it
        assert queue.claim_due(worker_id="w", lease_seconds=10, limit=5) == []
        row = queue.get("stuck")
        available.append(row.next_attempt_at)
        clock[0] += 3600  # skip past any backoff
    final = queue.get("stuck")
    assert final.status == "dead_letter"
    dead = queue.list_dead_letters(include_payload=False)
    assert dead and dead[0]["reason"].startswith(storage.LEASE_EXPIRED_REASON)
    assert queue.claim_due(worker_id="w", lease_seconds=10, limit=5) == []
    # backoff grows: attempts counted once per started execution only.
    assert final.attempts == 3


def test_unstarted_claim_expiry_is_released_without_penalty(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3", now=lambda: clock[0],
                                         max_lease_expiries=2)
    queue.enqueue_source_record(_src("a"))
    for _ in range(6):
        assert len(queue.claim_due(worker_id="w", lease_seconds=5, limit=5)) == 1
        clock[0] += 6
    job = queue.get("a")
    assert job.attempts == 0 and job.status == "leased"


def test_inflated_attempts_from_the_expiry_loop_are_normalised_once(tmp_path):
    clock = [1_000_000.0]
    worker, queue = _worker(tmp_path, clock)
    queue.enqueue_source_record(_src("stuck"))
    with closing(sqlite3.connect(queue.path, isolation_level=None)) as conn:
        for index in ("distillation_outbox_deferred_idx", "distillation_outbox_lease_idx",
                      "distillation_outbox_prune_idx"):
            conn.execute(f"DROP INDEX {index}")
        for column in ("lease_expiries", "lease_started", "pruned_at"):
            conn.execute(f"ALTER TABLE distillation_outbox DROP COLUMN {column}")
        conn.execute("UPDATE distillation_outbox SET attempts = 4321, status = 'pending', "
                     "last_error = 'lease_expired_before_registry_ack'")
    queue = storage.DistillationJobQueue(queue.path, now=lambda: clock[0])  # upgrade
    assert queue.get("stuck").attempts == 0
    worker._queue = queue
    result = worker.run_pending()
    assert result.processed == 1 and queue.get("stuck").status == "done"


def test_claim_reads_a_bounded_number_of_rows_and_never_sorts_on_disk(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3", now=lambda: clock[0])
    payload = json.dumps({"source_id": "x", "metadata": {"tenant_id": "t"}})
    with closing(sqlite3.connect(queue.path, isolation_level=None)) as conn:
        conn.execute("BEGIN")
        for i in range(3000):
            conn.execute("INSERT INTO distillation_source_versions VALUES (?, ?, 'v', ?, 1)",
                         (f"s{i}", f"d{i}", payload))
            conn.execute(
                "INSERT INTO distillation_outbox (job_id, event_id, source_id, source_digest,"
                " event_version, idempotency_key, status, enqueued_at, available_at, max_attempts,"
                " updated_at) VALUES (?,?,?,?, 'v', ?, 'pending', ?, ?, 3, 1)",
                (f"j{i}", f"e{i}", f"s{i}", f"d{i}", f"k{i}", i, i),
            )
        conn.execute("COMMIT")
    parsed = []
    real_loads = storage.json.loads
    storage.json.loads = lambda *a, **k: (parsed.append(1), real_loads(*a, **k))[1]
    try:
        claimed = queue.claim_due(worker_id="w", limit=7)
    finally:
        storage.json.loads = real_loads
    assert len(claimed) == 7 and len(parsed) <= 7
    with closing(sqlite3.connect(queue.path)) as conn:
        for status in ("pending", "retry_wait"):
            plan = " ".join(str(r) for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT job_id FROM distillation_outbox WHERE status=? "
                "AND available_at<=? ORDER BY available_at, enqueued_at LIMIT ?", (status, 1e12, 7)))
            assert "TEMP B-TREE" not in plan and "distillation_outbox_due_idx" in plan


def test_prune_bounds_payloads_but_keeps_idempotency(tmp_path):
    clock = [1_000_000.0]
    worker, queue = _worker(tmp_path, clock)
    source = _src("old")
    queue.enqueue_source_record(source)
    worker.run_pending()
    assert queue.get("old").status == "done"
    clock[0] += 8 * 86400
    counts = queue.prune()
    assert counts["payloads"] == 1 and counts["inbox"] == 1
    assert queue.prune() == {"payloads": 0, "inbox": 0, "dead_letters": 0, "bytes": 0}
    with closing(sqlite3.connect(queue.path)) as conn:
        assert conn.execute("SELECT payload_json FROM distillation_source_versions").fetchone()[0] \
            == storage.PRUNED_PAYLOAD_JSON
        assert conn.execute("SELECT COUNT(*) FROM distillation_inbox").fetchone()[0] == 0
    # catch_up must not re-enqueue the pruned version; re-enqueue stays idempotent.
    result = worker.catch_up([source])
    assert result.enqueued == 0 and queue.count() == 1
    queue.enqueue_source_record(source)
    assert queue.count() == 1


def test_resolved_dead_letters_are_pruned_unresolved_kept(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3", now=lambda: clock[0])
    with closing(sqlite3.connect(queue.path, isolation_level=None)) as conn:
        for i, resolved in enumerate((1.0, None)):
            conn.execute("INSERT INTO distillation_dead_letters (job_id, event_id, source_id,"
                         " source_digest, payload_json, attempts, reason, created_at, resolved_at)"
                         " VALUES (?, 'e', 's', 'd', '{}', 1, 'r', 1, ?)", (f"j{i}", resolved))
    assert queue.prune(now=10_000_000.0)["dead_letters"] == 1
    assert [d["job_id"] for d in queue.list_dead_letters()] == ["j1"]


def test_controller_alive_marker_stops_after_consecutive_hard_failures(tmp_path):
    config = SimpleNamespace(alive_path=tmp_path / "alive", max_consecutive_failed_ticks=3)
    controller._HARD_FAILURES.clear()
    controller._refresh_alive(config, hard_failure=False)
    assert config.alive_path.exists()
    config.alive_path.unlink()
    controller._refresh_alive(config, hard_failure=True)
    controller._refresh_alive(config, hard_failure=True)
    assert config.alive_path.exists()  # below the threshold: still healthy
    config.alive_path.unlink()
    controller._refresh_alive(config, hard_failure=True)
    assert not config.alive_path.exists()  # third consecutive failure: marker ages out
    controller._refresh_alive(config, hard_failure=False)
    assert config.alive_path.exists()  # one success restores it


def test_compaction_returns_pruned_payload_space_to_the_filesystem(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3", now=lambda: clock[0])
    big = json.dumps({"source_id": "x", "metadata": {"body": "y" * 2900}})
    with closing(sqlite3.connect(queue.path, isolation_level=None)) as conn:
        conn.execute("BEGIN")
        for i in range(2000):
            conn.execute("INSERT INTO distillation_source_versions VALUES (?, ?, 'v', ?, 1)",
                         (f"s{i}", f"d{i}", big))
            conn.execute(
                "INSERT INTO distillation_outbox (job_id, event_id, source_id, source_digest,"
                " event_version, idempotency_key, status, enqueued_at, available_at, processed_at,"
                " max_attempts, updated_at) VALUES (?,?,?,?, 'v', ?, 'done', 1, 1, 2, 3, 1)",
                (f"j{i}", f"e{i}", f"s{i}", f"d{i}", f"k{i}"),
            )
        conn.execute("COMMIT")
    before = queue.path.stat().st_size
    pruned = queue.prune(now=clock[0] + 8 * 86400, batch=5000)
    assert pruned["payloads"] == 2000 and pruned["bytes"] >= 2000 * 2900
    queue.compact()
    assert queue.path.stat().st_size < before / 3


# --- DISTILLATION-DEAD-LETTER-RECOVERY-R2-20261125 -------------------------

def _tenant_src(source_id: str, tenant: str | None) -> SourceRecord:
    record = _src(source_id)
    if tenant:
        record.metadata["tenant_id"] = tenant
    return record


def _dead_letter_via_lease_expiry(queue, clock, source_id, tenant="t1"):
    queue.enqueue_source_record(_tenant_src(source_id, tenant))
    for _ in range(queue._max_lease_expiries):
        job = queue.claim_due(worker_id="w", lease_seconds=10, limit=5, tenant_id=tenant)[0]
        queue.begin_job(job, lease_seconds=10)
        clock[0] += 11
        queue.claim_due(worker_id="w", lease_seconds=10, limit=5, tenant_id=tenant)
        clock[0] += 3600


def test_139_lease_expired_dead_letters_redrive_bounded_without_starving_new_work(tmp_path):
    clock = [1_000_000.0]
    worker, queue = _worker(tmp_path, clock, max_lease_expiries=1)
    for i in range(139):
        queue.enqueue_source_record(_tenant_src(f"old{i:03d}", "t1"))
    for _ in range(139):
        job = queue.claim_due(worker_id="w", lease_seconds=10, limit=1, tenant_id="t1")[0]
        queue.begin_job(job, lease_seconds=10)
        clock[0] += 11
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            queue._recover_expired(conn, now=clock[0])
            conn.execute("COMMIT")
    assert queue.dead_letter_count() == 139
    queue.enqueue_source_record(_tenant_src("fresh", "t1"))

    ticks = 0
    while queue.dead_letter_count() or queue.metrics()["pending"]:
        ticks += 1
        assert ticks < 40
        redriven = queue.redrive_lease_expired_dead_letters(
            requested_by="t", limit=10, tenant_id="t1",
        )
        assert len(redriven) <= 10
        result = worker.run_pending(limit=100, tenant_id="t1")
        if ticks == 1:
            # New work is processed in the same tick as the first redrive batch.
            assert queue.get("fresh").status == "done"
            assert result.processed == 11
    assert queue.metrics()["done"] == 140
    assert ticks == 14


def test_lease_expired_job_that_exhausts_again_stays_dead_lettered_with_count(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(
        tmp_path / "queue.sqlite3", now=lambda: clock[0], max_lease_expiries=2,
    )
    _dead_letter_via_lease_expiry(queue, clock, "stuck")
    for expected in (1, 2):
        assert queue.redrive_lease_expired_dead_letters(requested_by="t", max_redrives=2) != []
        for _ in range(2):
            job = queue.claim_due(worker_id="w", lease_seconds=10, limit=5, tenant_id="t1")[0]
            queue.begin_job(job, lease_seconds=10)
            clock[0] += 11
            queue.claim_due(worker_id="w", lease_seconds=10, limit=5, tenant_id="t1")
            clock[0] += 3600
        dead = queue.list_dead_letters(include_payload=False)
        assert dead[0]["redrive_count"] == expected and dead[0]["resolved_at"] is None
    # Budget spent: not redriven a third time.
    assert queue.redrive_lease_expired_dead_letters(requested_by="t", max_redrives=2) == []
    assert queue.get("stuck").status == "dead_letter"


def _insert_conflict_dead_letter(queue, source_id, reason):
    queue.enqueue_source_record(_tenant_src(source_id, "t1"))
    job = queue.claim_due(worker_id="w", lease_seconds=10, limit=5, tenant_id="t1")[0]
    queue.begin_job(job, lease_seconds=10)
    queue.mark_retry_or_dead_letter(job, error=reason, base_delay_seconds=0, permanent=True)
    return job.job_id


def test_lineage_conflict_is_replayed_once_then_closed_terminally_without_registry_io(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3", now=lambda: clock[0])
    reason = (
        "registry_sync_failed: Registry artifact reg-x exists with conflicting "
        "distillation lineage/content for source c1"
    )
    job_id = _insert_conflict_dead_letter(queue, "c1", reason)
    first = queue.settle_lineage_conflict_dead_letters(requested_by="t", tenant_id="t1")
    assert first == {"replayed": [job_id], "closed": []}
    # Replay still conflicts: dead-lettered again with its redrive count.
    job = queue.claim_due(worker_id="w", lease_seconds=10, limit=5, tenant_id="t1")[0]
    queue.begin_job(job, lease_seconds=10)
    queue.mark_retry_or_dead_letter(job, error=reason, base_delay_seconds=0, permanent=True)
    second = queue.settle_lineage_conflict_dead_letters(requested_by="t", tenant_id="t1")
    assert second == {"replayed": [], "closed": [job_id]}
    closed = queue.get("c1")
    assert closed.status == "skipped"
    assert closed.skip_reason.startswith("registry_lineage_conflict_unresolved: ")
    assert "reg-x" in closed.skip_reason
    assert queue.dead_letter_count() == 0
    assert queue.metrics()["dead_letter"] == 0
    # Nothing left to settle; an unrelated dead letter is never touched.
    other = _insert_conflict_dead_letter(queue, "o1", "registry_sync_failed: HTTP Error 500")
    assert queue.settle_lineage_conflict_dead_letters(requested_by="t", tenant_id="t1") == {
        "replayed": [], "closed": [],
    }
    assert queue.get("o1").status == "dead_letter" and other


def test_retired_tenant_pending_rows_close_out_of_scope_only_with_proof(tmp_path):
    clock = [1_000_000.0]
    queue = storage.DistillationJobQueue(tmp_path / "queue.sqlite3", now=lambda: clock[0])
    for i in range(5):
        queue.enqueue_source_record(_tenant_src(f"r{i}", "retired"))
    window_end = clock[0] + 100
    clock[0] += 200
    queue.enqueue_source_record(_tenant_src("live", "t1"))
    # Active tenant is refused.
    refused = queue.close_retired_tenant_pending(
        retired_tenant_ids=["t1"], active_tenant_id="t1",
        enqueued_from=0, enqueued_to=window_end, requested_by="t",
    )
    assert refused["closed"] == [] and refused["refused"] == {"t1": "tenant_is_active"}
    result = queue.close_retired_tenant_pending(
        retired_tenant_ids=["retired"], active_tenant_id="t1",
        enqueued_from=0, enqueued_to=window_end, requested_by="t",
    )
    assert len(result["closed"]) == 5
    assert queue.get("r0").skip_reason.startswith("out_of_scope_retired_runtime: tenant=retired")
    assert queue.metrics()["pending"] == 1  # only the live tenant's row
    assert queue.get("live").status == "pending"
    # A tenant that enqueued after the window is not retired.
    clock[0] += 10
    queue.enqueue_source_record(_tenant_src("late", "retired"))
    again = queue.close_retired_tenant_pending(
        retired_tenant_ids=["retired"], active_tenant_id="t1",
        enqueued_from=0, enqueued_to=window_end, requested_by="t",
    )
    assert again["closed"] == [] and again["refused"] == {"retired": "tenant_enqueued_after_window"}
    assert queue.get("late").status == "pending"
