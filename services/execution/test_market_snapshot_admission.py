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
    now_dt = datetime(2026, 10, 7, 6, 0, 0, tzinfo=timezone.utc)
    ev_time = "2026-10-07T05:30:00Z"
    obs_time = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    p1 = MarketSnapshotPoint(event_time="2026-10-06T05:30:00Z", close=950.0, source_id="tw-official:tw_price_daily:TWSE:2330:p1", connector_id="tw-twse-tpex-official-market", content_ref="tw-official://ref1", ingest_run_id="run1", market="TWSE")
    p2 = MarketSnapshotPoint(event_time=ev_time, close=955.0, source_id="tw-official:tw_price_daily:TWSE:2330:p2", connector_id="tw-twse-tpex-official-market", content_ref="tw-official://ref2", ingest_run_id="run1", market="TWSE")
    snap = LatestMarketSnapshot(symbol="2330.TWSE", points=(p1, p2), observed_at=obs_time, market="TWSE")

    # Real public DTO with requested_symbol="2330.TW"
    pub = snap.to_public_dict(requested_symbol="2330.TW")
    assert pub["symbol"] == "2330.TW"
    assert "points" not in pub
    dec = admit_canonical_source_snapshot(pub, expected_symbol="2330.TW", now_iso=obs_time)
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


def test_public_dto_missing_schema_version_symbol_market_rejected() -> None:
    for missing_field in ("schema_version", "symbol", "market"):
        pub = _make_canonical_snapshot(as_public=True)
        del pub[missing_field]
        dec1 = admit_canonical_source_snapshot(pub, expected_symbol="SPY")
        assert dec1.admitted is False
        assert dec1.reason_code == "market_input_missing"
        assert missing_field in (dec1.detail or "")

    # For symbol, admit_market_snapshot also requires symbol
    pub_no_sym = _make_canonical_snapshot(as_public=True)
    del pub_no_sym["symbol"]
    dec_no_sym = admit_market_snapshot(pub_no_sym, expected_symbol="SPY", max_age_seconds=86400)
    assert dec_no_sym.admitted is False
    assert dec_no_sym.reason_code == "market_input_missing"
    assert "symbol" in (dec_no_sym.detail or "")

    # For schema_version, public Source DTO with mss- ID delegates to canonical admission and is rejected
    pub_no_ver = _make_canonical_snapshot(as_public=True)
    del pub_no_ver["schema_version"]
    dec_no_ver = admit_market_snapshot(pub_no_ver, expected_symbol="SPY", max_age_seconds=86400)
    assert dec_no_ver.admitted is False
    assert dec_no_ver.reason_code == "market_input_missing"
    assert "schema_version" in (dec_no_ver.detail or "")

    # For market, canonical Source DTO with schema_version delegates to canonical admission
    pub_no_mkt = _make_canonical_snapshot(as_public=True)
    del pub_no_mkt["market"]
    dec_no_mkt = admit_market_snapshot(pub_no_mkt, expected_symbol="SPY", max_age_seconds=86400)
    assert dec_no_mkt.admitted is False
    assert dec_no_mkt.reason_code == "market_input_missing"
    assert "market" in (dec_no_mkt.detail or "")


def test_public_dto_invalid_schema_version_rejected() -> None:
    for bad_ver in (999, "999", "v2", "source_ingest_latest_market_snapshot.v999"):
        pub = _make_canonical_snapshot(as_public=True)
        pub["schema_version"] = bad_ver
        dec1 = admit_canonical_source_snapshot(pub, expected_symbol="SPY")
        assert dec1.admitted is False
        assert dec1.reason_code == "market_input_invalid"
        assert "schema_version" in (dec1.detail or "")

        dec2 = admit_market_snapshot(pub, expected_symbol="SPY", max_age_seconds=86400)
        assert dec2.admitted is False
        assert dec2.reason_code == "market_input_invalid"
        assert "schema_version" in (dec2.detail or "")


def test_public_dto_bool_closes_rejected() -> None:
    for bad_closes in ([True, False], [500.0, True], [False, 502.0]):
        pub = _make_canonical_snapshot(as_public=True)
        pub["closes"] = bad_closes
        dec1 = admit_canonical_source_snapshot(pub, expected_symbol="SPY")
        assert dec1.admitted is False
        assert dec1.reason_code == "market_input_invalid"
        assert "positive finite number" in (dec1.detail or "")

        dec2 = admit_market_snapshot(pub, expected_symbol="SPY", max_age_seconds=86400)
        assert dec2.admitted is False
        assert dec2.reason_code == "market_input_invalid"
        assert "positive finite number" in (dec2.detail or "")


def test_public_dto_malformed_lineage_rejected() -> None:
    for bad_lineage in (
        "tw-official:feed",
        {"source_ids": "not_a_list"},
        {"connector_ids": "not_a_list"},
        {"source_ids": [123]},
        {"source_ids": [""]},
        {},
    ):
        pub = _make_canonical_snapshot(as_public=True)
        pub["lineage"] = bad_lineage
        dec1 = admit_canonical_source_snapshot(pub, expected_symbol="SPY")
        assert dec1.admitted is False
        assert dec1.reason_code == "market_input_invalid"

        dec2 = admit_market_snapshot(pub, expected_symbol="SPY", max_age_seconds=86400)
        assert dec2.admitted is False
        assert dec2.reason_code == "market_input_invalid"


def test_admission_functions_converge_on_missing_observed_at() -> None:
    # Public Source DTO without observed_at
    pub = _make_canonical_snapshot(as_public=True)
    del pub["observed_at"]
    dec_canon = admit_canonical_source_snapshot(pub, expected_symbol="SPY")
    dec_market = admit_market_snapshot(pub, expected_symbol="SPY", max_age_seconds=86400)

    assert dec_canon.admitted is False
    assert dec_market.admitted is False
    assert dec_canon.reason_code == "market_input_missing"
    assert dec_market.reason_code == "market_input_missing"
    assert "observed_at" in (dec_canon.detail or "")
    assert "observed_at" in (dec_market.detail or "")

    # Non-canonical snapshot without observed_at
    raw = {
        "snapshot_id": "snap-us-custom",
        "symbol": "AAPL.US",
        "event_time": "2026-10-08T01:00:00Z",
        "source_ref": "source-ref-1",
        "lineage": {"source": "manual"},
        "closes": [150.0, 151.0],
    }
    dec_market_raw = admit_market_snapshot(raw, expected_symbol="AAPL.US", max_age_seconds=86400)
    assert dec_market_raw.admitted is False
    assert dec_market_raw.reason_code == "market_input_missing"
    assert "observed_at" in (dec_market_raw.detail or "")


def test_admit_market_snapshot_counterexample_convergence() -> None:
    # 1. Valid public DTO
    pub_valid = _make_canonical_snapshot(as_public=True)
    d_c1 = admit_canonical_source_snapshot(pub_valid, expected_symbol="SPY")
    d_m1 = admit_market_snapshot(pub_valid, expected_symbol="SPY", max_age_seconds=86400)
    assert d_c1.admitted is True and d_m1.admitted is True

    # 2. Missing schema_version (the exact PR #6349 reopen counterexample)
    pub_no_ver = _make_canonical_snapshot(as_public=True)
    del pub_no_ver["schema_version"]
    d_c2 = admit_canonical_source_snapshot(pub_no_ver, expected_symbol="SPY")
    d_m2 = admit_market_snapshot(pub_no_ver, expected_symbol="SPY", max_age_seconds=86400)
    assert d_c2.admitted is False and d_m2.admitted is False
    assert d_c2.reason_code == d_m2.reason_code == "market_input_missing"
    assert "schema_version" in (d_m2.detail or "")

    # 3. Missing observed_at
    pub_no_obs = _make_canonical_snapshot(as_public=True)
    del pub_no_obs["observed_at"]
    d_c3 = admit_canonical_source_snapshot(pub_no_obs, expected_symbol="SPY")
    d_m3 = admit_market_snapshot(pub_no_obs, expected_symbol="SPY", max_age_seconds=86400)
    assert d_c3.admitted is False and d_m3.admitted is False
    assert d_c3.reason_code == d_m3.reason_code == "market_input_missing"
    assert "observed_at" in (d_m3.detail or "")

    # 4. Wrong schema
    pub_bad_ver = _make_canonical_snapshot(as_public=True)
    pub_bad_ver["schema_version"] = 999
    d_c4 = admit_canonical_source_snapshot(pub_bad_ver, expected_symbol="SPY")
    d_m4 = admit_market_snapshot(pub_bad_ver, expected_symbol="SPY", max_age_seconds=86400)
    assert d_c4.admitted is False and d_m4.admitted is False
    assert d_c4.reason_code == d_m4.reason_code == "market_input_invalid"

    # 5. Boolean closes
    pub_bool_closes = _make_canonical_snapshot(as_public=True)
    pub_bool_closes["closes"] = [True, 502.0]
    d_c5 = admit_canonical_source_snapshot(pub_bool_closes, expected_symbol="SPY")
    d_m5 = admit_market_snapshot(pub_bool_closes, expected_symbol="SPY", max_age_seconds=86400)
    assert d_c5.admitted is False and d_m5.admitted is False
    assert d_c5.reason_code == d_m5.reason_code == "market_input_invalid"

    # 6. Malformed lineage
    pub_bad_lin = _make_canonical_snapshot(as_public=True)
    pub_bad_lin["lineage"] = "not_a_dict"
    d_c6 = admit_canonical_source_snapshot(pub_bad_lin, expected_symbol="SPY")
    d_m6 = admit_market_snapshot(pub_bad_lin, expected_symbol="SPY", max_age_seconds=86400)
    assert d_c6.admitted is False and d_m6.admitted is False
    assert d_c6.reason_code == d_m6.reason_code == "market_input_invalid"

    # 7. Valid non-canonical inline snapshot remains admitted
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_valid = {
        "snapshot_id": "snap-pass-001",
        "symbol": "SPY",
        "event_time": now_iso,
        "observed_at": now_iso,
        "source_ref": "custom-ref-001",
        "lineage": {"source": "manual"},
        "closes": [500.0, 502.0],
    }
    d_raw = admit_market_snapshot(raw_valid, expected_symbol="SPY", max_age_seconds=86400)
    assert d_raw.admitted is True


def test_evaluate_taiwan_market_freshness_extended_governed_calendar_2026() -> None:
    from services.execution.market_snapshot_admission import (
        TW_GOVERNED_CALENDAR_PINS,
        evaluate_taiwan_market_freshness,
    )
    from services.source_ingestion.connectors.taiwan_official import (
        TWSE_2026_SCHEDULE_CALENDAR_SHA256,
        TWSE_2026_SCHEDULE_CALENDAR_VERSION,
        governed_taiwan_calendar_evidence,
    )

    # Acceptance criterion 3: connector digest constant and admission trusted pin match
    assert TWSE_2026_SCHEDULE_CALENDAR_VERSION in TW_GOVERNED_CALENDAR_PINS
    assert (
        TW_GOVERNED_CALENDAR_PINS[TWSE_2026_SCHEDULE_CALENDAR_VERSION]
        == TWSE_2026_SCHEDULE_CALENDAR_SHA256
    )

    evidence_2026_10_08 = governed_taiwan_calendar_evidence(
        venue="TWSE",
        trade_date="2026-10-08",
    )
    assert evidence_2026_10_08 is not None
    assert evidence_2026_10_08["version"] == TWSE_2026_SCHEDULE_CALENDAR_VERSION
    assert evidence_2026_10_08["checksum"] == TWSE_2026_SCHEDULE_CALENDAR_SHA256

    lineage = {"connector_ids": ["tw-twse-tpex-official-market"]}
    close_2026_10_08 = datetime.fromisoformat("2026-10-08T05:30:00+00:00")
    now_2026_10_09_12 = datetime.fromisoformat("2026-10-09T12:00:00+00:00")
    receipt_2026_10_09_07 = datetime.fromisoformat("2026-10-09T07:00:00+00:00")

    # Acceptance criterion 4: admits TWSE snapshot with latest close 2026-10-08
    # at 2026-10-09T12:00:00Z with a refresh receipt from 2026-10-09T07:00:00Z
    ok, reason, detail = evaluate_taiwan_market_freshness(
        event_time_dt=close_2026_10_08,
        now_dt=now_2026_10_09_12,
        refresh_receipt_dt=receipt_2026_10_09_07,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=evidence_2026_10_08,
    )
    assert ok is True
    assert reason is None
    assert detail is None

    # Acceptance criterion 4: still fails closed for a missed regular weekday session
    # (e.g. latest close 2026-10-07 evaluated on 2026-10-09T12:00:00Z, missing 2026-10-08)
    evidence_2026_10_07 = governed_taiwan_calendar_evidence(
        venue="TWSE",
        trade_date="2026-10-07",
    )
    close_2026_10_07 = datetime.fromisoformat("2026-10-07T05:30:00+00:00")
    ok_missed, reason_missed, detail_missed = evaluate_taiwan_market_freshness(
        event_time_dt=close_2026_10_07,
        now_dt=now_2026_10_09_12,
        refresh_receipt_dt=receipt_2026_10_09_07,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=evidence_2026_10_07,
    )
    assert ok_missed is False
    assert reason_missed == "market_input_stale"
    assert "2026-10-08" in str(detail_missed)


def test_taiwan_refresh_receipt_session_window_pinned_cases() -> None:
    from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    lineage = {"connector_ids": ["tw-twse-tpex-official-market"]}

    # Acceptance 4 case 1:
    # Friday 2026-10-16 close (05:30Z) with Friday 07:00Z receipt (observed_at)
    # is admitted on Monday 2026-10-19T02:00Z (age = 67h > 24h, admitted).
    ev_2026_10_16 = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-16")
    assert ev_2026_10_16 is not None
    close_fri_10_16 = datetime.fromisoformat("2026-10-16T05:30:00+00:00")
    receipt_fri_07z = datetime.fromisoformat("2026-10-16T07:00:00+00:00")
    now_mon_02z = datetime.fromisoformat("2026-10-19T02:00:00+00:00")

    ok1, reason1, detail1 = evaluate_taiwan_market_freshness(
        event_time_dt=close_fri_10_16,
        now_dt=now_mon_02z,
        refresh_receipt_dt=receipt_fri_07z,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=ev_2026_10_16,
    )
    assert ok1 is True
    assert reason1 is None
    assert detail1 is None

    # Acceptance 4 case 2:
    # The same receipt (Friday 07:00Z) is rejected after Monday close (Monday 06:00Z)
    now_mon_06z = datetime.fromisoformat("2026-10-19T06:00:00+00:00")
    ok2, reason2, detail2 = evaluate_taiwan_market_freshness(
        event_time_dt=close_fri_10_16,
        now_dt=now_mon_06z,
        refresh_receipt_dt=receipt_fri_07z,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=ev_2026_10_16,
    )
    assert ok2 is False
    assert reason2 == "market_input_stale_refresh"
    assert "maximum is 86400s" in str(detail2)

    # Acceptance 4 case 3:
    # 2026-10-09 holiday with 2026-10-08 post-close receipt admitted on 2026-10-12T03:00Z
    # (Thursday close 05:30Z, receipt 07:00Z, age = 92h > 24h, admitted).
    ev_2026_10_08 = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-08")
    assert ev_2026_10_08 is not None
    close_thu_10_08 = datetime.fromisoformat("2026-10-08T05:30:00+00:00")
    receipt_thu_07z = datetime.fromisoformat("2026-10-08T07:00:00+00:00")
    now_mon_10_12_03z = datetime.fromisoformat("2026-10-12T03:00:00+00:00")

    ok3, reason3, detail3 = evaluate_taiwan_market_freshness(
        event_time_dt=close_thu_10_08,
        now_dt=now_mon_10_12_03z,
        refresh_receipt_dt=receipt_thu_07z,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=ev_2026_10_08,
    )
    assert ok3 is True
    assert reason3 is None
    assert detail3 is None

    # Acceptance 4 case 4:
    # Receipt taken before the latest close is rejected:
    # Friday 2026-10-16 05:00Z receipt (predates 05:30Z close) evaluated on Monday 02:00Z.
    receipt_fri_pre_close = datetime.fromisoformat("2026-10-16T05:00:00+00:00")
    ok4, reason4, detail4 = evaluate_taiwan_market_freshness(
        event_time_dt=close_fri_10_16,
        now_dt=now_mon_02z,
        refresh_receipt_dt=receipt_fri_pre_close,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=ev_2026_10_16,
    )
    assert ok4 is False
    assert reason4 == "market_input_stale_refresh"


def test_taiwan_refresh_receipt_14_day_ceiling_rejected() -> None:
    from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    lineage = {"connector_ids": ["tw-twse-tpex-official-market"]}
    ev = governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-10-01")
    assert ev is not None

    close_10_01 = datetime.fromisoformat("2026-10-01T05:30:00+00:00")
    # Receipt taken on 2026-10-01T07:00:00Z, now is 2026-10-16T02:00:00Z (age > 14 days)
    receipt_10_01 = datetime.fromisoformat("2026-10-01T07:00:00+00:00")
    now_10_16 = datetime.fromisoformat("2026-10-16T02:00:00+00:00")

    # Even with large max_refresh_age_seconds, 14-day hard ceiling rejects as market_input_stale_refresh
    ok, reason, detail = evaluate_taiwan_market_freshness(
        event_time_dt=close_10_01,
        now_dt=now_10_16,
        refresh_receipt_dt=receipt_10_01,
        lineage=lineage,
        max_refresh_age_seconds=999_999_999,
        calendar_evidence=ev,
    )
    assert ok is False
    assert reason == "market_input_stale_refresh"
    assert "14 days" in str(detail)


def test_taiwan_refresh_receipt_unverifiable_weekday_keeps_receipt_stale() -> None:
    from services.execution.market_snapshot_admission import (
        CALENDAR_EVIDENCE_UNVERIFIABLE,
        evaluate_taiwan_market_freshness,
    )

    lineage = {"connector_ids": ["tw-twse-tpex-official-market"]}
    close_thu_10_08 = datetime.fromisoformat("2026-10-08T05:30:00+00:00")
    receipt_thu_07z = datetime.fromisoformat("2026-10-08T07:00:00+00:00")
    now_mon_10_12_03z = datetime.fromisoformat("2026-10-12T03:00:00+00:00")

    # When Friday 2026-10-09 cannot be verified with calendar evidence,
    # the receipt cannot be proven fresh until next session and stays stale.
    def unverifiable_calendar_lookup(_date_iso: str):
        return CALENDAR_EVIDENCE_UNVERIFIABLE

    ok, reason, detail = evaluate_taiwan_market_freshness(
        event_time_dt=close_thu_10_08,
        now_dt=now_mon_10_12_03z,
        refresh_receipt_dt=receipt_thu_07z,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        holiday_lookup=unverifiable_calendar_lookup,
    )
    assert ok is False
    assert reason == "market_input_stale_refresh"
    assert "maximum is 86400s" in str(detail)


def test_taiwan_refresh_receipt_within_flat_window_accepted() -> None:
    from services.execution.market_snapshot_admission import evaluate_taiwan_market_freshness

    lineage = {"connector_ids": ["tw-twse-tpex-official-market"]}
    close_fri = datetime.fromisoformat("2026-08-28T05:30:00+00:00")
    receipt_sat = datetime.fromisoformat("2026-08-29T11:00:00+00:00")
    now_sat = datetime.fromisoformat("2026-08-29T12:00:00+00:00")

    # Within max_refresh_age_seconds (3600s <= 86400s), admitted without calendar evidence
    ok, reason, detail = evaluate_taiwan_market_freshness(
        event_time_dt=close_fri,
        now_dt=now_sat,
        refresh_receipt_dt=receipt_sat,
        lineage=lineage,
        max_refresh_age_seconds=86400,
        calendar_evidence=None,
    )
    assert ok is True
    assert reason is None
    assert detail is None



