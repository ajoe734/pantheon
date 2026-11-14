"""Recorded run-* shapes, session continuity and real controller/persistence timing."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timedelta

import pytest

from services.source_ingestion import controller_worker as cw
from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport, _intake_bundle
from services.source_ingestion.tests.test_scheduled_connector import client, admission_stack  # noqa: F401

TWSE = "collector-tw-twse-price-history"
TPEX = "collector-tw-tpex-price-history"
DAY = "2026-11-13"


def recorded_run(source=TWSE, count=8, *, fetched="2026-11-14T00:19:33Z", day=DAY, correction=False):
    """Use the collector's actual index/manifest fields; run directory date is fetch date."""
    files, entry = _intake_bundle(source, count, "tw_price_daily", "TPEX" if source == TPEX else "TWSE", day)
    old_base = f"bundles/{source}/{entry['date']}/{entry['run_id']}"
    run_id = "run-" + fetched.replace("-", "").replace(":", "")
    entry.update(date=fetched[:10], run_id=run_id, finished_at=fetched, status="complete")
    base = f"bundles/{source}/{entry['date']}/{run_id}"
    files = {k.replace(old_base, base): v for k, v in files.items()}
    manifest = json.loads(files[base + "/manifest.json"])
    manifest.update(run_id=run_id, started_at=fetched, finished_at=fetched)
    rows = [json.loads(line) for line in files[base + "/items.jsonl"].splitlines()]
    for row in rows:
        row["fetched_at"] = fetched
    if correction:
        item = manifest["items"][0]
        body = json.loads(files[base + "/" + item["raw_path"]])
        body["record"]["close"] += 1
        raw = json.dumps(body, sort_keys=True).encode()
        item.update(content_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        item["raw_path"] = f"raw/{item['content_sha256']}.json"
        files[base + "/" + item["raw_path"]] = raw
        rows[0]["close"] += 1
    manifest["bundle_sha256"] = cb.compute_bundle_sha256([m["content_sha256"] for m in manifest["items"]])
    entry["bundle_sha256"] = manifest["bundle_sha256"]
    files[base + "/manifest.json"] = json.dumps(manifest).encode()
    files[base + "/items.jsonl"] = b"\n".join(json.dumps(r).encode() for r in rows)
    return files, entry


def adapter(tmp_path, files, entries, cap=3):
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {TWSE: {
        "status": "ok", "last_run_at": entries[-1]["finished_at"], "cadence_seconds": 21600}}}).encode()
    return cb.CollectorBundleAdapter(connector_id=TWSE, source_id=TWSE, source_type="market", dataset="tw_price_daily",
        cadence_seconds=600, max_records=cap, transport=FakeTransport(files), raw_root=str(tmp_path), attribution_required=True)


def test_recorded_weekend_and_preclose_runs_use_rows_and_same_date_never_resets(tmp_path):
    files, first = recorded_run(fetched="2026-11-13T12:18:42Z")
    a = adapter(tmp_path, files, [first])
    pull = a.pull(); a.save_resume_cursor(pull)
    seen = {r.source_id for r in pull.records}
    for fetched in ("2026-11-13T18:18:44Z", "2026-11-14T00:19:33Z"):
        new_files, entry = recorded_run(fetched=fetched)
        assert entry["bundle_sha256"] == first["bundle_sha256"]
        files.update(new_files)
        a = adapter(tmp_path, files, [first, entry])
        pull = a.pull(cursor=pull.next_cursor); a.save_resume_cursor(pull)
        assert not seen.intersection(r.source_id for r in pull.records)
        seen.update(r.source_id for r in pull.records)
        assert pull.priority_trade_date == DAY
    assert len(seen) == 8 and pull.priority_done and pull.priority_delivered == 8
    # Recorded pre-close 06:17Z run carries yesterday's close, regardless of fetch date.
    older, entry = recorded_run(fetched="2026-11-13T06:17:00Z", day="2026-11-12")
    p = adapter(tmp_path / "preclose", older, [entry]).pull()
    assert p.priority_trade_date == "2026-11-12"


@pytest.mark.parametrize("fresh", [True, False], ids=["healthy", "stale"])
def test_completed_same_session_new_run_has_no_raw_resend(tmp_path, monkeypatch, fresh):
    files, first = recorded_run(count=2, fetched="2026-11-13T12:18:42Z")
    # Inherit the CLOCK per-run observation fixture; the second pull also
    # exercises the real twice-cadence stale guard, independent of wall time.
    observed_at = datetime.fromisoformat(first["finished_at"].replace("Z", "+00:00")) + timedelta(minutes=1)
    real_observed = cb.observed_from_collector_status
    monkeypatch.setattr(cb, "observed_from_collector_status", lambda *args, **kwargs: real_observed(
        *args, **{**kwargs, "now": observed_at}))
    a = adapter(tmp_path, files, [first]); pull = a.pull(); a.save_resume_cursor(pull)
    more, entry = recorded_run(count=2); files.update(more)
    observed_at = datetime.fromisoformat(entry["finished_at"].replace("Z", "+00:00")) + (
        timedelta(minutes=1) if fresh else timedelta(hours=13))
    a = adapter(tmp_path, files, [first, entry])
    reads = []
    original = a.transport.read
    a.transport.read = lambda path, **kw: (reads.append(path), original(path, **kw))[1]
    pull = a.pull(cursor=pull.next_cursor)
    if fresh:
        assert pull.records == () and pull.priority_done and pull.no_new_data
        assert pull.observed["health_state"] == "healthy"
        assert pull.observed["reasons"] == ()
    else:
        assert pull.records == () and pull.priority_done and not pull.no_new_data
        assert pull.observed["health_state"] == "degraded"
        assert "newest_bundle_older_than_twice_cadence" in pull.observed["reasons"]
    assert pull.observed["freshness"]["collector_last_run_at"] == "2026-11-14T00:19:33Z"
    assert pull.observed["freshness"]["cadence_seconds"] == 21600
    assert not any("/raw/" in path for path in reads)
    a.save_resume_cursor(pull)
    assert json.loads(a._priority_key_path().read_text())["key"].endswith(entry["run_id"])


def test_corrected_delivered_item_replays_with_new_hash_and_held_change_keeps_progress(tmp_path):
    files, first = recorded_run()
    a = adapter(tmp_path, files, [first]); one = a.pull(); a.save_resume_cursor(one)
    changed, entry = recorded_run(fetched="2026-11-14T06:19:33Z", correction=True)
    files.update(changed)
    a = adapter(tmp_path, files, [first, entry])
    two = a.pull(priority_symbols=("10007.TWSE",)); a.save_resume_cursor(two)
    assert [r.metadata["symbol"] for r in two.records] == ["10007", "10000", "10003"]
    assert two.records[1].source_id != one.records[0].source_id
    assert two.records[1].metadata["event_time"] == one.records[0].metadata["event_time"]
    assert two.priority_delivered == 5  # correction does not double-count the item


@pytest.mark.parametrize("legacy", ["bee62bc29", "a9284b68c"])
def test_real_rollback_readers_parse_additive_ack_state(tmp_path, legacy):
    files, entry = recorded_run()
    a = adapter(tmp_path, files, [entry]); pull = a.pull(); a.save_resume_cursor(pull)
    document = json.loads(a._priority_key_path().read_text())
    assert document["trade_date"] == DAY and len(document["items"]) == 3
    old = subprocess.run(["git", "show", f"{legacy}:services/source_ingestion/connectors/collector_bundle.py"],
                         capture_output=True, text=True, timeout=10)
    assert old.returncode == 0, "rollback object is required for this acceptance"
    path = tmp_path / "rollback.py"; path.write_text(old.stdout)
    name = "services.source_ingestion.connectors.rollback_session"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        reader = module.CollectorBundleAdapter(connector_id=TWSE, source_id=TWSE, raw_root=str(tmp_path))
        if hasattr(reader, "_load_priority_state"):
            assert reader._load_priority_state() == (pull.priority_key, 3, False, ())
        else:
            # bee62bc29 predates this sidecar: its actual cursor reader still works.
            assert reader.load_resume_cursor() == a.load_resume_cursor()
    finally:
        sys.modules.pop(name, None)


def test_old_prefix_migrates_against_original_manifest_before_same_day_correction(tmp_path):
    files, first = recorded_run(fetched="2026-11-13T12:18:42Z")
    a = adapter(tmp_path, files, [first]); pull = a.pull(); a.save_resume_cursor(pull)
    state = json.loads(a._priority_key_path().read_text())
    for key in ("trade_date", "items"):
        state.pop(key)
    a._priority_key_path().write_text(json.dumps(state))
    more, entry = recorded_run(correction=True); files.update(more)
    pull = adapter(tmp_path, files, [first, entry]).pull()
    assert [r.metadata["symbol"] for r in pull.records] == ["10000", "10003", "10004"]


def test_hosted_size_sessions_through_controller_and_real_persistence(admission_stack, monkeypatch):
    http, module, reads, wanted, files, transport, tick = admission_stack
    entries = json.loads(files["bundles/index.json"])["bundles"]
    entries = [e for e in entries if e["source_id"] not in {TWSE, TPEX}]
    for source, count in ((TWSE, 1377), (TPEX, 6090)):
        added, entry = recorded_run(source, count)
        files.update(added); entries.append(entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    # The real default declaration includes held/unoffered sources too. Model the
    # deployed capability set as 13 enabled sources with both price histories.
    enabled = {TWSE, TPEX} | set(sorted(set(wanted) - {TWSE, TPEX})[:11])
    files["status.json"] = json.dumps({"sources": {s: {"status": "ok", "offered": s in enabled,
        "last_run_at": "2026-11-14T00:19:33Z", "cadence_seconds": 21600} for s in wanted}}).encode()
    # Reproduce the supplied hosted journal cardinality/size with valid local
    # historical fetch-state envelopes. This is synthetic data, not a VM copy.
    tick(cw.RECONCILE_ONLY_MODE)
    journal = module.runtime.connector_store.path
    content = journal.read_text()
    n = 1980 - len(content.splitlines())
    event = {"schema_version": "source_connector_config_store.v1", "record_type": "connector_fetch_state",
             "record_id": "collector-news-rss", "payload": {"connector_id": "collector-news-rss",
             "attempts": 0, "successful_attempts": 0, "failed_attempts": 0, "last_error": None,
             "collector_outcome": {"complete": False, "observed": {}, "paused_reasons": []}}}
    line = json.dumps(event, separators=(",", ":"))
    padding = max((1445430 - len(content.encode())) // n - len(line.encode()) - 3, 0)
    event["payload"]["collector_outcome"]["paused_reasons"] = ["x" * padding]
    journal.write_text(content + (json.dumps(event, separators=(",", ":")) + "\n") * n)
    journal_metadata = {"lines": len(journal.read_text().splitlines()), "bytes": journal.stat().st_size}
    assert journal_metadata["lines"] == 1980 and journal_metadata["bytes"] >= 1400000
    clock = [0.0]
    lock = threading.Lock()
    original_read = transport.read

    def read(path, *, max_bytes):
        with lock:
            start = clock[0]
        time.sleep(0.002 if "/raw/" in path else 0)
        try:
            return original_read(path, max_bytes=max_bytes)
        finally:
            with lock:
                # Measured hosted fresh-connection range, worst end for raw.
                clock[0] = max(clock[0], start + (0.19 if "/raw/" in path else 0.14))

    monkeypatch.setattr(transport, "read", read)
    monkeypatch.setattr(cb, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    original_dedupe = module.runtime.evidence_repository.get_source_record_by_dedupe_key
    def dedupe(key, **kwargs):
        assert not key.startswith("collector-close:"), "native per-item ids must not scan the entire evidence table"
        return original_dedupe(key, **kwargs)
    monkeypatch.setattr(module.runtime.evidence_repository, "get_source_record_by_dedupe_key", dedupe)
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "11376.TWSE,16089.TPEX")
    measurements = []
    elapsed = 600.0  # worst phase: bundle appears just after the deployed ten-minute cadence
    for _ in range(120):
        before, wall = clock[0], time.perf_counter()
        result = tick(cw.COLLECTOR_STEADY_PULL_MODE)
        wall = time.perf_counter() - wall
        model = clock[0] - before
        assert result["status"] == "ok" and result["schedule_summary"]["total_ran"] == 1
        # Charge transport latency AND all actual CPU/disk/persistence/readback time.
        assert model + wall < 30, (model, wall)
        elapsed += model + wall + cw.CONTINUATION_PAUSE_SECONDS
        state = module.runtime.store.read_freshness_snapshot()
        receipt = state["receipts"][-1]
        measurements.append({"source": receipt.connector_id, "rows": receipt.normalized_count,
                             "wall_seconds": wall, "transport_seconds": model, "tick_seconds_upper_bound": model + wall})
        snapshots = module.runtime.latest_market_snapshot_store.reload()
        if len(measurements) == 1:
            assert module.runtime.latest_market_snapshot_store.get("16089.TPEX").points[-1].event_time[:10] == DAY
        counts = {source: sum(snap.points[-1].connector_id == source and snap.points[-1].event_time[:10] == DAY
                             for snap in snapshots.values()) for source in (TWSE, TPEX)}
        if counts == {TWSE: 1377, TPEX: 6090}:
            break
        assert receipt.connector_id in {TWSE, TPEX}, "backlog must wait for the newest sessions"
    assert counts == {TWSE: 1377, TPEX: 6090}
    assert elapsed < 1800
    # Once priority is drained, least-recently-attempted non-market sources advance.
    result = tick(cw.COLLECTOR_STEADY_PULL_MODE)
    assert result["status"] == "ok"
    assert module.runtime.store.read_freshness_snapshot()["receipts"][-1].connector_id not in {TWSE, TPEX}
    print("NEWEST_SESSION_TIMING " + json.dumps({"enabled_connectors": sorted(enabled), "pulls": measurements,
        "elapsed_seconds_upper_bound_including_worst_cadence_phase": elapsed, "snapshot_counts": counts,
        "initial_config_journal": journal_metadata}))


@pytest.mark.parametrize("source,count", [(TWSE, 1377), (TPEX, 6090)])
def test_exact_candidate_restarts_same_session_on_recorded_run_keys(tmp_path, monkeypatch, source, count):
    old = subprocess.run(["git", "show", "a9284b68c:services/source_ingestion/connectors/collector_bundle.py"],
                         capture_output=True, text=True, timeout=10)
    assert old.returncode == 0
    path = tmp_path / "candidate.py"; path.write_text(old.stdout)
    name = "services.source_ingestion.connectors.session_candidate"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    clock, lock = [0.0], threading.Lock()
    files, entries, measurements, identities = {}, [], [], []
    class Transport(FakeTransport):
        def read(self, path, *, max_bytes):
            with lock:
                start = clock[0]
            if "/raw/" in path:
                time.sleep(0.002)
            try:
                return super().read(path, max_bytes=max_bytes)
            finally:
                with lock:
                    clock[0] = max(clock[0], start + (0.19 if "/raw/" in path else 0.14))
    module.time = types.SimpleNamespace(monotonic=lambda: clock[0])
    try:
        for fetched in ("2026-11-13T12:18:42Z", "2026-11-13T18:18:44Z", "2026-11-14T00:19:33Z"):
            more, entry = recorded_run(source, count, fetched=fetched)
            files.update(more); entries.append(entry)
            files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
            files["status.json"] = json.dumps({"sources": {source: {"status": "ok", "last_run_at": fetched}}}).encode()
            a = module.CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market",
                dataset="tw_price_daily", cadence_seconds=600, max_records=1000, raw_root=str(tmp_path / "store"),
                attribution_required=True, transport=Transport(files))
            prior_state = a._load_priority_state()
            start, wall = clock[0], time.perf_counter()
            pull = a.pull(); a.save_resume_cursor(pull)
            measurements.append({"run_id": entry["run_id"], "rows": len(pull.records),
                "delivered": pull.priority_delivered, "done": pull.priority_done,
                "wall_seconds": time.perf_counter() - wall, "transport_seconds": clock[0] - start,
                "priority_key": pull.priority_key, "prior_priority_state": prior_state,
                "saved_priority_state": a._load_priority_state(),
                "first_identity": pull.records[0].source_id if pull.records else None})
            identities.append({r.source_id for r in pull.records})
        for measured, ids in zip(measurements, identities):
            measured["replayed_first_pull_rows"] = len(ids & identities[0])
        # Print even when an invariant fails, so the actual windows/timings survive.
        print("CANDIDATE_SESSION_TIMING " + json.dumps({"candidate": "a9284b68c", "source": source,
                                                      "rows_in_bundle": count, "pulls": measurements}))
        # The historical bug resets acknowledged progress on each new run key.
        # Concurrent reads share a deadline; their window lengths need not match.
        # Prove that saved nonzero progress was loaded, then restarted at the
        # same native item with real overlap instead of continuing after it.
        assert measurements[0]["prior_priority_state"] == (None, 0, False, ())
        assert len({m["priority_key"] for m in measurements}) == 3
        for index, measured in enumerate(measurements):
            assert measured["priority_key"].endswith("#" + measured["run_id"])
            assert 0 < measured["delivered"] == measured["rows"] < count
            assert not measured["done"]
            assert measured["saved_priority_state"] == (measured["priority_key"], measured["rows"], False, ())
            assert measured["first_identity"] == measurements[0]["first_identity"]
            assert measured["replayed_first_pull_rows"] > 0
            if index:
                previous = measurements[index - 1]
                assert measured["prior_priority_state"] == previous["saved_priority_state"]
    finally:
        sys.modules.pop(name, None)


def test_smaller_same_day_run_retains_undelivered_tail(tmp_path):
    files, first = recorded_run(count=8, fetched="2026-11-13T12:18:42Z")
    a = adapter(tmp_path, files, [first]); pull = a.pull(); a.save_resume_cursor(pull)
    ids = {r.source_id for r in pull.records}
    fewer, entry = recorded_run(count=4); files.update(fewer)
    for _ in range(3):
        a = adapter(tmp_path, files, [first, entry])
        pull = a.pull(cursor=pull.next_cursor); a.save_resume_cursor(pull)
        assert not ids.intersection(r.source_id for r in pull.records)
        ids.update(r.source_id for r in pull.records)
        if pull.priority_done:
            break
    assert len(ids) == 8 and pull.priority_delivered == 8 and pull.priority_done


def test_empty_unacknowledged_bundle_does_not_claim_no_new_data(tmp_path):
    files, entry = recorded_run(count=0)
    pull = adapter(tmp_path, files, [entry]).pull()
    assert not pull.no_new_data


@pytest.mark.parametrize("declared", ["2026-11-12", "2026-11-14"])
def test_additive_trade_date_must_agree_with_rows(tmp_path, declared):
    files, entry = recorded_run()
    entry["trade_date"] = declared
    with pytest.raises(cb.SourceEvidenceError, match="disagrees with rows"):
        adapter(tmp_path, files, [entry]).pull()
