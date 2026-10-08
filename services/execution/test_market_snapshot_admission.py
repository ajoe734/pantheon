"""Unit tests for canonical Source snapshot admission in market_snapshot_admission."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.execution.market_snapshot_admission import (
    admit_canonical_source_snapshot,
    admit_market_snapshot,
)
from services.source_ingestion.requirement_state import (
    LatestMarketSnapshot,
    MarketSnapshotPoint,
)


def _make_canonical_snapshot(
    symbol: str = "SPY",
    closes: tuple[float, ...] = (500.0, 502.0),
    event_time: str | None = None,
    observed_at: str | None = None,
    market: str = "US",
    source_id: str = "test-source",
    connector_id: str = "dev-paper-us-equity-simulation",
    as_public: bool = False,
) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    ev_time = event_time or now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    obs_time = observed_at or now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    base_dt = datetime.fromisoformat(ev_time.replace("Z", "+00:00"))
    points = []
    for i, c in enumerate(closes):
        pt_dt = base_dt - timedelta(minutes=(len(closes) - 1 - i) * 5)
        points.append(
            MarketSnapshotPoint(
                event_time=pt_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                close=c,
                source_id=source_id,
                connector_id=connector_id,
                content_ref=f"ref-{i}",
                ingest_run_id=f"run-{i}",
                market=market,
            )
        )
    snap = LatestMarketSnapshot(
        symbol=symbol,
        points=tuple(points),
        observed_at=obs_time,
        market=market,
    )
    if as_public:
        return snap.to_public_dict(requested_symbol=symbol)
    return snap.to_dict()


def test_admit_canonical_source_snapshot_valid_both_public_and_internal() -> None:
    # 1. Internal storage DTO with points
    internal_data = _make_canonical_snapshot(symbol="SPY", closes=(500.0, 502.0), as_public=False)
    decision1 = admit_canonical_source_snapshot(internal_data, expected_symbol="SPY")
    assert decision1.admitted is True
    assert decision1.snapshot_id == internal_data["snapshot_id"]
    assert decision1.reason_code is None

    # 2. Real owner public DTO without points (ingest_operations.py:111 to_public_dict)
    public_data = _make_canonical_snapshot(symbol="SPY", closes=(500.0, 502.0), as_public=True)
    assert "points" not in public_data
    decision2 = admit_canonical_source_snapshot(public_data, expected_symbol="SPY")
    assert decision2.admitted is True
    assert decision2.snapshot_id == public_data["snapshot_id"]
    assert decision2.reason_code is None


def test_admit_canonical_source_snapshot_taiwan_symbol_alias() -> None:
    now_dt = datetime.now(timezone.utc)
    ev_time = "2026-10-07T05:30:00Z"
    obs_time = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    p1 = MarketSnapshotPoint(event_time="2026-10-06T05:30:00Z", close=950.0, source_id="tw-official:tw_price_daily:TWSE:2330:p1", connector_id="tw-twse-tpex-official-market", content_ref="tw-official://ref1", ingest_run_id="run1", market="TWSE")
    p2 = MarketSnapshotPoint(event_time=ev_time, close=955.0, source_id="tw-official:tw_price_daily:TWSE:2330:p2", connector_id="tw-twse-tpex-official-market", content_ref="tw-official://ref2", ingest_run_id="run1", market="TWSE")
    snap = LatestMarketSnapshot(symbol="2330.TWSE", points=(p1, p2), observed_at=obs_time, market="TWSE")

    # Real public DTO with requested_symbol="2330.TW"
    pub = snap.to_public_dict(requested_symbol="2330.TW")
    assert pub["symbol"] == "2330.TW"
    assert "points" not in pub
    dec = admit_canonical_source_snapshot(pub, expected_symbol="2330.TW")
    assert dec.admitted is True


def test_admit_market_snapshot_delegates_for_points() -> None:
    data = _make_canonical_snapshot(symbol="SPY", closes=(500.0, 502.0))
    decision = admit_market_snapshot(data, expected_symbol="SPY", max_age_seconds=86400)
    assert decision.admitted is True
    assert decision.snapshot_id == data["snapshot_id"]


def test_admit_canonical_source_snapshot_rejects_non_mapping() -> None:
    assert admit_canonical_source_snapshot(None).admitted is False
    assert admit_canonical_source_snapshot("invalid").admitted is False
    assert admit_canonical_source_snapshot([1, 2, 3]).admitted is False


def test_admit_canonical_source_snapshot_rejects_adversarial_counterexample() -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    adv = {"closes": [100.0, 101.0], "market": "US", "event_time": now_str}
    decision = admit_canonical_source_snapshot(adv, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code in ("market_input_missing", "market_input_invalid")


def test_admit_canonical_source_snapshot_rejects_missing_required_fields() -> None:
    base = _make_canonical_snapshot()
    for key in ("points", "observed_at", "event_time", "symbol", "schema_version", "snapshot_id"):
        corrupted = copy.deepcopy(base)
        del corrupted[key]
        decision = admit_canonical_source_snapshot(corrupted, expected_symbol="SPY")
        assert decision.admitted is False
        assert decision.reason_code in ("market_input_missing", "market_input_invalid")


def test_admit_canonical_source_snapshot_rejects_tampered_hash() -> None:
    data = _make_canonical_snapshot()
    data["snapshot_id"] = "mss-000000000000000000000000"
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "validation failed" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_naive_event_time() -> None:
    data = _make_canonical_snapshot()
    data["event_time"] = "2026-10-08T01:00:00"
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "naive" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_naive_observed_at() -> None:
    data = _make_canonical_snapshot()
    data["observed_at"] = "2026-10-08T01:00:00"
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "naive" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_naive_point_event_time() -> None:
    data = _make_canonical_snapshot()
    data["points"][0]["event_time"] = "2026-10-08T00:55:00"
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "naive" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_future_event_time() -> None:
    future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
    data = _make_canonical_snapshot(event_time=future_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "future" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_future_observed_at() -> None:
    future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
    data = _make_canonical_snapshot(observed_at=future_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "future" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_mismatched_symbol() -> None:
    data = _make_canonical_snapshot(symbol="SPY")
    decision = admit_canonical_source_snapshot(data, expected_symbol="QQQ")
    assert decision.admitted is False
    assert decision.reason_code == "market_input_invalid"
    assert "QQQ" in (decision.detail or "")


def test_admit_canonical_source_snapshot_rejects_insufficient_closes() -> None:
    data = _make_canonical_snapshot(closes=(500.0,))
    decision = admit_canonical_source_snapshot(data, minimum_closes=2)
    assert decision.admitted is False
    assert decision.reason_code == "market_input_insufficient"


def test_admit_canonical_source_snapshot_rejects_stale_us_snapshot() -> None:
    stale_dt = datetime.now(timezone.utc) - timedelta(days=2)
    data = _make_canonical_snapshot(
        event_time=stale_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        observed_at=stale_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    decision = admit_canonical_source_snapshot(data, expected_symbol="SPY", max_age_seconds=86400)
    assert decision.admitted is False
    assert decision.reason_code == "market_input_stale"
