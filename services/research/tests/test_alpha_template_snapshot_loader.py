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
