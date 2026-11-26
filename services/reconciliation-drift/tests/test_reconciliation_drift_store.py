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
        store.count_alert_handoffs()

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
        calls: list[tuple[str, str]] = []
        init_kwargs: dict[str, dict[str, object]] = {}

        def __init__(self, *, table, **kwargs):
            self.table = table
            self.records = self.tables.setdefault(table, {})
            self.init_kwargs[table] = dict(kwargs)

        def list_all(self):
            self.calls.append((self.table, "list_all"))
            return list(self.records.values())

        def count(self):
            self.calls.append((self.table, "count"))
            return len(self.records)

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
    FakeOwnerStore.calls = []
    FakeOwnerStore.init_kwargs = {}
    with mock.patch.object(module, "PostgresJsonOwnerStore", FakeOwnerStore):
        store = module.PostgresReconciliationDriftStore(
            tmp_path,
            dsn="postgresql://unused",
        )
    assert FakeOwnerStore.init_kwargs["reconciliation_drift.drift_evaluations"].get("count_index") is True
    for non_eval_table in (
        "reconciliation_drift.alert_handoffs",
        "reconciliation_drift.reconciliation_records",
        "reconciliation_drift.drift_reports",
        "reconciliation_drift.work_claims",
        "reconciliation_drift.worker_states",
    ):
        assert not FakeOwnerStore.init_kwargs[non_eval_table].get("count_index", False)

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
    FakeOwnerStore.calls.clear()
    assert store.count_reconciliation_records() == 1
    assert store.count_drift_reports() == 1
    assert store.count_worker_states() == 1
    assert store.count_evaluations() == 0
    assert store.count_alert_handoffs() == 0
    assert ("reconciliation_drift.reconciliation_records", "count") in FakeOwnerStore.calls
    assert ("reconciliation_drift.reconciliation_records", "list_all") not in FakeOwnerStore.calls
    assert ("reconciliation_drift.drift_evaluations", "count") in FakeOwnerStore.calls
    assert ("reconciliation_drift.drift_evaluations", "list_all") not in FakeOwnerStore.calls
    assert not store.reconciliation_records_path.exists()
    assert not store.drift_reports_path.exists()
    assert not store.worker_states_path.exists()
    assert not store.work_claims_path.exists()
    assert store.backend == "postgres"
    assert module.ReconciliationDriftStore(tmp_path).backend == "json"


def test_live_postgres_reconciliation_drift_store_loopback(tmp_path: Path) -> None:
    """Acceptance 5: Postgres store test against a disposable loopback database or skipped with reason."""
    import os
    import uuid

    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set; skipping live Postgres loopback integration test")

    module = _load_store_module()
    schema = f"recon_test_{uuid.uuid4().hex[:12]}"
    eval_table = f"{schema}.drift_evaluations"
    alerts_table = f"{schema}.alert_handoffs"
    rec_table = f"{schema}.reconciliation_records"
    reports_table = f"{schema}.drift_reports"
    claims_table = f"{schema}.work_claims"
    states_table = f"{schema}.worker_states"

    try:
        store = module.PostgresReconciliationDriftStore(
            tmp_path,
            dsn=dsn,
            evaluations_table=eval_table,
            alerts_table=alerts_table,
            reconciliation_records_table=rec_table,
            drift_reports_table=reports_table,
            work_claims_table=claims_table,
            worker_states_table=states_table,
            bootstrap=True,
        )
        assert store.backend == "postgres"

        eval_rec = store.put_evaluation(
            {"evaluation_id": "eval-live-1", "tenant_id": "tenant-live", "status": "ok"}
        )
        assert store.get_evaluation("eval-live-1", tenant_id="tenant-live") == eval_rec
        assert store.count_evaluations() == 1
        assert store.count_alert_handoffs() == 0

        import psycopg

        with psycopg.connect(dsn) as conn:
            indexes = [
                row[0]
                for row in conn.execute(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND indexname LIKE %s",
                    (schema, "%_count_idx"),
                ).fetchall()
            ]
            assert indexes == ["drift_evaluations_count_idx"]

            conn.execute(
                f"INSERT INTO {store._evaluation_records.table} (record_id, payload) VALUES (%s, %s::jsonb)",
                (
                    "eval-str-obj",
                    json.dumps('{"evaluation_id": "eval-str-obj", "tenant_id": "tenant-live", "status": "ok"}'),
                ),
            )
            conn.execute(
                f"INSERT INTO {store._evaluation_records.table} (record_id, payload) VALUES (%s, %s::jsonb)",
                ("eval-str-arr", json.dumps("[1, 2]")),
            )
            conn.execute(
                f"INSERT INTO {store._evaluation_records.table} (record_id, payload) VALUES (%s, %s::jsonb)",
                ("eval-arr", "[1, 2]"),
            )
        assert store.count_evaluations() == 2

        with psycopg.connect(dsn) as conn:
            conn.execute(
                f"INSERT INTO {store._evaluation_records.table} (record_id, payload) VALUES (%s, %s::jsonb)",
                ("eval-bad-str", json.dumps("not-json")),
            )
        with pytest.raises(Exception):
            store.count_evaluations()
        with psycopg.connect(dsn) as conn:
            conn.execute(
                f"DELETE FROM {store._evaluation_records.table} WHERE record_id = %s",
                ("eval-bad-str",),
            )
        assert store.count_evaluations() == 2

        claim = store.claim_work(
            tenant_id="tenant-live",
            work_type="scheduled_reconcile",
            window_id="win-live-1",
            owner_id="worker-live",
            lease_seconds=30,
            now=datetime(2026, 10, 8, 5, 0, tzinfo=timezone.utc),
        )
        assert claim["acquired"] is True
    finally:
        try:
            import psycopg

            with psycopg.connect(dsn) as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        except Exception:
            pass


def test_reconciliation_drift_store_counts_match_real_totals_and_bypass_full_list(
    tmp_path: Path,
) -> None:
    module = _load_store_module()

    # 1. JSON store contract: count returns real record totals
    json_store = module.ReconciliationDriftStore(tmp_path / "json_data")
    assert json_store.count_evaluations() == 0
    assert json_store.count_alert_handoffs() == 0
    assert json_store.count_reconciliation_records() == 0
    assert json_store.count_drift_reports() == 0
    assert json_store.count_worker_states() == 0

    json_store.put_evaluation({"evaluation_id": "eval-1", "status": "ok"})
    json_store.put_evaluation({"evaluation_id": "eval-2", "status": "warning"})
    json_store.put_alert_handoff({"alert_id": "alert-1", "status": "sent"})
    json_store.put_reconciliation_record({"record_id": "rec-1", "severity": "low"})
    json_store.put_drift_report({"drift_report_id": "rep-1", "status": "open"})
    json_store.put_worker_state({"state_id": "st-1", "status": "healthy"})

    assert json_store.count_evaluations() == len(json_store.list_evaluations()) == 2
    assert json_store.count_alert_handoffs() == len(json_store.list_alert_handoffs()) == 1
    assert json_store.count_reconciliation_records() == len(json_store.list_reconciliation_records()) == 1
    assert json_store.count_drift_reports() == len(json_store.list_drift_reports()) == 1
    assert json_store.count_worker_states() == len(json_store.list_worker_states()) == 1


def test_keyed_existence_reads_are_tenant_scoped(tmp_path) -> None:
    store = _load_store_module().ReconciliationDriftStore(tmp_path)
    store.put_evaluation({"evaluation_id": "ev-1", "tenant_id": "t1", "binding_id": "b1"})
    store.put_evaluation({"evaluation_id": "ev-2", "tenant_id": "t1", "binding_id": "b2"})
    store.put_drift_report({"drift_report_id": "dr-1", "tenant_id": "t1"})
    store.put_reconciliation_record({"record_id": "rc-1", "tenant_id": "t1"})

    assert store.evaluation_exists("ev-1", "t1")
    assert not store.evaluation_exists("ev-1", "t2")
    assert not store.evaluation_exists("ev-9", "t1")
    assert store.drift_report_exists("dr-1", "t1")
    assert not store.drift_report_exists("dr-1", "t2")
    assert store.reconciliation_record_exists("rc-1", "t1")
    assert not store.reconciliation_record_exists("rc-2", "t1")
    assert [r["evaluation_id"] for r in store.list_evaluations_for_binding("b2")] == ["ev-2"]


def _evaluation(evaluation_id: str, binding_id: str, evaluated_at: str, *, breached: bool) -> dict:
    # Shape of a real hosted drift_evaluations row (reduced to the fields read).
    return {
        "id": evaluation_id,
        "evaluation_id": evaluation_id,
        "tenant_id": "tenant-a",
        "binding_id": binding_id,
        "evaluated_at": evaluated_at,
        "drift_checks": [
            {
                "metric": "drawdown",
                "status": "ok",
                "baseline": 0.0325,
                "observed": 0.0,
                "one_sided": True,
                "comparator": "gt",
                "signal_type": "performance_degradation",
                "threshold_value": 1.25,
                "threshold_metric": "rolling_drawdown_multiple",
                "governed_threshold_breached": breached,
            }
        ],
    }


def test_json_store_filters_evaluations_by_window_and_governed_breach(tmp_path: Path) -> None:
    store_module = _load_store_module()
    store = store_module.ReconciliationDriftStore(tmp_path)
    store.put_evaluation(_evaluation("e1", "b1", "2026-11-01T10:00:00Z", breached=True))
    store.put_evaluation(_evaluation("e2", "b1", "2026-11-02T10:00:00Z", breached=False))
    store.put_evaluation(_evaluation("e3", "b1", "2026-11-03T10:00:00Z", breached=True))
    store.put_evaluation(_evaluation("e4", "b2", "2026-11-03T10:00:00Z", breached=True))

    assert {r["id"] for r in store.list_evaluations_for_binding("b1")} == {"e1", "e2", "e3"}
    windowed = store.list_evaluations_for_binding("b1", since="2026-11-02T00:00:00Z")
    assert {r["id"] for r in windowed} == {"e2", "e3"}
    breached = store.list_evaluations_for_binding(
        "b1", since="2026-11-02T00:00:00Z", governed_breached_only=True
    )
    assert [r["id"] for r in breached] == ["e3"]
    until = store.list_evaluations_for_binding("b1", until="2026-11-01T23:59:59Z")
    assert [r["id"] for r in until] == ["e1"]
    assert {r["id"] for r in store.list_evaluations(governed_breached_only=True)} == {"e1", "e3", "e4"}
    assert len(store.list_evaluations()) == 4


class _InMemoryPostgresOwnerStore:
    """In-memory PostgresJsonOwnerStore replacement with real CAS delete_if_matches."""

    tables: dict[str, dict[str, dict]] = {}
    calls: list[tuple[str, str]] = []

    def __init__(self, *, table: str, **_kwargs: object) -> None:
        self.table = table
        self.records = self.tables.setdefault(table, {})

    def list_all(self) -> list[dict]:
        self.calls.append((self.table, "list_all"))
        return list(self.records.values())

    def count(self) -> int:
        self.calls.append((self.table, "count"))
        return len(self.records)

    def get(self, record_id: str) -> dict | None:
        value = self.records.get(record_id)
        return dict(value) if value is not None else None

    def put(self, record_id: str, payload: dict) -> None:
        self.records[record_id] = dict(payload)

    def compare_and_set(
        self,
        record_id: str,
        expected: dict | None,
        payload: dict,
    ) -> tuple[bool, dict | None]:
        self.calls.append((self.table, "compare_and_set"))
        current = self.records.get(record_id)
        if current != expected:
            return False, dict(current) if current is not None else None
        stored = dict(payload)
        self.records[record_id] = stored
        return True, dict(stored)

    def delete_if_matches(self, record_id: str, expected_payload: dict) -> bool:
        self.calls.append((self.table, "delete_if_matches"))
        current = self.records.get(record_id)
        if current is None or current != expected_payload:
            return False
        del self.records[record_id]
        return True


def _load_service_module_for_store_test(data_dir: Path):
    import sys

    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop("reconciliation_drift_store_service_test_main", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "reconciliation_drift_store_service_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["reconciliation_drift_store_service_test_main"] = module
        with mock.patch.dict(
            "os.environ",
            {
                "RECONCILIATION_DRIFT_DATA_DIR": str(data_dir),
                "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("consumer", None)
        sys.modules.pop("store", None)


@pytest.mark.parametrize("backend", ["json", "postgres"])
def test_prune_work_claims_deletes_expired_scheduled_and_consume_in_bounded_cas_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    module = _load_store_module()
    _InMemoryPostgresOwnerStore.tables = {}
    _InMemoryPostgresOwnerStore.calls = []
    if backend == "postgres":
        monkeypatch.setattr(module, "PostgresJsonOwnerStore", _InMemoryPostgresOwnerStore)
        store = module.PostgresReconciliationDriftStore(
            tmp_path,
            dsn="postgresql://unused",
        )
    else:
        store = module.ReconciliationDriftStore(tmp_path)

    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

    # 1. Completed and failed scheduled_reconcile claims older than 24h -> prunable
    for idx, age_hours in enumerate((30, 26, 25)):
        stamp = now - timedelta(hours=age_hours)
        claimed = store.claim_work(
            tenant_id="tenant-a",
            work_type="scheduled_reconcile",
            window_id=f"sched-old-{idx}",
            owner_id="scheduler-a",
            lease_seconds=60,
            now=stamp,
        )
        if idx % 2 == 0:
            store.complete_work(
                claim_id=claimed["claim"]["claim_id"],
                lease_token=claimed["claim"]["lease_token"],
                result={"status": "ok"},
                now=stamp,
            )
        else:
            store.fail_work(
                claim_id=claimed["claim"]["claim_id"],
                lease_token=claimed["claim"]["lease_token"],
                error="timeout",
                now=stamp,
            )

    # 2. Scheduled_reconcile claims within 24h -> retained
    sched_recent = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="sched-recent",
        owner_id="scheduler-a",
        lease_seconds=60,
        now=now - timedelta(hours=12),
    )
    store.complete_work(
        claim_id=sched_recent["claim"]["claim_id"],
        lease_token=sched_recent["claim"]["lease_token"],
        result={"status": "ok"},
        now=now - timedelta(hours=12),
    )

    # 3. Completed and failed telemetry_consume claims older than 7d -> prunable
    for idx, age_days in enumerate((10, 8)):
        stamp = now - timedelta(days=age_days)
        claimed = store.claim_work(
            tenant_id="tenant-a",
            work_type="telemetry_consume",
            window_id=f"evt-old-{idx}",
            owner_id="consumer-a",
            lease_seconds=60,
            now=stamp,
        )
        if idx == 0:
            store.complete_work(
                claim_id=claimed["claim"]["claim_id"],
                lease_token=claimed["claim"]["lease_token"],
                result={"status": "ok", "event_id": f"evt-old-{idx}"},
                now=stamp,
            )
        else:
            store.fail_work(
                claim_id=claimed["claim"]["claim_id"],
                lease_token=claimed["claim"]["lease_token"],
                error="incidents 502",
                now=stamp,
            )

    # 4. Telemetry_consume claim within 7d -> retained
    consume_recent = store.claim_work(
        tenant_id="tenant-a",
        work_type="telemetry_consume",
        window_id="evt-recent",
        owner_id="consumer-a",
        lease_seconds=60,
        now=now - timedelta(days=3),
    )
    store.complete_work(
        claim_id=consume_recent["claim"]["claim_id"],
        lease_token=consume_recent["claim"]["lease_token"],
        result={"status": "ok", "event_id": "evt-recent"},
        now=now - timedelta(days=3),
    )

    assert store.count_work_claims() == 7

    # Bounded batch run: batch_size=2, max_batches=2 deletes 4 of the 5 expired claims
    first_pass = store.prune_work_claims(now=now, batch_size=2, max_batches=2)
    assert first_pass["batches_executed"] == 2
    assert first_pass["deleted_count"] == 4
    assert store.count_work_claims() == 3

    # Next bounded pass deletes the remaining 1 expired claim
    second_pass = store.prune_work_claims(now=now, batch_size=2, max_batches=2)
    assert second_pass["batches_executed"] == 1
    assert second_pass["deleted_count"] == 1
    assert store.count_work_claims() == 2

    surviving_windows = {claim["window_id"] for claim in store.list_work_claims()}
    assert surviving_windows == {"sched-recent", "evt-recent"}

    if backend == "postgres":
        delete_calls = [
            call
            for call in _InMemoryPostgresOwnerStore.calls
            if call == ("reconciliation_drift.work_claims", "delete_if_matches")
        ]
        assert len(delete_calls) == 5

    # Verify configurability via environment variables
    monkeypatch.setenv("RECONCILIATION_DRIFT_SCHEDULED_CLAIM_RETENTION_HOURS", "6")
    monkeypatch.setenv("RECONCILIATION_DRIFT_TELEMETRY_CONSUME_CLAIM_RETENTION_DAYS", "2")
    configured_pass = store.prune_work_claims(now=now)
    assert configured_pass["deleted_count"] == 2
    assert configured_pass["deleted_by_work_type"] == {
        "scheduled_reconcile": 1,
        "telemetry_consume": 1,
    }
    assert store.count_work_claims() == 0


def test_prune_work_claims_never_deletes_live_lease_or_concurrently_recovered_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_store_module()
    _InMemoryPostgresOwnerStore.tables = {}
    _InMemoryPostgresOwnerStore.calls = []
    monkeypatch.setattr(module, "PostgresJsonOwnerStore", _InMemoryPostgresOwnerStore)
    store = module.PostgresReconciliationDriftStore(tmp_path, dsn="postgresql://unused")

    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

    # 1. Live lease acquired in the past but still active at `now`
    live = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="sched-live-lease",
        owner_id="scheduler-a",
        lease_seconds=3600,
        now=now - timedelta(minutes=10),
    )
    # 2. Failed claim older than 24h that a worker recovers right before CAS delete
    failed_old = store.claim_work(
        tenant_id="tenant-a",
        work_type="scheduled_reconcile",
        window_id="sched-failed-recovering",
        owner_id="scheduler-a",
        lease_seconds=60,
        now=now - timedelta(hours=36),
    )
    store.fail_work(
        claim_id=failed_old["claim"]["claim_id"],
        lease_token=failed_old["claim"]["lease_token"],
        error="transient",
        now=now - timedelta(hours=36),
    )
    recovering_claim_id = failed_old["claim"]["claim_id"]

    orig_delete = store._work_claim_records.delete_if_matches

    def _concurrent_recovery_before_delete(record_id: str, expected_payload: dict) -> bool:
        if record_id == recovering_claim_id:
            # Competing worker recovers the failed claim with a live lease before CAS delete runs
            recovered = store.claim_work(
                tenant_id="tenant-a",
                work_type="scheduled_reconcile",
                window_id="sched-failed-recovering",
                owner_id="scheduler-b",
                lease_seconds=120,
                now=now,
            )
            assert recovered["acquired"] is True
        return orig_delete(record_id, expected_payload)

    monkeypatch.setattr(
        store._work_claim_records,
        "delete_if_matches",
        _concurrent_recovery_before_delete,
    )

    result = store.prune_work_claims(now=now)
    assert result["deleted_count"] == 0
    assert result["cas_conflict_count"] == 1
    assert store.count_work_claims() == 2

    live_claim = store._get_work_claim(live["claim"]["claim_id"])
    recovered_claim = store._get_work_claim(recovering_claim_id)
    assert live_claim is not None and live_claim["status"] == "in_progress"
    assert recovered_claim is not None and recovered_claim["status"] == "in_progress"
    assert recovered_claim["owner_id"] == "scheduler-b"


def test_prune_work_claims_protects_runtime_summary_events_and_consumer_state_backlog(
    tmp_path: Path,
) -> None:
    module = _load_store_module()
    store = module.ReconciliationDriftStore(tmp_path)
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    old_time = now - timedelta(days=10)

    for window_id in (
        "evt-stopped-last",
        "evt-stopped-hb",
        "evt-consumer-pending",
        "evt-consumer-dlq",
        "evt-unreferenced-old",
    ):
        claimed = store.claim_work(
            tenant_id="tenant-a",
            work_type="telemetry_consume",
            window_id=window_id,
            owner_id="consumer-a",
            lease_seconds=60,
            now=old_time,
        )
        store.complete_work(
            claim_id=claimed["claim"]["claim_id"],
            lease_token=claimed["claim"]["lease_token"],
            result={"status": "ok", "event_id": window_id},
            now=old_time,
        )

    consumer_state_path = tmp_path / "consumer-worker-state.json"
    consumer_state_path.write_text(
        json.dumps(
            {
                "version": 2,
                "pending": {
                    "tenant-event-pending": {
                        "event": {"event_id": "evt-consumer-pending", "tenant_id": "tenant-a"},
                        "attempt_count": 1,
                    }
                },
                "dead_letters": {
                    "tenant-event-dlq": {
                        "event": {"event_id": "evt-consumer-dlq", "tenant_id": "tenant-a"},
                        "attempt_count": 3,
                    }
                },
                "completed": {},
            }
        ),
        encoding="utf-8",
    )
    runtime_summaries = [
        {
            "runtime_id": "runtime-stopped-001",
            "binding_id": "binding-stopped-001",
            "state": "stopped",
            "last_event_id": "evt-stopped-last",
            "last_heartbeat_event_id": "evt-stopped-hb",
        }
    ]

    pruned = store.prune_work_claims(
        now=now,
        runtime_summaries=runtime_summaries,
        consumer_state=consumer_state_path,
    )
    assert pruned["deleted_count"] == 1
    surviving_windows = {claim["window_id"] for claim in store.list_work_claims()}
    assert surviving_windows == {
        "evt-stopped-last",
        "evt-stopped-hb",
        "evt-consumer-pending",
        "evt-consumer-dlq",
    }

    # Re-presenting the stopped runtime's last_event_id hits the preserved receipt
    replay = store.claim_work(
        tenant_id="tenant-a",
        work_type="telemetry_consume",
        window_id="evt-stopped-last",
        owner_id="consumer-b",
        lease_seconds=60,
        now=now,
    )
    assert replay["acquired"] is False
    assert replay["reason"] == "completed"

    # Once the runtime summary advances and consumer state drains, the old claims are pruned
    consumer_state_path.write_text(
        json.dumps({"version": 2, "pending": {}, "dead_letters": {}, "completed": {}}),
        encoding="utf-8",
    )
    pruned_after_advance = store.prune_work_claims(
        now=now,
        runtime_summaries=[
            {
                "runtime_id": "runtime-stopped-001",
                "binding_id": "binding-stopped-001",
                "state": "active",
                "last_event_id": "evt-new-active",
                "last_heartbeat_event_id": "evt-new-hb",
            }
        ],
        consumer_state=consumer_state_path,
    )
    assert pruned_after_advance["deleted_count"] == 4
    assert store.count_work_claims() == 0


def test_representing_pruned_window_after_horizon_produces_at_most_one_evaluation_and_no_duplicate_incident(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    svc = _load_service_module_for_store_test(tmp_path)
    client = TestClient(svc.app)

    summary = {
        "runtime_id": "runtime-sched-001",
        "binding_id": "binding-sched-001",
        "runtime_binding_id": "binding-sched-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-sched-001",
        "capital_pool_id": "pool-sched-001",
        "persona_capital_binding_id": "pcb-sched-001",
        "artifact_id": "artifact-sched-001",
        "artifact_version": "1.0.0",
        "trace_id": "trace-sched-001",
        "last_event_id": "evt-sched-001",
        "last_heartbeat_event_id": "evt-hb-sched-001",
        "state": "active",
        "health_summary": {"telemetry": "ok", "paper_runtime": "ok"},
        "pnl": 10.0,
        "drawdown": 0.02,
        "queue_lag_ms": 20_000.0,
        "event_delivery_lag_ms": 100.0,
    }
    window_id = "scheduled:default:2026-11-01T00:00:00Z:PT300S"

    with (
        mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
        mock.patch.object(
            svc,
            "_classify_drift_report_incident",
            return_value={"incident_id": "inc-sched-001"},
        ) as sched_classify,
    ):
        first = client.post(
            "/api/reconciliation-drift/scheduled-reconcile",
            json={"window_id": window_id, "tick_id": window_id},
        )
        assert first.status_code == 201
        first_payload = first.json()
        assert first_payload["evaluated_binding_count"] == 1
        assert first_payload["incident_ids"] == ["inc-sched-001"]
        assert sched_classify.call_count == 1

        # Prune the scheduled_reconcile work claim after its 24h horizon
        future_after_24h = datetime.now(timezone.utc) + timedelta(hours=26)
        pruned_sched = svc.store.prune_work_claims(now=future_after_24h)
        assert pruned_sched["deleted_by_work_type"]["scheduled_reconcile"] == 1
        assert svc.store.count_work_claims() == 0

        # Re-present the pruned window with the same tick_id -> 0 new evaluations, 0 duplicate incidents
        re_presented_same_tick = client.post(
            "/api/reconciliation-drift/scheduled-reconcile",
            json={"window_id": window_id, "tick_id": window_id},
        )
        assert re_presented_same_tick.status_code == 201
        same_tick_payload = re_presented_same_tick.json()
        assert same_tick_payload["evaluated_binding_count"] <= 1
        assert same_tick_payload["evaluated_binding_count"] == 0
        assert sched_classify.call_count == 1
        assert svc.store.count_evaluations() == 1

        # Prune again and re-present the same window_id with a new tick_id -> at most 1 new evaluation, 0 duplicate incidents
        svc.store.prune_work_claims(now=future_after_24h)
        re_presented_new_tick = client.post(
            "/api/reconciliation-drift/scheduled-reconcile",
            json={"window_id": window_id, "tick_id": f"{window_id}:retry"},
        )
        assert re_presented_new_tick.status_code == 201
        new_tick_payload = re_presented_new_tick.json()
        assert new_tick_payload["evaluated_binding_count"] == 1
        assert sched_classify.call_count == 1

    # Also verify telemetry_consume re-presentation after its 7d horizon
    consume_event = {
        "event_id": "evt-consume-pruned-001",
        "binding_id": "binding-sched-001",
        "runtime_id": "runtime-sched-001",
        "deployment_stage": "live",
        "metrics": {"pnl": 10.0},
        "baseline_metrics": {"pnl": 100.0},
        "thresholds": {"pnl": {"critical_relative_delta": 0.5}},
    }
    with mock.patch.object(
        svc,
        "_classify_drift_report_incident",
        return_value={"incident_id": "inc-consume-001"},
    ) as consume_classify:
        first_consume = client.post(
            "/api/reconciliation-drift/telemetry-events/consume",
            json={"events": [consume_event]},
        )
        assert first_consume.status_code == 201
        assert first_consume.json()["incident_case_count"] == 1
        assert consume_classify.call_count == 1

        # Prune after 8 days (event is not in active runtime summaries)
        future_after_8d = datetime.now(timezone.utc) + timedelta(days=8)
        pruned_consume = svc.store.prune_work_claims(
            now=future_after_8d,
            runtime_summaries=[summary],
        )
        assert pruned_consume["deleted_by_work_type"]["telemetry_consume"] == 1

        # Re-presenting the pruned event after 7d horizon produces no duplicate incident
        re_consumed = client.post(
            "/api/reconciliation-drift/telemetry-events/consume",
            json={"events": [consume_event]},
        )
        assert re_consumed.status_code == 201
        re_payload = re_consumed.json()
        assert re_payload["incident_case_count"] == 0
        assert re_payload["duplicate_event_ids"] == ["evt-consume-pruned-001"]
        assert consume_classify.call_count == 1


def test_hosted_shape_150k_existing_completed_claims_pruned_to_steady_state_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 5: Measure on 150,000 existing completed claims at hosted shape (~6,000/day)."""
    module = _load_store_module()
    _InMemoryPostgresOwnerStore.tables = {}
    _InMemoryPostgresOwnerStore.calls = []
    monkeypatch.setattr(module, "PostgresJsonOwnerStore", _InMemoryPostgresOwnerStore)
    store = module.PostgresReconciliationDriftStore(tmp_path, dsn="postgresql://unused")

    now = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)
    days = 25
    scheduled_per_day = 288        # one per 300s (5 min) window
    consume_per_day = 5712         # 288 + 5712 = 6000 claims/day * 25 days = 150,000 claims
    records = store._work_claim_records.records

    for day in range(days):
        # Bucket timestamps inside each 24h day window before `now`:
        # day=0 is the newest day ([now - 24h + 300s, now - 60s]), inside the 24h and 7d horizons.
        # day=1..6 are >24h old and <=7d old.
        # day=7..24 are >7d old.
        day_end = now - timedelta(days=day)
        sched_ts = (day_end - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        for slot in range(scheduled_per_day):
            cid = f"work-scheduled_reconcile-d{day:02d}-s{slot:03d}"
            records[cid] = {
                "id": cid,
                "claim_id": cid,
                "tenant_id": "default",
                "work_type": "scheduled_reconcile",
                "window_id": f"scheduled:default:d{day:02d}:s{slot:03d}",
                "status": "completed",
                "owner_id": "reconciliation-scheduler",
                "lease_token": None,
                "lease_expires_at": None,
                "attempt_count": 1,
                "created_at": sched_ts,
                "updated_at": sched_ts,
                "completed_at": sched_ts,
                "failed_at": None,
                "last_error": None,
                "result": {"status": "ok"},
            }
        consume_ts = (day_end - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        for idx in range(consume_per_day):
            cid = f"work-telemetry_consume-d{day:02d}-e{idx:04d}"
            records[cid] = {
                "id": cid,
                "claim_id": cid,
                "tenant_id": "default",
                "work_type": "telemetry_consume",
                "window_id": f"evt-d{day:02d}-e{idx:04d}",
                "status": "completed",
                "owner_id": "reconciliation-consumer",
                "lease_token": None,
                "lease_expires_at": None,
                "attempt_count": 1,
                "created_at": consume_ts,
                "updated_at": consume_ts,
                "completed_at": consume_ts,
                "failed_at": None,
                "last_error": None,
                "result": {"status": "ok", "event_id": f"evt-d{day:02d}-e{idx:04d}"},
            }

    assert store.count_work_claims() == 150_000

    prune_result = store.prune_work_claims(now=now, batch_size=5000)
    expected_sched_deleted = (days - 1) * scheduled_per_day       # 24 * 288 = 6,912
    expected_consume_deleted = (days - 7) * consume_per_day       # 18 * 5,712 = 102,816
    expected_total_deleted = expected_sched_deleted + expected_consume_deleted  # 109,728
    expected_steady_state = scheduled_per_day + (7 * consume_per_day)           # 288 + 39,984 = 40,272

    assert prune_result["deleted_count"] == expected_total_deleted == 109_728
    assert prune_result["deleted_by_work_type"] == {
        "scheduled_reconcile": 6_912,
        "telemetry_consume": 102_816,
    }
    assert prune_result["batches_executed"] == 22
    assert store.count_work_claims() == expected_steady_state == 40_272
    assert store.count_work_claims() <= 45_000

    # Simulate one more full day (+6,000 claims) and prune at now + 1 day: steady state holds at 40,272
    next_day = now + timedelta(days=1)
    next_sched_ts = (next_day - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    for slot in range(scheduled_per_day):
        cid = f"work-scheduled_reconcile-next-s{slot:03d}"
        records[cid] = {
            "id": cid,
            "claim_id": cid,
            "tenant_id": "default",
            "work_type": "scheduled_reconcile",
            "window_id": f"scheduled:default:next:s{slot:03d}",
            "status": "completed",
            "owner_id": "reconciliation-scheduler",
            "lease_token": None,
            "lease_expires_at": None,
            "attempt_count": 1,
            "created_at": next_sched_ts,
            "updated_at": next_sched_ts,
            "completed_at": next_sched_ts,
            "failed_at": None,
            "last_error": None,
            "result": {"status": "ok"},
        }
    next_consume_ts = (next_day - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    for idx in range(consume_per_day):
        cid = f"work-telemetry_consume-next-e{idx:04d}"
        records[cid] = {
            "id": cid,
            "claim_id": cid,
            "tenant_id": "default",
            "work_type": "telemetry_consume",
            "window_id": f"evt-next-e{idx:04d}",
            "status": "completed",
            "owner_id": "reconciliation-consumer",
            "lease_token": None,
            "lease_expires_at": None,
            "attempt_count": 1,
            "created_at": next_consume_ts,
            "updated_at": next_consume_ts,
            "completed_at": next_consume_ts,
            "failed_at": None,
            "last_error": None,
            "result": {"status": "ok", "event_id": f"evt-next-e{idx:04d}"},
        }

    next_prune = store.prune_work_claims(now=next_day, batch_size=5000)
    assert next_prune["deleted_count"] == 6_000
    assert store.count_work_claims() == 40_272 <= 45_000


def test_previous_release_store_works_against_pruned_work_claims_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 6: Previous accepted release reads and writes the pruned table cleanly."""
    prev_source = subprocess.run(
        ["git", "show", "ede3b8035:services/reconciliation-drift/store.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    prev_path = tmp_path / "previous_release_store.py"
    prev_path.write_text(prev_source, encoding="utf-8")
    prev_module = _load_module_from_path(prev_path, "previous_release_store_mod")
    new_module = _load_store_module()

    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    data_dir = tmp_path / "store_data"

    # Populate and prune using the new store on both JSON and Postgres backends
    _InMemoryPostgresOwnerStore.tables = {}
    _InMemoryPostgresOwnerStore.calls = []
    monkeypatch.setattr(new_module, "PostgresJsonOwnerStore", _InMemoryPostgresOwnerStore)
    monkeypatch.setattr(prev_module, "PostgresJsonOwnerStore", _InMemoryPostgresOwnerStore)

    for new_store, prev_store in (
        (
            new_module.ReconciliationDriftStore(data_dir),
            prev_module.ReconciliationDriftStore(data_dir),
        ),
        (
            new_module.PostgresReconciliationDriftStore(data_dir, dsn="postgresql://unused"),
            prev_module.PostgresReconciliationDriftStore(data_dir, dsn="postgresql://unused"),
        ),
    ):
        old_claim = new_store.claim_work(
            tenant_id="tenant-a",
            work_type="scheduled_reconcile",
            window_id="win-expired",
            owner_id="worker-1",
            lease_seconds=60,
            now=now - timedelta(hours=30),
        )
        new_store.complete_work(
            claim_id=old_claim["claim"]["claim_id"],
            lease_token=old_claim["claim"]["lease_token"],
            result={"status": "ok", "evaluation_ids": ["eval-old"]},
            now=now - timedelta(hours=30),
        )
        kept_claim = new_store.claim_work(
            tenant_id="tenant-a",
            work_type="scheduled_reconcile",
            window_id="win-kept",
            owner_id="worker-1",
            lease_seconds=60,
            now=now - timedelta(hours=2),
        )
        new_store.complete_work(
            claim_id=kept_claim["claim"]["claim_id"],
            lease_token=kept_claim["claim"]["lease_token"],
            result={"status": "ok", "evaluation_ids": ["eval-kept"]},
            now=now - timedelta(hours=2),
        )
        active_claim = new_store.claim_work(
            tenant_id="tenant-a",
            work_type="telemetry_consume",
            window_id="evt-active",
            owner_id="worker-1",
            lease_seconds=600,
            now=now,
        )
        assert active_claim["acquired"] is True

        pruned = new_store.prune_work_claims(now=now)
        assert pruned["deleted_count"] == 1

        # Rollback: previous release reads kept receipt, respects active lease, and claims pruned window
        kept_check = prev_store.claim_work(
            tenant_id="tenant-a",
            work_type="scheduled_reconcile",
            window_id="win-kept",
            owner_id="worker-rollback",
            lease_seconds=60,
            now=now,
        )
        assert kept_check["acquired"] is False
        assert kept_check["reason"] == "completed"
        assert kept_check["claim"]["result"] == {"status": "ok", "evaluation_ids": ["eval-kept"]}

        active_check = prev_store.claim_work(
            tenant_id="tenant-a",
            work_type="telemetry_consume",
            window_id="evt-active",
            owner_id="worker-rollback",
            lease_seconds=60,
            now=now,
        )
        assert active_check["acquired"] is False
        assert active_check["reason"] == "lease_active"

        reclaimed = prev_store.claim_work(
            tenant_id="tenant-a",
            work_type="scheduled_reconcile",
            window_id="win-expired",
            owner_id="worker-rollback",
            lease_seconds=60,
            now=now,
        )
        assert reclaimed["acquired"] is True
        assert reclaimed["reason"] == "acquired"
        completed_reclaimed = prev_store.complete_work(
            claim_id=reclaimed["claim"]["claim_id"],
            lease_token=reclaimed["claim"]["lease_token"],
            result={"status": "ok"},
            now=now,
        )
        assert completed_reclaimed["status"] == "completed"

