"""After /readyz reports ready, the first snapshot lookup does no journal pass and no sweep.

Cause (measured, see the evidence file): ``SnapshotUniverse._last_sweep`` started at 0, so the first
``snapshot_for_request`` after a start ran the due sweep in its ``finally`` -> ``withdraw_symbols`` ->
``_compact_journal_in_place`` (a full journal parse).  The first due sweep now runs in
``SourceIngestionRuntime._warm_snapshot_lookup`` during runtime construction, before the app can answer /readyz.
No wall-clock threshold: counters only.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion import requirement_state as m
from services.source_ingestion import snapshot_universe as su
from services.source_ingestion.main import create_app
from services.source_ingestion import runtime as rt
from services.source_ingestion.runtime import create_runtime
from services.source_ingestion.tests.bench_market_snapshot_incremental_read import _line

KEYS = 400  # hosted has about 10700 keys; the shape (60 closes per key, member state ready, most keys non-members) is kept
SYMBOLS = ["1001.TWSE", "1002.TWSE", "1004.TWSE", "1003.TWSE"]


def _build(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    with (data / "latest_market_snapshots.jsonl").open("w", encoding="utf-8") as handle:
        for s in range(KEYS):
            handle.write(_line(f"{1000 + s}.TWSE", 0) + "\n")
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date().isoformat()
    members = {str(c): {"symbol": f"{c}.TWSE", "last_requested_date": today, "origin": "request"} for c in (1001, 1002, 1003, 1004)}
    (data / "latest_market_snapshots.jsonl.universe.json").write_text(
        json.dumps({"schema_version": su.STATE_SCHEMA_VERSION, "updated_at": "2026-11-18T00:00:00Z",
                    "members": members, "transitions": [], "transitions_total": 0}),
        encoding="utf-8",
    )
    return data


@pytest.fixture
def counters(monkeypatch):
    c = {"reload": 0, "compact": 0, "sweep": 0, "sync": 0}
    for cls, name, key in (
        (m.LatestMarketSnapshotStore, "_reload_journal", "reload"),
        (m, "_compact_journal_in_place", "compact"),
        (m.LatestMarketSnapshotStore, "_sync_locked", "sync"),
        (su.SnapshotUniverse, "sweep", "sweep"),
    ):
        original = getattr(cls, name)

        def wrapper(*a, __o=original, __k=key, **kw):
            c[__k] += 1
            return __o(*a, **kw)

        monkeypatch.setattr(cls, name, wrapper)
    return c


@pytest.mark.parametrize("mode", ["journal", "dual", "sqlite"])
def test_first_lookup_after_ready_does_no_journal_pass(tmp_path, monkeypatch, counters, mode):
    data = _build(tmp_path)
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", mode)
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(data))
    runtime = create_runtime(data)
    client = TestClient(create_app(runtime))
    assert counters["sweep"] == 1  # the due sweep ran once, during construction
    before = dict(counters)

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["dependencies"]["snapshot_lookup"]["status"] == "ok"
    assert counters == before  # a readiness call reads no journal (acceptance 8)

    bodies = []
    for _ in range(2):
        for symbol in SYMBOLS:
            response = client.get("/api/source-ingest/snapshots/latest", params={"symbol": symbol})
            assert response.status_code == 200, response.text
            bodies.append(response.json())
    assert counters == before  # zero reload / compact / sweep from the lookups (acceptance 4)
    assert bodies[0]["symbol"] and len(bodies[0]["closes"] if "closes" in bodies[0] else bodies[0]["points"]) == 60


def test_not_ready_while_warm_start_runs(tmp_path, monkeypatch):
    data = _build(tmp_path)
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", "journal")
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(data))
    seen = {}
    original = rt.SourceIngestionRuntime._warm_snapshot_lookup

    def watching(self):
        seen["flag_during"] = runtime_box[0].snapshot_lookup_ready if runtime_box else "no-runtime-yet"
        return original(self)

    runtime_box: list = []
    monkeypatch.setattr(rt.SourceIngestionRuntime, "_warm_snapshot_lookup", watching)
    runtime = create_runtime(data)
    assert seen["flag_during"] == "no-runtime-yet"  # the runtime (hence the app) did not exist while the load ran
    assert runtime.snapshot_lookup_ready is True
    runtime.snapshot_lookup_ready = False  # the flag /readyz reads while a warm start is running
    client = TestClient(create_app(runtime))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["dependencies"]["snapshot_lookup"]["warm"] is False


def test_sweep_not_run_again_by_first_request(tmp_path, monkeypatch, counters):
    data = _build(tmp_path)
    monkeypatch.setenv("MARKET_SNAPSHOT_STORE", "journal")
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(data))
    runtime = create_runtime(data)
    assert runtime.snapshot_warm_start_count == 1
    sweeps = counters["sweep"]
    runtime.snapshot_universe.snapshot_for_request("1001.TWSE")
    assert counters["sweep"] == sweeps
