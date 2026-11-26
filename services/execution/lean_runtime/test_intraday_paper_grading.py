"""K7: next-session paper decision, delayed settlement and grading for intraday bars.

SD_INTRADAY_MINUTE_BARS section 6.4.  D = 2026-06-12 (Fri), D+1 = 2026-06-15 (Mon, the next
governed trading day), D+2 = 2026-06-16.  Everything is Asia/Taipei.
"""
from __future__ import annotations

import json
import copy
from datetime import datetime, timedelta, timezone

import pytest

from services.execution.lean_runtime import intraday_settlement as isl
from services.execution.lean_runtime.instrument_candidates import (
    in_regular_session,
    next_trading_day,
    session_bounds,
    submission_cutoff,
)
from services.execution.lean_runtime.paper_signal_producer import (
    CurrentArtifactStrategy,
    PaperSignalProducer,
    SignalDecisionUnavailable,
)
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.test_current_artifact_signal import _artifact, _binding

TZ = timezone(timedelta(hours=8))
D_EVENT = "2026-06-12T13:30:00+08:00"       # last bar end of D
D_AVAILABLE = "2026-06-12T16:20:00+08:00"   # fetched ~15:50 and reconciled: real stamp
D_DECISION = "2026-06-12T18:00:00+08:00"    # step 3
D1_OPEN = "2026-06-15T09:00:00+08:00"
D1_AVAILABLE = "2026-06-15T16:30:00+08:00"  # D+1 reconciled after its close


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _plan(**over):
    args = dict(event_time=D_EVENT, bar_availability_ts=D_AVAILABLE, decision_time=D_DECISION, bar_frequency="5m")
    args.update(over)
    return isl.plan_next_session_decision(**args)


def _bars(frequency="5m", *, available=D1_AVAILABLE, day="2026-06-15", opens=None, partial=()):
    width = int(frequency[:-1])
    start = _at(f"{day}T09:00:00+08:00")
    out = []
    for i in range(isl.bars_per_session(frequency)):
        begin = start + timedelta(minutes=i * width)
        end = min(begin + timedelta(minutes=width), _at(f"{day}T13:30:00+08:00"))
        px = 100.0 + i
        out.append({
            "symbol": "2330.TW", "bar_frequency": frequency,
            "bar_start": begin.isoformat(), "bar_end": end.isoformat(),
            "open": (opens or {}).get(i, px), "close": px + 0.5,
            "half_bucket": end - begin < timedelta(minutes=width),
            "partial": i in partial, "bar_availability_ts": available,
        })
    return out


def _pending():
    return isl.pending_record(_plan(), decision_id="dec-1", symbol="2330.TW", direction="LONG")


# ---- session helpers (existing governed calendar) --------------------------------------

def test_next_session_skips_weekend_and_session_helper_bounds():
    assert next_trading_day(_at(D_EVENT).date()).isoformat() == "2026-06-15"
    opened, closed = session_bounds("2026-06-15")
    assert (opened.hour, opened.minute, closed.hour, closed.minute) == (9, 0, 13, 30)
    assert submission_cutoff("2026-06-15").isoformat() == "2026-06-15T08:55:00+08:00"
    assert in_regular_session(_at("2026-06-12T10:00:00+08:00"))
    assert not in_regular_session(_at("2026-06-12T18:00:00+08:00"))
    assert not in_regular_session(_at("2026-06-13T10:00:00+08:00"))  # Saturday
    with pytest.raises(ValueError):
        session_bounds("2026-06-13")


# ---- step 3: admission window -----------------------------------------------------------

def test_plan_targets_next_session_first_bar_and_records_timeline():
    plan = _plan()
    assert plan.target_day == "2026-06-15"
    assert plan.execution_target_bar_start == D1_OPEN
    assert plan.window_closes_at == "2026-06-15T08:55:00+08:00"
    assert plan.expires_at == "2026-06-16T08:55:00+08:00"


def test_after_close_bar_is_not_admitted_before_its_availability_ts():
    with pytest.raises(isl.IntradayWindowError) as err:
        _plan(decision_time="2026-06-12T15:55:00+08:00")  # bar exists at 13:30 but was not reconciled yet
    assert err.value.reason == isl.REASON_BAR_NOT_AVAILABLE


def test_availability_is_never_backdated_to_bar_end():
    with pytest.raises(isl.IntradayWindowError):
        _plan(bar_availability_ts="2026-06-12T13:30:00+08:00")  # a bar cannot be available at the close


def test_live_session_decision_is_unavailable_no_live_feed():
    with pytest.raises(isl.IntradayWindowError) as err:
        _plan(decision_time="2026-06-12T11:00:00+08:00")
    assert err.value.reason == "no_live_intraday_feed"


def test_decision_inside_next_session_or_after_cutoff_is_rejected():
    with pytest.raises(isl.IntradayWindowError) as err:
        _plan(decision_time="2026-06-15T08:55:00+08:00")
    assert err.value.reason == isl.REASON_WINDOW_CLOSED
    with pytest.raises(isl.IntradayWindowError) as err:
        _plan(decision_time="2026-06-15T10:00:00+08:00")
    assert err.value.reason == "no_live_intraday_feed"


def test_window_accepts_just_before_cutoff():
    assert _plan(decision_time="2026-06-15T08:54:59+08:00").target_day == "2026-06-15"


# ---- step 4/5: pending, never filled at D, delayed_simulated at the real D+1 open --------

def test_pending_record_has_no_fill_and_target_is_d_plus_1_open():
    rec = _pending()
    assert rec["status"] == isl.STATUS_PENDING and rec["fill"] is None
    assert rec["plan"]["execution_target_bar_start"] == D1_OPEN


def test_d_bars_never_fill_the_decision():
    d_bars = _bars(day="2026-06-12", available=D_AVAILABLE)  # D's own bars incl. 09:00 open and 13:30 close
    out = isl.settle(_pending(), d_bars, now="2026-06-15T17:00:00+08:00")
    assert out["status"] == isl.STATUS_PENDING and out["fill"] is None


def test_intraday_next_session_stays_pending_while_session_runs():
    out = isl.settle(_pending(), _bars(), now="2026-06-15T11:00:00+08:00")  # bars exist but not yet reconciled
    assert out["status"] == isl.STATUS_PENDING


def test_fill_only_after_d_plus_1_reconciled_at_real_first_open():
    bars = _bars(opens={0: 123.4})
    assert isl.settle(_pending(), bars, now="2026-06-15T16:29:59+08:00")["status"] == isl.STATUS_PENDING
    out = isl.settle(_pending(), bars, now="2026-06-15T16:31:00+08:00")
    assert out["status"] == isl.STATUS_SETTLED
    fill = out["fill"]
    assert fill["price"] == 123.4 and fill["bar_start"] == D1_OPEN
    assert fill["price_observed_at"] == D1_AVAILABLE
    assert fill["settlement_mode"] == "delayed_simulated"
    assert fill["is_live_fill_evidence"] is False and fill["execution_latency_claim"] is None


def test_bar_stamped_before_session_close_is_not_a_reconciled_observation():
    early = _bars(available="2026-06-15T09:06:00+08:00")  # backdated: cannot be reconciled during the session
    assert isl.settle(_pending(), early, now="2026-06-15T17:00:00+08:00")["status"] == isl.STATUS_PENDING


def test_partial_first_bucket_is_never_used_for_the_fill():
    out = isl.settle(_pending(), _bars(partial=(0,)), now="2026-06-15T17:00:00+08:00")
    assert out["status"] == isl.STATUS_PENDING


def test_unreconciled_d_plus_1_expires_without_price_or_substitute():
    assert isl.settle(_pending(), [], now="2026-06-16T08:54:00+08:00")["status"] == isl.STATUS_PENDING
    out = isl.settle(_pending(), [], now="2026-06-16T08:55:00+08:00")
    assert out["status"] == isl.STATUS_EXPIRED and out["fill"] is None
    # D's close / open are not substituted even when those bars are all that exist
    out = isl.settle(_pending(), _bars(day="2026-06-12", available=D_AVAILABLE), now="2026-06-17T09:00:00+08:00")
    assert out["status"] == isl.STATUS_EXPIRED and out["fill"] is None


def test_reconciled_after_expiry_is_still_expired():
    late = _bars(available="2026-06-16T09:30:00+08:00")
    assert isl.settle(_pending(), late, now="2026-06-16T10:00:00+08:00")["status"] == isl.STATUS_EXPIRED


def test_expired_decision_has_no_grade():
    expired = isl.settle(_pending(), [], now="2026-06-17T00:00:00+08:00")
    assert isl.grade(expired, _bars(), now="2026-06-17T01:00:00+08:00")["status"] == "not_gradable"


# ---- step 6: grading ----------------------------------------------------------------------

def _settled(frequency="5m", horizon=3, direction="LONG", opens=None):
    plan = _plan(bar_frequency=frequency, horizon_bars=horizon)
    pend = isl.pending_record(plan, decision_id="dec-1", symbol="2330.TW", direction=direction)
    out = isl.settle(pend, _bars(frequency, opens=opens), now="2026-06-15T17:00:00+08:00")
    assert out["status"] == isl.STATUS_SETTLED
    return out


def test_non_daily_decision_is_graded_open_to_horizon_close():
    settled = _settled(horizon=3, opens={0: 100.0})
    graded = isl.grade(settled, _bars(), now="2026-06-15T17:05:00+08:00")
    assert graded["status"] == isl.GRADE_DONE
    assert graded["entry_open"] == 100.0 and graded["exit_close"] == 102.5  # third bar close
    assert graded["gross_return"] == pytest.approx(0.025)
    assert graded["horizon_bar_start"] == "2026-06-15T09:10:00+08:00"
    assert graded["truncated"] is False and graded["graded_at"].startswith("2026-06-15T17:05")


def test_short_grade_is_sign_flipped():
    graded = isl.grade(_settled(direction="SHORT", horizon=1), _bars(), now="2026-06-15T17:05:00+08:00")
    assert graded["signed_return"] == pytest.approx(-graded["gross_return"])


def test_horizon_beyond_session_is_truncated_to_1330():
    settled = _settled(frequency="60m", horizon=9)
    graded = isl.grade(settled, _bars("60m"), now="2026-06-15T17:05:00+08:00")
    assert graded["status"] == isl.GRADE_DONE and graded["truncated"] is True
    assert graded["horizon_bar_start"] == "2026-06-15T13:00:00+08:00"


def test_grade_waits_for_a_reconciled_horizon_bar():
    settled = _settled(horizon=3)
    bars = [b for i, b in enumerate(_bars()) if i != 2]  # horizon bar absent
    assert isl.grade(settled, bars, now="2026-06-15T17:05:00+08:00")["status"] == isl.GRADE_PENDING
    partial = _bars(partial=(2,))  # skipped partial bucket must not shift the grading bar
    assert isl.grade(settled, partial, now="2026-06-15T17:05:00+08:00")["status"] == isl.GRADE_PENDING


def test_wrong_frequency_bars_are_rejected():
    with pytest.raises(isl.IntradayWindowError):
        isl.settle(_pending(), _bars("15m"), now="2026-06-15T17:00:00+08:00")


# ---- store-backed state machine ----------------------------------------------------------

def test_advance_decision_writes_immutable_settlement_then_grade():
    store = InMemoryPendingSignalStore()
    plan = _plan(horizon_bars=2)
    store.put_decision_record("dec-1" + isl.PENDING_RECORD_SUFFIX,
                              isl.pending_record(plan, decision_id="dec-1", symbol="2330.TW", direction="LONG"))
    reads = []

    def reader(symbol, day, freq):
        reads.append((symbol, day, freq))
        return _bars(freq)

    assert isl.advance_decision(store, "dec-1", reader, now="2026-06-15T12:00:00+08:00")["status"] == isl.STATUS_PENDING
    assert store.get_decision_record("dec-1" + isl.SETTLEMENT_RECORD_SUFFIX) is None
    graded = isl.advance_decision(store, "dec-1", reader, now="2026-06-15T17:00:00+08:00")
    assert graded["status"] == isl.GRADE_DONE
    again = isl.advance_decision(store, "dec-1", lambda *a: [], now="2026-06-30T00:00:00+08:00")
    assert again == graded  # first write wins
    assert store.get_decision_record("dec-1" + isl.SETTLEMENT_RECORD_SUFFIX)["status"] == isl.STATUS_SETTLED
    assert isl.advance_decision(store, "missing", reader, now="2026-06-15T17:00:00+08:00")["status"] == "no_pending_record"


# ---- producer wiring --------------------------------------------------------------------

def _minute_artifact(frequency="5m"):
    from services.registry.strategy_artifact import BAR_FREQUENCY_DATASETS
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    art = copy.deepcopy(_sample_factor_rule_artifact())
    art["parameters"]["bar_frequency"] = frequency
    art["parameters"]["symbols"] = ["2330.TW"]
    art["strategy_logic"]["inputs"] = [
        {"name": "px", "dataset": BAR_FREQUENCY_DATASETS[frequency], "field": "close", "availability_lag_days": 0}
    ]
    node = {"op": "ts_mean", "args": [{"ref": "px"}, {"const_param": "z_window"}]}
    art["strategy_logic"]["signal"] = node
    art["strategy_logic"]["entry"]["long_when"] = {"op": "gt", "args": [node, {"const_param": "entry_z"}]}
    return art


def _intraday_binding(frequency="5m", day="2026-06-12", **snapshot):
    from services.registry.strategy_artifact import BAR_FREQUENCY_DATASETS

    binding = _binding(_minute_artifact(frequency), binding_id="rb-intraday", include_market_input=False)
    binding["symbol"] = "2330.TW"
    dataset = BAR_FREQUENCY_DATASETS[frequency]
    binding["market_input"] = {
        "symbol": "2330.TW", "closes": [100.0, 110.0, 120.0, 130.0, 140.0], "dataset": dataset,
        "features": {"px": [
            {"event_time": f"{day}T13:{m:02d}:00+08:00", "available_time": f"{day}T16:20:00+08:00",
             "value": 100.0 + m, "dataset": dataset, "field": "close"}
            for m in (15, 20, 25, 30)
        ]},
        "source_ref": "source-ingest://snapshots/s1", "snapshot_id": "s1",
        "event_time": f"{day}T13:30:00+08:00", "observed_at": f"{day}T16:20:00+08:00", **snapshot,
    }
    return binding


def _produce(binding, now):
    return CurrentArtifactStrategy()(binding, now)


def test_producer_emits_pending_settlement_decision_with_availability_and_target():
    out = _produce(_intraday_binding(), D_DECISION)
    md = out["metadata"]
    assert md["market_input_bar_frequency"] == "5m"
    assert md["bar_availability_ts"] == D_AVAILABLE
    assert md["execution_target_bar_start"] == D1_OPEN
    assert md["settlement_status"] == "pending_settlement" and md["settlement_mode"] == "delayed_simulated"
    assert md["market_input_bar"] == D_EVENT  # intraday key stays the exact event_time
    assert out["intraday_settlement"]["fill"] is None
    assert out["intraday_settlement"]["plan"]["horizon_bars"] == 3  # the artifact hold_bars


def test_producer_live_intraday_is_no_live_intraday_feed():
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(_intraday_binding(), "2026-06-15T10:00:00+08:00")
    assert err.value.code == "no_live_intraday_feed"


def test_producer_does_not_admit_bar_before_availability_ts():
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(_intraday_binding(), "2026-06-12T15:00:00+08:00")
    assert err.value.code == isl.REASON_BAR_NOT_AVAILABLE


def test_producer_rejects_daily_dataset_for_an_intraday_declaration():
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(_intraday_binding(dataset="tw_price_daily"), D_DECISION)
    assert err.value.code == "market_input_frequency_mismatch"
    binding = _intraday_binding()
    del binding["market_input"]["dataset"]  # undeclared dataset is not assumed to be minute data
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(binding, D_DECISION)
    assert err.value.code == "market_input_frequency_mismatch"


def test_producer_rejects_other_minute_dataset():
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(_intraday_binding("5m", dataset="tw_price_1m"), D_DECISION)
    assert err.value.code == "market_input_frequency_mismatch"


def test_daily_declaration_rejects_minute_snapshot_and_keeps_daily_path():
    art = _artifact()
    binding = _binding(art, binding_id="rb-daily", include_market_input=False)
    binding["market_input"] = {
        "symbol": "AAPL.US", "closes": [100.0, 110.0], "source_ref": "x", "observed_at": "2026-06-12T10:30:00Z",
        "event_time": "2026-06-12T00:00:00+08:00",
    }
    out = _produce(binding, "2026-06-12T10:30:00Z")
    assert "intraday_settlement" not in out and out["metadata"]["market_input_bar"] == "2026-06-12"
    binding["market_input"]["dataset"] = "tw_price_1m"
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(binding, "2026-06-12T10:30:00Z")
    assert err.value.code == "market_input_frequency_mismatch"


def _decision_ids(store):
    return [k[: -len(isl.PENDING_RECORD_SUFFIX)] for k in store._decision_records
            if k.endswith(isl.PENDING_RECORD_SUFFIX)]


def _producer(store, reader=None):
    return PaperSignalProducer(store_for=lambda _: store, strategy=CurrentArtifactStrategy(),
                               intraday_bar_reader=reader or (lambda s, d, f, now: []))


def _reader(bars_by_day=None, calls=None):
    def read(symbol, day, freq, now):
        if calls is not None:
            calls.append((symbol, day, freq, now.isoformat()))
        return (bars_by_day or {}).get(day, [])
    return read


def test_mixed_daily_and_minute_bindings_do_not_cross_contaminate():
    store = InMemoryPendingSignalStore()
    producer = _producer(store)
    assert producer.produce(_intraday_binding(), D_DECISION) == 0  # parked, never queued
    daily = _binding(_artifact(), binding_id="rb-daily-mixed", include_market_input=False)
    daily["market_input"] = {"symbol": "AAPL.US", "closes": [100.0, 110.0], "source_ref": "x",
                             "observed_at": "2026-06-12T10:30:00Z", "event_time": "2026-06-12T00:00:00+08:00"}
    assert producer.produce(daily, "2026-06-12T10:30:00Z") == 1
    [only] = store.get_pending()
    assert only["metadata"]["market_input_bar"] == "2026-06-12"
    assert "settlement_status" not in only["metadata"]
    assert len(_decision_ids(store)) == 1


def test_pending_intraday_decision_is_never_executable_before_settlement():
    """D close must never become an order: the queue stays empty and the consumer idle."""
    from unittest.mock import MagicMock
    from services.execution.lean_runtime.signal_consumer import SignalConsumer

    store = InMemoryPendingSignalStore()
    producer = _producer(store)
    assert producer.produce(_intraday_binding(), D_DECISION) == 0
    assert store.get_pending() == []
    [decision_id] = _decision_ids(store)
    pend = store.get_decision_record(decision_id + isl.PENDING_RECORD_SUFFIX)
    assert pend["status"] == "pending_settlement" and pend["fill"] is None
    algo = MagicMock()
    for _ in range(3):
        SignalConsumer(store_client=store).drain(algo)
    assert not algo.method_calls or all("Submit" not in c[0] and "Order" not in c[0]
                                        for c in algo.method_calls)
    # still pending through D+1 while no reconciled D+1 data exists
    for now in ("2026-06-15T08:00:00+08:00", "2026-06-15T10:00:00+08:00", "2026-06-15T15:00:00+08:00"):
        assert producer.settle_pending_intraday(_intraday_binding(), now) == 0
    assert store.get_pending() == []
    assert store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX) is None


def test_settled_decision_is_released_once_at_the_real_d_plus_1_open():
    store = InMemoryPendingSignalStore()
    calls = []
    producer = _producer(store, _reader({"2026-06-15": _bars("5m", opens={0: 101.25})}, calls))
    binding = _intraday_binding()
    assert producer.produce(binding, D_DECISION) == 0
    [decision_id] = _decision_ids(store)
    assert producer.settle_pending_intraday(binding, "2026-06-15T10:00:00+08:00") == 0
    assert producer.settle_pending_intraday(binding, "2026-06-15T17:00:00+08:00") == 1
    [signal] = store.get_pending()
    md = signal["metadata"]
    assert md["market_price"] == 101.25 and md["market_data"]["price"] == 101.25  # not D close 140
    assert md["settlement_status"] == "settled" and md["settlement_mode"] == "delayed_simulated"
    assert md["price_observed_at"] == D1_AVAILABLE
    assert md["is_live_fill_evidence"] is False
    from services.execution.lean_runtime.executor import _signal_market_price
    assert _signal_market_price(signal) == 101.25
    settled = store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX)
    assert settled["status"] == "settled" and settled["fill"]["price"] == 101.25
    grade = store.get_decision_record(decision_id + isl.GRADE_RECORD_SUFFIX)
    assert grade["status"] == isl.GRADE_DONE and grade["entry_open"] == 101.25
    assert ("2330.TW", "2026-06-15", "5m") == calls[-1][:3]
    # idempotent: a later tick neither re-enqueues nor re-settles
    assert producer.settle_pending_intraday(binding, "2026-06-15T17:30:00+08:00") == 0


def test_missing_or_quarantined_d_plus_1_expires_without_an_order():
    store = InMemoryPendingSignalStore()
    producer = _producer(store, _reader({}))  # reconciled-only reader: nothing reconciled
    binding = _intraday_binding()
    assert producer.produce(binding, D_DECISION) == 0
    [decision_id] = _decision_ids(store)
    assert producer.settle_pending_intraday(binding, "2026-06-16T08:00:00+08:00") == 0
    assert store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX) is None
    assert producer.settle_pending_intraday(binding, "2026-06-16T08:56:00+08:00") == 0
    expired = store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX)
    assert expired["status"] == isl.STATUS_EXPIRED and expired["fill"] is None
    assert store.get_pending() == []
    assert store.get_decision_record(decision_id + isl.GRADE_RECORD_SUFFIX) is None


def test_reader_fault_leaves_the_decision_pending_not_expired():
    store = InMemoryPendingSignalStore()

    def broken(symbol, day, freq, now):
        raise RuntimeError("store root not configured")

    producer = _producer(store, broken)
    binding = _intraday_binding()
    producer.produce(binding, D_DECISION)
    [decision_id] = _decision_ids(store)
    assert producer.settle_pending_intraday(binding, "2026-06-16T09:30:00+08:00") == 0
    assert store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX) is None
    assert "intraday_settlement_unavailable" in producer.degraded_bindings["rb-intraday"]


def test_default_reader_is_reconciled_only_and_empty_without_reconciled_rows(tmp_path, monkeypatch):
    from services.execution.lean_runtime.paper_signal_producer import reconciled_intraday_bar_reader

    monkeypatch.setenv("INTRADAY_STORE_ROOT", str(tmp_path))
    assert reconciled_intraday_bar_reader("2330.TW", "2026-06-15", "5m", _at("2026-06-15T17:00:00+08:00")) == []


def test_snapshot_only_history_fails_closed_without_invented_event_times():
    """The public snapshot has no per-observation timestamps: no history is fabricated."""
    binding = _intraday_binding()
    del binding["market_input"]["features"]
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(binding, D_DECISION)  # the 4-bar window needs history the snapshot cannot date
    assert err.value.code == "market_input_invalid"


def test_projection_emits_only_the_last_real_observation():
    from services.execution.lean_runtime.paper_signal_producer import _project_snapshot_closes_as_features

    feature_map: dict = {}
    raw = {"event_time": D_EVENT, "observed_at": D_AVAILABLE}
    _project_snapshot_closes_as_features(feature_map, _minute_artifact("60m"), "60m", [1.0, 2.0, 3.0], raw)
    [obs] = feature_map["px"]
    assert obs["event_time"] == D_EVENT and obs["available_time"] == D_AVAILABLE and obs["value"] == 3.0


def _reconciled_projection(tmp_path):
    from services.source_ingestion.intraday_reconcile import OfficialDaily, load_config, reconcile_day
    from services.source_ingestion.requirement_state import LatestMarketSnapshotStore
    from services.source_ingestion.tests.test_intraday_reconcile import DAY, NOW, _bars, _official, _store

    istore = _store(tmp_path / "intraday")
    istore.put_symbol_day("2330", DAY, _bars())
    snap = LatestMarketSnapshotStore(tmp_path / "snapshots.jsonl")
    results = reconcile_day(istore, OfficialDaily([_official()], [DAY]), DAY, load_config(),
                            symbols=["2330"], clock=lambda: NOW, snapshot_store=snap)
    assert [r.outcome for r in results] == ["pass"]
    return istore, snap, results[0]


def test_real_reconciled_projection_with_closing_auction_bar_is_admitted(tmp_path):
    """K3/K4 1m snapshot: last bar_end is 13:31 (closing auction); availability is the reconcile stamp."""
    from services.execution.lean_runtime.paper_signal_producer import _market_input_for_binding

    _, snap, res = _reconciled_projection(tmp_path)
    public = snap.get("2330", "tw_price_1m").to_public_dict()
    assert public["event_time"] == "2026-11-10T05:31:00Z"  # 13:31 +08
    assert public["observed_at"] == res.bar_availability_ts
    binding = _binding(_minute_artifact("1m"), binding_id="rb-real", include_market_input=False)
    binding["symbol"] = "2330"
    binding["market_input"] = public
    out = _market_input_for_binding(binding, {}, strategy_artifact=_minute_artifact("1m"),
                                    now_iso="2026-11-10T18:00:00+08:00")
    assert out["intraday_plan"]["bar_event_time"].startswith("2026-11-10T13:31:00")
    assert out["intraday_plan"]["target_day"] == "2026-11-11"
    assert out["bar_availability_ts"] == res.bar_availability_ts
    [obs] = out["features"]["px"]
    assert obs["event_time"].startswith("2026-11-10T13:31:00") and obs["value"] == 104.0
    # a bar ending after the closing auction minute is still refused
    with pytest.raises(isl.IntradayWindowError):
        isl.plan_next_session_decision(event_time="2026-11-10T13:32:00+08:00",
                                       bar_availability_ts="2026-11-10T17:00:00+08:00",
                                       decision_time="2026-11-10T18:00:00+08:00", bar_frequency="1m")


def test_default_reader_reads_native_shard_for_execution_symbols(tmp_path, monkeypatch):
    from services.execution.lean_runtime.paper_signal_producer import reconciled_intraday_bar_reader

    istore, _, _ = _reconciled_projection(tmp_path)
    monkeypatch.setenv("INTRADAY_STORE_ROOT", str(istore.root))
    now = _at("2026-11-12T00:00:00+08:00")
    for symbol in ("2330", "2330.TW", "2330.TWSE"):
        bars = reconciled_intraday_bar_reader(symbol, "2026-11-10", "1m", now)
        assert len(bars) == 271, symbol
        assert {b["symbol"] for b in bars} == {symbol}  # execution identity is preserved


def test_deferred_grading_continues_after_the_decision_was_enqueued():
    """First open settles and enqueues once; the horizon bar arrives later -> grade on tick two."""
    store = InMemoryPendingSignalStore()
    full = _bars("5m")
    available = {"bars": [b for b in full if b["bar_start"] < "2026-06-15T09:10:00+08:00"]}  # horizon (3rd) bar absent

    def read(symbol, day, freq, now):
        return available["bars"] if day == "2026-06-15" else []

    producer = _producer(store, read)
    binding = _intraday_binding()
    assert producer.produce(binding, D_DECISION) == 0
    [decision_id] = _decision_ids(store)
    assert producer.settle_pending_intraday(binding, "2026-06-15T17:00:00+08:00") == 1
    assert store.get_decision_record(decision_id + isl.GRADE_RECORD_SUFFIX) is None
    available["bars"] = full
    assert producer.settle_pending_intraday(binding, "2026-06-15T17:30:00+08:00") == 0  # no re-enqueue
    grade = store.get_decision_record(decision_id + isl.GRADE_RECORD_SUFFIX)
    assert grade is not None and grade["status"] == isl.GRADE_DONE
    assert len(store.get_pending()) == 1


def test_missing_intraday_bar_is_signal_decision_unavailable(monkeypatch):
    import urllib.error
    import urllib.request

    binding = _intraday_binding()
    del binding["market_input"]
    binding["market_data_policy"] = {"owner": "source-ingest", "contract": "latest_stored_normalized",
                                     "max_age_seconds": 86400, "minimum_closes": 2}
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", "http://source.invalid")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(binding, D_DECISION)
    assert err.value.code == "intraday_bar_missing"
    assert seen and seen[0].endswith("symbol=2330.TW&frequency=5m")


def test_daily_url_is_unchanged(monkeypatch):
    import urllib.error
    import urllib.request

    binding = _binding(_artifact(), binding_id="rb-daily-url", include_market_input=False)
    binding["market_data_policy"] = {"owner": "source-ingest", "contract": "latest_stored_normalized",
                                     "max_age_seconds": 86400, "minimum_closes": 2}
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    monkeypatch.setenv("PANTHEON_SOURCE_INGEST_URL", "http://source.invalid")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(binding, "2026-06-12T10:30:00Z")
    assert err.value.code == "market_input_unavailable"
    assert seen == ["http://source.invalid/api/source-ingest/snapshots/latest?symbol=AAPL.US"]


def test_features_available_after_the_decision_clock_are_not_visible():
    binding = _intraday_binding()
    for obs in binding["market_input"]["features"]["px"]:
        obs["available_time"] = "2026-06-12T19:00:00+08:00"  # reconciled after the 18:00 decision
    with pytest.raises(SignalDecisionUnavailable) as err:
        _produce(binding, D_DECISION)
    assert err.value.code == "market_input_invalid"


# ---- review fixes: durable discovery across long holidays, 1m closing-auction horizon ----

LONG_D = "2026-02-11"
LONG_D1 = "2026-02-23"  # governed TWSE calendar: 12 calendar days later


def _long_holiday_setup():
    binding = _intraday_binding(day=LONG_D)
    decision = f"{LONG_D}T18:00:00+08:00"
    store = InMemoryPendingSignalStore()
    return binding, decision, store


def test_long_holiday_decision_is_settled_after_more_than_eight_calendar_days():
    binding, decision, store = _long_holiday_setup()
    bars = _bars(day=LONG_D1, available=f"{LONG_D1}T16:30:00+08:00")
    producer = _producer(store, lambda s, d, f, now: bars if d == LONG_D1 else [])
    assert producer.produce(binding, decision) == 0
    [decision_id] = _decision_ids(store)
    assert store.get_decision_record(decision_id + isl.PENDING_RECORD_SUFFIX)["plan"]["target_day"] == LONG_D1
    assert producer.settle_pending_intraday(binding, f"{LONG_D1}T17:00:00+08:00") == 1
    settled = store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX)
    assert settled["status"] == isl.STATUS_SETTLED
    assert store.get_decision_record(decision_id + isl.GRADE_RECORD_SUFFIX)["status"] == isl.GRADE_DONE


def test_pending_decision_is_discovered_by_a_restarted_producer_beyond_eight_days():
    binding, decision, store = _long_holiday_setup()
    producer = _producer(store)
    producer.produce(binding, decision)
    [decision_id] = _decision_ids(store)
    bars = _bars(day=LONG_D1, available=f"{LONG_D1}T16:30:00+08:00")
    restarted = _producer(store, lambda s, d, f, now: bars if d == LONG_D1 else [])
    assert restarted.settle_pending_intraday(binding, f"{LONG_D1}T17:00:00+08:00") == 1
    assert store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX)["status"] == isl.STATUS_SETTLED


def test_long_holiday_missing_price_expires_without_enqueue():
    binding, decision, store = _long_holiday_setup()
    producer = _producer(store)
    producer.produce(binding, decision)
    [decision_id] = _decision_ids(store)
    assert producer.settle_pending_intraday(binding, "2026-02-24T09:00:00+08:00") == 0
    settled = store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX)
    assert settled["status"] == isl.STATUS_EXPIRED
    assert len(store.get_pending()) == 0


def _auction_bars(last_close, count):
    out = []
    for i in range(count):
        begin = _at("2026-11-10T09:00:00+08:00") + timedelta(minutes=i)
        out.append({"symbol": "2330", "bar_frequency": "1m", "bar_start": begin.isoformat(),
                    "bar_end": (begin + timedelta(minutes=1)).isoformat(), "open": 100.0,
                    "close": last_close if i == count - 1 else 100.5, "half_bucket": False, "partial": False,
                    "bar_availability_ts": "2026-11-10T17:00:00+08:00"})
    return out


@pytest.mark.parametrize("horizon,count,expected_close,truncated", [
    (270, 271, 100.5, False), (271, 271, 104.0, False), (999, 271, 104.0, True)])
def test_1m_horizon_counts_the_real_closing_auction_bar(horizon, count, expected_close, truncated):
    plan = isl.plan_next_session_decision(
        event_time="2026-11-09T13:31:00+08:00", bar_availability_ts="2026-11-09T17:00:00+08:00",
        decision_time="2026-11-09T18:00:00+08:00", bar_frequency="1m", horizon_bars=horizon)
    pend = isl.pending_record(plan, decision_id="d1", symbol="2330", direction="LONG")
    bars = _auction_bars(expected_close, count)
    now = _at("2026-11-11T09:00:00+08:00")
    settled = isl.settle(pend, bars, now=now)
    assert settled["status"] == isl.STATUS_SETTLED
    out = isl.grade(settled, bars, now=now)
    assert out["status"] == isl.GRADE_DONE and out["exit_close"] == expected_close
    assert out["truncated"] is truncated
    assert out["gross_return"] == pytest.approx(expected_close / 100.0 - 1.0)


def test_1m_horizon_271_through_real_reconciled_reader(tmp_path, monkeypatch):
    from services.execution.lean_runtime.paper_signal_producer import reconciled_intraday_bar_reader

    istore, _, _ = _reconciled_projection(tmp_path)
    monkeypatch.setenv("INTRADAY_STORE_ROOT", str(istore.root))
    now = _at("2026-11-12T00:00:00+08:00")
    bars = reconciled_intraday_bar_reader("2330", "2026-11-10", "1m", now)
    assert len(bars) == 271
    for horizon, close in ((271, 104.0), (500, 104.0)):
        plan = isl.plan_next_session_decision(
            event_time="2026-11-09T13:31:00+08:00", bar_availability_ts="2026-11-09T17:00:00+08:00",
            decision_time="2026-11-09T18:00:00+08:00", bar_frequency="1m", horizon_bars=horizon)
        settled = isl.settle(isl.pending_record(plan, decision_id="d", symbol="2330", direction="LONG"),
                             bars, now=now)
        out = isl.grade(settled, bars, now=now)
        assert out["status"] == isl.GRADE_DONE and out["exit_close"] == close == bars[-1]["close"]


def test_interrupted_pending_persistence_recovers_on_restart_and_settles():
    binding = _intraday_binding()
    store = InMemoryPendingSignalStore()
    real_put = store.put_decision_record
    state = {"armed": True}

    def put(key, value):  # fault between the index write and the pending write
        if state["armed"] and key.endswith(isl.PENDING_RECORD_SUFFIX):
            state["armed"] = False
            raise RuntimeError("injected persistence fault")
        return real_put(key, value)
    store.put_decision_record = put
    with pytest.raises(RuntimeError):
        _producer(store).produce(binding, D_DECISION)
    assert _decision_ids(store) == []
    # restart + retry completes both idempotent writes
    _producer(store).produce(binding, D_DECISION)
    [decision_id] = _decision_ids(store)
    bars = {"2026-06-15": _bars("5m", opens={0: 101.25})}
    restarted = _producer(store, _reader(bars))
    assert restarted.settle_pending_intraday(binding, "2026-06-15T17:00:00+08:00") == 1
    assert store.get_decision_record(decision_id + isl.SETTLEMENT_RECORD_SUFFIX)["status"] == isl.STATUS_SETTLED


def test_pending_without_index_from_old_ordering_is_repaired_on_retry():
    binding = _intraday_binding()
    store = InMemoryPendingSignalStore()
    _producer(store).produce(binding, D_DECISION)
    [decision_id] = _decision_ids(store)
    for k in [k for k in store._decision_records if k.startswith(isl.INDEX_RECORD_PREFIX)]:
        del store._decision_records[k]
    producer = _producer(store, _reader({"2026-06-15": _bars("5m", opens={0: 101.25})}))
    assert producer.settle_pending_intraday(binding, "2026-06-15T17:00:00+08:00") == 0
    producer.produce(binding, D_DECISION)
    assert producer.settle_pending_intraday(binding, "2026-06-15T17:00:00+08:00") == 1
