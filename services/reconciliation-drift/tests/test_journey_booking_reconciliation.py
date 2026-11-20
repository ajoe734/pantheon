"""Booking reconciliation: broker record vs fill vs booked position and cash.

Fixture is the hosted 2026-11-16 sell (tj_29473e86): broker order
c771bc1adfff4f2cbbd709face8c6378 filled 1 @ 2215.0, fee 0 tax 0, position 1 -> 0.
Cash values are synthetic (hosted fill event body was not recorded).
"""

from __future__ import annotations

import json
import tempfile
from unittest import mock

import pytest

from broker_paper_client import BrokerOrderNotFound, BrokerPaperUnavailable
from test_reconciliation_drift_scheduler import (
    _hosted_eval,
    _load_service_module,
    _paper_lifecycle_summary,
)

BROKER_ORDER = {
    "order_id": "c771bc1adfff4f2cbbd709face8c6378",
    "capital_pool_id": "pool-persona-paper-06d4775a9b6a8f63b311",
    "symbol": "2330",
    "qty": 1,
    "side": "sell",
    "status": "filled",
    "fill_qty": 1,
    "fill_price": 2215.0,
    "instrument_id": "eq:2330",
    "kind": "cash_share",
    "fee": 0.0,
    "tax": 0.0,
}
FILL_META = {
    "broker_order_id": "c771bc1adfff4f2cbbd709face8c6378",
    "side": "sell",
    "instrument_id": "eq:2330",
    "kind": "cash_share",
    "multiplier": 1.0,
    "fill_quantity": 1.0,
    "fill_price": 2215.0,
    "fee": 3.0,
    "tax": 6.0,
    "position_quantity_before": 1.0,
    "position_quantity_after": 0.0,
    "cash_before": 100000.0,
    "cash_after": 102206.0,  # +2215 - 3 - 6
}


def _summary(meta_overrides=None, metrics_overrides=None):
    summary = _paper_lifecycle_summary()
    ident = summary["last_lifecycle_identity"]
    ident["metadata"].update(FILL_META)
    ident["metadata"].update(meta_overrides or {})
    ident["metrics"] = {
        "position_qty": 0.0,
        "price": 2215.0,
        "cash": 102206.0,
        **(metrics_overrides or {}),
    }
    summary["committed_position_snapshot"] = ident
    return summary


def _run(summary, *, order=None, error=None, broker_url="http://broker:8102",
         timestamp="2026-07-15T10:10:00Z"):
    svc = _load_service_module(tempfile.mkdtemp())
    evaluation = _hosted_eval("2026-11-16T07:03:47Z", summary)
    fetch = mock.Mock(return_value=order, side_effect=error)
    env = {"PANTHEON_PAPER_BROKER_URL": broker_url}
    with mock.patch.dict("os.environ", env), mock.patch.object(
        svc, "fetch_paper_order", fetch
    ):
        result = svc._scheduled_lifecycle_event(
            summary=summary, evaluation=evaluation, timestamp=timestamp
        )
    return result, fetch


def _failed(event, code):
    assert event["event_type"] == "reconciliation_failed"
    assert event["reason_code"] == code
    return {d["check"]: d for d in event["reason_detail"]}


def test_match_completes_and_lists_each_passed_check() -> None:
    (event, reason), fetch = _run(_summary(), order=BROKER_ORDER)
    assert reason is None
    assert event["event_type"] == "reconciliation_completed"
    detail = {d["check"]: d for d in event["reason_detail"]}
    assert {"broker_fill_qty", "broker_fill_price", "position_change", "cash_change",
            "committed_snapshot_position", "committed_snapshot_cash"} <= set(detail)
    assert all(d["status"] == "passed" for d in detail.values())
    assert detail["cash_change"]["expected"] == pytest.approx(2206.0)
    fetch.assert_called_once()
    assert fetch.call_args.args[1] == "c771bc1adfff4f2cbbd709face8c6378"


def test_broker_quantity_mismatch() -> None:
    (event, _), _ = _run(_summary(), order={**BROKER_ORDER, "fill_qty": 2})
    d = _failed(event, "broker_fill_mismatch")["broker_fill_qty"]
    assert (d["expected"], d["actual"], d["difference"]) == (1.0, 2.0, 1.0)


def test_position_variance() -> None:
    summary = _summary({"position_quantity_after": 1.0}, {"position_qty": 1.0})
    (event, _), _ = _run(summary, order=BROKER_ORDER)
    d = _failed(event, "position_variance")["position_change"]
    assert (d["expected"], d["actual"], d["difference"]) == (-1.0, 0.0, 1.0)


def test_cash_variance() -> None:
    summary = _summary({"cash_after": 102215.0}, {"cash": 102215.0})
    (event, _), _ = _run(summary, order=BROKER_ORDER)
    d = _failed(event, "cash_variance")["cash_change"]
    assert d["expected"] == pytest.approx(2206.0)
    assert d["actual"] == pytest.approx(2215.0)
    assert d["difference"] == pytest.approx(9.0)  # fee 3 + tax 6 not booked


def test_snapshot_must_equal_fill_after_values() -> None:
    (event, _), _ = _run(_summary(metrics_overrides={"cash": 1.0}), order=BROKER_ORDER)
    assert "committed_snapshot_cash" in _failed(event, "cash_variance")


def test_missing_broker_order() -> None:
    (event, _), _ = _run(_summary(), error=BrokerOrderNotFound("x"))
    assert _failed(event, "broker_record_missing")["broker_record"]["actual"] == "missing"


def test_broker_timeout_defers_then_fails_after_sixty_minutes() -> None:
    err = BrokerPaperUnavailable("timed out")
    (event, reason), _ = _run(_summary(), error=err)
    assert event is None and reason == "reconciliation_deferred_broker_unavailable"
    (event, _), _ = _run(_summary(), error=err, timestamp="2026-07-15T11:00:00Z")
    _failed(event, "broker_unavailable_at_reconciliation")


def test_no_broker_url_records_not_configured_and_decides_on_booking() -> None:
    (event, _), fetch = _run(_summary(), broker_url="")
    assert event["event_type"] == "reconciliation_completed"
    assert event["reason_detail"][0] == {"check": "broker_record", "status": "not_configured"}
    fetch.assert_not_called()
    bad = _summary({"cash_after": 0.0}, {"cash": 0.0})
    (event, _), _ = _run(bad, broker_url="")
    _failed(event, "cash_variance")


def test_journey_without_fill_reconciles_as_before() -> None:
    summary = _paper_lifecycle_summary()
    (event, reason), fetch = _run(summary)
    assert event["event_type"] == "reconciliation_completed"
    assert "reason_detail" not in event
    fetch.assert_not_called()
