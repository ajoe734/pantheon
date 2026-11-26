"""Controller terminal readback age checks for FinMind fundamentals follow publication."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from services.source_ingestion.controller_worker import ControllerTickError
from services.source_ingestion.tests import test_controller_worker as tcw

MONTHLY = "tw-finmind-monthly-revenue"
QUARTERLY = "tw-finmind-quarterly-statements"
DATASETS = {MONTHLY: "tw_monthly_revenue", QUARTERLY: "tw_quarterly_statements"}


def _utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _validate(connector_id: str, dataset: str, available: datetime, now: datetime, *, cadence="daily") -> None:
    actual = tcw._actual_readback()
    actual["captured_at"] = _iso(now)
    item = actual["connectors"][0]
    item["connector_id"] = connector_id
    item["connector"]["connector_id"] = connector_id
    item["schedule"]["connector_id"] = connector_id
    item["source_health"]["source_id"] = connector_id
    item["desired_state"]["dataset"] = dataset
    item["desired_state"]["cadence"] = cadence
    item["latest_source_record"]["connector_id"] = connector_id
    item["latest_source_record"]["provenance"]["dataset"] = dataset
    item["latest_source_record"]["provenance"]["available_time"] = _iso(available)
    reconcile = deepcopy(tcw._reconcile())
    action = reconcile["results"][0]["actions"][0]
    action["dataset"] = dataset
    action["connector_id"] = connector_id
    tcw._validate_terminal_readback(
        reconcile=reconcile, schedule=tcw._schedule(), actual=actual, now=now
    )


def _stale(connector_id, dataset, available, now) -> bool:
    try:
        _validate(connector_id, dataset, available, now)
    except ControllerTickError as exc:
        assert "source_data_stale" in str(exc), str(exc)
        return True
    return False


def test_hosted_monthly_shape_accepted_after_ten_days() -> None:
    # published 2026-11-10T10:00Z; 10 days elapse at 2026-11-20T10:00Z (hosted failure)
    assert not _stale(MONTHLY, "tw_monthly_revenue", _utc(2026, 11, 10, 10), _utc(2026, 11, 20, 12, 40))


def test_hosted_quarterly_shape_accepted_after_ten_days() -> None:
    assert not _stale(QUARTERLY, "tw_quarterly_statements", _utc(2026, 11, 14, 10), _utc(2026, 11, 24, 12))


@pytest.mark.parametrize(
    "connector,ts,now,stale",
    [
        (MONTHLY, _utc(2026, 10, 10, 10), _utc(2026, 11, 9, 4), False),   # day before statutory date
        (MONTHLY, _utc(2026, 10, 10, 10), _utc(2026, 11, 11, 4), False),  # inside grace
        (MONTHLY, _utc(2026, 10, 10, 10), _utc(2026, 11, 13, 4), True),   # missing past grace
        (MONTHLY, _utc(2026, 11, 10, 10), _utc(2026, 11, 13, 4), False),  # newest stored
        (MONTHLY, _utc(2026, 11, 10, 10), _utc(2026, 12, 9, 4), False),
        (MONTHLY, _utc(2026, 11, 10, 10), _utc(2026, 12, 13, 4), True),
        (QUARTERLY, _utc(2026, 8, 14, 10), _utc(2026, 11, 13, 4), False),
        (QUARTERLY, _utc(2026, 8, 14, 10), _utc(2026, 11, 15, 4), False),
        (QUARTERLY, _utc(2026, 8, 14, 10), _utc(2026, 11, 17, 4), True),
        (QUARTERLY, _utc(2026, 11, 14, 10), _utc(2027, 3, 30, 4), False),
        (QUARTERLY, _utc(2026, 11, 14, 10), _utc(2027, 4, 3, 4), True),
    ],
)
def test_nothing_new_past_statutory_date_fails_closed(connector, ts, now, stale) -> None:
    assert _stale(connector, DATASETS[connector], ts, now) is stale


def test_future_available_time_rejected() -> None:
    now = _utc(2026, 11, 20, 12)
    assert _stale(MONTHLY, "tw_monthly_revenue", now + timedelta(hours=1), now)


def test_other_connectors_keep_generic_cadence_rule() -> None:
    now = _utc(2026, 11, 20, 12)
    old = _utc(2026, 11, 10, 10)
    # valuation and a chip connector still use the 10 day daily limit
    for cid, ds in (("tw-finmind-valuation", "tw_valuation_bwibbu"), ("tw-finmind-institutional-chip", "tw_institutional_chip")):
        assert _stale(cid, ds, old, now)
        assert not _stale(cid, ds, now - timedelta(days=9), now)
