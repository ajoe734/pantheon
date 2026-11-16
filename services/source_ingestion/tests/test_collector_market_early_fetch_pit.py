"""Recorded 11/11 two-date TWSE shape, hosted PIT failure and persistence recovery.

The incident timestamps/counts are recorded evidence; bodies use the checked-in
TWSE response fixture rather than claiming to be a downloaded hosted bundle.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime as _real_datetime, timezone as _tz
from pathlib import Path

import pytest

from services.source_ingestion import controller_worker as cw
from services.source_ingestion.connectors import collector_bundle as cb
from services.source_ingestion.external_sources import validate_external_source_record
from services.source_ingestion.tests.test_collector_bundle_connector import FakeTransport
from services.source_ingestion.tests.test_collector_newest_session_progress import TWSE, recorded_run
from services.source_ingestion.tests.test_scheduled_connector import admission_stack, client  # noqa: F401


class _FrozenDatetime(_real_datetime):
    """Recorded fixtures are pinned to 2026-11-14; keep health staleness deterministic."""

    @classmethod
    def now(cls, tz=None):
        frozen = _real_datetime(2026, 11, 14, 19, 0, 0, tzinfo=_tz.utc)
        return frozen if tz is not None else frozen.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def frozen_collector_clock(monkeypatch):
    monkeypatch.setattr(cb, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def isolated_metadata_cache():
    cb._METADATA_CACHE.clear()
    yield
    cb._METADATA_CACHE.clear()


def price_bundle(*, fetched="2026-11-11T06:16:05Z", counts=(("2026-11-10", 1375), ("2026-11-11", 1376))):
    files, items, rows = {}, [], []
    entry = manifest = None
    for day, count in counts:
        more, entry = recorded_run(TWSE, count, fetched=fetched, day=day)
        base = f"bundles/{TWSE}/{entry['date']}/{entry['run_id']}"
        manifest = json.loads(more[base + "/manifest.json"])
        items.extend(manifest["items"])
        for line in more[base + "/items.jsonl"].splitlines():
            row = json.loads(line)
            row["published_at"] = day + "T06:30:00Z"  # unchanged collector output
            rows.append(row)
        files.update(more)
    manifest["items"] = items
    manifest["bundle_sha256"] = cb.compute_bundle_sha256([item["content_sha256"] for item in items])
    entry["bundle_sha256"] = manifest["bundle_sha256"]
    files[base + "/manifest.json"] = json.dumps(manifest).encode()
    files[base + "/items.jsonl"] = b"\n".join(json.dumps(row).encode() for row in rows)
    return files, entry


def adapter(root, files, entries, *, module=cb, cap=4000):
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {TWSE: {
        "status": "ok", "last_run_at": entries[-1]["finished_at"], "cadence_seconds": 86400}}}).encode()
    return module.CollectorBundleAdapter(connector_id=TWSE, source_id=TWSE, source_type="market",
        dataset="tw_price_daily", cadence_seconds=600, max_records=cap, read_budget_seconds=None,
        raw_root=str(root), transport=FakeTransport(files), attribution_required=True)


def load_candidate(root, revision):
    result = subprocess.run(["git", "show", f"{revision}:services/source_ingestion/connectors/collector_bundle.py"],
                            capture_output=True, text=True, timeout=10, check=True)
    path = root / f"candidate_{revision}.py"
    path.write_text(result.stdout)
    name = f"services.source_ingestion.connectors.early_fetch_{revision}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def seed_priority(a, files, entry, count=1340):
    base = f"bundles/{TWSE}/{entry['date']}/{entry['run_id']}"
    manifest = json.loads(files[base + "/manifest.json"])
    path = a._priority_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source_id": TWSE, "key": entry["finished_at"] + "#" + entry["run_id"],
        "trade_date": "2026-11-13", "delivered": count, "done": False, "held": [],
        "items": {item["item_id"]: item["content_sha256"] for item in manifest["items"][:count]}}))


def test_recorded_candidate_loses_newest_tail_when_ordered_sweep_reaches_early_bundle(tmp_path):
    old = load_candidate(tmp_path, "15bee6816")
    files, early = price_bundle()
    more, newest = price_bundle(fetched="2026-11-14T18:19:33Z", counts=(("2026-11-13", 1377),))
    files.update(more)
    a = adapter(tmp_path / "old", files, [early, newest], module=old)
    seed_priority(a, files, newest)
    before = a._priority_key_path().read_bytes()
    try:
        with pytest.raises(cb.SourceEvidenceError, match="available_time must be >= event_time"):
            a.pull()
        assert a._priority_key_path().read_bytes() == before
        assert a._load_priority_state()[1] == 1340
        # Priority raw was verified before the ordered part killed the entire pull.
        assert len(list(a._raw_bundle_path(old.verify_bundle(a.transport,
            expected_source_id=TWSE, entry=newest, item_window=(0, 0))).glob("*.json"))) >= 38
    finally:
        sys.modules.pop(old.__name__, None)


def test_recorded_2751_row_bundle_maps_only_1376_early_rows_and_keeps_newest_tail(tmp_path):
    files, early = price_bundle()
    more, newest = price_bundle(fetched="2026-11-14T18:19:33Z", counts=(("2026-11-13", 1377),))
    files.update(more)
    a = adapter(tmp_path, files, [early, newest])
    seed_priority(a, files, newest)
    result = a.pull()
    a.save_resume_cursor(result)
    assert len(result.records) == 2751 + 37
    assert result.priority_done and result.priority_delivered == 1377 and result.complete
    early_rows = [r for r in result.records if r.metadata["trade_date"] == "2026-11-11"]
    assert len(early_rows) == 1376
    assert {r.metadata["event_time"] for r in early_rows} == {"2026-11-11T06:16:05Z"}
    assert {r.metadata["available_time"] for r in early_rows} == {"2026-11-11T06:16:05Z"}
    older = [r for r in result.records if r.metadata["trade_date"] == "2026-11-10"]
    assert len(older) == 1375
    assert {r.metadata["event_time"] for r in older} == {"2026-11-10T06:30:00Z"}
    assert result.observed["health_state"] == "healthy"


@pytest.mark.parametrize("fetched,event", [("05:30:00", "05:30:00"), ("06:16:05", "06:16:05"),
                                           ("06:40:00", "06:30:00")])
def test_session_close_boundary_and_normal_1440_capture(tmp_path, fetched, event):
    files, entry = price_bundle(fetched=f"2026-11-11T{fetched}Z", counts=(("2026-11-11", 2),))
    a = adapter(tmp_path, files, [entry])
    result = a.pull()
    assert len(result.records) == 2
    for row in result.records:
        assert row.metadata["event_time"] == f"2026-11-11T{event}Z"
        assert row.metadata["available_time"] == f"2026-11-11T{fetched}Z"
        invalid = replace(row, metadata={**row.metadata, "available_time": "2026-11-11T05:29:59Z"})
        with pytest.raises(cb.SourceEvidenceError, match="available_time must be >= event_time"):
            validate_external_source_record(invalid, connector=a.connector(), force=True)


def test_preclose_price_is_rejected_with_typed_reason(tmp_path):
    files, entry = price_bundle(fetched="2026-11-11T05:29:59Z", counts=(("2026-11-11", 2),))
    a = adapter(tmp_path, files, [entry])
    with pytest.raises(cb.MarketTimeEvidenceError) as error:
        a.pull()
    assert error.value.reason == "market_price_fetched_before_session_close"
    assert not a._priority_key_path().exists()


def test_quarantine_only_sweep_keeps_acknowledged_newest_session_healthy(tmp_path):
    files, early = price_bundle(fetched="2026-11-11T05:29:59Z")
    more, newest = price_bundle(fetched="2026-11-14T18:19:33Z", counts=(("2026-11-13", 1377),))
    files.update(more)
    a = adapter(tmp_path, files, [early, newest])
    seed_priority(a, files, newest, count=1377)
    result = a.pull()
    assert result.records == () and result.no_new_data and result.complete
    assert result.next_cursor.endswith("#" + newest["run_id"])
    assert result.observed["health_state"] == "healthy"
    assert len(result.observed["rejected_bundles"]) == 1
    assert not a._raw_bundle_path(cb.verify_bundle(a.transport,
        expected_source_id=TWSE, entry=early, item_window=(0, 0))).exists()


@pytest.mark.parametrize("reject,same_second", [(False, False), (True, False), (True, True)],
                         ids=["early-admitted", "preclose-quarantined", "preclose-same-second-receipts"])
def test_real_pipeline_persists_newest_and_advances_sweep_after_restart(
        admission_stack, monkeypatch, reject, same_second):
    if same_second:
        # Receipt finished_at has 1s precision; every pull finishing in one second used to make
        # receipts[-1] an arbitrary pull (random run-id tiebreak). Force that tie deterministically.
        from datetime import datetime, timezone
        from services.source_ingestion.connectors import base
        monkeypatch.setattr(base, "_utc_now", lambda: datetime(2026, 11, 15, tzinfo=timezone.utc))
    http, module, _, _, files, _, tick = admission_stack
    more, newest = price_bundle(fetched="2026-11-14T18:19:33Z", counts=(("2026-11-13", 1377),))
    files.update(more)
    files["bundles/index.json"] = json.dumps({"bundles": [newest]}).encode()
    files["status.json"] = json.dumps({"sources": {TWSE: {
        "status": "ok", "last_run_at": newest["finished_at"], "cadence_seconds": 86400}}}).encode()
    tick(cw.RECONCILE_ONLY_MODE)

    def pull():
        # Receipts sort by (finished_at at 1s precision, random run id), so two pulls
        # finishing in the same second have arbitrary order: pick this pull's own receipt.
        before = {r.ingest_run_id for r in module.runtime.store.read_freshness_snapshot()["receipts"]}
        response = http.post("/api/source-ingest/run-scheduled", json={
            "force_connector_ids": [TWSE], "exclusive_connector_ids": [TWSE]},
            headers={"Authorization": f"Bearer {module.runtime.controller_token}"})
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["total_failed"] == 0, response.text
        module.runtime.store.reload()
        module.runtime.latest_market_snapshot_store.reload()
        created = [r for r in module.runtime.store.read_freshness_snapshot()["receipts"]
                   if r.ingest_run_id not in before]
        assert len(created) == 1, created
        return created[0]

    for _ in range(5):
        assert pull().normalized_count == 256
    monkeypatch.setattr(cb, "LATEST_CLOSE_PERSISTED_ROWS_PER_PULL", 60)
    assert pull().normalized_count == 60
    root = module.runtime.connector_store.path.parent / "market"
    # Locate actual configured adapter state rather than relying on a test-only ack.
    priorities = list(root.rglob("latest_priority.json"))
    assert len(priorities) == 1
    assert json.loads(priorities[0].read_text())["delivered"] == 1340
    monkeypatch.setattr(cb, "LATEST_CLOSE_PERSISTED_ROWS_PER_PULL", 256)
    history, early = price_bundle(fetched="2026-11-11T05:29:59Z" if reject else "2026-11-11T06:16:05Z")
    files.update(history)
    files["bundles/index.json"] = json.dumps({"bundles": [early, newest]}).encode()
    receipt = pull()
    assert receipt.normalized_count >= 37
    assert json.loads(priorities[0].read_text())["delivered"] == 1377
    snapshots = module.runtime.latest_market_snapshot_store.reload()
    assert sum(s.points[-1].connector_id == TWSE and s.points[-1].event_time[:10] == "2026-11-13"
               for s in snapshots.values()) == 1377
    for _ in range(5):
        state = module.runtime.connector_store.get_fetch_state(TWSE)
        if state["collector_outcome"]["complete"]:
            break
        pull()
    state = module.runtime.connector_store.get_fetch_state(TWSE)
    assert state["collector_outcome"]["complete"]
    assert state["collector_outcome"]["observed"]["health_state"] == "healthy"
    assert not state.get("last_error")
    partitions = {ref["uri"] for receipt in module.runtime.store.read_freshness_snapshot()["receipts"]
                  if receipt.status == "completed" and receipt.connector_id == TWSE
                  for ref in receipt.storage_refs.get("normalized_refs", ())
                  if ref.get("date") == "2026-11-11"}
    persisted = [json.loads(line) for uri in partitions for line in Path(uri).read_text().splitlines()]
    assert len(persisted) == (0 if reject else 1376)
    if not reject:
        assert {r["metadata"]["event_time"] for r in persisted} == {"2026-11-11T06:16:05Z"}
        assert {r["metadata"]["available_time"] for r in persisted} == {"2026-11-11T06:16:05Z"}
    quarantine = list(root.rglob("rejected_bundles/*.json"))
    assert len(quarantine) == int(reject)
    if reject:
        evidence = json.loads(quarantine[0].read_text())
        assert evidence["reason"] == "market_price_fetched_before_session_close"
        assert evidence["run_id"] == early["run_id"]
        assert state["collector_outcome"]["observed"]["rejected_bundles"][0] == evidence
    # A subsequent configured empty pull is healthy, with no permanent retry/degrade.
    assert pull().normalized_count == 0
    assert module.runtime.connector_store.get_fetch_state(TWSE)["collector_outcome"]["no_new_data"]
    for revision in ("bee62bc29", "15bee6816"):
        old = load_candidate(root, revision)
        try:
            reader = old.CollectorBundleAdapter(connector_id=TWSE, source_id=TWSE, raw_root=str(root))
            assert reader.load_resume_cursor() is None
            if hasattr(reader, "_load_priority_state"):
                assert reader._load_priority_state()[1:3] == (1377, True)
        finally:
            sys.modules.pop(old.__name__, None)
