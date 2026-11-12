"""K3: minute vs official daily reconciliation — positive and negative outcomes (SD §5)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_ingestion.intraday_reconcile import (
    OfficialDaily,
    OfficialRow,
    ReconcileError,
    load_config,
    pass_rate_summary,
    read_reconciled_rows,
    readmit_after_refetch,
    reconcile_day,
    reconcile_symbol_day,
    record_waiver,
)
from services.source_ingestion.intraday_store import IntradayStore

DAY = "2026-11-10"
CFG = load_config()
FETCHED = "2026-11-10T08:00:00Z"
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[3]


def _bars(symbol="2330", day=DAY, *, skip=(), close=104.0, vol=100, fetched=FETCHED, extra=()):
    rows = []
    for i in range(271):
        if i in skip:
            continue
        h, m = divmod(9 * 60 + i, 60)
        s = f"{day}T{h:02d}:{m:02d}:00+08:00"
        e = (datetime.fromisoformat(s) + timedelta(minutes=1)).isoformat()
        rows.append({"symbol": symbol, "session_date": day, "bar_start": s, "bar_end": e,
                     "open": 100.0, "high": 110.0 if i == 5 else 105.0, "low": 95.0 if i == 7 else 99.0,
                     "close": close if i == 270 else 100.5, "volume": vol, "fetched_at": fetched})
    for s in extra:
        rows.append({**rows[0], "bar_start": s})
    return rows


def _official(symbol="2330", day=DAY, *, o=100.0, h=110.0, l=95.0, c=104.0, v=271 * 100 * 1000, avail="2026-11-10T06:30:00Z"):
    ref = {"path": "p.jsonl", "line": 1, "line_sha256": f"sha-{symbol}-{day}-{c}-{v}"}
    return OfficialRow(symbol, day, *(Decimal(str(x)) for x in (o, h, l, c, v)),
                       datetime.fromisoformat(avail.replace("Z", "+00:00")), ref)


def _store(tmp_path):
    return IntradayStore(tmp_path, usage_fn=lambda p: SimpleNamespace(free=10**13), clock=lambda: NOW)


def _run(store, official, symbol="2330", **kw):
    return reconcile_symbol_day(store, official, symbol, DAY, CFG, clock=lambda: NOW, **kw)


def test_pass_records_real_reconciled_at_and_availability(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    res = _run(store, OfficialDaily([_official()], [DAY]))
    assert res.outcome == "pass" and not res.violations
    assert res.reconciled_at == "2026-11-10T09:00:00Z"
    # availability = max(bar_end 09:00+08 = ..., fetched, reconciled): never the 13:31 bar_end
    assert res.bar_availability_ts == "2026-11-10T09:00:00Z"
    rows = read_reconciled_rows(store, "2330", DAY)
    assert len(rows) == 271 and all(r["bar_availability_ts"] == "2026-11-10T09:00:00Z" for r in rows)
    assert all(r["replication_eligible"] and not r["waived"] for r in rows)
    # unchanged rerun keeps the original reconciled_at
    later = reconcile_symbol_day(store, OfficialDaily([_official()], [DAY]), "2330", DAY, CFG,
                                 clock=lambda: NOW + timedelta(hours=5))
    assert later.unchanged and later.reconciled_at == res.reconciled_at


def test_bar_end_after_reconcile_is_not_backdated(tmp_path):
    """A bar can never be available before its own bar_end."""
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars(fetched="2026-11-10T01:00:00Z"))
    early = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)
    res = reconcile_symbol_day(store, OfficialDaily([_official(avail="2026-11-10T01:30:00Z")], [DAY]),
                               "2330", DAY, CFG, clock=lambda: early)
    assert res.outcome == "pass"
    last_end = datetime.fromisoformat(_bars()[-1]["bar_end"]).astimezone(timezone.utc)
    assert res.bar_availability_ts == last_end.isoformat().replace("+00:00", "Z")
    assert res.bar_availability_ts >= res.reconciled_at


@pytest.mark.parametrize("kwargs,field", [
    ({"c": 106.0}, "close"), ({"h": 112.0}, "high"), ({"o": 98.0}, "open"), ({"l": 93.0}, "low"),
    ({"v": 271 * 100 * 1000 * 1.05}, "volume"),
])
def test_field_outside_tolerance_quarantines(tmp_path, kwargs, field):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    res = _run(store, OfficialDaily([_official(**kwargs)], [DAY]))
    assert res.outcome == "fail" and field in {v["field"] for v in res.violations}
    assert store.symbol_day_status("2330", DAY) == "quarantined"
    assert store.read_rows("2330", DAY) == [] and read_reconciled_rows(store, "2330", DAY) == []
    rec = json.loads(store.quarantine_path("2330", DAY).read_text())
    d = rec["detail"]
    assert d["violations"][0]["observed"] is not None and d["source_refs"]["official"]["path"] == "p.jsonl"
    assert store.shard_path("2330", DAY).exists()  # bars untouched, not deleted


def test_price_tolerance_is_max_of_tick_and_pct(tmp_path):
    # close 104 official: tick 0.5, 0.1% = 0.104 -> tolerance 0.5; 0.5 off passes, 0.6 fails.
    for official_close, outcome in ((104.5, "pass"), (104.6, "fail")):
        store = _store(tmp_path / outcome)
        store.put_symbol_day("2330", DAY, _bars())
        assert _run(store, OfficialDaily([_official(c=official_close)], [DAY])).outcome == outcome
    # high-price: 0.1% beats the tick. close 1000 -> tick 5, 0.1% = 1 -> tolerance 5.
    assert CFG.tick_size(Decimal("1000")) == 5 and CFG.tick_size(Decimal("99.9")) == Decimal("0.1")


def test_volume_boundary_two_percent(tmp_path):
    ok = _store(tmp_path / "a"); ok.put_symbol_day("2330", DAY, _bars())
    assert _run(ok, OfficialDaily([_official(v=27_100_000 * 1.019)], [DAY])).outcome == "pass"
    bad = _store(tmp_path / "b"); bad.put_symbol_day("2330", DAY, _bars())
    assert _run(bad, OfficialDaily([_official(v=27_100_000 * 1.03)], [DAY])).outcome == "fail"


def test_coverage_and_gap_rules(tmp_path):
    cov = _store(tmp_path / "c")  # 12 scattered missing minutes = 95.57% coverage, gaps of 1: pass
    cov.put_symbol_day("2330", DAY, _bars(skip=range(10, 130, 10)))
    sparse = OfficialDaily([_official(v=259 * 100 * 1000)], [DAY])
    assert _run(cov, sparse).outcome == "pass"
    low = _store(tmp_path / "l"); low.put_symbol_day("2330", DAY, _bars(skip=range(0, 271, 12)))  # 23 missing
    res = _run(low, OfficialDaily([_official(v=248 * 100 * 1000)], [DAY]))
    assert res.outcome == "fail" and "coverage" in {v["field"] for v in res.violations}
    gap = _store(tmp_path / "g"); gap.put_symbol_day("2330", DAY, _bars(skip=range(100, 111)))  # 11-min gap, 95.9%
    res = _run(gap, OfficialDaily([_official(v=260 * 100 * 1000)], [DAY]))
    assert res.outcome == "fail" and [v["field"] for v in res.violations] == ["gap"]
    ten = _store(tmp_path / "t"); ten.put_symbol_day("2330", DAY, _bars(skip=range(100, 110)))  # exactly 10: allowed
    assert _run(ten, OfficialDaily([_official(v=261 * 100 * 1000)], [DAY])).outcome == "pass"


def test_missing_official_row_quarantines_but_absent_partition_is_pending(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    pending = _run(store, OfficialDaily([], []))
    assert pending.outcome == "pending_official" and store.symbol_day_status("2330", DAY) == "accepted"
    assert read_reconciled_rows(store, "2330", DAY) == []  # accepted but unreconciled: not loader-visible
    res = _run(store, OfficialDaily([_official(symbol="2317")], [DAY]))
    assert res.outcome == "no_official_row" and store.symbol_day_status("2330", DAY) == "quarantined"


def test_official_not_yet_available_stays_pending(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    res = _run(store, OfficialDaily([_official(avail="2026-11-10T10:00:00Z")], [DAY]))
    assert res.outcome == "pending_official" and store.symbol_day_status("2330", DAY) == "accepted"


def test_conflicting_official_rows_quarantine(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    res = _run(store, OfficialDaily([_official(c=104.0), _official(c=105.0)], [DAY]))
    assert res.outcome == "official_row_conflict" and store.symbol_day_status("2330", DAY) == "quarantined"
    same = _store(tmp_path / "s"); same.put_symbol_day("2330", DAY, _bars())
    assert _run(same, OfficialDaily([_official(), _official()], [DAY])).outcome == "pass"  # identical dup ok


def test_minute_missing_is_reported_not_quarantined(tmp_path):
    store = _store(tmp_path)
    res = _run(store, OfficialDaily([_official()], [DAY]))
    assert res.outcome == "minute_missing"
    store.put_symbol_day("2330", DAY, [])  # provider_empty
    assert _run(store, OfficialDaily([_official()], [DAY])).outcome == "minute_missing"
    assert store.symbol_day_status("2330", DAY) == "provider_empty"
    assert _run(store, OfficialDaily([], [DAY])).outcome == "not_applicable"


def test_bars_outside_regular_session_fail(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars(extra=[f"{DAY}T13:33:00+08:00"]))
    res = _run(store, OfficialDaily([_official()], [DAY]))
    assert res.outcome == "fail" and "session_shape" in {v["field"] for v in res.violations}


def test_already_quarantined_by_store_is_reported(tmp_path):
    store = _store(tmp_path)
    store.quarantine_symbol_day("2330", DAY, reason="unsupported_session_shape")
    res = _run(store, OfficialDaily([_official()], [DAY]))
    assert res.outcome == "already_quarantined" and res.reason == "unsupported_session_shape"


def test_no_minute_edit_and_no_daily_write(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    before = store.shard_path("2330", DAY).read_bytes()
    _run(store, OfficialDaily([_official(c=120.0)], [DAY]))
    assert store.shard_path("2330", DAY).read_bytes() == before
    assert not (tmp_path / "normalized" / "tw_price_daily").exists()


def test_dry_run_writes_nothing(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    store.put_symbol_day("2317", DAY, _bars("2317"))
    snapshot = sorted(p.relative_to(tmp_path).as_posix() + str(p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file())
    off = OfficialDaily([_official(), _official("2317", c=130.0)], [DAY])
    results = reconcile_day(store, off, DAY, CFG, clock=lambda: NOW, write=False)
    assert {r.symbol: r.outcome for r in results} == {"2317": "fail", "2330": "pass"}
    assert snapshot == sorted(p.relative_to(tmp_path).as_posix() + str(p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file())


def test_tampered_shard_or_stale_pass_is_not_visible(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    _run(store, OfficialDaily([_official()], [DAY]))
    assert read_reconciled_rows(store, "2330", DAY)
    store.shard_path("2330", DAY).write_bytes(store.shard_path("2330", DAY).read_bytes() + b"x")
    assert read_reconciled_rows(store, "2330", DAY) == []
    # a day that later fails loses its old pass record
    store2 = _store(tmp_path / "2"); store2.put_symbol_day("2330", DAY, _bars())
    _run(store2, OfficialDaily([_official()], [DAY]))
    _run(store2, OfficialDaily([_official(c=130.0)], [DAY]))
    assert read_reconciled_rows(store2, "2330", DAY) == []


def test_readmission_only_after_later_passing_refetch(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars(close=120.0))
    off = OfficialDaily([_official()], [DAY])
    assert _run(store, off).outcome == "fail"
    later = NOW + timedelta(hours=1)
    stale = readmit_after_refetch(store, off, "2330", DAY, _bars(fetched="2026-11-10T08:00:00Z"), CFG, clock=lambda: later)
    assert stale.outcome == "fail" and stale.reason == "refetch_not_after_quarantine"
    still_bad = readmit_after_refetch(store, off, "2330", DAY, _bars(close=120.0, fetched="2026-11-10T09:30:00Z"), CFG, clock=lambda: later)
    assert still_bad.outcome == "fail" and read_reconciled_rows(store, "2330", DAY) == []
    ok = readmit_after_refetch(store, off, "2330", DAY, _bars(fetched="2026-11-10T09:30:00Z"), CFG,
                               raw_envelope={"raw_rows": [{"a": 1}], "response_content_hash": "rh"}, clock=lambda: later)
    assert ok.outcome == "pass"
    rows = read_reconciled_rows(store, "2330", DAY)
    assert len(rows) == 271 and rows[0]["replication_eligible"] is True
    assert rows[-1]["close"] == 104.0
    assert store.read_rows("2330", DAY) == []  # the store view stays closed; the original shard is intact
    assert json.loads(store.quarantine_path("2330", DAY).read_text())["resolution"] == "refetch_reconciled_pass"
    with pytest.raises(ReconcileError):
        readmit_after_refetch(store, off, "2330", DAY, _bars(fetched="2026-11-10T09:31:00Z"), CFG, clock=lambda: later)


def test_waiver_is_flagged_and_never_replication_evidence(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars(close=120.0))
    _run(store, OfficialDaily([_official()], [DAY]))
    with pytest.raises(ReconcileError):
        record_waiver(store, "2330", DAY, operator="op", reason=" ")
    record_waiver(store, "2330", DAY, operator="op", reason="vendor confirmed odd-lot print", clock=lambda: NOW)
    assert read_reconciled_rows(store, "2330", DAY) == []
    rows = read_reconciled_rows(store, "2330", DAY, include_waived=True)
    assert rows and all(r["waived"] and not r["replication_eligible"] and r["bar_availability_ts"] is None for r in rows)
    assert json.loads(store.quarantine_path("2330", DAY).read_text())["waived"] is True


def test_pass_rate_below_threshold_reports_without_widening(tmp_path):
    store = _store(tmp_path)
    syms = [f"{1000 + i}" for i in range(10)]
    for i, s in enumerate(syms):
        store.put_symbol_day(s, DAY, _bars(s))
    off = OfficialDaily([_official(s, c=104.0 if i < 8 else 130.0) for i, s in enumerate(syms)], [DAY])
    summary = pass_rate_summary(reconcile_day(store, off, DAY, CFG, clock=lambda: NOW), CFG)
    assert summary["pass_rate"] == "0.8" and summary["operator_review_required"] is True
    assert summary["tolerance_widened"] is False and CFG.volume_tolerance_pct == Decimal("0.02")
    assert pass_rate_summary([], CFG)["pass_rate"] is None


def test_config_rejects_auto_widen(tmp_path):
    raw = json.loads((ROOT / "config/intraday_reconciliation.json").read_text())
    raw["auto_widen_tolerance"] = True
    p = tmp_path / "c.json"; p.write_text(json.dumps(raw))
    with pytest.raises(ReconcileError):
        load_config(p)
    assert CFG.price_tolerance_ticks == 1 and CFG.min_coverage == Decimal("0.95") and CFG.max_gap_minutes == 10


def test_official_snapshot_reader_and_report_script_are_read_only(tmp_path):
    store = _store(tmp_path / "store")
    store.put_symbol_day("2330", DAY, _bars())
    snap = tmp_path / "off" / "normalized" / "tw_price_daily" / DAY
    snap.mkdir(parents=True)
    body = {"symbol": "2330", "date": DAY, "open": 100, "high": 110, "low": 95, "close": 104,
            "volume": 27_100_000, "available_time": "2026-11-10T06:30:00Z"}
    (snap / "a.jsonl").write_text(json.dumps({"metadata": {"body": json.dumps(body)}}) + "\n"
                                  + json.dumps({"metadata": {"body": json.dumps({**body, "symbol": "9999", "open": None, "high": None, "low": None, "close": None, "volume": None})}}) + "\n")
    off = OfficialDaily.from_snapshot(tmp_path / "off")
    assert len(off.rows_for("2330", DAY)) == 1 and off.rows_for("9999", DAY) == []
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    out = subprocess.run([sys.executable, str(ROOT / "scripts/report_intraday_reconciliation.py"),
                          "--store-root", str(tmp_path / "store"), "--official-root", str(tmp_path / "off")],
                         capture_output=True, text=True, check=True, cwd=tmp_path)
    report = json.loads(out.stdout)
    assert report["read_only"] and report["summary"]["outcome_counts"] == {"pass": 1}
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def _report(tmp_path, official_rows, official_dates, **kw):
    import importlib.util
    spec = importlib.util.spec_from_file_location("rep", ROOT / "scripts/report_intraday_reconciliation.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    snap = tmp_path / "off" / "normalized" / "tw_price_daily"
    for d in official_dates:
        (snap / d).mkdir(parents=True, exist_ok=True)
    for r in official_rows:
        body = {"symbol": r.symbol, "date": r.date, "open": 100, "high": 110, "low": 95, "close": 104,
                "volume": 27_100_000, "available_time": "2026-11-10T06:30:00Z"}
        with (snap / r.date / "a.jsonl").open("a") as fh:
            fh.write(json.dumps({"metadata": {"body": json.dumps(body)}}) + "\n")
    return mod.build_report(tmp_path / "store", tmp_path / "off", None, None, **kw)


def test_report_surfaces_absent_symbol_entry_and_absent_day_manifest(tmp_path):
    d1, d2, d3 = "2026-11-09", "2026-11-10", "2026-11-11"
    store = _store(tmp_path / "store")
    store.put_symbol_day("2317", d1, _bars("2317", d1, fetched=f"{d1}T08:00:00Z"))
    store.put_symbol_day("2330", d2, _bars("2330", d2, fetched=f"{d2}T08:00:00Z"))
    rows = [_official(s, d) for s, d in
            [("2317", d1), ("2330", d2), ("2317", d2), ("2317", d3), ("2330", d3), ("9999", d2)]]
    before = sorted(str(p) for p in (tmp_path / "store").rglob("*"))
    rep = _report(tmp_path, rows, [d1, d2, d3])
    assert rep["symbol_universe"] == ["2317", "2330"]
    assert rep["per_day_outcomes"][d2].get("minute_missing") == 1   # 2317 entry absent
    assert rep["per_day_outcomes"][d3] == {"minute_missing": 2}     # whole day manifest absent
    assert not any(r["symbol"] == "9999" for r in rep["non_pass"])  # unrelated official symbol ignored
    assert sorted(str(p) for p in (tmp_path / "store").rglob("*")) == before


def _recovered(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars(close=120.0))
    off = OfficialDaily([_official()], [DAY])
    _run(store, off)
    later = NOW + timedelta(hours=1)
    ok = readmit_after_refetch(store, off, "2330", DAY, _bars(fetched="2026-11-10T09:30:00Z"), CFG, clock=lambda: later)
    assert ok.outcome == "pass"
    return store, off, later


def test_requarantine_after_readmit_closes_loader(tmp_path):
    store, _, _ = _recovered(tmp_path)
    assert len(read_reconciled_rows(store, "2330", DAY)) == 271
    store.quarantine_symbol_day("2330", DAY, reason="new_conflicting_provider_rows")
    assert read_reconciled_rows(store, "2330", DAY) == []


def test_interrupted_resolution_fails_closed(tmp_path):
    store, _, _ = _recovered(tmp_path)
    q = store.quarantine_path("2330", DAY)
    rec = json.loads(q.read_text())
    rec.update(resolved_at=None, resolution=None)
    q.write_text(json.dumps(rec))
    assert read_reconciled_rows(store, "2330", DAY) == []


def test_waiver_sidecar_bound_to_quarantine_generation(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars(close=120.0))
    _run(store, OfficialDaily([_official()], [DAY]))
    record_waiver(store, "2330", DAY, operator="op", reason="r", clock=lambda: NOW)
    assert read_reconciled_rows(store, "2330", DAY, include_waived=True)
    store.quarantine_symbol_day("2330", DAY, reason="again")
    assert read_reconciled_rows(store, "2330", DAY, include_waived=True) == []


def test_pass_then_pending_withdraws_pass_but_dry_run_is_read_only(tmp_path):
    store = _store(tmp_path)
    store.put_symbol_day("2330", DAY, _bars())
    assert _run(store, OfficialDaily([_official()], [DAY])).outcome == "pass"
    assert len(read_reconciled_rows(store, "2330", DAY)) == 271
    assert _run(store, OfficialDaily([], []), write=False).outcome == "pending_official"
    assert len(read_reconciled_rows(store, "2330", DAY)) == 271
    assert _run(store, OfficialDaily([], [])).outcome == "pending_official"
    assert read_reconciled_rows(store, "2330", DAY) == []
    store2 = _store(tmp_path / "b")
    store2.put_symbol_day("2330", DAY, _bars())
    _run(store2, OfficialDaily([_official()], [DAY]))
    late = _official(avail="2026-11-10T10:00:00Z")
    assert _run(store2, OfficialDaily([late], [DAY])).outcome == "pending_official"
    assert read_reconciled_rows(store2, "2330", DAY) == []


def test_recovered_day_reconciles_as_pass_with_real_timestamps(tmp_path):
    store, off, later = _recovered(tmp_path)
    before = read_reconciled_rows(store, "2330", DAY)[0]["reconciled_at"]
    ro = _run(store, off, write=False)
    assert ro.outcome == "pass" and ro.unchanged and ro.reconciled_at == before
    assert pass_rate_summary([ro], CFG)["pass_rate"] == "1"
    # official change is revalidated: a now-conflicting official value fails and re-quarantines
    bad = OfficialDaily([_official(c=130.0)], [DAY])
    assert _run(store, bad, write=False).outcome == "fail"
    assert read_reconciled_rows(store, "2330", DAY)
    assert _run(store, bad).outcome == "fail"
    assert read_reconciled_rows(store, "2330", DAY) == []


def test_readmitted_pending_absent_partition_then_official_restored_recovers(tmp_path):
    store, off, later = _recovered(tmp_path)
    assert _run(store, OfficialDaily([], [])).outcome == "pending_official"
    assert read_reconciled_rows(store, "2330", DAY) == []  # loader closed while pending
    res = reconcile_symbol_day(store, off, "2330", DAY, CFG, clock=lambda: later)
    assert res.outcome == "pass" and not res.unchanged
    assert len(read_reconciled_rows(store, "2330", DAY)) == 271


def test_readmitted_future_official_then_restored_recovers(tmp_path):
    store, off, later = _recovered(tmp_path)
    late = OfficialDaily([_official(avail="2999-01-01T00:00:00Z")], [DAY])
    assert _run(store, late).outcome == "pending_official"
    assert read_reconciled_rows(store, "2330", DAY) == []
    assert reconcile_symbol_day(store, off, "2330", DAY, CFG, clock=lambda: later).outcome == "pass"
    assert len(read_reconciled_rows(store, "2330", DAY)) == 271


def test_readmitted_pending_then_failing_official_requarantines_and_refetch_recovers(tmp_path):
    store, off, later = _recovered(tmp_path)
    assert _run(store, OfficialDaily([], [])).outcome == "pending_official"
    bad = OfficialDaily([_official(c=999.0)], [DAY])
    assert reconcile_symbol_day(store, bad, "2330", DAY, CFG, clock=lambda: later).outcome == "fail"
    assert read_reconciled_rows(store, "2330", DAY) == []
    much_later = later + timedelta(hours=2)
    ok = readmit_after_refetch(store, off, "2330", DAY, _bars(fetched="2026-11-10T12:00:00Z"), CFG,
                               clock=lambda: much_later)
    assert ok.outcome == "pass"
    assert len(read_reconciled_rows(store, "2330", DAY)) == 271
