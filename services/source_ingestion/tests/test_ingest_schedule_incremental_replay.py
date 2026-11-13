"""Incremental replay using captured production receipts and real schedule rows."""

from __future__ import annotations

import json
import multiprocessing
import os
from dataclasses import replace
from pathlib import Path
from queue import Empty
from statistics import median
from time import perf_counter, process_time

import pytest

from services.source_ingestion.connectors.base import IngestRun, SourceEvidenceError
from services.source_ingestion.process_lock import exclusive_file_lock
from services.source_ingestion.scheduler import (
    CrawlFrontierItem,
    IngestReceipt,
    JsonlIngestScheduleStore,
    SourceWatermark,
)

STAMP = "2026-11-12T00:00:00Z"


@pytest.fixture(scope="module")
def captured():
    return json.loads((Path(__file__).parent / "fixtures/hosted_registry_20261112.json").read_bytes())


def _receipt(captured, index=0, *, large=False):
    row = captured["connectors"][-1 if large else -2]
    return replace(IngestReceipt.from_dict(row["freshness"]["latest_receipt"]),
                   ingest_run_id=f"ingest-incremental-{index:05d}")


def _run(receipt):
    run = IngestRun(ingest_run_id=receipt.ingest_run_id, connector_id=receipt.connector_id,
                    source_type="market", trigger_type=receipt.trigger_type,
                    trace_id=receipt.trace_id, started_at=receipt.started_at,
                    raw_count=receipt.raw_count, normalized_count=receipt.normalized_count,
                    rejected_count=receipt.rejected_count)
    for status in ("fetching", "normalizing", "indexing", "completed"):
        run.transition(status, message=f"Scheduled source records {status}")
    return run


def _row(record_type, record_id, payload, *, unicode=False):
    return (json.dumps({"schema_version": "ingest_schedule_store.v1", "record_type": record_type,
                        "record_id": record_id, "payload": payload}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=not unicode) + "\n").encode()


def _state(store):
    store.read_freshness_snapshot()
    return {name: {key: value.to_dict() for key, value in getattr(store, name).items()}
            for name in ("_runs", "_receipts", "_watermarks", "_frontier")}


def _spy_reads(monkeypatch, path):
    original = Path.open
    seen = {"seeks": [], "bytes": 0}

    class Reader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def seek(self, offset):
            seen["seeks"].append(offset)
            return self.handle.seek(offset)

        def readline(self):
            line = self.handle.readline()
            seen["bytes"] += len(line)
            return line

        def tell(self):
            return self.handle.tell()

    def open_file(self, mode="r", *args, **kwargs):
        handle = original(self, mode, *args, **kwargs)
        return Reader(handle) if self == path and mode == "rb" else handle

    monkeypatch.setattr(Path, "open", open_file)
    return seen


def test_interleaved_stores_consume_only_external_tail_and_match_full_replay(tmp_path, captured, monkeypatch):
    path = tmp_path / "schedule.jsonl"
    first, second = JsonlIngestScheduleStore(path), JsonlIngestScheduleStore(path)
    seen = _spy_reads(monkeypatch, path)
    for index in range(4):
        writer, reader = (first, second) if index % 2 == 0 else (second, first)
        receipt = _receipt(captured, index, large=index % 2 == 0)
        # Synchronize before measuring each independent writer's append.
        reader.read_freshness_snapshot()
        old_size = path.stat().st_size if path.exists() else 0
        writer.upsert_run(_run(receipt))
        writer.upsert_receipt(receipt)
        writer.update_watermark(SourceWatermark(receipt.connector_id, "market", str(index), STAMP,
                                                receipt.ingest_run_id))
        item = writer.enqueue_frontier(connector_id=receipt.connector_id,
                                       job_parameters={"dataset": "tw_price_daily", "symbol": "2330.TWSE"})
        writer.claim_frontier(item.frontier_id)
        writer.complete_frontier(item.frontier_id, ingest_run_id=receipt.ingest_run_id)
        seen.update(seeks=[], bytes=0)
        writer.read_freshness_snapshot()
        assert seen == {"seeks": [], "bytes": 0}  # Own writes already applied.
        reader.read_freshness_snapshot()
        assert seen == {"seeks": [old_size], "bytes": path.stat().st_size - old_size}
        assert _state(first) == _state(second) == _state(JsonlIngestScheduleStore(path))


@pytest.mark.parametrize("change", ["replace", "shrink", "rewrite", "delete"])
def test_changed_prefix_rebuilds_all_projections(tmp_path, captured, monkeypatch, change):
    path = tmp_path / "schedule.jsonl"
    store = JsonlIngestScheduleStore(path)
    receipt = _receipt(captured, large=True)
    store.upsert_run(_run(receipt))
    store.upsert_receipt(receipt)
    store.enqueue_frontier(connector_id=receipt.connector_id)
    watermark = SourceWatermark(receipt.connector_id, "market", "a", STAMP)
    store.update_watermark(watermark)
    original = path.read_bytes()
    replacement = _row("source_watermark", watermark.connector_id, replace(watermark, value="b").to_dict())
    if change == "replace":
        other = tmp_path / "replacement"
        other.write_bytes(replacement)
        old_inode = path.stat().st_ino
        other.replace(path)
        assert path.stat().st_ino != old_inode
    elif change == "shrink":
        path.write_bytes(replacement)
    elif change == "rewrite":
        path.write_bytes(original.replace(b'"value":"a"', b'"value":"b"'))
        stamp = store._read_key[2] + 1_000_000
        os.utime(path, ns=(stamp, stamp))
        assert path.stat().st_size == len(original)
    else:
        path.unlink()
    seen = _spy_reads(monkeypatch, path)
    actual = _state(store)
    assert seen["seeks"] == ([] if change == "delete" else [0])
    assert actual == _state(JsonlIngestScheduleStore(path))
    if change in {"replace", "shrink", "delete"}:
        assert not actual["_runs"] and not actual["_receipts"] and not actual["_frontier"]


@pytest.mark.parametrize("startup", [False, True])
def test_partial_unicode_receipt_is_deferred_until_newline(tmp_path, captured, startup):
    path = tmp_path / "schedule.jsonl"
    store = JsonlIngestScheduleStore(path)
    prior = _receipt(captured, 99)
    store.upsert_receipt(prior)
    prefix = path.read_bytes()
    receipt = _receipt(captured, large=True)
    refs = {**receipt.evidence_refs, "operator_note": "臺灣市場"}
    receipt = replace(receipt, evidence_refs=refs)
    raw = _row("ingest_receipt", receipt.ingest_run_id, receipt.to_dict(), unicode=True)
    cut = raw.index("臺".encode()) + 1
    with path.open("ab") as handle:
        handle.write(raw[:cut])
    if startup:
        store = JsonlIngestScheduleStore(path)
    assert store.get_receipt(receipt.ingest_run_id) is None
    assert store.get_receipt(prior.ingest_run_id).to_dict() == prior.to_dict()
    assert store._read_offset == len(prefix)
    before = path.read_bytes()
    with pytest.raises(SourceEvidenceError, match="Incomplete ingest schedule JSONL tail"):
        store.upsert_receipt(receipt)
    assert path.read_bytes() == before
    with path.open("ab") as handle:
        handle.write(raw[cut:-1])
    assert store.get_receipt(receipt.ingest_run_id) is None  # Valid JSON still needs its newline.
    with path.open("ab") as handle:
        handle.write(b"\n")
    assert store.get_receipt(receipt.ingest_run_id).to_dict() == receipt.to_dict()
    assert store._read_offset == len(prefix) + len(raw)


def test_invalid_complete_row_is_rejected_at_same_line_on_retry(tmp_path, captured):
    path = tmp_path / "schedule.jsonl"
    store = JsonlIngestScheduleStore(path)
    receipt = _receipt(captured)
    store.upsert_receipt(receipt)
    offset = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b"\n{broken}\n")
    for _ in range(2):
        with pytest.raises(SourceEvidenceError, match=r":3:"):
            store.read_freshness_snapshot()
        assert store._read_offset == offset + 1


def test_startup_recovery_is_durable_and_only_runs_on_explicit_reload(tmp_path, captured):
    path = tmp_path / "schedule.jsonl"
    reader, writer = JsonlIngestScheduleStore(path), JsonlIngestScheduleStore(path)
    receipt = replace(_receipt(captured, large=True), status="processing", finished_at=None)
    writer.upsert_run(_run(receipt))
    writer.upsert_receipt(receipt)
    assert reader.get_receipt(receipt.ingest_run_id).status == "processing"
    before = path.stat().st_size
    recovered = JsonlIngestScheduleStore(path)
    final = recovered.get_receipt(receipt.ingest_run_id)
    assert final.status == "failed"
    assert final.typed_failure["code"] == "post_processing_interrupted"
    assert final.evidence_refs == receipt.evidence_refs
    assert path.stat().st_size > before
    assert recovered._read_offset == path.stat().st_size
    size = path.stat().st_size
    assert _state(reader) == _state(writer) == _state(recovered)
    recovered.reload()
    assert path.stat().st_size == size  # Recovery is not appended twice.


def _claim_in_process(path, ready, go, output):
    store = JsonlIngestScheduleStore(path)
    ready.set()
    if not go.wait(10):
        raise TimeoutError("claim process was not released")
    output.put([item.frontier_id for item in store.claim_due_frontier(limit=1)])


def test_existing_file_lock_fences_independent_process_claims(tmp_path, captured):
    path = tmp_path / "schedule.jsonl"
    store = JsonlIngestScheduleStore(path)
    item = store.enqueue_frontier(connector_id=_receipt(captured).connector_id,
                                  job_parameters={"dataset": "futures_options_chip"})
    ctx = multiprocessing.get_context("spawn")
    ready, go, output = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=_claim_in_process, args=(path, ready, go, output))
    process.start()
    try:
        assert ready.wait(10)
        with exclusive_file_lock(store._lock_path, store._lock):
            go.set()
            with pytest.raises(Empty):
                output.get(timeout=0.2)
        process.join(10)
        assert process.exitcode == 0
        assert output.get(timeout=2) == [item.frontier_id]
        assert store.claim_due_frontier(limit=1) == []
        assert store.get_frontier(item.frontier_id).attempts == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        output.close()


def build_real_shape_journal(path, captured):
    """14,234 rows from the brief's per-type counts (which sum to 14,234).

    Five 96-snapshot Taiwan official receipts and 3,197 ~10KB TAIFEX
    receipts reuse the complete captured payloads, including calendar/lineage.
    No padding blobs or invented large rows are used.
    """
    with path.open("wb") as handle:
        for index in range(1580):
            receipt = _receipt(captured, index)
            run = _run(receipt)
            for event_count in (2, 4, 5):
                payload = run.to_dict()
                payload["events"] = payload["events"][:event_count]
                payload["status"] = payload["events"][-1]["status"]
                handle.write(_row("ingest_run", run.ingest_run_id, payload))
        for index in range(3202):
            receipt = _receipt(captured, index, large=index < 5)
            handle.write(_row("ingest_receipt", receipt.ingest_run_id, receipt.to_dict()))
        for index in range(1585):
            item = CrawlFrontierItem(frontier_id=f"frontier-{index:012d}",
                                      connector_id=_receipt(captured).connector_id,
                                      job_parameters={"dataset": "futures_options_chip", "venue": "TAIFEX"},
                                      trace_id=f"trace-incremental-{index:05d}",
                                      created_at=STAMP, updated_at=STAMP, available_at=STAMP)
            for status in ("queued", "running", "done"):
                current = replace(item, status=status, attempts=int(status != "queued"))
                handle.write(_row("crawl_frontier_item", item.frontier_id, current.to_dict()))
        for index in range(1537):
            watermark = SourceWatermark(f"connector-{index % 22:02d}", "market", STAMP, STAMP,
                                        f"ingest-incremental-{index:05d}")
            handle.write(_row("source_watermark", watermark.connector_id, watermark.to_dict()))


def measure_registry(runtime, client, writer, captured, *, samples=5, interval=0):
    """Same endpoint, external append before each sample, wall and CPU time."""
    from time import sleep

    elapsed, cpu, write_elapsed, write_cpu = [], [], [], []
    for index in range(samples):
        if interval:
            sleep(interval)
        start, cpu_start = perf_counter(), process_time()
        writer.upsert_receipt(_receipt(captured, 4000 + index, large=True))
        write_elapsed.append((perf_counter() - start) * 1000)
        write_cpu.append((process_time() - cpu_start) * 1000)
        start, cpu_start = perf_counter(), process_time()
        response = client.get("/api/source-ingest/registry")
        elapsed.append((perf_counter() - start) * 1000)
        cpu.append((process_time() - cpu_start) * 1000)
        assert response.status_code == 200, response.text
        assert len(response.json()["connectors"]) == 22
        assert runtime.store.get_receipt(f"ingest-incremental-{4000 + index:05d}") is not None
    return {"registry_wall_ms": elapsed, "registry_cpu_ms": cpu,
            "registry_p50_ms": median(elapsed), "registry_cpu_p50_ms": median(cpu),
            "write_wall_ms": write_elapsed, "write_cpu_ms": write_cpu,
            "write_p50_ms": median(write_elapsed), "write_cpu_p50_ms": median(write_cpu)}


def registry_on_journal(root, captured, store_class=JsonlIngestScheduleStore):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.source_ingestion.connectors.base import SourceConnector
    from services.source_ingestion.routers.catalog_controller import create_catalog_controller_router
    from services.source_ingestion.runtime import SourceIngestionRuntime

    root.mkdir(parents=True, exist_ok=True)
    runtime = SourceIngestionRuntime(data_dir=root)
    for index in range(22):
        row = captured["connectors"][index % len(captured["connectors"])]
        policy = row["policy"]
        connector_id = row["connector_id"] if index < len(captured["connectors"]) else f"connector-{index:02d}"
        connector = SourceConnector.from_dict({**row, "connector_id": connector_id,
            "license_scope": policy["license"]["license_scope"], "auth_policy": policy["auth"],
            "rate_limit_policy": policy["rate_limit"], "license_policy": policy["license"],
            "source_metadata": policy["source_metadata"]})
        runtime.manager.register_connector(connector)
    build_real_shape_journal(runtime.SCHEDULE_STORE_PATH, captured)
    runtime.store = store_class(runtime.SCHEDULE_STORE_PATH)
    app = FastAPI()
    app.include_router(create_catalog_controller_router(runtime))
    return runtime, TestClient(app), store_class(runtime.SCHEDULE_STORE_PATH)


def test_real_shape_registry_read_after_appends_is_under_300ms(tmp_path, captured, monkeypatch):
    runtime, client, writer = registry_on_journal(tmp_path, captured)
    size = runtime.SCHEDULE_STORE_PATH.stat().st_size
    assert 44_000_000 < size < 50_000_000
    seen = _spy_reads(monkeypatch, runtime.SCHEDULE_STORE_PATH)
    with client:
        metrics = measure_registry(runtime, client, writer, captured)
    assert metrics["registry_p50_ms"] < 300
    assert seen["bytes"] < 4_000_000  # Five large receipts; never the old prefix.
    assert len(seen["seeks"]) == 5  # Only the reader needs to catch up writer appends.
    seen.update(seeks=[], bytes=0)
    for _ in range(5):
        assert writer.claim_due_frontier(limit=1) == []
    assert seen == {"seeks": [], "bytes": 0}
    print("INCREMENTAL_REGISTRY=" + json.dumps({"journal_bytes": size, **metrics}))
