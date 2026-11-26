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
    assert event["metadata"]["reason_code"] == code
    return {d["check"]: d for d in event["metadata"]["reason_detail"]}


def test_match_completes_and_lists_each_passed_check() -> None:
    (event, reason), fetch = _run(_summary(), order=BROKER_ORDER)
    assert reason is None
    assert event["event_type"] == "reconciliation_completed"
    detail = {d["check"]: d for d in event["metadata"]["reason_detail"]}
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
    assert event["metadata"]["reason_detail"][0] == {"check": "broker_record", "status": "not_configured"}
    fetch.assert_not_called()
    bad = _summary({"cash_after": 0.0}, {"cash": 0.0})
    (event, _), _ = _run(bad, broker_url="")
    _failed(event, "cash_variance")
    cash_check = next(c for c in event["metadata"]["reason_detail"] if c["check"] == "cash_change")
    assert cash_check["difference"] == -102206.0
    assert cash_check["delta"] == -102206.0


def test_journey_without_fill_reconciles_as_before() -> None:
    summary = _paper_lifecycle_summary()
    (event, reason), fetch = _run(summary)
    assert event["event_type"] == "reconciliation_completed"
    assert "reason_detail" not in event["metadata"]
    fetch.assert_not_called()

    # Legacy in-process non-broker fill snapshots carry ledger_committed/fill_id/source_fill_event_id
    # with submitted_to_broker=False and no booking-evidence keys; they reconcile as before.
    legacy_summary = _paper_lifecycle_summary()
    legacy_summary["last_lifecycle_identity"]["metadata"].update(
        {
            "fill_id": "paper-fill-legacy-001",
            "source_fill_event_id": "legacy-fill-event-001",
            "ledger_committed": True,
            "submitted_to_broker": False,
        }
    )
    legacy_summary["committed_position_snapshot"] = legacy_summary["last_lifecycle_identity"]
    (legacy_event, legacy_reason), fetch_legacy = _run(legacy_summary)
    assert legacy_reason is None
    assert legacy_event["event_type"] == "reconciliation_completed"
    assert "reason_detail" not in legacy_event["metadata"]
    fetch_legacy.assert_not_called()


def test_incomplete_booking_evidence_is_classified_distinctly_from_accounting_variance() -> None:
    # 1. Missing cash_before/cash_after on a fill must report reconciliation_evidence_incomplete, never cash_variance
    missing_cash = _summary({"cash_before": None, "cash_after": None})
    (event, reason), _ = _run(missing_cash, order=BROKER_ORDER)
    assert reason is None
    _failed(event, "reconciliation_evidence_incomplete")
    cash_check = next(c for c in event["metadata"]["reason_detail"] if c["check"] == "cash_change")
    assert cash_check["status"] == "unverified"
    assert cash_check["difference"] is None and cash_check["delta"] is None
    assert cash_check["missing_evidence"] == ["actual"]

    # 2. Missing position_quantity_before/after on a fill (with fill_id/broker_order_id present)
    # must report reconciliation_evidence_incomplete, never reconciliation_completed or position_variance
    missing_pos = _summary({"position_quantity_before": None, "position_quantity_after": None})
    (event2, _), _ = _run(missing_pos, order=BROKER_ORDER)
    _failed(event2, "reconciliation_evidence_incomplete")
    pos_check = next(c for c in event2["metadata"]["reason_detail"] if c["check"] == "position_change")
    assert pos_check["status"] == "unverified"
    assert pos_check["missing_evidence"] == ["actual"]

    # 3. Missing broker_order_id when broker_url is configured reports unverified evidence, not broker_record_missing
    missing_broker_id = _summary({"broker_order_id": ""})
    (event3, _), fetch3 = _run(missing_broker_id, order=BROKER_ORDER)
    fetch3.assert_not_called()
    _failed(event3, "reconciliation_evidence_incomplete")
    broker_check = next(c for c in event3["metadata"]["reason_detail"] if c["check"] == "broker_record")
    assert broker_check["status"] == "unverified"
    assert broker_check["missing_evidence"] == ["broker_order_id"]


def test_recorded_tj_57418ba7_sell_reconciliation_with_complete_and_incomplete_evidence() -> None:
    broker_order = {
        "order_id": "b00575cfc62f44d18ed052dad34d23ea",
        "instrument_id": "2317.TWSE",
        "side": "sell",
        "qty": 1.0,
        "status": "filled",
        "fill_qty": 1.0,
        "fill_price": 218.5,
    }
    summary = _summary(
        {
            "side": "sell",
            "instrument_id": "2317.TWSE",
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "fill_quantity": 1.0,
            "fill_price": 218.5,
            "fee": 0.3113625,
            "tax": 0.6555,
            "position_quantity_before": 1.0,
            "position_quantity_after": 0.0,
            "cash_before": 100000.0,
            "cash_after": 100217.5331375,
        },
        {"position_qty": 0.0, "cash": 100217.5331375},
    )
    (completed_event, _), _ = _run(summary, order=broker_order)
    assert completed_event["event_type"] == "reconciliation_completed"
    assert all(c["status"] == "passed" for c in completed_event["metadata"]["reason_detail"])

    # Proven cash discrepancy on tj_57418ba7 carries expected, actual, difference and delta
    mismatch_summary = _summary(
        {
            "side": "sell",
            "instrument_id": "2317.TWSE",
            "broker_order_id": "b00575cfc62f44d18ed052dad34d23ea",
            "fill_quantity": 1.0,
            "fill_price": 218.5,
            "fee": 0.3113625,
            "tax": 0.6555,
            "position_quantity_before": 1.0,
            "position_quantity_after": 0.0,
            "cash_before": 100000.0,
            "cash_after": 100172.5331375,
        },
        {"position_qty": 0.0, "cash": 100172.5331375},
    )
    (variance_event, _), _ = _run(mismatch_summary, order=broker_order)
    _failed(variance_event, "cash_variance")
    cash_change = next(c for c in variance_event["metadata"]["reason_detail"] if c["check"] == "cash_change")
    assert cash_change["status"] == "failed"
    assert cash_change["expected"] == pytest.approx(217.5331375)
    assert cash_change["actual"] == pytest.approx(172.5331375)
    assert cash_change["delta"] == pytest.approx(-45.0)
    assert cash_change["difference"] == pytest.approx(-45.0)


def test_mixed_proven_cash_variance_and_unverified_snapshot_position_preserves_variance() -> None:
    mixed_summary = _summary(
        meta_overrides={"cash_after": 102161.0},
        metrics_overrides={"position_qty": None, "cash": 102161.0},
    )
    (event, reason), _ = _run(mixed_summary, order=BROKER_ORDER)
    assert reason is None
    detail = _failed(event, "cash_variance")
    assert detail["cash_change"]["status"] == "failed"
    assert detail["cash_change"]["expected"] == pytest.approx(2206.0)
    assert detail["cash_change"]["actual"] == pytest.approx(2161.0)
    assert detail["cash_change"]["difference"] == pytest.approx(-45.0)
    assert detail["cash_change"]["delta"] == pytest.approx(-45.0)
    assert detail["committed_snapshot_position"]["status"] == "unverified"
    assert detail["committed_snapshot_position"]["actual"] is None
    assert detail["committed_snapshot_position"]["missing_evidence"] == ["actual"]


def test_missing_or_invalid_fee_and_tax_leave_cash_change_unverified_and_preserve_explicit_zero() -> None:
    # 1. Deleting only metadata.fee or setting invalid fee leaves cash_change unverified with ["fee"]
    for bad_fee in ("__delete__", None, "", "invalid", True):
        summary_no_fee = _summary()
        if bad_fee == "__delete__":
            del summary_no_fee["last_lifecycle_identity"]["metadata"]["fee"]
        else:
            summary_no_fee["last_lifecycle_identity"]["metadata"]["fee"] = bad_fee
        (event_no_fee, reason_no_fee), _ = _run(summary_no_fee, order=BROKER_ORDER)
        assert reason_no_fee is None
        detail_no_fee = _failed(event_no_fee, "reconciliation_evidence_incomplete")
        assert detail_no_fee["cash_change"]["status"] == "unverified"
        assert detail_no_fee["cash_change"]["expected"] is None
        assert detail_no_fee["cash_change"]["actual"] == pytest.approx(2206.0)
        assert detail_no_fee["cash_change"]["difference"] is None
        assert detail_no_fee["cash_change"]["delta"] is None
        assert detail_no_fee["cash_change"]["missing_evidence"] == ["fee"]

    # 2. Deleting only metadata.tax or setting invalid tax leaves cash_change unverified with ["tax"]
    for bad_tax in ("__delete__", None, "", "invalid", False):
        summary_no_tax = _summary()
        if bad_tax == "__delete__":
            del summary_no_tax["last_lifecycle_identity"]["metadata"]["tax"]
        else:
            summary_no_tax["last_lifecycle_identity"]["metadata"]["tax"] = bad_tax
        (event_no_tax, reason_no_tax), _ = _run(summary_no_tax, order=BROKER_ORDER)
        assert reason_no_tax is None
        detail_no_tax = _failed(event_no_tax, "reconciliation_evidence_incomplete")
        assert detail_no_tax["cash_change"]["status"] == "unverified"
        assert detail_no_tax["cash_change"]["expected"] is None
        assert detail_no_tax["cash_change"]["actual"] == pytest.approx(2206.0)
        assert detail_no_tax["cash_change"]["difference"] is None
        assert detail_no_tax["cash_change"]["delta"] is None
        assert detail_no_tax["cash_change"]["missing_evidence"] == ["tax"]

    # 3. Deleting both metadata.fee and metadata.tax lists ["fee", "tax"]
    summary_no_both = _summary()
    del summary_no_both["last_lifecycle_identity"]["metadata"]["fee"]
    del summary_no_both["last_lifecycle_identity"]["metadata"]["tax"]
    (event_no_both, _), _ = _run(summary_no_both, order=BROKER_ORDER)
    detail_no_both = _failed(event_no_both, "reconciliation_evidence_incomplete")
    assert detail_no_both["cash_change"]["status"] == "unverified"
    assert detail_no_both["cash_change"]["missing_evidence"] == ["fee", "tax"]

    # 4. Explicit zero fee and tax (0 or 0.0) are valid and complete the cash comparison
    zero_summary = _summary(
        meta_overrides={"fee": 0.0, "tax": 0.0, "cash_after": 102215.0},
        metrics_overrides={"cash": 102215.0},
    )
    (zero_event, zero_reason), _ = _run(zero_summary, order=BROKER_ORDER)
    assert zero_reason is None
    assert zero_event["event_type"] == "reconciliation_completed"
    zero_detail = {d["check"]: d for d in zero_event["metadata"]["reason_detail"]}
    assert zero_detail["cash_change"]["status"] == "passed"
    assert zero_detail["cash_change"]["expected"] == pytest.approx(2215.0)
    assert zero_detail["cash_change"]["actual"] == pytest.approx(2215.0)

    # 5. Mixed genuine mismatch (position_variance or committed_snapshot_cash) with missing fee
    mixed_pos_no_fee = _summary(
        meta_overrides={"position_quantity_after": 1.0},
        metrics_overrides={"position_qty": 1.0},
    )
    del mixed_pos_no_fee["last_lifecycle_identity"]["metadata"]["fee"]
    (mixed_pos_event, _), _ = _run(mixed_pos_no_fee, order=BROKER_ORDER)
    mixed_pos_detail = _failed(mixed_pos_event, "position_variance")
    assert mixed_pos_detail["position_change"]["status"] == "failed"
    assert mixed_pos_detail["position_change"]["delta"] == pytest.approx(1.0)
    assert mixed_pos_detail["cash_change"]["status"] == "unverified"
    assert mixed_pos_detail["cash_change"]["missing_evidence"] == ["fee"]


