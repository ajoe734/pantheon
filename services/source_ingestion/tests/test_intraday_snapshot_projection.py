"""K4 end-to-end: IntradayStore ingest -> K3 reconcile run -> GET snapshots/latest?frequency=1m.

Uses the public path only: ``reconcile_day`` (which ends with the projection) and the real
FastAPI route. An empty store is never a pass: every positive case asserts real bar values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from services.source_ingestion.intraday_reconcile import OfficialDaily, load_config, reconcile_day
from services.source_ingestion.intraday_snapshot_projection import project_reconciled_minute_snapshots
from services.source_ingestion.tests.test_intraday_reconcile import DAY, NOW, _bars, _official, _store
from services.source_ingestion.tests.test_latest_market_snapshot import _ingest_prices, _load_source_main

CFG = load_config()
SYM = "2330.TWSE"


def _run(istore, official, snap, symbols=("2330",), report=None, **kw):
    return reconcile_day(istore, official, DAY, CFG, symbols=list(symbols), clock=lambda: NOW,
                         snapshot_store=snap, projection_report=report, **kw)


def _get(client, frequency=None, symbol="2330"):
    qs = f"symbol={symbol}" + (f"&frequency={frequency}" if frequency else "")
    return client.get(f"/api/source-ingest/snapshots/latest?{qs}")


def test_pass_day_is_served_at_frequency_1m_with_real_bars(tmp_path, monkeypatch: Any):
    module = _load_source_main(monkeypatch, tmp_path / "svc")
    client = TestClient(module.app)
    snap = module.runtime.latest_market_snapshot_store
    istore = _store(tmp_path / "intraday")
    rows = _bars()
    istore.put_symbol_day("2330", DAY, rows)

    assert _get(client, "1m").status_code == 404  # nothing before reconciliation

    report: dict[str, Any] = {}
    results = _run(istore, OfficialDaily([_official()], [DAY]), snap, report=report)
    assert [r.outcome for r in results] == ["pass"]
    assert report["projected_symbol_days"] == 1 and report["updated_snapshot_count"] == 1

    resp = _get(client, "1m")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_closes = [r["close"] for r in rows][-60:]
    assert body["closes"] == expected_closes and body["closes"][-1] == 104.0  # real last bar
    assert body["dataset"] == "tw_price_1m"
    assert body["event_time"] == "2026-11-10T05:31:00Z"  # last bar_end 13:31+08
    assert body["observed_at"] == results[0].bar_availability_ts  # reconciled availability, not bar_end
    assert body["lineage"]["connector_ids"] == ["tw-finmind-kbar-1m"]
    assert all("sha256=" in ref for ref in body["lineage"]["content_refs"])
    # frequency-isolated: no snapshot appears under daily or an unprojected frequency
    assert _get(client).status_code == 404
    assert _get(client, "1d").status_code == 404
    assert _get(client, "5m").status_code == 404
    assert _get(client, "2m").status_code == 422


def test_pending_reconciliation_is_not_visible(tmp_path, monkeypatch: Any):
    module = _load_source_main(monkeypatch, tmp_path / "svc")
    client = TestClient(module.app)
    snap = module.runtime.latest_market_snapshot_store
    istore = _store(tmp_path / "intraday")
    istore.put_symbol_day("2330", DAY, _bars())
    report: dict[str, Any] = {}
    # official partition for the day not available yet -> pending, not pass
    results = _run(istore, OfficialDaily([], []), snap, report=report)
    assert [r.outcome for r in results] == ["pending_official"]
    assert report["projected_symbol_days"] == 0 and report["skipped_outcome_counts"] == {"pending_official": 1}
    assert _get(client, "1m").status_code == 404


def test_quarantined_day_is_not_visible(tmp_path, monkeypatch: Any):
    module = _load_source_main(monkeypatch, tmp_path / "svc")
    client = TestClient(module.app)
    snap = module.runtime.latest_market_snapshot_store
    istore = _store(tmp_path / "intraday")
    istore.put_symbol_day("2330", DAY, _bars())
    results = _run(istore, OfficialDaily([_official(c=150.0)], [DAY]), snap)  # close disagrees with official
    assert [r.outcome for r in results] == ["fail"]
    assert _get(client, "1m").status_code == 404
    # a mixed run projects only the passing symbol
    istore.put_symbol_day("2317", DAY, _bars("2317"))
    istore.put_symbol_day("2330", DAY, _bars())
    off = OfficialDaily([_official("2317"), _official(c=150.0)], [DAY])
    results = _run(istore, off, snap, symbols=("2317", "2330"))
    by = {r.symbol: r.outcome for r in results}
    assert by["2317"] == "pass" and by["2330"] != "pass"
    assert _get(client, "1m", "2317").status_code == 200
    assert _get(client, "1m", "2330").status_code == 404


def test_default_1d_results_identical_before_and_after_minute_projection(tmp_path, monkeypatch: Any):
    module = _load_source_main(monkeypatch, tmp_path / "svc")
    client = TestClient(module.app)
    _ingest_prices(client)  # real daily ingest through the public jobs API
    before = client.get("/api/source-ingest/snapshots/latest?symbol=AAPL.US")
    before_explicit = client.get("/api/source-ingest/snapshots/latest?symbol=AAPL.US&frequency=1d")
    assert before.status_code == 200 and before.json()["closes"] == [220.0, 224.5, 223.0]
    log_before = (tmp_path / "svc").rglob("*snapshot*")
    log_before = {p: p.read_bytes() for p in log_before if p.is_file()}

    istore = _store(tmp_path / "intraday")
    istore.put_symbol_day("2330", DAY, _bars())
    assert [r.outcome for r in _run(istore, OfficialDaily([_official()], [DAY]),
                                    module.runtime.latest_market_snapshot_store)] == ["pass"]
    assert _get(client, "1m").status_code == 200

    after = client.get("/api/source-ingest/snapshots/latest?symbol=AAPL.US")
    assert after.content == before.content == before_explicit.content
    assert "dataset" not in after.json()
    # the daily snapshot log lines written earlier are byte-for-byte unchanged (only appended to)
    for path, data in log_before.items():
        assert path.read_bytes().startswith(data)


def test_projection_ignores_waived_tampered_and_non_pass(tmp_path):
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

    istore = _store(tmp_path / "intraday")
    snap = LatestMarketSnapshotStore(tmp_path / "snap.jsonl")
    istore.put_symbol_day("2330", DAY, _bars())
    results = reconcile_day(istore, OfficialDaily([_official()], [DAY]), DAY, CFG, clock=lambda: NOW)
    assert results[0].outcome == "pass"
    # tamper with the stored shard after reconciliation: hash check withholds the day
    shard = istore.shard_path("2330", DAY)
    shard.write_bytes(shard.read_bytes() + b"x")
    report = project_reconciled_minute_snapshots(snap, results, intraday_store=istore)
    assert report["projected_symbol_days"] == 0 and report["withheld"] == [{"symbol": "2330", "session_date": DAY}]
    assert snap.get("2330", "tw_price_1m") is None


def test_dry_run_reconcile_never_projects(tmp_path):
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore

    istore = _store(tmp_path / "intraday")
    snap = LatestMarketSnapshotStore(tmp_path / "snap.jsonl")
    istore.put_symbol_day("2330", DAY, _bars())
    results = reconcile_day(istore, OfficialDaily([_official()], [DAY]), DAY, CFG, clock=lambda: NOW,
                            write=False, snapshot_store=snap)
    assert results[0].outcome == "pass"
    assert not (tmp_path / "snap.jsonl").exists()
