"""K5: bar-keyed intraday backtest + intraday IC horizons (SD_INTRADAY_MINUTE_BARS §6-§7).

Covers: keying by real bar_start (several bars per date), frequency-correct annualisation,
next-bar-open fills, session-local lookback, flat-by-13:25, replay labelling / source snapshot,
fail-closed unavailable data, strict PIT (positive and negative) and unchanged daily behaviour.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from services.research.alpha_templates.evaluator import (
    INTRADAY_EVALUATION_HORIZONS,
    HORIZON_KEYS,
    IntradayEvaluationUnavailable,
    attach_intraday_metrics,
    compute_intraday_ic_metrics,
)
from services.research.vectorbt.adapter.vectorbt_adapter import (
    BacktestConfig,
    GovernedVectorbtInputAdapter,
    IntradayDataUnavailable,
    VectorbtWorkflowError,
    _artifact_cross_session,
    _bar_features_for_index,
    _closes_window,
    _session_start_indexes,
    bars_per_year,
    build_intraday_replay_dataset,
    plan_intraday_orders,
    run_vectorbt_workflow,
)
from services.source_ingestion.intraday_reconcile import OfficialDaily, load_config, reconcile_day
from services.source_ingestion.tests.test_intraday_reconcile import DAY, NOW, _bars, _official, _store

CEILING = datetime(2026, 11, 12, tzinfo=timezone.utc)
SESSIONS = ("2026-11-09", "2026-11-10")


def _starts(day: str, width: int, count: int | None = None) -> list[datetime]:
    base = datetime.fromisoformat(f"{day}T09:00:00+08:00")
    total = -(-270 // width)
    return [base + timedelta(minutes=width * i) for i in range(count or total)]


def _records(instrument, *, width=5, opens=None, closes=None, avail="2026-11-10T08:00:00Z", days=SESSIONS):
    out, k = [], 0
    for day in days:
        for st in _starts(day, width):
            c = closes(k) if closes else 100.0 + math.sin(k / 3.0) * 3
            o = opens(k) if opens else c
            end = min(st + timedelta(minutes=width), datetime.fromisoformat(f"{day}T13:30:00+08:00"))
            out.append({
                "instrument": instrument, "bar_start": st.isoformat(), "bar_end": end.isoformat(),
                "bar_availability_ts": avail, "open": o, "high": max(o, c) + 1, "low": min(o, c) - 1,
                "close": c, "volume": 1000.0,
            })
            k += 1
    return out


def _dataset(width=5, freq="5m", ceiling=CEILING, **kw):
    return {
        "dataset_id": "ds-intraday", "strategy_id": "S-1", "source_dataset_refs": ["source-ingest:kbar/tw_price_1m"],
        "data_frequency": freq,
        "records": _records("2330", width=width, **kw) + _records("2317", width=width, **kw),
        "replay": {"label": "replay", "clock": "bar_end", "availability_ceiling": ceiling.isoformat(),
                   "source_snapshot": {"source_dataset": "tw_price_1m", "content_sha256": "abc"}},
    }


# --- keying / annualisation -------------------------------------------------------------

def test_bars_keyed_by_bar_start_allow_many_bars_per_date():
    prepared = GovernedVectorbtInputAdapter().prepare(_dataset())
    assert prepared.bars_per_instrument["2330"] == 108 and prepared.is_intraday
    starts = prepared.dates_by_instrument["2330"]
    assert len(set(starts)) == 108 and len({s[:10] for s in starts}) == 2  # many bars on one date
    assert starts[0] == "2026-11-09T09:00:00+08:00"
    # replay decision clock is the bar END, never the start
    assert prepared.decision_clock_by_instrument["2330"][0] == "2026-11-09T09:05:00+08:00"
    assert prepared.dataset_summary()["evidence_mode"] == "replay"


def test_duplicate_instrument_bar_start_rejected():
    ds = _dataset()
    ds["records"].append(dict(ds["records"][0]))
    with pytest.raises(VectorbtWorkflowError, match="duplicate bar"):
        GovernedVectorbtInputAdapter().prepare(ds)


def test_annualisation_by_frequency_and_daily_unchanged():
    assert bars_per_year("daily") == 252 and bars_per_year("1d") == 252
    assert bars_per_year("1m") == 252 * 270
    assert bars_per_year("5m") == 252 * 54
    assert bars_per_year("15m") == 252 * 18
    assert bars_per_year("30m") == 252 * 9
    assert bars_per_year("60m") == 252 * 5  # last bucket is a half bucket but still a bar


def test_unknown_minute_frequency_rejected():
    with pytest.raises(VectorbtWorkflowError, match="not an allowed intraday"):
        GovernedVectorbtInputAdapter().prepare(_dataset(freq="3m"))


# --- fail closed: unavailable, never daily / synthetic ---------------------------------------

def test_daily_style_records_cannot_stand_in_for_minute_bars():
    ds = _dataset()
    for r in ds["records"]:
        r["date"] = r.pop("bar_start")[:10]
    with pytest.raises(IntradayDataUnavailable) as exc:
        GovernedVectorbtInputAdapter().prepare(ds)
    assert exc.value.reason == "missing_bar_start"
    empty = _dataset()
    empty["records"] = []
    with pytest.raises(IntradayDataUnavailable) as exc:
        GovernedVectorbtInputAdapter().prepare(empty)
    assert exc.value.reason == "no_records"


def test_replay_declaration_and_snapshot_required():
    for mutate in (lambda d: d.pop("replay"), lambda d: d["replay"].update(clock="bar_start"),
                   lambda d: d["replay"].update(source_snapshot={}),
                   lambda d: d["replay"].pop("availability_ceiling")):
        ds = _dataset()
        mutate(ds)
        with pytest.raises(VectorbtWorkflowError):
            GovernedVectorbtInputAdapter().prepare(ds)


def test_partial_or_waived_bar_is_unavailable():
    for flag in ("partial", "waived"):
        ds = _dataset()
        ds["records"][3][flag] = True
        with pytest.raises(IntradayDataUnavailable):
            GovernedVectorbtInputAdapter().prepare(ds)


# --- strict PIT: positive and negative -------------------------------------------------------

def test_pit_negative_bar_unreconciled_at_ceiling_is_rejected():
    early = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)  # before the 08:00Z availability stamp
    with pytest.raises(IntradayDataUnavailable) as exc:
        GovernedVectorbtInputAdapter().prepare(_dataset(ceiling=early))
    assert exc.value.reason == "bar_not_available_at_replay_ceiling"
    # positive: same data is accepted when the ceiling is at/after availability
    GovernedVectorbtInputAdapter().prepare(_dataset(ceiling=datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)))


def test_features_at_bar_i_contain_no_later_bar():
    prepared = GovernedVectorbtInputAdapter().prepare(_dataset(closes=lambda k: 100.0 + k))
    for idx in (0, 10, 60):  # across the session boundary too
        feats, decision_time = _bar_features_for_index(prepared, "2330", idx)
        assert len(feats["close"]) == idx + 1 and feats["close"][-1] == 100.0 + idx
        assert decision_time == prepared.decision_clock_by_instrument["2330"][idx]
    # the decision time of bar i never reaches into bar i+1
    c = prepared.decision_clock_by_instrument["2330"]
    assert c[10] < c[11]
    assert c[10] == "2026-11-09T09:55:00+08:00"  # bar 10 = 09:50-09:55: usable from 09:55, never earlier


def test_lookback_resets_per_session_unless_cross_session():
    prepared = GovernedVectorbtInputAdapter().prepare(_dataset(closes=lambda k: float(k + 1)))
    second_open = _session_start_indexes(prepared.dates_by_instrument["2330"])[60]
    assert second_open == 54
    assert _closes_window(prepared, "2330", 56, cross_session=False) == [55.0, 56.0, 57.0]
    assert len(_closes_window(prepared, "2330", 56, cross_session=True)) == 57
    assert _artifact_cross_session({"parameters": {}, "strategy_logic": {}}) is False
    assert _artifact_cross_session({"parameters": {"cross_session": True}, "strategy_logic": {}}) is True


# --- order planning: next-bar-open, flat by 13:25 --------------------------------------------

def test_plan_fills_on_next_bar_and_never_across_session_or_after_flatten():
    starts = [s.isoformat() for s in _starts("2026-11-09", 5)] + [s.isoformat() for s in _starts("2026-11-10", 5)]
    actions = ["HOLD"] * len(starts)
    actions[3] = "BUY"           # decided on bar 3 -> fills at bar 4 open
    actions[53] = "BUY"          # last bar of session 1 (13:25) -> no next bar in session: no fill
    actions[54] = "BUY"          # first bar of session 2 -> fills bar 55
    sizes = plan_intraday_orders(starts, actions, 1000.0)
    assert sizes[4] == 1000.0 and math.isnan(sizes[3])
    assert math.isnan(sizes[53]) is False and sizes[53] == -1000.0  # 13:25 bar open = forced flat
    assert math.isnan(sizes[54])  # nothing fills at the overnight first bar from yesterday's signal
    assert sizes[55] == 1000.0
    assert sizes[107] == -1000.0  # flat again at session 2's flatten bar
    # no order after the flatten bar of a session
    assert all(math.isnan(x) for x in sizes[54:55])
    # overnight holding is only allowed when declared
    carried = plan_intraday_orders(starts, actions, 1000.0, holding="overnight")
    assert carried[54] == 1000.0 and not any(x < 0 for x in carried if not math.isnan(x))
    with pytest.raises(VectorbtWorkflowError):
        plan_intraday_orders(starts, actions, 1.0, holding="forever")


def test_flatten_bar_for_coarse_frequencies_is_at_or_before_1325():
    for width, expected in ((15, "13:15"), (30, "13:00"), (60, "13:00")):
        starts = [s.isoformat() for s in _starts("2026-11-09", width)]
        actions = ["BUY"] + ["HOLD"] * (len(starts) - 1)
        sizes = plan_intraday_orders(starts, actions, 1.0)
        idx = max(i for i, v in enumerate(sizes) if not math.isnan(v) and v < 0)
        assert starts[idx][11:16] == expected


# --- stub engine: next-bar-open, replay label, gross vs net -----------------------------------

def _run(ds, **params):
    cfg = BacktestConfig(strategy_params={"short_window": 2, "long_window": 4, **params}, fees=0.002)
    return run_vectorbt_workflow(ds, config=cfg)


def test_engine_replay_label_snapshot_gross_net_and_annualisation():
    ds = _dataset(closes=lambda k: 100.0 + 5 * math.sin(k / 4.0))
    res = _run(ds)
    agg = res.backtest_result.aggregate_metrics
    assert agg["evidence_mode"] == "replay" and agg["live_or_paper_evidence"] is False
    assert agg["bars_per_year"] == 252 * 54 and agg["bar_frequency"] == "5m"
    assert agg["source_snapshot"]["content_sha256"] == "abc"
    assert agg["mean_gross_total_return"] > agg["mean_net_total_return"]  # costs dominate intraday
    for m in res.backtest_result.per_instrument_metrics.values():
        assert m["fill_rule"] == "next_bar_open" and m["trade_count"] > 0 and m["trade_count"] % 2 == 0  # flat
    bundle, entry = res.artifact_bundle, res.registry_entry
    assert bundle["evidence_mode"] == "replay" and bundle["not_live_or_paper_evidence"] is True
    assert bundle["dataset_summary"]["replay"]["source_snapshot"]["source_dataset"] == "tw_price_1m"
    assert entry["metadata"]["evidence_mode"] == "replay" and entry["metadata"]["live_or_paper_evidence"] is False


def test_fill_uses_next_bar_open_not_signal_bar_close():
    def closes(k):
        return 100.0 + k * 0.5  # steady uptrend -> MA cross long early in each session

    base = _run(_dataset(closes=closes, opens=lambda k: closes(k)))
    # Raising every open by 10 changes fills (entries/exits happen at opens): result must move.
    bumped = _run(_dataset(closes=closes, opens=lambda k: closes(k) + (10.0 if k % 2 else -10.0)))
    a = base.backtest_result.per_instrument_metrics["2330"]["final_portfolio_value"]
    b = bumped.backtest_result.per_instrument_metrics["2330"]["final_portfolio_value"]
    assert a != b
    # Changing only the SIGNAL bar's own open (bar before the first entry fill) must not matter:
    first_signal = 4 - 1  # long_window=4 -> first signal on in-session bar index 3, fill on bar 4
    def opens_a(k):
        return closes(k)
    def opens_b(k):
        return closes(k) + (50.0 if k in (first_signal, 54 + first_signal) else 0.0)
    ra = _run(_dataset(closes=closes, opens=opens_a)).backtest_result.per_instrument_metrics["2330"]
    rb = _run(_dataset(closes=closes, opens=opens_b)).backtest_result.per_instrument_metrics["2330"]
    assert ra["final_portfolio_value"] == rb["final_portfolio_value"]


def test_future_bars_do_not_change_earlier_decisions():
    def closes(k):
        return 100.0 + 5 * math.sin(k / 4.0)

    full = GovernedVectorbtInputAdapter().prepare(_dataset(closes=closes))
    changed = GovernedVectorbtInputAdapter().prepare(
        _dataset(closes=lambda k: closes(k) if k < 80 else closes(k) + 40.0)
    )
    for idx in (10, 40, 79):
        f1, t1 = _bar_features_for_index(full, "2330", idx)
        f2, t2 = _bar_features_for_index(changed, "2330", idx)
        assert f1["close"] == f2["close"] and t1 == t2  # perturbing bars >= 80 is invisible to bar <= 79


def test_daily_behaviour_unchanged():
    days = [(datetime(2026, 1, 1) + timedelta(days=i)).date().isoformat() for i in range(40)]
    recs = [
        {"instrument": inst, "date": d, "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 1000}
        for inst in ("2330", "2317") for i, d in enumerate(days)
    ]
    ds = {"dataset_id": "d", "strategy_id": "s", "source_dataset_refs": ["x"], "records": recs}
    res = run_vectorbt_workflow(ds)
    assert res.prepared_dataset.data_frequency == "daily" and not res.prepared_dataset.is_intraday
    assert "evidence_mode" not in res.backtest_result.aggregate_metrics
    assert "evidence_mode" not in res.artifact_bundle
    assert "replay" not in res.prepared_dataset.dataset_summary()
    assert res.prepared_dataset.dates_by_instrument["2330"][0] == days[0]


# --- loader over reconciled data ----------------------------------------------------------------

def _reconciled_store(tmp_path, symbols=("2330", "2317"), official_close=104.0):
    store = _store(tmp_path)
    for sym in symbols:
        store.put_symbol_day(sym, DAY, _bars(symbol=sym))
    rows = [_official(symbol=s, c=official_close) for s in symbols]
    reconcile_day(store, OfficialDaily(rows, [DAY]), DAY, load_config(), clock=lambda: NOW)
    return store


def test_replay_dataset_from_reconciled_days_runs_and_records_source_snapshot(tmp_path):
    store = _reconciled_store(tmp_path)
    ds = build_intraday_replay_dataset(
        store, dataset_id="d", strategy_id="s", symbols=["2330", "2317"], session_dates=[DAY],
        bar_frequency="5m", availability_ceiling=CEILING,
    )
    snap = ds["replay"]["source_snapshot"]
    assert snap["reconciled_only"] and snap["bars_by_symbol"] == {"2317": 54, "2330": 54} and len(snap["content_sha256"]) == 64
    prepared = GovernedVectorbtInputAdapter().prepare(ds)
    assert prepared.bars_per_instrument == {"2317": 54, "2330": 54}
    # replay ceiling before reconciliation: bars are not obtainable -> unavailable, not daily
    with pytest.raises(IntradayDataUnavailable) as exc:
        build_intraday_replay_dataset(
            store, dataset_id="d", strategy_id="s", symbols=["2330", "2317"], session_dates=[DAY],
            bar_frequency="5m", availability_ceiling=datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc),
        )
    assert exc.value.reason == "no_available_bars"


def test_quarantined_missing_or_daily_request_is_unavailable_not_substituted(tmp_path):
    bad = _reconciled_store(tmp_path / "q", official_close=150.0)  # disagrees with official -> quarantine
    with pytest.raises(IntradayDataUnavailable) as exc:
        build_intraday_replay_dataset(
            bad, dataset_id="d", strategy_id="s", symbols=["2330"], session_dates=[DAY],
            bar_frequency="5m", availability_ceiling=CEILING,
        )
    assert exc.value.reason == "insufficient_clean_history"
    with pytest.raises(IntradayDataUnavailable):  # never stored
        build_intraday_replay_dataset(
            _store(tmp_path / "e"), dataset_id="d", strategy_id="s", symbols=["2330"],
            session_dates=[DAY], bar_frequency="1m", availability_ceiling=CEILING,
        )
    with pytest.raises(VectorbtWorkflowError):  # daily is not served by the intraday builder
        build_intraday_replay_dataset(
            None, dataset_id="d", strategy_id="s", symbols=["2330"], session_dates=[DAY],
            bar_frequency="1d", availability_ceiling=CEILING,
        )


# --- intraday IC horizons ------------------------------------------------------------------------

def _ic_panel(n_days=2, width=5, noise=0.0):
    entry = {"bar_starts": [], "opens": [], "closes": [], "factor_values": []}
    k = 0
    for day in ("2026-11-09", "2026-11-10", "2026-11-11")[:n_days + 1]:
        for st in _starts(day, width):
            c = 100.0 + math.sin(k / 3.0) * 3
            entry["bar_starts"].append(st.isoformat())
            entry["closes"].append(c)
            entry["opens"].append(c)
            entry["factor_values"].append(math.cos(k / 3.0) + noise * ((k * 7919) % 13))
            k += 1
    return {"2330": entry}


def test_intraday_ic_horizons_are_bars_and_reported_next_to_daily():
    metrics = compute_intraday_ic_metrics(_ic_panel(), bar_frequency="5m")
    assert INTRADAY_EVALUATION_HORIZONS == (1, 5, 15, 30)
    assert list(metrics["pooled_ic"]) == ["1b", "5b", "15b", "30b"]
    assert metrics["evidence_mode"] == "replay" and metrics["bars_per_year"] == 252 * 54
    assert metrics["observations_by_horizon"]["1b"] > metrics["observations_by_horizon"]["30b"] > 0
    daily = {"horizons": [1, 5, 20], "ic_1d": 0.1, "pooled_ic": {"1d": 0.1}}
    merged = attach_intraday_metrics(daily, metrics)
    assert merged["ic_1d"] == 0.1 and merged["pooled_ic"] == {"1d": 0.1} and merged["intraday"] is not None
    assert HORIZON_KEYS == ("1d", "5d", "20d")  # daily set unchanged


def test_intraday_forward_returns_never_cross_the_session_end():
    metrics = compute_intraday_ic_metrics(_ic_panel(n_days=1), bar_frequency="5m", horizons=(30,))
    # 2 sessions x 54 bars; a 30-bar window starting at bar t must end inside the same session
    assert metrics["observations_by_horizon"]["30b"] == 2 * (54 - 1 - 29)  # entry at t+1 .. exit at t+30
    crossing = compute_intraday_ic_metrics(_ic_panel(n_days=1), bar_frequency="5m", horizons=(30,), cross_session=True)
    assert crossing["observations_by_horizon"]["30b"] > metrics["observations_by_horizon"]["30b"]


def test_intraday_ic_is_a_function_of_the_next_bar_open_not_the_signal_bar():
    panel = _ic_panel()
    base = compute_intraday_ic_metrics(panel, bar_frequency="5m")
    shifted = _ic_panel()
    # opens[0] is never an entry (entries are open[t+1], t>=0): moving it changes nothing
    shifted["2330"]["opens"][0] += 25.0
    assert compute_intraday_ic_metrics(shifted, bar_frequency="5m")["pooled_ic"] == base["pooled_ic"]
    # but perturbing a bar that IS an entry (open[t+1]) moves the metric
    moved = _ic_panel()
    for i in range(1, len(moved["2330"]["opens"]), 2):
        moved["2330"]["opens"][i] += 7.0 * (1 if i % 4 else -1)
    assert compute_intraday_ic_metrics(moved, bar_frequency="5m")["pooled_ic"] != base["pooled_ic"]


def test_intraday_ic_unavailable_rather_than_daily_fallback():
    with pytest.raises(IntradayEvaluationUnavailable):
        compute_intraday_ic_metrics({}, bar_frequency="5m")
    with pytest.raises(IntradayEvaluationUnavailable):
        compute_intraday_ic_metrics(_ic_panel(), bar_frequency="1d")
    broken = _ic_panel()
    broken["2330"]["factor_values"].pop()
    with pytest.raises(IntradayEvaluationUnavailable):
        compute_intraday_ic_metrics(broken, bar_frequency="5m")
