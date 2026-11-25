"""PAPER-COUNTERFACTUAL-GRADED-ONCE-20261125: each counterfactual row is graded and emitted once.

Real PaperSignalProducer + real decision-record store + PaperRuntimeService over consecutive
daily decisions (Fri 11-13, Mon 11-16, Tue 11-17, Wed 11-18).  Rows are collected at park time
and marked graded at release a session later; the gap used to let two parked decisions carry the
same row, whose second emission conflicted with the stored event.
"""
import json

from services.execution.lean_runtime import paper_signal_producer as psp
from services.execution.lean_runtime.intraday_settlement import (
    PENDING_RECORD_SUFFIX, SETTLEMENT_RECORD_SUFFIX,
)
from services.execution.lean_runtime.test_daily_settlement_integration import OFFICIAL, _setup

OFFICIAL4 = {**OFFICIAL, "2026-11-18": (113.5, 114.0)}
CF = "persona_instrument_counterfactual"


def _cf_events(telemetry):
    return [e for e in telemetry.events if e["event_type"] == CF]


def _run(tmp_path):
    decide, drain, _p, _f, store, producer, binding, service, telemetry = _setup(tmp_path, official=OFFICIAL4)
    days = [("2026-11-13", [100, 110]), ("2026-11-16", [110, 112]), ("2026-11-17", [112, 113]),
            ("2026-11-18", [113, 114])]
    for day, closes in days:
        decide(day, closes)
        drain()
    return store, telemetry, producer


def _ingest(events):
    """The ingest duplicate check: same event_id must be byte-identical, else a conflict."""
    bound = {}
    for event in events:
        body = json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)
        assert bound.setdefault(event["event_id"], body) == body, f"conflict on {event['event_id']}"
    return bound


def test_each_row_is_emitted_once_and_never_conflicts(tmp_path):
    store, telemetry, producer = _run(tmp_path)
    events = _cf_events(telemetry)
    assert events, "the scenario must grade at least one decision"
    bound = _ingest(events)
    assert len(bound) == len(events), "a graded row was emitted more than once"
    assert not producer.degraded_bindings
    # No two parked decisions ever carried the same row (collected once, not just emitted once).
    carried = []
    for key in _keys(store):
        if key.endswith(PENDING_RECORD_SUFFIX):
            carried += [gid for gid, _s in store.get_decision_record(key).get("grading_marks") or ()]
    assert carried and len(carried) == len(set(carried)), carried


def _carried_ids(store):
    carried = []
    for key in _keys(store):
        if key.endswith(PENDING_RECORD_SUFFIX):
            carried += [gid for gid, _s in store.get_decision_record(key).get("grading_marks") or ()]
    return carried


def test_delayed_release_does_not_let_the_next_decision_recollect_the_row(tmp_path):
    """The carrier of a row is not released yet (D+1 official data late): the next decision skips it."""
    official = {k: v for k, v in OFFICIAL4.items() if k != "2026-11-17"}
    decide, drain, _p, _f, store, producer, binding, service, telemetry = _setup(tmp_path, official=official)
    for day, closes in [("2026-11-13", [100, 110]), ("2026-11-16", [110, 112]), ("2026-11-17", [112, 113])]:
        decide(day, closes)
        drain()
    carried = _carried_ids(store)
    assert carried and len(carried) == len(set(carried)), carried
    official["2026-11-17"] = OFFICIAL4["2026-11-17"]
    for day, closes in [("2026-11-18", [113, 114])]:
        decide(day, closes)
        drain()
    carried = _carried_ids(store)
    assert len(carried) == len(set(carried)), carried
    assert len(_ingest(_cf_events(telemetry))) == len(_cf_events(telemetry))


def test_event_content_is_independent_of_the_carrying_signal(tmp_path):
    _store, telemetry, _producer = _run(tmp_path)
    for event in _cf_events(telemetry):
        session = event["metadata"]["graded_session"]
        assert event["created_at"] == f"{session}T00:00:00+00:00"


def test_dropped_carrier_releases_rows_to_grading(tmp_path):
    decide, drain, _p, _f, store, producer, binding, service, telemetry = _setup(tmp_path, official=OFFICIAL4)
    decide("2026-11-13", [100, 110])
    decide("2026-11-16", [110, 112])  # parks the carrier of the 11-13 rows
    ids = [key for key in _keys(store) if "#grading-carry@" in key]
    assert ids, "parking a decision with rows must record the carry"
    carrier_id = store.get_decision_record(ids[0])["carrier_decision_id"]
    # Expire the carrier without releasing its rows.
    store.put_decision_record(carrier_id + SETTLEMENT_RECORD_SUFFIX, {"status": "expired_no_price"})
    decide("2026-11-17", [112, 113])
    decide("2026-11-18", [113, 114])  # releases the 11-17 decision that re-collected the rows
    drain()
    assert _cf_events(telemetry), "rows of an expired carrier must be graded by the next decision"
    _ingest(_cf_events(telemetry))


def _keys(store):
    for attr in ("_decision_records", "_decisions", "_records"):
        if hasattr(store, attr):
            return list(getattr(store, attr))
    raise AssertionError("cannot enumerate the store")


def test_rows_parked_before_the_fix_do_not_conflict_at_release(tmp_path):
    """Two released signals both carrying the same legacy row (no graded_session): the second drops it."""
    store, _t, producer = _run(tmp_path)[0:3]
    out = {"signal_id": "sig-2", "metadata": {psp.COUNTERFACTUAL_ROWS_KEY: [
        {"decision_id": "dec-a", "instrument_id": "eq:2330"}, {"decision_id": "dec-b", "instrument_id": "eq:2330"}]}}
    store.put_decision_record(psp._grading_record_id("dec-a"), {"status": "done", "carried_by_signal_id": "sig-1"})
    psp.PaperSignalProducer._drop_already_graded_rows(store, out)
    assert [r["decision_id"] for r in out["metadata"][psp.COUNTERFACTUAL_ROWS_KEY]] == ["dec-b"]
    # the carrier itself keeps its own rows on a replayed release
    store.put_decision_record(psp._grading_record_id("dec-b"), {"status": "done", "carried_by_signal_id": "sig-2"})
    psp.PaperSignalProducer._drop_already_graded_rows(store, out)
    assert [r["decision_id"] for r in out["metadata"][psp.COUNTERFACTUAL_ROWS_KEY]] == ["dec-b"]
