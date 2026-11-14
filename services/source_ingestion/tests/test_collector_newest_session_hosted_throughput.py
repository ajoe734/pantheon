"""Hosted-size regression: real controller/persistence, modelled remote transport."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import types
from dataclasses import replace

import pytest

from services.source_ingestion import controller_worker as cw
from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.tests import test_controller_worker as tcw
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport
from services.source_ingestion.tests.test_collector_newest_session_progress import DAY, TWSE, TPEX, recorded_run
from services.source_ingestion.tests.test_scheduled_connector import admission_stack, client  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_cache():
    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()
    yield
    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()


def make_adapter(root, transport, source=TWSE, cap=2):
    return cb.CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market",
        dataset="tw_price_daily", cadence_seconds=600, max_records=cap, raw_root=str(root), transport=transport)


def test_verified_cache_survives_adapter_rebuild_and_raw_is_still_checked(tmp_path):
    files, entry = recorded_run()
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    transport = FakeTransport(files)
    a = make_adapter(tmp_path, transport)
    first = a.pull(); a.save_resume_cursor(first)
    metadata = [p for p in files if p.endswith(("manifest.json", "items.jsonl"))]
    for path in metadata:
        del files[path]  # continuation must not touch either immutable metadata file
    a = make_adapter(tmp_path, transport)
    second = a.pull(); a.save_resume_cursor(second)
    assert len(second.records) == 2 and second.priority_delivered == 4
    cached = next(iter(cb._METADATA_CACHE.values()))[1]
    item = cached.manifest_items[str(cached.items[4]["id"])]
    base = f"bundles/{entry['source_id']}/{entry['date']}/{entry['run_id']}"
    files[f"{base}/{item['raw_path']}"] = b"tampered raw"
    before = a._priority_key_path().read_bytes()
    with pytest.raises(cb.SourceEvidenceError, match="raw hash mismatch"):
        a.pull()
    assert a._priority_key_path().read_bytes() == before


@pytest.mark.parametrize("change", ["missing_digest", "changed_digest", "missing_entry"])
def test_cache_miss_requires_full_verification(tmp_path, change):
    files, entry = recorded_run()
    a = make_adapter(tmp_path, FakeTransport(files))
    a._verified_metadata(a.transport, entry)
    if change == "missing_digest":
        entry.pop("bundle_sha256")
    elif change == "changed_digest":
        entry["bundle_sha256"] = "0" * 64
    else:
        cb._METADATA_CACHE.clear()
    base = f"bundles/{entry['source_id']}/{entry['date']}/{entry['run_id']}"
    files[base + "/items.jsonl"] = b'{"id":"wrong"}'
    with pytest.raises(cb.SourceEvidenceError, match="mismatch|do not match"):
        a._verified_metadata(a.transport, entry)


def test_unverified_metadata_never_enters_cache_and_changed_run_evicts(tmp_path):
    files, entry = recorded_run()
    a = make_adapter(tmp_path, FakeTransport(files))
    base = f"bundles/{entry['source_id']}/{entry['date']}/{entry['run_id']}"
    original = files[base + "/items.jsonl"]
    files[base + "/items.jsonl"] = b'{"id":"wrong"}'
    with pytest.raises(cb.SourceEvidenceError):
        a._verified_metadata(a.transport, entry)
    assert not cb._METADATA_CACHE
    files[base + "/items.jsonl"] = original
    a._verified_metadata(a.transport, entry)
    changed, correction = recorded_run(correction=True)
    files.update(changed)
    a._verified_metadata(a.transport, correction)
    assert len(cb._METADATA_CACHE) == 1
    assert next(iter(cb._METADATA_CACHE.values()))[1].manifest["bundle_sha256"] == correction["bundle_sha256"]


def test_cache_is_bounded_and_superseded_sessions_evict(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "VERIFIED_METADATA_CACHE_ENTRIES", 2)
    files, entries = {}, []
    transport = FakeTransport(files)
    a = make_adapter(tmp_path, transport)
    for day in ("2026-11-11", "2026-11-12", DAY):
        more, entry = recorded_run(day=day, fetched=day + "T12:18:42Z")
        files.update(more); entries.append(entry)
        a._verified_metadata(transport, entry)
        assert len(cb._METADATA_CACHE) <= 2
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    result = a.pull()
    assert result.priority_trade_date == DAY
    assert all(cb._bundle_trade_date({}, value[1].items) == DAY for value in cb._METADATA_CACHE.values())
    cb._METADATA_CACHE.clear()
    monkeypatch.setattr(cb, "VERIFIED_METADATA_CACHE_BYTES", 1)
    a._verified_metadata(transport, entries[-1])
    assert not cb._METADATA_CACHE


def test_fresh_index_discovers_new_run_during_cached_continuation(tmp_path):
    files, entry = recorded_run(fetched="2026-11-13T18:18:44Z")
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    transport = FakeTransport(files)
    a = make_adapter(tmp_path, transport)
    first = a.pull(); a.save_resume_cursor(first)
    correction, newer = recorded_run(correction=True)
    files.update(correction)
    files["bundles/index.json"] = json.dumps({"bundles": [entry, newer]}).encode()
    result = make_adapter(tmp_path, transport).pull()
    assert result.priority_key.endswith(newer["run_id"])
    assert result.records[0].metadata["symbol"] == first.records[0].metadata["symbol"]
    assert result.records[0].metadata["content_hash"] != first.records[0].metadata["content_hash"]
    assert result.priority_delivered == 3  # corrected identity plus one fresh item


def test_http_cache_reuses_rebuilt_transport_and_isolates_authorization(tmp_path, monkeypatch):
    files, entry = recorded_run()
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "unit-test-token-one")
    reads = []
    def read(self, path, **kwargs):
        reads.append(path)
        return files[path]
    monkeypatch.setattr(cb.HttpBundleTransport, "read", read)
    for _ in range(2):
        transport = cb.HttpBundleTransport(host="unit.test")
        a = make_adapter(tmp_path, transport)
        a._verified_metadata(transport, entry)
    assert len(reads) == 2
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "unit-test-token-two")
    a._verified_metadata(transport, entry)
    assert len(reads) == 4


@pytest.mark.parametrize("legacy", ["bee62bc29", "0940f6248"])
def test_rollback_readers_still_parse_state(tmp_path, legacy):
    from services.source_ingestion.tests.test_collector_newest_session_progress import (
        test_real_rollback_readers_parse_additive_ack_state,
    )
    test_real_rollback_readers_parse_additive_ack_state(tmp_path, legacy)


def hosted_run(source, count, **kwargs):
    """Valid native records with the supplied remote encoded metadata sizes."""
    files, entry = recorded_run(source, count, **kwargs)
    base = f"bundles/{source}/{entry['date']}/{entry['run_id']}"
    sizes = (570_000, 1_240_000) if source == TWSE else (2_500_000, 5_700_000)
    for filename, target in zip(("manifest.json", "items.jsonl"), sizes):
        path = base + "/" + filename
        # JSON whitespace preserves the complete native data and exercises the
        # actual byte reader/parser; no reduced-size metadata fixture is used.
        assert len(files[path]) <= target, (path, len(files[path]), target)
        files[path] += b" " * (target - len(files[path]))
    return files, entry


def hosted_index(entries):
    entries = list(entries)
    while len(entries) < 1721:
        entries.append({"source_id": "other-source", "date": "2024-01-01", "run_id": f"old-{len(entries)}",
                        "finished_at": "2024-01-01T12:00:00Z", "bundle_sha256": "a" * 64})
    data = json.dumps({"bundles": entries}).encode()
    assert len(data) <= 504_000
    return data + b" " * (504_000 - len(data))


class HostedClock:
    """Charge real elapsed CPU/disk plus overlapping virtual remote latency."""
    def __init__(self, transport):
        self.transport = transport
        self.offset = 0.0
        self.started = time.perf_counter()
        self.lock = threading.Lock()
        self.steps = []

    def now(self):
        with self.lock:
            return self.offset + time.perf_counter() - self.started

    def advance(self, seconds):
        with self.lock:
            self.offset += seconds

    def read(self, path, *, max_bytes):
        start = self.now()
        data = self.transport(path, max_bytes=max_bytes)
        assert len(data) <= max_bytes
        # Give the real bounded reader pool a chance to overlap connection starts.
        if "/raw/" in path:
            time.sleep(0.002)
        latency = (0.19 if "/raw/" in path else 0.14) + len(data) / 3_000_000
        with self.lock:
            self.offset = max(self.offset, start + latency - (time.perf_counter() - self.started))
            self.steps.append({"path": path, "bytes": len(data), "remote_seconds": latency})
        return data


@pytest.mark.parametrize("source,count", [(TWSE, 1377), (TPEX, 6090)])
def test_recorded_candidate_repeats_full_metadata_downloads(tmp_path, source, count):
    old = subprocess.run(["git", "show", "0940f6248:services/source_ingestion/connectors/collector_bundle.py"],
                         capture_output=True, text=True, timeout=10, check=True)
    path = tmp_path / "candidate.py"; path.write_text(old.stdout)
    name = "services.source_ingestion.connectors.hosted_candidate"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    files, entries = {}, []
    for fetched in ("2026-11-13T12:18:42Z", "2026-11-13T18:18:44Z", "2026-11-14T00:19:33Z", "2026-11-14T06:19:33Z"):
        more, entry = hosted_run(source, count, fetched=fetched)
        files.update(more); entries.append(entry)
    files["bundles/index.json"] = hosted_index(entries)
    clock = HostedClock(FakeTransport(files).read)
    module.time = types.SimpleNamespace(monotonic=clock.now)
    transport = FakeTransport(files); transport.read = clock.read
    a = module.CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market",
        dataset="tw_price_daily", cadence_seconds=600, max_records=1000, raw_root=str(tmp_path), transport=transport)
    pulls = []
    try:
        for _ in range(2):
            start, step = clock.now(), len(clock.steps)
            result = a.pull(); a.save_resume_cursor(result)
            reads = clock.steps[step:]
            metadata = [r for r in reads if r["path"].endswith(("manifest.json", "items.jsonl"))]
            assert len(metadata) == 8
            pulls.append({"rows": len(result.records), "seconds": clock.now() - start,
                          "metadata_reads": metadata})
        print("HOSTED_BASELINE " + json.dumps({"source": source, "pulls": pulls}))
    finally:
        sys.modules.pop(name, None)


def test_full_hosted_size_sessions_use_real_controller_ticks(admission_stack, monkeypatch):
    http, module, reads, wanted, files, transport, _tick = admission_stack
    entries = [e for e in json.loads(files["bundles/index.json"])["bundles"] if e["source_id"] not in {TWSE, TPEX}]
    for source, count in ((TWSE, 1377), (TPEX, 6090)):
        for fetched in ("2026-11-13T12:18:42Z", "2026-11-13T18:18:44Z", "2026-11-14T00:19:33Z", "2026-11-14T06:19:33Z"):
            more, entry = hosted_run(source, count, fetched=fetched)
            files.update(more); entries.append(entry)
    files["bundles/index.json"] = hosted_index(entries)
    enabled = {TWSE, TPEX} | set(sorted(set(wanted) - {TWSE, TPEX})[:11])
    files["status.json"] = json.dumps({"sources": {s: {"status": "ok", "offered": s in enabled,
        "last_run_at": "2026-11-14T06:19:33Z", "cadence_seconds": 21600} for s in wanted}}).encode()
    config = replace(tcw._config(module.runtime.CONTROLLER_STATE_PATH.parent, truth_level="scheduled_tick",
                                mode=cw.COLLECTOR_STEADY_PULL_MODE),
                     state_path=module.runtime.CONTROLLER_STATE_PATH, max_ticks=0, max_concurrency=1,
                     interval_seconds=60, timeout_seconds=30)
    state = tcw._state()
    store, writer = cw.ControllerStateStore(config.state_path), tcw.RecordingWriter([])
    # Drive actual controller ticks with the production loop's 60s idle/1s
    # continuation sleep decisions. Charge real CPU/disk wall time on two cores.
    clock = HostedClock(transport.read)
    monkeypatch.setattr(transport, "read", clock.read)
    monkeypatch.setattr(cb, "time", types.SimpleNamespace(monotonic=clock.now))
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "11376.TWSE,16089.TPEX")
    affinity = os.sched_getaffinity(0)
    cores = set(sorted(affinity)[:2])
    os.sched_setaffinity(0, cores)
    pulls, last_start, counts = [], {}, {}
    elapsed_phase = 600.0
    start = clock.now()
    try:
        for n in range(80):
            tick_start, step = clock.now(), len(clock.steps)
            result = cw.run_controller_tick(config=config, state=state, store=store, writer=writer)
            seconds = clock.now() - tick_start
            assert result["status"] == "ok" and seconds < config.timeout_seconds, seconds
            receipt = module.runtime.store.read_freshness_snapshot()["receipts"][-1]
            source = receipt.connector_id
            assert source in {TWSE, TPEX}, "unrelated backlog must not displace a pending newest close"
            gap = tick_start - last_start[source] if source in last_start else None
            assert gap is None or gap <= 90, gap
            last_start[source] = tick_start
            steps = clock.steps[step:]
            metadata = [r for r in steps if r["path"].endswith(("manifest.json", "items.jsonl"))]
            # After the source's cold pull, immutable metadata stays off the wire.
            if any(p["source"] == source for p in pulls):
                # The last pull can additionally walk the ordered cursor boundary.
                assert metadata == []
            pulls.append({"source": source, "rows": receipt.normalized_count, "seconds": seconds,
                          "start_seconds": tick_start - start, "same_source_gap_seconds": gap,
                          "metadata_remote_seconds": sum(r["remote_seconds"] for r in metadata)})
            snapshots = module.runtime.latest_market_snapshot_store.reload()
            counts = {s: sum(snap.points[-1].connector_id == s and snap.points[-1].event_time[:10] == DAY
                             for snap in snapshots.values()) for s in (TWSE, TPEX)}
            if counts == {TWSE: 1377, TPEX: 6090}:
                break
            assert result["continuation_pending"] is True
            clock.advance(min(config.interval_seconds, cw.CONTINUATION_PAUSE_SECONDS))
        assert counts == {TWSE: 1377, TPEX: 6090}
        elapsed = elapsed_phase + clock.now() - start
        assert elapsed < 1800, elapsed
        assert len(cb._METADATA_CACHE) <= cb.VERIFIED_METADATA_CACHE_ENTRIES
        print("HOSTED_THROUGHPUT " + json.dumps({"cpu_affinity": sorted(cores), "index_bytes": 504000,
              "index_entries": 1721, "fresh_connection_seconds": [0.14, 0.19], "transfer_bytes_per_second": 3000000,
              "metadata_sizes": {TWSE: [570000, 1240000], TPEX: [2500000, 5700000]}, "pulls": pulls,
              "counts": counts, "elapsed_with_worst_cadence_phase_seconds": elapsed}))
    finally:
        os.sched_setaffinity(0, affinity)
