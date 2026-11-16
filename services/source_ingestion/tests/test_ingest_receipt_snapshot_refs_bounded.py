"""SOURCE-INGEST-RECEIPT-SNAPSHOT-BODIES-BOUND-20261116.

Receipts carry one bounded reference per updated snapshot instead of its body; journals
written by the deployed version (e7ec353d6, bodies embedded) shrink on start; both
versions read each other's journals.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion import scheduler as new_scheduler
from services.source_ingestion.pipeline import (
    ReceiptSnapshotUnavailable,
    resolve_receipt_snapshot_bodies,
)
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
from services.source_ingestion.scheduler import (
    IngestReceipt,
    JsonlIngestScheduleStore,
    slim_market_snapshot_batch,
    slim_receipt_payload,
)
from services.source_ingestion.tests.test_latest_market_snapshot import (
    _connector,
    _load_source_main,
    _read_headers,
)

DEPLOYED_SHA = "e7ec353d6"  # hosted stack when this task was opened
REF_KEYS = {"snapshot_id", "symbol", "dataset", "event_time"}
RECEIPT_LINE_BOUND_500 = 250_000  # bytes; a 500 record bundle receipt line (measured ~150 KB, ids only)


def _static_record(symbol: str, day: int, close: float) -> dict[str, Any]:
    event_time = f"2026-09-{day:02d}T20:00:00Z"
    return {
        "source_id": f"{symbol}-{event_time}",
        "title": f"{symbol} close {event_time}",
        "content_ref": f"source-test://{symbol}/{event_time}",
        "metadata": {"normalized_row": {
            "schema_version": "us_equity_price_daily.v1", "symbol_canonical": symbol,
            "trade_date": event_time, "close": close}},
    }


def _run_job(client: TestClient, connector_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    headers = _read_headers()
    connector = {**_connector(), "connector_id": connector_id}
    configured = client.post("/api/source-ingest/connectors", headers=headers,
                             json={"connector": connector, "fetch": {"mode": "static_records", "records": records}})
    assert configured.status_code == 201, configured.text
    job = client.post("/api/source-ingest/jobs", headers=headers,
                      json={"connector_id": connector_id, "trace_id": f"trace-{connector_id}"})
    assert job.status_code == 201, job.text
    assert job.json()["run"]["status"] == "completed"
    return job.json()


@pytest.fixture()
def bundle_run(tmp_path, monkeypatch):
    """500-record bundle on top of a 28-day history, through the real app, pipeline and stores."""
    module = _load_source_main(monkeypatch, tmp_path)
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "20000")
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.reload(importlib.import_module("services.source_ingestion.main"))
    client = TestClient(module.app)
    symbols = [f"S{i:04d}.US" for i in range(500)]
    history = [_static_record(s, d, 100.0 + d) for s in symbols for d in range(1, 29)]
    seed = _run_job(client, "seed-history", history)
    assert seed["evidence_refs"]["market_snapshots"]["updated_snapshot_count"] == 500
    day29 = [_static_record(s, 29, 130.0) for s in symbols]
    job = _run_job(client, "stored-price-test", day29)
    return module.runtime, client, job, symbols


def _journal_receipt_lines(path: Path, run_id: str) -> list[bytes]:
    return [line for line in path.read_bytes().splitlines()
            if b'"record_type":"ingest_receipt"' in line and run_id.encode() in line]


def test_bundle_receipt_holds_refs_not_bodies_and_is_bounded(bundle_run):
    runtime, client, job, symbols = bundle_run
    run_id = job["run"]["ingest_run_id"]
    batch = runtime.store.get_receipt(run_id).evidence_refs["market_snapshots"]
    assert batch["accepted_record_count"] == batch["updated_snapshot_count"] == 500
    assert len(batch["snapshots"]) == 500
    assert all(set(ref) == REF_KEYS for ref in batch["snapshots"])
    assert {ref["symbol"] for ref in batch["snapshots"]} == set(symbols)
    assert all(ref["dataset"] == "tw_price_daily" or ref["dataset"] for ref in batch["snapshots"])
    lines = _journal_receipt_lines(runtime.SCHEDULE_STORE_PATH, run_id)
    assert lines, "receipt rows for the bundle run must be in the journal"
    assert max(len(line) for line in lines) < RECEIPT_LINE_BOUND_500
    assert all(b'"closes"' not in line for line in lines)
    print("RECEIPT_LINE_BYTES_500=" + str(max(len(line) for line in lines)))
    # Every reader keeps its counts: summary, receipt endpoint, run endpoint.
    full = client.get(f"/api/source-ingest/receipts/{run_id}", headers=_read_headers())
    assert full.status_code == 200
    assert full.json()["receipt"]["evidence_refs"]["market_snapshots"] == batch
    # The ingest response is unchanged for the caller: bodies are still returned there.
    assert all("closes" in body for body in job["evidence_refs"]["market_snapshots"]["snapshots"])


def test_receipt_size_does_not_grow_with_closes_per_snapshot(tmp_path):
    from services.source_ingestion.connectors.base import SourceRecord

    sizes = []
    for depth in (2, 60):
        store = LatestMarketSnapshotStore(tmp_path / f"snap-{depth}.jsonl")
        for day in range(1, depth + 1):
            records = [SourceRecord(
                source_id=f"{s}-{day}", connector_id="c", source_type="market", title="t",
                content_ref=f"ref://{s}/{day}", created_at="2026-10-01T00:00:00Z",
                metadata={"symbol": s, "event_time": (datetime(2026, 1, 1) + timedelta(days=day)).strftime("%Y-%m-%dT00:00:00Z"),
                          "close": 100.0 + day, "market": "TW", "dataset": "tw_price_daily"},
            ) for s in ("2330.TWSE", "2317.TWSE")]
            batch = store.append_normalized_records(records, ingest_run_id=f"run-{day:03d}",
                                                    observed_at="2026-10-01T00:00:00Z")
        assert max(len(snap["closes"]) for snap in batch["snapshots"]) == min(depth, 60)
        sizes.append(len(json.dumps(slim_market_snapshot_batch(batch), sort_keys=True)))
    assert sizes[0] == sizes[1]


def test_new_receipts_resolve_bodies_by_identifier_and_fail_closed_when_superseded(bundle_run):
    runtime, _, job, _ = bundle_run
    store = runtime.latest_market_snapshot_store
    batch = runtime.store.get_receipt(job["run"]["ingest_run_id"]).evidence_refs["market_snapshots"]
    bodies = resolve_receipt_snapshot_bodies(store, batch)
    assert bodies == job["evidence_refs"]["market_snapshots"]["snapshots"]  # same bodies as before the change
    superseding = [_static_record("S0000.US", 30, 140.0)]
    # A newer close for one symbol supersedes its referenced snapshot.
    from services.source_ingestion.connectors.base import SourceRecord
    store.append_normalized_records([SourceRecord(
        source_id="S0000.US-new", connector_id="c", source_type="market", title="t",
        content_ref="ref://S0000.US/new", created_at="2026-10-01T00:00:00Z",
        metadata={"normalized_row": superseding[0]["metadata"]["normalized_row"]})],
        ingest_run_id="run-newer", observed_at="2026-10-01T00:00:00Z")
    with pytest.raises(ReceiptSnapshotUnavailable, match=r"S0000\.US.*superseded by"):
        resolve_receipt_snapshot_bodies(store, batch)
    missing = {"snapshots": [{"snapshot_id": "snap-x", "symbol": "NOPE.US", "dataset": "tw_price_daily",
                              "event_time": "2026-09-01T00:00:00Z"}]}
    with pytest.raises(ReceiptSnapshotUnavailable, match="no longer in the latest market snapshot store"):
        resolve_receipt_snapshot_bodies(store, missing)
    legacy = {"snapshots": [{"snapshot_id": "a", "closes": [1.0]}]}
    assert resolve_receipt_snapshot_bodies(store, legacy) == legacy["snapshots"]  # embedded body kept


def test_runtime_metrics_expose_schedule_journal_size_and_receipt_count(bundle_run):
    runtime, *_ = bundle_run
    stats = runtime._source_runtime_metrics()["ingest_schedule_journal"]
    assert stats["journal_bytes"] == runtime.SCHEDULE_STORE_PATH.stat().st_size
    assert stats["receipt_count"] == len(runtime.store.list_receipts()) >= 2
    assert stats["journal_lines"] >= stats["receipt_count"]


# ---- legacy journal: slim on start, crash safe, lossless otherwise -------------------------------

def _legacy_receipt(index: int, closes: int = 60) -> IngestReceipt:
    bodies = [{"schema_version": "x", "snapshot_id": f"snap-{index}-{n}", "symbol": f"{n}.TWSE",
               "event_time": "2026-11-16T00:00:00Z", "observed_at": "2026-11-16T01:00:00Z",
               "closes": [100.0 + i for i in range(closes)], "lineage": {"content_refs": ["r"] * closes},
               "source_ref": f"source-ingest://snapshots/snap-{index}-{n}"} for n in range(120)]
    return IngestReceipt(
        ingest_run_id=f"ingest-legacy-{index:04d}", connector_id="collector-tw-twse-price-history",
        status="completed", trigger_type="scheduled", trace_id="t", started_at="2026-11-16T01:00:00Z",
        finished_at="2026-11-16T01:01:00Z", raw_count=120, normalized_count=120, rejected_count=0,
        watermark="w", source_timestamp="2026-11-16T00:00:00Z", source_timestamp_status="valid",
        evidence_refs={"source_ids": ["a", "b"], "evidence_bundle_id": "eb", "knowledge_object_ids": [],
                       "storage_refs": {"k": "v"},
                       "market_snapshots": {"schema_version": "b.v1", "ingest_run_id": f"ingest-legacy-{index:04d}",
                                            "accepted_record_count": 120, "updated_snapshot_count": 120,
                                            "snapshots": bodies}},
    )


def _write_legacy_journal(path: Path, count: int = 12) -> list[IngestReceipt]:
    receipts = [_legacy_receipt(i) for i in range(count)]
    with path.open("wb") as handle:
        for receipt in receipts:  # exactly the deployed writer's row format
            handle.write((json.dumps({"schema_version": "ingest_schedule_store.v1", "record_type": "ingest_receipt",
                                      "record_id": receipt.ingest_run_id, "payload": receipt.to_dict()},
                                     sort_keys=True, separators=(",", ":")) + "\n").encode())
    return receipts


def _identity(receipt: IngestReceipt) -> dict[str, Any]:
    data = receipt.to_dict()
    refs = dict(data["evidence_refs"])
    refs["market_snapshots"] = {k: v for k, v in refs["market_snapshots"].items() if k != "snapshots"}
    return {**data, "evidence_refs": refs}


def test_start_slims_embedded_bodies_and_keeps_every_other_field(tmp_path):
    path = tmp_path / "schedule.jsonl"
    receipts = _write_legacy_journal(path)
    before = path.stat().st_size
    store = JsonlIngestScheduleStore(path)
    assert path.stat().st_size < before // 5
    assert not path.with_name(path.name + ".slim.tmp").exists()
    for original in receipts:
        loaded = store.get_receipt(original.ingest_run_id)
        assert _identity(loaded) == _identity(original)
        slim = loaded.evidence_refs["market_snapshots"]["snapshots"]
        assert [r["snapshot_id"] for r in slim] == [b["snapshot_id"] for b in original.evidence_refs["market_snapshots"]["snapshots"]]
        assert all(set(r) == REF_KEYS for r in slim)
    assert store.journal_stats()["last_slim_rewrite"]["slimmed_rows"] == len(receipts)
    # A second start finds nothing to do and leaves the journal byte-identical.
    after = path.read_bytes()
    JsonlIngestScheduleStore(path)
    assert path.read_bytes() == after


def test_replay_slims_in_memory_even_when_the_rewrite_cannot_run(tmp_path, monkeypatch):
    path = tmp_path / "schedule.jsonl"
    receipts = _write_legacy_journal(path, count=3)
    before = path.read_bytes()

    def refuse(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(new_scheduler.os, "replace", refuse)
    store = JsonlIngestScheduleStore(path)
    assert path.read_bytes() == before  # failed rewrite leaves the journal untouched and readable
    assert not path.with_name(path.name + ".slim.tmp").exists()
    for original in receipts:
        assert "closes" not in json.dumps(store.get_receipt(original.ingest_run_id).evidence_refs)


def test_every_intermediate_file_state_is_a_readable_journal(tmp_path):
    path = tmp_path / "schedule.jsonl"
    receipts = _write_legacy_journal(path, count=4)
    # Crash before replace: journal untouched, a partial temp is discarded on next start.
    tmp = path.with_name(path.name + ".slim.tmp")
    tmp.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])  # torn temp from a crash
    store = JsonlIngestScheduleStore(path)
    assert not tmp.exists()
    assert {r.ingest_run_id for r in store.list_receipts()} == {r.ingest_run_id for r in receipts}
    # Crash after replace: the rewritten file is itself a complete journal.
    assert JsonlIngestScheduleStore(path).journal_stats()["receipt_count"] == len(receipts)


# ---- rollback: deployed reader <-> new journal, in both directions -----------------------------

def _previous_scheduler_module():
    source = subprocess.run(
        ["git", "show", f"{DEPLOYED_SHA}:services/source_ingestion/scheduler.py"],
        capture_output=True, text=True, check=False, cwd=Path(__file__).resolve().parents[3])
    if source.returncode != 0:
        pytest.fail(f"deployed reader {DEPLOYED_SHA} is not available from git: {source.stderr.strip()}")
    name = "services.source_ingestion._deployed_scheduler"
    module = types.ModuleType(name)
    module.__package__ = "services.source_ingestion"
    module.__file__ = "<deployed scheduler>"
    sys.modules[name] = module
    exec(compile(source.stdout, module.__file__, "exec"), module.__dict__)
    return module


def _state(store) -> dict[str, Any]:
    store.read_freshness_snapshot()
    return {name: {k: v.to_dict() for k, v in getattr(store, name).items()}
            for name in ("_runs", "_receipts", "_watermarks", "_frontier")}


def test_deployed_reader_reads_journal_written_and_rewritten_by_new_code(tmp_path):
    previous = _previous_scheduler_module()
    path = tmp_path / "schedule.jsonl"
    receipts = _write_legacy_journal(path, count=5)
    new_store = JsonlIngestScheduleStore(path)  # rewrites the journal
    new_store.upsert_receipt(_legacy_receipt(99))  # and writes a new slim receipt
    old_view = previous.JsonlIngestScheduleStore(path)
    assert set(old_view._receipts) == set(new_store._receipts) == {r.ingest_run_id for r in receipts} | {"ingest-legacy-0099"}
    assert _state(old_view) == _state(new_store)  # same runs, watermarks, frontier, receipt identities


def test_new_reader_reads_journal_written_by_deployed_version(tmp_path):
    previous = _previous_scheduler_module()
    path = tmp_path / "schedule.jsonl"
    writer = previous.JsonlIngestScheduleStore(path)
    legacy = [writer.upsert_receipt(_legacy_receipt(i)) for i in range(5)]
    old_state = _state(writer)
    new_store = JsonlIngestScheduleStore(path)
    new_state = _state(new_store)
    assert set(new_state["_receipts"]) == set(old_state["_receipts"]) == {r.ingest_run_id for r in legacy}
    for run_id, old_row in old_state["_receipts"].items():
        assert _identity(IngestReceipt.from_dict(new_state["_receipts"][run_id])) == _identity(IngestReceipt.from_dict(old_row))
    assert new_state["_runs"] == old_state["_runs"] and new_state["_watermarks"] == old_state["_watermarks"]


def test_slim_is_idempotent_and_leaves_non_snapshot_receipts_alone():
    payload = _legacy_receipt(1).to_dict()
    once, changed = slim_receipt_payload(payload)
    twice, changed_again = slim_receipt_payload(once)
    assert changed and not changed_again and twice is once
    plain = {"evidence_refs": {"source_ids": []}}
    assert slim_receipt_payload(plain) == (plain, False)
