"""Loader tests for the hosted ``normalized/tw_price_daily`` snapshot layout (format fixtures only)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.research.alpha_templates.evaluator import (
    MIN_IC_OBSERVATIONS,
    TaiwanEvaluationDataError,
    compute_snapshot_id,
    compute_template_evaluation_metrics,
    load_tw_price_daily_snapshot,
    snapshot_coverage,
    summarize_universe_provenance,
)


def _rec(sym, day, close, volume=1000.0, avail=None, **over):
    body = {
        "symbol": sym, "date": day, "open": close, "high": close and close + 1, "low": close and close - 1,
        "close": close, "volume": volume, "value": volume and close and volume * close,
        "available_time": avail or f"{day}T14:30:00+08:00",
    }
    body.update(over)
    return {"metadata": {"symbol": sym, "date": day, "body": json.dumps(body), "available_time": body["available_time"]}}


def _write(root: Path, name: str, recs):
    d = root / "normalized" / "tw_price_daily" / f"date={name}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"ingest-{len(list(d.iterdir()))}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def _snap(tmp_path, n=45):
    start = date(2026, 1, 5)
    for i in range(n):
        day = (start + timedelta(days=i)).isoformat()
        _write(tmp_path, day, [_rec("AAA", day, 100 + i), _rec("BBB", day, 50 + i) if i < 5 else _rec("AAA", day, 100 + i)])
    return tmp_path


def test_loads_dedups_and_excludes_short_symbols(tmp_path):
    uni = load_tw_price_daily_snapshot(_snap(tmp_path))
    assert sorted(uni) == ["AAA"]
    assert len(uni["AAA"]["decision_times"]) == 45
    assert snapshot_coverage(tmp_path) == {"AAA": 45, "BBB": 5}
    assert uni["AAA"]["decision_times"][0].endswith("06:30:00Z")


def test_date_only_availability_is_not_midnight(tmp_path):
    _write(tmp_path, "2026-01-05", [_rec("AAA", "2026-01-05", 10, avail="2026-01-05")])
    for i in range(1, 41):
        day = (date(2026, 1, 5) + timedelta(days=i)).isoformat()
        _write(tmp_path, day, [_rec("AAA", day, 10 + i)])
    uni = load_tw_price_daily_snapshot(tmp_path)
    assert uni["AAA"]["decision_times"][0] == "2026-01-05T06:30:00Z"


def test_ohlc_conflict_fails_closed_but_volume_revision_keeps_final(tmp_path):
    _write(tmp_path, "2026-01-05", [_rec("AAA", "2026-01-05", 10, volume=5), _rec("AAA", "2026-01-05", 10, volume=9)])
    for i in range(1, 41):
        day = (date(2026, 1, 5) + timedelta(days=i)).isoformat()
        _write(tmp_path, day, [_rec("AAA", day, 10 + i)])
    assert load_tw_price_daily_snapshot(tmp_path)["AAA"]["features"]["volume"][0]["value"] == 9
    _write(tmp_path, "2026-01-05", [_rec("AAA", "2026-01-05", 11)])
    with pytest.raises(TaiwanEvaluationDataError, match="conflicting duplicate prices"):
        load_tw_price_daily_snapshot(tmp_path)


def test_all_null_no_trade_rows_are_skipped(tmp_path):
    _snap(tmp_path)
    _write(tmp_path, "2026-03-01", [_rec("AAA", "2026-03-01", None, volume=None, open=None, high=None, low=None, value=None)])
    assert len(load_tw_price_daily_snapshot(tmp_path)["AAA"]["decision_times"]) == 45


def test_snapshot_tampering_breaks_provenance(tmp_path):
    uni = load_tw_price_daily_snapshot(_snap(tmp_path))
    sid = compute_snapshot_id(tmp_path / "normalized" / "tw_price_daily")[0]
    assert summarize_universe_provenance(uni)["symbols"]["AAA"]["snapshot_id"] == sid
    f = next((tmp_path / "normalized" / "tw_price_daily").rglob("*.jsonl"))
    f.write_text(f.read_text() + "\n")
    with pytest.raises(TaiwanEvaluationDataError, match="cannot be re-verified"):
        summarize_universe_provenance(uni)


def test_short_history_is_flagged_insufficient_not_estimated():
    closes = [100.0 + i for i in range(30)]
    metrics = compute_template_evaluation_metrics(
        {"AAA": {"factor_values": [float(i % 5) for i in range(30)], "closes": closes}}
    )
    assert metrics["observations_by_horizon"] == {"1d": 29, "5d": 25, "20d": 10}
    assert metrics["insufficient_by_horizon"] == {"1d": False, "5d": False, "20d": True}
    assert metrics["min_ic_observations"] == MIN_IC_OBSERVATIONS
    # numeric absence below the floor, per-symbol and pooled, incl. aliases and derived decay
    assert metrics["pooled_ic"]["20d"] is None
    assert metrics["pooled_ic"]["1d"] is not None
    assert metrics["ic_20d"] is None and metrics["decay"]["20d"] is None
    assert metrics["ic_decay"]["20d"] is None
    assert metrics["hit_rate_by_horizon"]["20d"] is None
    assert metrics["hit_rate_by_horizon"]["1d"] is not None
    assert metrics["mean_symbol_ic"]["20d"] is None
    sym = metrics["per_symbol"]["AAA"]
    assert sym["ic"]["20d"] is None and sym["hit_rate_by_horizon"]["20d"] is None
    assert metrics["per_symbol_ic"]["AAA"]["20d"] is None
    d = metrics["decay"]
    for k in ("ic_change_1d_to_20d", "abs_ic_decay_1d_to_20d", "decay_ratio_20d_over_1d",
              "slope_per_day", "half_life_days"):
        assert d[k] is None, k


def test_zero_observations_yield_null_not_zero():
    metrics = compute_template_evaluation_metrics(
        {"AAA": {"factor_values": [float("nan")] * 30, "closes": [100.0 + i for i in range(30)]}}
    )
    assert all(v == 0 for v in metrics["observations_by_horizon"].values())
    for h in ("1d", "5d", "20d"):
        assert metrics["pooled_ic"][h] is None
        assert metrics["hit_rate_by_horizon"][h] is None
        assert metrics["mean_symbol_ic"][h] is None
        assert metrics["per_symbol"]["AAA"]["ic"][h] is None
    assert metrics["hit_rate"] is None and metrics["ic_1d"] is None


# --- SOURCE-MARKET-STORE-BACKFILL-AND-CLEANUP-20261118-R2: the same bars through DailyBarsReader -------------

def _backfilled(tmp_path, n=45):
    """Run-file snapshot plus its daily bars (backfilled through the real backfill script)."""
    pytest.importorskip("pyarrow")
    from scripts import backfill_daily_bars as bf
    from services.source_ingestion.security_listing import ListingState

    _snap(tmp_path, n)
    state = ListingState(last_success_date="2026-11-17")
    for sym in ("AAA", "BBB"):
        state.allowlist[sym] = {"venue": "TWSE", "category": "x", "cfi": "ESVUFR", "is_warrant": False,
                                "first_seen": "2026-01-01", "last_seen": "2026-11-17"}
    for p in (tmp_path / "normalized" / "tw_price_daily").glob("date=*/*.jsonl"):
        pass
    bf.backfill(tmp_path, state, today="2026-11-17", apply=True)
    return tmp_path


def _ohlcv(uni):
    out = {}
    for sym, entry in uni.items():
        feats = entry["features"]
        out[sym] = {k: [(r["event_time"], r["value"]) for r in feats[k]] for k in ("open", "high", "low", "close", "volume")}
        out[sym]["times"] = entry["decision_times"]
    return out


def test_daily_bars_read_matches_run_file_read_and_rollback_setting(tmp_path, monkeypatch):
    root = _backfilled(tmp_path)
    monkeypatch.delenv("PANTHEON_DAILY_PRICE_READER", raising=False)
    old = load_tw_price_daily_snapshot(root)  # default: the old read
    assert old["AAA"]["provenance"].get("reader") is None
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    new = load_tw_price_daily_snapshot(root)
    assert new["AAA"]["provenance"]["reader"] == "daily_bars"
    assert _ohlcv(new) == _ohlcv(old)  # zero mismatches in time, open, high, low, close, volume
    assert snapshot_coverage(root) == {"AAA": 45, "BBB": 5}
    assert summarize_universe_provenance(new)["governed"] is True
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "run_files")  # rollback state
    assert _ohlcv(load_tw_price_daily_snapshot(root)) == _ohlcv(old)


def test_daily_bars_read_survives_run_file_deletion_and_five_day_window(tmp_path, monkeypatch):
    import shutil

    root = _backfilled(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    shutil.rmtree(root / "normalized")
    uni = load_tw_price_daily_snapshot(root, symbols=["AAA"])
    assert list(uni) == ["AAA"] and len(uni["AAA"]["decision_times"]) == 45
    assert summarize_universe_provenance(uni)["governed"] is True


def test_daily_bars_provenance_tamper_and_unknown_setting_fail_closed(tmp_path, monkeypatch):
    root = _backfilled(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    uni = load_tw_price_daily_snapshot(root)
    manifest = next((root / "daily_bars" / "_manifest").glob("date=*.json"))
    doc = json.loads(manifest.read_text())
    doc["content_digest"] = "0" * 64
    manifest.write_text(json.dumps(doc))
    with pytest.raises(TaiwanEvaluationDataError, match="cannot be re-verified"):
        summarize_universe_provenance(uni)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "typo")
    with pytest.raises(ValueError, match="PANTHEON_DAILY_PRICE_READER"):
        load_tw_price_daily_snapshot(root)


def test_daily_bars_read_pages_symbols_and_dates_within_reader_caps(tmp_path, monkeypatch):
    """600 symbols (> 500 per call) over 3 write days: the loader chunks instead of hitting the cap."""
    pytest.importorskip("pyarrow")
    from scripts import backfill_daily_bars as bf
    from services.source_ingestion.security_listing import ListingState

    state = ListingState(last_success_date="2026-11-17")
    syms = [f"{2000 + i}" for i in range(600)]
    for s in syms:
        state.allowlist[s] = {"venue": "TWSE", "category": "x", "cfi": "ESVUFR", "is_warrant": False,
                              "first_seen": "2026-01-01", "last_seen": "2026-11-17"}
    for i in range(3):
        day = (date(2026, 1, 5) + timedelta(days=i)).isoformat()
        _write(tmp_path, day, [_rec(s, day, 10 + i) for s in syms])
    bf.backfill(tmp_path, state, today="2026-11-17", apply=True)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    assert snapshot_coverage(tmp_path) == {s: 3 for s in sorted(syms)}


def test_cached_reader_sees_a_rewritten_day_and_one_reader_per_root(tmp_path, monkeypatch):
    from services.research.alpha_templates import evaluator as ev
    from services.source_ingestion.daily_bars_store import DailyBarsStore

    root = _backfilled(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    first = load_tw_price_daily_snapshot(root, symbols=["AAA"])
    assert ev._daily_bars_reader(root) is ev._daily_bars_reader(root)
    day = DailyBarsStore(root)
    manifest_dates = sorted(p.name[5:-5] for p in day.manifest_dir.glob("date=*.json"))
    target = manifest_dates[-1]
    rec = _rec("AAA", target, 777.0)
    from scripts.backfill_daily_bars import _Rec, effective_metadata

    day.write_records([_Rec(metadata=effective_metadata(rec), source_id="x")], ingest_run_id="rewrite", default_market="TW")
    again = load_tw_price_daily_snapshot(root, symbols=["AAA"])
    assert first["AAA"]["closes"][-1] != 777.0 and again["AAA"]["closes"][-1] == 777.0  # not served stale
