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
from services.source_ingestion.runtime import _receipt_summary
from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
from services.source_ingestion.scheduler import (
    IngestReceipt,
    JsonlIngestScheduleStore,
    slim_market_snapshot_batch,
    compact_receipt_payload,
    slim_receipt_payload,
)
from services.source_ingestion.tests.test_latest_market_snapshot import (
    _connector,
    _load_source_main,
    _read_headers,
)

DEPLOYED_SHA = "e7ec353d6"  # hosted stack when this task was opened
REF_KEYS = {"snapshot_id", "symbol", "dataset", "event_time"}
RECEIPT_LINE_BOUND_500 = 16 * 1024  # bytes; a 500 record batch receipt line: counts and references only


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
    assert "snapshots" not in batch  # no per-record list
    assert batch["snapshot_count"] == 500
    assert batch["snapshot_refs"]["ingest_run_id"] == run_id
    refs = runtime.store.get_receipt(run_id).evidence_refs
    for listed in ("source_ids", "evidence_item_ids", "knowledge_object_ids", "distillation_admissions"):
        assert listed not in refs
    assert refs["source_id_count"] == refs["evidence_item_id_count"] == refs["knowledge_object_id_count"] == 500
    assert refs["distillation_admission_count"] == 500
    assert refs["evidence_bundle_id"]
    lines = _journal_receipt_lines(runtime.SCHEDULE_STORE_PATH, run_id)
    assert lines, "receipt rows for the bundle run must be in the journal"
    assert max(len(line) for line in lines) <= RECEIPT_LINE_BOUND_500, max(len(line) for line in lines)
    assert all(b'"closes"' not in line for line in lines)
    print("RECEIPT_LINE_BYTES_500=" + str(max(len(line) for line in lines)))
    # Every reader keeps its counts: summary, receipt endpoint, run endpoint.
    full = client.get(f"/api/source-ingest/receipts/{run_id}", headers=_read_headers())
    assert full.status_code == 200
    assert full.json()["receipt"]["evidence_refs"]["market_snapshots"] == batch
    summary = _receipt_summary(runtime.store.get_receipt(run_id))["evidence_refs"]
    assert summary["source_id_count"] == 500 and summary["market_snapshots"]["snapshot_count"] == 500
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
    counts_only = runtime.store.get_receipt(job["run"]["ingest_run_id"]).evidence_refs["market_snapshots"]
    with pytest.raises(ReceiptSnapshotUnavailable, match="counts and a reference only"):
        resolve_receipt_snapshot_bodies(store, counts_only)
    batch = slim_market_snapshot_batch(job["evidence_refs"]["market_snapshots"])  # reference list form
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
    return dict(compact_receipt_payload({**data, "evidence_refs": refs})[0])


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
        market = loaded.evidence_refs["market_snapshots"]
        assert "snapshots" not in market and market["snapshot_count"] == 120
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

def _previous_scheduler_module(sha: str = DEPLOYED_SHA):
    source = subprocess.run(
        ["git", "show", f"{sha}:services/source_ingestion/scheduler.py"],
        capture_output=True, text=True, check=False, cwd=Path(__file__).resolve().parents[3])
    if source.returncode != 0:
        pytest.fail(f"deployed reader {sha} is not available from git: {source.stderr.strip()}")
    name = f"services.source_ingestion._deployed_scheduler_{sha}"
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


# ---- SOURCE-RECEIPT-STORAGE-REFS-BOUND-20261118: storage_refs as counts, dates and a digest -------------

PRE_STORAGE_REFS_SHA = "638f5b1bd"  # dev head when this task was opened (storage_refs still a full list)
STORAGE_LINE_BOUND = 16384


def _history_storage_refs(root: Path, run_id: str, dates: int, *, write_files: bool = True) -> dict[str, Any]:
    """Hosted shape, fixed seed: one raw/normalized/feature ref per dataset and date of a history pull."""
    import random
    rng = random.Random(20261118)
    raw_refs, normalized_refs, feature_refs = [], [], []
    for n in range(dates):
        date = (datetime(2024, 1, 1) + timedelta(days=n)).strftime("%Y-%m-%d")
        rows = rng.randint(1, 5)
        paths = {
            "raw": root / "raw" / "collector-tw-twse-taiex-history" / "tw_index_daily" / f"date={date}" / f"{run_id}.jsonl.gz",
            "normalized": root / "normalized" / "tw_index_daily" / f"date={date}" / f"{run_id}.jsonl",
            "features": root / "features" / "tw_index_feature" / f"date={date}" / f"{run_id}.jsonl",
        }
        if write_files:
            import gzip
            for kind, path in paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "raw":
                    with gzip.open(path, "wt") as handle:
                        handle.write("{}\n" * rows)
                else:
                    path.write_text("{}\n" * rows)
        raw_refs.append({"ref_type": "raw_object", "source": "collector-tw-twse-taiex-history", "dataset": "tw_index_daily",
                         "date": date, "uri": paths["raw"].as_posix(), "row_count": rows, "compression": "gzip",
                         "retention_days": 365, "retention_policy_ref": "policy://raw", "storage_class": "standard"})
        normalized_refs.append({"ref_type": "normalized_rows", "dataset": "tw_index_daily", "date": date,
                                "uri": paths["normalized"].as_posix(), "row_count": rows, "schema_hash": f"{rng.getrandbits(128):032x}"})
        feature_refs.append({"ref_type": "feature_rows", "dataset": "tw_index_feature", "source_dataset": "tw_index_daily",
                             "date": date, "uri": paths["features"].as_posix(), "row_count": rows, "feature_as_of_time": date})
    return {"schema_version": "market_data_storage_manifest.v1", "ingest_run_id": run_id,
            "raw_refs": raw_refs, "normalized_refs": normalized_refs, "feature_refs": feature_refs,
            "summary": {"raw_ref_count": dates, "normalized_ref_count": dates, "feature_ref_count": dates,
                        "normalized_row_count": sum(r["row_count"] for r in normalized_refs)}}


def _history_receipt(root: Path, run_id: str, dates: int, connector: str = "collector-tw-twse-taiex-history") -> IngestReceipt:
    return IngestReceipt(
        ingest_run_id=run_id, connector_id=connector, status="completed", trigger_type="scheduled", trace_id="t",
        started_at="2026-11-17T22:35:00Z", finished_at="2026-11-17T22:35:17Z", raw_count=dates, normalized_count=dates,
        rejected_count=0, watermark="w", source_timestamp="2026-11-17T00:00:00Z", source_timestamp_status="valid",
        evidence_refs={"evidence_bundle_id": "eb"}, storage_refs=_history_storage_refs(root, run_id, dates))


def _stored_lines(path: Path) -> list[bytes]:
    return [line for line in path.read_bytes().splitlines() if b'"record_type":"ingest_receipt"' in line]


def test_reproduce_previous_release_writes_oversize_storage_refs_line(tmp_path):
    previous = _previous_scheduler_module(PRE_STORAGE_REFS_SHA)
    path = tmp_path / "schedule.jsonl"
    previous.JsonlIngestScheduleStore(path).upsert_receipt(_history_receipt(tmp_path / "data", "ingest-hist-old", 500))
    size = max(len(line) for line in _stored_lines(path))
    print("OLD_RECEIPT_LINE_BYTES_500_DATES=" + str(size))
    assert size > STORAGE_LINE_BOUND * 10
    alarm = previous.JsonlIngestScheduleStore(path).journal_stats()
    assert alarm["receipt_lines_over_budget"] >= 1


def test_history_pull_of_500_dates_stores_counts_dates_and_digest_under_bound(tmp_path):
    path = tmp_path / "schedule.jsonl"
    store = JsonlIngestScheduleStore(path)
    receipt = _history_receipt(tmp_path / "data", "ingest-hist-new", 500)
    full = {k: [dict(r) for r in receipt.storage_refs[k]] for k in ("raw_refs", "normalized_refs", "feature_refs")}
    store.upsert_receipt(receipt)
    lines = _stored_lines(path)
    size = max(len(line) for line in lines)
    print("NEW_RECEIPT_LINE_BYTES_500_DATES=" + str(size))
    assert size <= STORAGE_LINE_BOUND
    stored = json.loads(lines[-1])["payload"]["storage_refs"]
    assert "raw_refs" not in stored and "normalized_refs" not in stored and "feature_refs" not in stored
    assert stored["ref_counts"] == {"raw": 500, "normalized": 500, "feature": 500}
    (group,) = stored["stores"]["normalized"]
    assert (group["dataset"], group["count"], group["first_date"], group["last_date"]) == (
        "tw_index_daily", 500, "2024-01-01", "2025-05-14")
    assert group["row_count"] == stored["summary"]["normalized_row_count"]
    assert len(stored["digest"]) == 64
    assert store.journal_stats()["receipt_lines_over_budget"] == 0
    # Idempotent and secret-free (no credential keys ever enter this shape).
    assert new_scheduler.compact_receipt_storage_refs(stored) == (stored, False)
    # Readers of the memory copy and of a restarted store get the same lists back from the store itself.
    for reloaded in (store.get_receipt("ingest-hist-new"), JsonlIngestScheduleStore(path).get_receipt("ingest-hist-new")):
        refs = reloaded.storage_refs
        assert [r["uri"] for r in refs.get("normalized_refs", ())] == [r["uri"] for r in full["normalized_refs"]]
        assert [r["date"] for r in refs.get("raw_refs", ())] == [r["date"] for r in full["raw_refs"]]
        assert refs["feature_refs"][3]["feature_as_of_time"] == full["feature_refs"][3]["feature_as_of_time"]
        assert all(r["row_count"] == f["row_count"] for r, f in zip(refs.get("normalized_refs", ()), full["normalized_refs"]))
        assert refs.get("raw_refs")[0]["retention_days"] == 365
        assert new_scheduler.IngestReceipt.from_dict(reloaded.to_dict()).to_dict() == reloaded.to_dict()


def test_500_record_batch_storage_refs_are_bounded_too(tmp_path):
    receipt = _history_receipt(tmp_path / "data", "ingest-batch-500", 500)
    receipt = IngestReceipt.from_dict({**receipt.to_dict(), "evidence_refs": {"source_ids": [f"s{i}" for i in range(500)]}})
    path = tmp_path / "schedule.jsonl"
    JsonlIngestScheduleStore(path).upsert_receipt(receipt)
    assert max(len(line) for line in _stored_lines(path)) <= STORAGE_LINE_BOUND


def test_ref_that_does_not_fit_its_pattern_stays_inline_and_is_capped(tmp_path):
    refs = _history_storage_refs(tmp_path / "d", "r1", 3, write_files=False)
    refs["normalized_refs"][1]["uri"] = "/elsewhere/odd.jsonl"
    compact, changed = new_scheduler.compact_receipt_storage_refs(refs)
    assert changed and compact["ref_counts"]["normalized"] == 3
    assert [r["uri"] for r in compact["inline_refs"]["normalized"]] == ["/elsewhere/odd.jsonl"]
    many = _history_storage_refs(tmp_path / "d", "r2", 60, write_files=False)
    for ref in many["normalized_refs"]:
        ref["uri"] = f"/odd/{ref['date']}.jsonl"
    compact, _ = new_scheduler.compact_receipt_storage_refs(many)
    assert len(compact["inline_refs"]["normalized"]) == 32 and compact["unlisted_ref_count"] == 28


def test_existing_oversize_lines_are_compacted_by_the_in_service_rewrite_with_backup(tmp_path):
    previous = _previous_scheduler_module(PRE_STORAGE_REFS_SHA)
    path = tmp_path / "schedule.jsonl"
    writer = previous.JsonlIngestScheduleStore(path)
    originals = [_history_receipt(tmp_path / "data", f"ingest-hist-{i}", 300) for i in range(4)]
    for receipt in originals:
        writer.upsert_receipt(previous.IngestReceipt.from_dict(receipt.to_dict()))
    before = path.read_bytes()
    assert max(len(line) for line in _stored_lines(path)) > STORAGE_LINE_BOUND
    store = JsonlIngestScheduleStore(path)
    assert max(len(line) for line in _stored_lines(path)) <= STORAGE_LINE_BOUND
    assert store.journal_stats()["receipt_lines_over_budget"] == 0
    assert path.with_name(path.name + ".pre-migrate.bak").read_bytes() == before  # previous file kept
    for original in originals:
        loaded = store.get_receipt(original.ingest_run_id)
        assert [r["uri"] for r in loaded.storage_refs.get("normalized_refs", ())] == [
            r["uri"] for r in original.storage_refs["normalized_refs"]]
    # The previous release reads the rewritten journal, and the new code reads the previous release's journal.
    old_view = previous.JsonlIngestScheduleStore(path)
    assert set(old_view._receipts) == set(store._receipts)
    assert old_view._receipts["ingest-hist-0"].storage_refs["ref_counts"]["normalized"] == 300
    assert _state(old_view)["_runs"] == _state(store)["_runs"]


def test_reader_replay_reuse_gets_what_it_needs_from_a_compact_receipt(tmp_path):
    """services/source_ingestion/pipeline.py:1258 reads ref dataset/date/uri, and ref['row_count'] of reused refs."""
    store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    original = _history_receipt(tmp_path / "data", "ingest-reuse", 40)
    expected = {(r["dataset"], r["date"]): (r["uri"], r["row_count"]) for r in original.storage_refs["normalized_refs"]}
    store.upsert_receipt(original)
    receipt = next(r for r in store.read_freshness_snapshot()["receipts"] if r.ingest_run_id == "ingest-reuse")
    seen = {}
    for ref in receipt.storage_refs.get("normalized_refs", ()):  # the reader's exact loop
        seen[(str(ref.get("dataset") or ""), str(ref.get("date") or ""))] = (str(ref["uri"]), ref["row_count"])
    assert seen == expected
    by_uri = {r["uri"]: r for r in receipt.storage_refs.get("raw_refs", ())}
    assert sum(r["row_count"] for r in receipt.storage_refs.get("normalized_refs", ())) == sum(v[1] for v in expected.values())
    assert len(by_uri) == 40


def test_reader_controller_readback_gets_what_it_needs_from_a_compact_receipt(tmp_path):
    """services/source_ingestion/runtime.py:1340 filters by ref date and opens ref uri."""
    store = JsonlIngestScheduleStore(tmp_path / "schedule.jsonl")
    store.upsert_receipt(_history_receipt(tmp_path / "data", "ingest-readback", 30))
    receipt = store.get_receipt("ingest-readback")
    day = "2024-01-10"
    uris = [str(ref["uri"]) for ref in receipt.storage_refs.get("normalized_refs", ()) if ref.get("date") == day]
    assert len(uris) == 1 and Path(uris[0]).is_file() and Path(uris[0]).parent.name == f"date={day}"
    store.upsert_receipt(_history_receipt(tmp_path / "data", "ingest-gone", 30))
    for path in (tmp_path / "data" / "normalized" / "tw_index_daily").glob("date=2024-01-0*/ingest-gone.jsonl"):
        path.unlink()  # a removed partition is absent, not an error, and the other days still resolve
    gone = store.get_receipt("ingest-gone").storage_refs.get("normalized_refs", ())
    assert len(gone) == 30 - 9


# ---- previous release proof through scripts/previous_release_reader.py ---------------------------------

_JOURNAL_DRIVER = r"""
import json, sys
from services.source_ingestion.scheduler import JsonlIngestScheduleStore, IngestReceipt
path, mode, receipt_file = sys.argv[1], sys.argv[2], sys.argv[3]
store = JsonlIngestScheduleStore(path)
if mode == "write":
    store.upsert_receipt(IngestReceipt.from_dict(json.load(open(receipt_file))))
out = {"module_file": sys.modules[JsonlIngestScheduleStore.__module__].__file__,
       "max_line": store.journal_stats()["max_receipt_line_bytes"], "receipts": {}}
for receipt in store.list_receipts():
    out["receipts"][receipt.ingest_run_id] = {
        "normalized": len(receipt.storage_refs.get("normalized_refs", ())),
        "raw": len(receipt.storage_refs.get("raw_refs", ()))}
print(json.dumps(out))
"""


def _run_journal_driver(revision: str, tmp_path: Path, journal: Path, mode: str, receipt_file: Path) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import previous_release_reader as harness
    driver = tmp_path / "journal_driver.py"
    driver.write_text(_JOURNAL_DRIVER, encoding="utf-8")
    result = harness.run_driver(revision, driver, modules=["services.source_ingestion.scheduler"],
                                driver_args=[str(journal), mode, str(receipt_file)], check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_previous_release_reader_proof_both_directions_through_the_harness(tmp_path):
    receipt = _history_receipt(tmp_path / "data", "ingest-hist-driver", 120)
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(receipt.to_dict()))
    # New code (HEAD) writes; the release served on hosted when this task opened reads it.
    written_by_new = tmp_path / "new.jsonl"
    new = _run_journal_driver("HEAD", tmp_path, written_by_new, "write", receipt_file)
    assert new["max_line"] <= STORAGE_LINE_BOUND and new["receipts"]["ingest-hist-driver"]["normalized"] == 120
    old_reads = _run_journal_driver(PRE_STORAGE_REFS_SHA, tmp_path, written_by_new, "read", receipt_file)
    assert "ingest-hist-driver" in old_reads["receipts"]  # the previous release reads the compact row without error
    assert old_reads["receipts"]["ingest-hist-driver"] == {"normalized": 0, "raw": 0}  # difference, named: it has no lists to read
    # Previous release writes; new code reads and compacts.
    written_by_old = tmp_path / "old.jsonl"
    old = _run_journal_driver(PRE_STORAGE_REFS_SHA, tmp_path, written_by_old, "write", receipt_file)
    assert old["max_line"] > STORAGE_LINE_BOUND and old["receipts"]["ingest-hist-driver"]["normalized"] == 120
    new_reads = _run_journal_driver("HEAD", tmp_path, written_by_old, "read", receipt_file)
    assert new_reads["receipts"]["ingest-hist-driver"]["normalized"] == 120
    assert new_reads["max_line"] > 0 and written_by_old.stat().st_size < 100_000  # rewritten in place
