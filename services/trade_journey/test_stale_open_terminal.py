"""Stale-open journeys get explicit terminal statuses (no_order / unreconciled)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.trade_journey.materializer import (
    JourneyMaterializer,
    TERMINAL_STATUSES,
)
from services.trade_journey.projection_migration import reclassify_stale_open_journeys

T0 = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _events(*stages: tuple[str, str]) -> list[dict]:
    return [
        {
            "event_id": f"e{i}", "journey_id": "j1", "tenant_id": "t", "environment": "dev",
            "occurred_at": (T0 + timedelta(seconds=i)).isoformat().replace("+00:00", "Z"),
            "stage": stage, "stage_status": status,
        }
        for i, (stage, status) in enumerate(stages)
    ]


def _status(events, as_of=None) -> str:
    m = JourneyMaterializer(as_of=as_of)
    m.rebuild(events)
    return m.get("j1", tenant_id="t", environment="dev").snapshot["status"]


LEDGER = (("order_submission", "succeeded"), ("ledger_booking", "succeeded"))
LATE = T0 + timedelta(days=10)


def test_skipped_order_is_no_order_without_deadline():
    assert _status(_events(("trade_decision", "succeeded"), ("order_submission", "skipped"))) == "no_order"


def test_unreconciled_only_after_deadline():
    events = _events(*LEDGER)
    assert _status(events) == "open"
    assert _status(events, as_of=T0 + timedelta(hours=1)) == "open"
    assert _status(events, as_of=LATE) == "unreconciled"


def test_decision_only_past_deadline_is_no_order():
    assert _status(_events(("trade_decision", "succeeded")), as_of=LATE) == "no_order"


def test_reconciled_not_overridden():
    assert _status(_events(*LEDGER, ("reconciliation", "succeeded")), as_of=LATE) == "completed"


def test_new_statuses_terminal_never_completed():
    assert {"no_order", "unreconciled"} <= TERMINAL_STATUSES


def test_reclassifier_bounded_and_resumable():
    remaining = {"n": 25}

    def run(as_of, deadline, size):
        take = min(size, remaining["n"])
        remaining["n"] -= take
        return {"unreconciled": take} if take else {}

    first = reclassify_stale_open_journeys(run, batch_size=10, max_batches=2)
    assert first == {"batches": 2, "reclassified": {"unreconciled": 20}, "complete": False}
    second = reclassify_stale_open_journeys(run, batch_size=10, max_batches=5)
    assert second["complete"] and second["reclassified"] == {"unreconciled": 5}


def test_bff_does_not_flag_new_terminal_statuses_stalled():
    from services.control_plane.bff import trade_journeys as tj  # noqa: PLC0415
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    for status in ("no_order", "unreconciled"):
        assert tj._is_stalled({"status": status, "updated_at": old}, now) is False
    assert tj._is_stalled({"status": "open", "updated_at": old}, now) is True


# --- sweep wiring (no database) -------------------------------------------------

import asyncio  # noqa: E402

from services.trade_journey.lifecycle_projector import StaleOpenSweeper  # noqa: E402


def test_sweeper_runs_one_batch_per_interval_and_survives_failure():
    clock = {"t": 100.0}
    calls: list[int] = []
    fail = {"on": True}

    def run(as_of, deadline, size):
        calls.append(size)
        if fail["on"]:
            raise RuntimeError("statement timeout")
        return {"unreconciled": size}

    sweeper = StaleOpenSweeper(run, interval_seconds=10, batch_size=7, clock=lambda: clock["t"])
    assert asyncio.run(sweeper.maybe_sweep()) is False  # failure swallowed
    assert asyncio.run(sweeper.maybe_sweep()) is False  # not due yet, no extra call
    assert calls == [7]
    clock["t"] += 10
    fail["on"] = False
    assert asyncio.run(sweeper.maybe_sweep()) is True
    assert calls == [7, 7] and sweeper.last_result == {"unreconciled": 7}


def test_bff_unreconciled_is_warning_and_not_in_attention():
    from services.control_plane.bff import trade_journeys as tj  # noqa: PLC0415
    assert tj._severity({"status": "unreconciled"}, []) == "warning"
    assert tj._severity({"status": "no_order"}, []) == "info"
