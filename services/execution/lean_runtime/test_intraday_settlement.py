"""Daily next-session-open planning and settlement (PAPER-DAILY-FILL-TIMING-AND-FLUSH-20261115)."""
import pytest

from services.execution.lean_runtime.intraday_settlement import (
    IntradayWindowError,
    advance_decision,
    pending_record,
    plan_daily_next_open_decision,
    settle,
)


def _plan(decision_time="2026-11-13T19:00:00+08:00", event="2026-11-13T00:00:00+08:00"):
    return plan_daily_next_open_decision(
        event_time=event, bar_availability_ts="2026-11-13T18:30:00+08:00", decision_time=decision_time
    )


def _bar(day, open_, available):
    return {"symbol": "2330.TW", "bar_start": f"{day}T09:00:00+08:00", "bar_frequency": "1d",
            "bar_availability_ts": available, "open": open_}


def test_friday_decision_targets_monday_open():
    plan = _plan()
    assert (plan.decision_day, plan.target_day) == ("2026-11-13", "2026-11-16")
    assert plan.execution_target_bar_start == "2026-11-16T09:00:00+08:00"
    assert plan.bar_frequency == "1d"


def test_bare_trade_date_event_time_is_accepted():
    assert _plan(event="2026-11-13").decision_day == "2026-11-13"


def test_late_decision_rolls_to_the_next_open_still_ahead_never_a_past_open():
    # Data landed after Monday's 08:55 cutoff: Monday's open already printed, so Tuesday's is targeted.
    plan = plan_daily_next_open_decision(
        event_time="2026-11-13", bar_availability_ts="2026-11-16T15:00:00+08:00",
        decision_time="2026-11-16T15:01:00+08:00",
    )
    assert plan.target_day == "2026-11-17"


def test_decision_before_the_bar_is_available_is_rejected():
    with pytest.raises(IntradayWindowError):
        plan_daily_next_open_decision(
            event_time="2026-11-13", bar_availability_ts="2026-11-13T18:30:00+08:00",
            decision_time="2026-11-13T18:00:00+08:00",
        )


def test_availability_before_the_close_is_rejected():
    with pytest.raises(IntradayWindowError):
        plan_daily_next_open_decision(
            event_time="2026-11-13", bar_availability_ts="2026-11-13T12:00:00+08:00",
            decision_time="2026-11-13T19:00:00+08:00",
        )


def test_settles_at_the_official_open_with_source_recorded():
    record = pending_record(_plan(), decision_id="d1", symbol="2330.TW", direction="LONG")
    done = settle(record, [_bar("2026-11-16", 104.0, "2026-11-16T16:00:00+08:00")], now="2026-11-16T16:30:00+08:00")
    assert done["status"] == "settled"
    assert done["fill"]["price"] == 104.0
    assert done["fill"]["price_observed_at"] == "2026-11-16T16:00:00+08:00"
    assert done["fill"]["settlement_mode"] == "delayed_simulated"
    assert done["fill"]["is_live_fill_evidence"] is False


def test_missing_session_stays_pending_then_expires_without_any_price():
    record = pending_record(_plan(), decision_id="d1", symbol="2330.TW", direction="LONG")
    assert settle(record, [], now="2026-11-16T16:30:00+08:00")["status"] == "pending_settlement"
    expired = settle(record, [], now="2026-11-17T09:00:00+08:00")
    assert expired["status"] == "expired_no_price" and expired["fill"] is None
    assert expired["reason"] == "target_session_not_reconciled_by_expiry"


def test_wrong_session_bar_is_never_used():
    record = pending_record(_plan(), decision_id="d1", symbol="2330.TW", direction="LONG")
    friday = _bar("2026-11-13", 99.0, "2026-11-13T18:30:00+08:00")
    assert settle(record, [friday], now="2026-11-16T16:30:00+08:00")["status"] == "pending_settlement"


class _Store:
    def __init__(self):
        self.records = {}

    def get_decision_record(self, key):
        return self.records.get(key)

    def put_decision_record(self, key, value):
        return self.records.setdefault(key, value)


def test_advance_decision_settles_daily_and_does_not_grade():
    store = _Store()
    store.records["d1#intraday-pending"] = pending_record(_plan(), decision_id="d1", symbol="2330.TW", direction="LONG")
    reader = lambda symbol, day, freq: [_bar(day, 104.0, f"{day}T16:00:00+08:00")]  # noqa: E731
    out = advance_decision(store, "d1", reader, now="2026-11-16T16:30:00+08:00")
    assert out["status"] == "settled"
    assert "d1#intraday-grade" not in store.records
