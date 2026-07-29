from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]

# The exact commit that merged PR #3753 (atomic replace without a
# transaction-scoped lock, and silent-empty recovery of malformed input).
# Kept as a pinned reference so the incident it caused stays reproducible
# regardless of how store.py evolves afterward.
INCIDENT_COMMIT = "d55a0caf7772ceb15b7914fe74856929f96d0283"


def _load_store_module():
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_store_test",
        SERVICE_DIR / "store.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_incident_store_module(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = subprocess.run(
        ["git", "show", f"{INCIDENT_COMMIT}:services/reconciliation-drift/store.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    module_path = tmp_path / "incident_store_source.py"
    module_path.write_text(source, encoding="utf-8")
    return module_path


def _load_module_from_path(module_path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worker_put_many(module_path_str: str, module_name: str, data_dir_str: str, prefix: str, count: int) -> None:
    module = _load_module_from_path(Path(module_path_str), module_name)
    store = module.ReconciliationDriftStore(data_dir_str)
    for index in range(count):
        store.put_evaluation({"evaluation_id": f"{prefix}-{index}", "status": "ok"})


def _worker_incident_put_after_barrier(
    module_path_str: str,
    module_name: str,
    data_dir_str: str,
    evaluation_id: str,
    barrier,
) -> None:
    """Put one record on the historical (PR #3753) store, but only replace
    the file once every writer has completed its own pre-write read.

    ``_write_map`` runs strictly after ``_read_map`` inside ``_put_record``,
    so waiting on the barrier here guarantees both processes have already
    read the (still-empty) map before either one replaces the file. That
    removes all dependence on OS scheduling: the loss is forced every run,
    not just likely under contention.
    """
    module = _load_module_from_path(Path(module_path_str), module_name)
    store = module.ReconciliationDriftStore(data_dir_str)
    original_write_map = store._write_map

    def _write_map_after_barrier(path, payload):
        barrier.wait(timeout=30)
        return original_write_map(path, payload)

    store._write_map = _write_map_after_barrier
    store.put_evaluation({"evaluation_id": evaluation_id, "status": "ok"})


def _assert_alert_source_fails_closed(module, store, original: bytes) -> None:
    store.alerts_path.write_bytes(original)
    original_sha = hashlib.sha256(original).hexdigest()

    with pytest.raises(module.ReconciliationStoreError):
        store.list_alert_handoffs()

    with pytest.raises(module.ReconciliationStoreError):
        store.put_alert_handoff({"alert_id": "alert-new", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha
    assert list(store.data_dir.glob(f".{store.alerts_path.name}.*.tmp")) == []


def test_json_store_recovers_concatenated_maps_and_rewrites_valid_json(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.evaluations_path.write_text(
        json.dumps({"eval-a": {"evaluation_id": "eval-a", "status": "ok"}})
        + " \t\r\n"
        + json.dumps({"eval-b": {"evaluation_id": "eval-b", "status": "warning"}}),
        encoding="utf-8",
    )

    evaluations = store.list_evaluations()

    assert [item["evaluation_id"] for item in evaluations] == ["eval-a", "eval-b"]

    store.put_evaluation({"evaluation_id": "eval-c", "status": "critical"})

    payload = json.loads(store.evaluations_path.read_text(encoding="utf-8"))
    assert set(payload) == {"eval-a", "eval-b", "eval-c"}


def test_json_store_concatenated_recovery_deterministic_duplicate_id_last_wins(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.evaluations_path.write_text(
        json.dumps({"eval-a": {"evaluation_id": "eval-a", "status": "stale"}})
        + json.dumps({"eval-a": {"evaluation_id": "eval-a", "status": "fresh"}, "eval-b": {"evaluation_id": "eval-b", "status": "ok"}}),
        encoding="utf-8",
    )

    evaluations = {item["evaluation_id"]: item for item in store.list_evaluations()}

    assert set(evaluations) == {"eval-a", "eval-b"}
    assert evaluations["eval-a"]["status"] == "fresh"


def test_json_store_fails_closed_on_malformed_suffix(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = (
        json.dumps({"alert-a": {"alert_id": "alert-a", "status": "sent"}}) + "garbage-suffix"
    ).encode("utf-8")
    store.alerts_path.write_bytes(original)
    original_sha = hashlib.sha256(original).hexdigest()

    with pytest.raises(module.ReconciliationStoreError):
        store.list_alert_handoffs()

    with pytest.raises(module.ReconciliationStoreError):
        store.put_alert_handoff({"alert_id": "alert-b", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha


def test_json_store_fails_closed_on_truncated_json(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = b'{"alert-a": {"alert_id": "alert-a", "status": "sen'
    store.alerts_path.write_bytes(original)
    original_sha = hashlib.sha256(original).hexdigest()

    with pytest.raises(module.ReconciliationStoreError):
        store.list_alert_handoffs()

    with pytest.raises(module.ReconciliationStoreError):
        store.put_alert_handoff({"alert_id": "alert-b", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha


def test_json_store_fails_closed_on_invalid_utf8(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = b'{"alert-a": {"alert_id": "alert-a", "status": "\xff\xfe"}}'
    store.alerts_path.write_bytes(original)
    original_sha = hashlib.sha256(original).hexdigest()

    with pytest.raises(module.ReconciliationStoreError):
        store.list_alert_handoffs()

    with pytest.raises(module.ReconciliationStoreError):
        store.put_alert_handoff({"alert_id": "alert-b", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha


def test_json_store_fails_closed_on_invalid_map_values(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = json.dumps({"alert-a": "not-an-object"}).encode("utf-8")
    store.alerts_path.write_bytes(original)
    original_sha = hashlib.sha256(original).hexdigest()

    with pytest.raises(module.ReconciliationStoreError):
        store.list_alert_handoffs()

    with pytest.raises(module.ReconciliationStoreError):
        store.put_alert_handoff({"alert_id": "alert-b", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha


@pytest.mark.parametrize(
    "original",
    [b"", b" \t\r\n"],
    ids=["zero-byte", "json-whitespace-only"],
)
def test_json_store_fails_closed_on_existing_source_without_json_document(
    tmp_path: Path, original: bytes
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)

    _assert_alert_source_fails_closed(module, store, original)


@pytest.mark.parametrize(
    "suffix",
    ["\f", "\u00a0"],
    ids=["form-feed", "non-breaking-space"],
)
def test_json_store_fails_closed_on_non_json_whitespace_suffix(
    tmp_path: Path, suffix: str
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = (
        json.dumps({"alert-a": {"alert_id": "alert-a", "status": "sent"}}) + suffix
    ).encode("utf-8")

    _assert_alert_source_fails_closed(module, store, original)


def test_json_store_fails_closed_when_later_concatenated_map_is_invalid(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = (
        json.dumps({"alert-a": {"alert_id": "alert-a", "status": "sent"}})
        + json.dumps({"alert-b": "not-an-object"})
    ).encode("utf-8")

    _assert_alert_source_fails_closed(module, store, original)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_json_store_fails_closed_on_non_finite_numeric_value(
    tmp_path: Path, number: str
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = (
        '{"alert-a":{"alert_id":"alert-a","score":' + number + "}}"
    ).encode("utf-8")

    _assert_alert_source_fails_closed(module, store, original)


def test_json_store_fails_closed_on_duplicate_key_within_document(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = (
        b'{"alert-a":{"alert_id":"alert-a","status":"sent"},'
        b'"alert-a":{"alert_id":"alert-a","status":"replaced"}}'
    )

    _assert_alert_source_fails_closed(module, store, original)


def test_json_store_rejects_non_finite_put_without_changing_source(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.put_alert_handoff({"alert_id": "alert-a", "status": "sent"})
    original = store.alerts_path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()

    with pytest.raises(ValueError):
        store.put_alert_handoff({"alert_id": "alert-b", "score": float("nan")})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha
    assert list(tmp_path.glob(f".{store.alerts_path.name}.*.tmp")) == []


def test_json_store_simulated_replace_failure_keeps_original_and_cleans_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.put_alert_handoff({"alert_id": "alert-a", "status": "sent"})
    original = store.alerts_path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()

    def _boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", _boom)

    with pytest.raises(OSError):
        store.put_alert_handoff({"alert_id": "alert-b", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha

    leftover_tmp_files = list(tmp_path.glob(f".{store.alerts_path.name}.*.tmp"))
    assert leftover_tmp_files == []


def test_json_store_simulated_fsync_failure_keeps_original_and_cleans_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injects a flush/fsync failure on the temporary file, before replace.

    This is a distinct failure point from ``os.replace`` failing: it proves
    the durable-write step itself (flush the temp file, fsync it) fails
    closed too, leaving the source bytes untouched and every task-created
    temporary file removed, without ever reaching ``os.replace``.
    """
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    store.put_alert_handoff({"alert_id": "alert-a", "status": "sent"})
    original = store.alerts_path.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()

    def _boom(*args, **kwargs):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(module.os, "fsync", _boom)
    replace_calls = []
    monkeypatch.setattr(module.os, "replace", lambda *args, **kwargs: replace_calls.append(args))

    with pytest.raises(OSError):
        store.put_alert_handoff({"alert_id": "alert-b", "status": "sent"})

    surviving_bytes = store.alerts_path.read_bytes()
    assert surviving_bytes == original
    assert hashlib.sha256(surviving_bytes).hexdigest() == original_sha

    leftover_tmp_files = list(tmp_path.glob(f".{store.alerts_path.name}.*.tmp"))
    assert leftover_tmp_files == []
    assert replace_calls == []


def test_incident_pr3753_concurrent_distinct_writers_lose_exactly_one_update(tmp_path: Path) -> None:
    """Deterministically reproduces the do-not-merge finding on PR #3753.

    A shared barrier forces both writer processes to complete their
    pre-write read of the (still-empty) map before either one is allowed to
    replace the file. This does not depend on OS scheduling: whichever
    writer replaces the file first or second, the other one always started
    from the same stale empty read, so the loser's record is discarded
    every single run.
    """
    module_path = _write_incident_store_module(tmp_path / "incident")
    data_dir = tmp_path / "incident" / "data"
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)

    processes = [
        ctx.Process(
            target=_worker_incident_put_after_barrier,
            args=(str(module_path), "incident_barrier_worker_a", str(data_dir), "eval-a", barrier),
        ),
        ctx.Process(
            target=_worker_incident_put_after_barrier,
            args=(str(module_path), "incident_barrier_worker_b", str(data_dir), "eval-b", barrier),
        ),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    payload = json.loads((data_dir / "drift_evaluations.json").read_text(encoding="utf-8"))
    assert len(payload) == 1, (
        "expected the historical unlocked implementation to deterministically "
        "lose exactly one of the two distinct concurrent records"
    )
    assert set(payload) in ({"eval-a"}, {"eval-b"})


def test_fixed_store_repeated_concurrent_process_writes_retain_every_record(tmp_path: Path) -> None:
    """Proves the transaction-scoped lock stops the PR #3753 lost-update race."""
    module_path = SERVICE_DIR / "store.py"
    count = 40
    writers = 4

    for trial in range(3):
        data_dir = tmp_path / f"trial-{trial}"
        processes = [
            multiprocessing.Process(
                target=_worker_put_many,
                args=(str(module_path), f"fixed_store_worker_{trial}_{writer}", str(data_dir), f"w{writer}", count),
            )
            for writer in range(writers)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
            assert process.exitcode == 0

        payload = json.loads((data_dir / "drift_evaluations.json").read_text(encoding="utf-8"))
        assert len(payload) == count * writers
        expected_ids = {f"w{writer}-{index}" for writer in range(writers) for index in range(count)}
        assert set(payload) == expected_ids


def test_json_store_treats_unrecoverable_map_as_fail_closed_error(tmp_path: Path) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    original = b"{not-json"
    store.alerts_path.write_bytes(original)

    with pytest.raises(module.ReconciliationStoreError):
        store.list_alert_handoffs()

    assert store.alerts_path.read_bytes() == original

    with pytest.raises(module.ReconciliationStoreError):
        store.put_alert_handoff({"alert_id": "alert-a", "status": "sent"})

    assert store.alerts_path.read_bytes() == original


def test_work_claim_allows_only_one_owner_and_keeps_completed_receipt(
    tmp_path: Path,
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    now = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)

    first = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="2026-07-26T10:00:00Z/PT5M",
        owner_id="scheduler-a",
        lease_seconds=60,
        now=now,
    )
    competing = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="2026-07-26T10:00:00Z/PT5M",
        owner_id="scheduler-b",
        lease_seconds=60,
        now=now,
    )

    assert first["acquired"] is True
    assert competing["acquired"] is False
    assert competing["reason"] == "lease_active"
    assert competing["claim"]["owner_id"] == "scheduler-a"

    completed = store.complete_work(
        claim_id=first["claim"]["claim_id"],
        lease_token=first["claim"]["lease_token"],
        result={"status": "ok", "evaluation_ids": ["eval-a"]},
        now=now + timedelta(seconds=2),
    )
    duplicate = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="2026-07-26T10:00:00Z/PT5M",
        owner_id="scheduler-b",
        lease_seconds=60,
        now=now + timedelta(seconds=3),
    )

    assert completed["status"] == "completed"
    assert completed["result"]["evaluation_ids"] == ["eval-a"]
    assert duplicate["acquired"] is False
    assert duplicate["reason"] == "completed"
    assert duplicate["claim"]["result"] == completed["result"]


def test_expired_or_failed_work_claim_can_recover_but_old_owner_cannot_finish(
    tmp_path: Path,
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    now = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    first = store.claim_work(
        tenant_id="tenant-a",
        work_type="telemetry_consume",
        window_id="evt-001",
        owner_id="consumer-a",
        lease_seconds=10,
        now=now,
    )
    recovered = store.claim_work(
        tenant_id="tenant-a",
        work_type="telemetry_consume",
        window_id="evt-001",
        owner_id="consumer-b",
        lease_seconds=10,
        now=now + timedelta(seconds=11),
    )

    assert recovered["acquired"] is True
    assert recovered["reason"] == "recovered"
    assert recovered["claim"]["attempt_count"] == 2
    with pytest.raises(module.ReconciliationStoreError, match="lease lost"):
        store.complete_work(
            claim_id=first["claim"]["claim_id"],
            lease_token=first["claim"]["lease_token"],
            result={"status": "ok"},
            now=now + timedelta(seconds=12),
        )

    store.fail_work(
        claim_id=recovered["claim"]["claim_id"],
        lease_token=recovered["claim"]["lease_token"],
        error="dependency timeout",
        result={"status": "failure"},
        now=now + timedelta(seconds=12),
    )
    retry = store.claim_work(
        tenant_id="tenant-a",
        work_type="telemetry_consume",
        window_id="evt-001",
        owner_id="consumer-c",
        lease_seconds=10,
        now=now + timedelta(seconds=12),
    )
    assert retry["acquired"] is True
    assert retry["claim"]["attempt_count"] == 3


def test_work_claim_identity_is_tenant_scoped_and_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    now = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
    tenant_a = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="window-1",
        owner_id="worker-a",
        lease_seconds=30,
        now=now,
    )
    tenant_b = store.claim_work(
        tenant_id="tenant-b",
        work_type="scheduled_reconcile",
        window_id="window-1",
        owner_id="worker-b",
        lease_seconds=30,
        now=now,
    )
    assert tenant_a["claim"]["claim_id"] != tenant_b["claim"]["claim_id"]

    original = b'{"broken":'
    store.work_claims_path.write_bytes(original)
    with pytest.raises(module.ReconciliationStoreError):
        store.claim_work(
            tenant_id="tenant-c",
            work_type="scheduled_reconcile",
            window_id="window-1",
            owner_id="worker-c",
            lease_seconds=30,
            now=now,
        )
    assert store.work_claims_path.read_bytes() == original


def test_tenant_records_with_same_external_id_do_not_overwrite_each_other(
    tmp_path: Path,
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    tenant_a = store.put_drift_report(
        {
            "drift_report_id": "shared-report-id",
            "tenant_id": "tenant-a",
            "severity": "medium",
        }
    )
    tenant_b = store.put_drift_report(
        {
            "drift_report_id": "shared-report-id",
            "tenant_id": "tenant-b",
            "severity": "critical",
        }
    )

    assert (
        store.get_drift_report("shared-report-id", tenant_id="tenant-a")
        == tenant_a
    )
    assert (
        store.get_drift_report("shared-report-id", tenant_id="tenant-b")
        == tenant_b
    )
    assert len(store.list_drift_reports()) == 2


def test_postgres_store_owns_records_reports_worker_state_and_work_claims(
    tmp_path: Path,
) -> None:
    module = _load_store_module()

    class FakeOwnerStore:
        tables: dict[str, dict[str, dict]] = {}

        def __init__(self, *, table, **_kwargs):
            self.records = self.tables.setdefault(table, {})

        def list_all(self):
            return list(self.records.values())

        def get(self, record_id):
            value = self.records.get(record_id)
            return dict(value) if value is not None else None

        def put(self, record_id, payload):
            self.records[record_id] = dict(payload)

        def compare_and_set(self, record_id, expected, payload):
            current = self.records.get(record_id)
            if current != expected:
                return False, dict(current) if current is not None else None
            self.records[record_id] = dict(payload)
            return True, dict(payload)

    FakeOwnerStore.tables = {}
    with mock.patch.object(module, "PostgresJsonOwnerStore", FakeOwnerStore):
        store = module.PostgresReconciliationDriftStore(
            tmp_path,
            dsn="postgresql://unused",
        )

    record = store.put_reconciliation_record(
        {"record_id": "record-1", "tenant_id": "tenant-a"}
    )
    report = store.put_drift_report(
        {"drift_report_id": "report-1", "tenant_id": "tenant-a"}
    )
    state = store.put_worker_state(
        {"state_id": "state-1", "tenant_id": "tenant-a", "status": "healthy"}
    )
    claim = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="window-1",
        owner_id="worker-a",
        lease_seconds=30,
        now=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc),
    )

    assert (
        store.get_reconciliation_record("record-1", tenant_id="tenant-a")
        == record
    )
    assert store.get_drift_report("report-1", tenant_id="tenant-a") == report
    assert store.get_worker_state("state-1", tenant_id="tenant-a") == state
    assert claim["acquired"] is True
    assert not store.reconciliation_records_path.exists()
    assert not store.drift_reports_path.exists()
    assert not store.worker_states_path.exists()
    assert not store.work_claims_path.exists()
