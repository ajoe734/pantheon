"""Unit tests for the governed Taiwan chip publication schedule and delay bounds."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import pytest

from services.source_ingestion.chip_publication_schedule import (
    CHIP_PUBLICATION_SCHEDULES,
    DEFAULT_MAX_PUBLICATION_DELAY,
    MEASURED_CHIP_PROVIDER_PROBES,
    OFFICIAL_CHIP_REFRESH_PROFILES,
    TAIPEI_TZ,
    evaluate_chip_provider_session,
    expected_chip_publication_datetime,
    get_chip_publication_schedule,
    latest_published_chip_session,
)
from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES
from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence


def _twse_session_day(day: date) -> bool:
    calendar = governed_taiwan_calendar_evidence(venue="TWSE", trade_date=day.isoformat())
    return day.weekday() < 5 and not (calendar and day.isoformat() in (calendar.get("holidays") or {}))


def _taipei_dt(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=TAIPEI_TZ)


def test_schedule_covers_all_six_official_and_taifex_chip_profiles_and_probes() -> None:
    expected_ids = {
        cid for cid, prof in TW_CHIP_REFRESH_PROFILES.items() if prof.get("base") != "tw-finmind-fundamentals"
    }
    assert len(expected_ids) == 6
    assert set(OFFICIAL_CHIP_REFRESH_PROFILES) == expected_ids
    assert set(CHIP_PUBLICATION_SCHEDULES) == expected_ids

    for cid, schedule in CHIP_PUBLICATION_SCHEDULES.items():
        profile = TW_CHIP_REFRESH_PROFILES[cid]
        assert schedule.connector_id == cid
        assert schedule.dataset == profile["dataset"]
        assert schedule.endpoint.startswith("https://")
        assert 10 <= schedule.safety_margin_minutes <= 30
        assert schedule.publication_day_offset in {0, 1}
        assert timedelta(minutes=30) <= schedule.max_publication_delay <= DEFAULT_MAX_PUBLICATION_DELAY
        # Scheduled publication time is strictly after earliest observed time on the same publication day.
        earliest_dt = datetime.combine(date(2026, 11, 18), schedule.earliest_observed_publication_time_taipei)
        scheduled_dt = datetime.combine(date(2026, 11, 18), schedule.publication_time_taipei)
        assert scheduled_dt >= earliest_dt + timedelta(minutes=schedule.safety_margin_minutes - 1)

    assert len(MEASURED_CHIP_PROVIDER_PROBES) >= 5
    probe_ids = {probe["probe_id"] for probe in MEASURED_CHIP_PROVIDER_PROBES}
    assert "2026-11-14-morning-post-publication" in probe_ids
    assert "2026-11-18-evening-2154-taipei-coordinator" in probe_ids
    assert "2026-11-18-evening-pre-2200-taipei" in probe_ids
    assert "2026-11-18-evening-post-2200-taipei" in probe_ids
    for probe in MEASURED_CHIP_PROVIDER_PROBES:
        assert set(probe["observations"]) == expected_ids


def test_naive_datetime_and_unknown_profile_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        latest_published_chip_session(
            "tw-official-twse-margin-balances",
            datetime(2026, 11, 18, 8, 0),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_chip_provider_session(
            "tw-official-twse-margin-balances",
            provider_session="2026-11-17",
            now=datetime(2026, 11, 18, 8, 0),
        )
    with pytest.raises(KeyError, match="unknown chip refresh profile"):
        get_chip_publication_schedule("tw-finmind-valuation")


@pytest.mark.parametrize(
    ("connector_id", "pub_day_offset", "pub_hour", "pub_minute"),
    [
        ("tw-official-twse-margin-balances", 1, 5, 40),
        ("tw-official-tpex-margin-balances", 0, 22, 15),
        ("tw-official-tpex-institutional-flow", 0, 20, 15),
        ("tw-taifex-institutional-positions", 1, 6, 50),
        ("tw-taifex-put-call", 1, 6, 50),
        ("tw-taifex-tx-daily", 1, 6, 50),
    ],
)
def test_exact_publication_boundary_and_utc_taipei_date_conversion(
    connector_id: str,
    pub_day_offset: int,
    pub_hour: int,
    pub_minute: int,
) -> None:
    session_t = date(2026, 11, 18)  # Wednesday
    session_t_minus_1 = date(2026, 11, 17)  # Tuesday

    pub_dt_taipei = expected_chip_publication_datetime(connector_id, session_t)
    assert pub_dt_taipei == datetime.combine(
        session_t + timedelta(days=pub_day_offset),
        time(pub_hour, pub_minute),
        tzinfo=TAIPEI_TZ,
    )

    one_sec_before_utc = (pub_dt_taipei - timedelta(seconds=1)).astimezone(timezone.utc)
    exact_utc = pub_dt_taipei.astimezone(timezone.utc)
    one_sec_after_utc = (pub_dt_taipei + timedelta(seconds=1)).astimezone(timezone.utc)

    # 1 second before publication: still expects T-1.
    assert (
        latest_published_chip_session(connector_id, one_sec_before_utc, is_session_day=_twse_session_day)
        == session_t_minus_1
    )
    # Also support (now, connector_id=...) calling order.
    assert (
        latest_published_chip_session(one_sec_before_utc, connector_id=connector_id, is_session_day=_twse_session_day)
        == session_t_minus_1
    )
    # At exact publication instant and 1 second after: expects T.
    assert latest_published_chip_session(connector_id, exact_utc, is_session_day=_twse_session_day) == session_t
    assert latest_published_chip_session(connector_id, one_sec_after_utc, is_session_day=_twse_session_day) == session_t


@pytest.mark.parametrize("taipei_hm", [(14, 40), (15, 40), (18, 50)])
@pytest.mark.parametrize("connector_id", tuple(CHIP_PUBLICATION_SCHEDULES))
def test_afternoon_dev_host_timer_dispatches_resolve_to_prior_session_t_minus_1(
    taipei_hm: tuple[int, int],
    connector_id: str,
) -> None:
    """At 14:40, 15:40, and 18:50 Taipei on session T, every chip profile resolves to T-1."""
    now_utc = _taipei_dt(2026, 11, 18, taipei_hm[0], taipei_hm[1]).astimezone(timezone.utc)
    assert latest_published_chip_session(connector_id, now_utc, is_session_day=_twse_session_day) == date(2026, 11, 17)


@pytest.mark.parametrize("taipei_hm", [(7, 30), (8, 30)])
@pytest.mark.parametrize("connector_id", tuple(CHIP_PUBLICATION_SCHEDULES))
def test_morning_dispatches_on_t_plus_1_resolve_to_session_t(
    taipei_hm: tuple[int, int],
    connector_id: str,
) -> None:
    """At 07:30 and 08:30 Taipei on T+1, all six chip profiles have published session T."""
    now_utc = _taipei_dt(2026, 11, 19, taipei_hm[0], taipei_hm[1]).astimezone(timezone.utc)
    assert latest_published_chip_session(connector_id, now_utc, is_session_day=_twse_session_day) == date(2026, 11, 18)


def test_tpex_staggered_evening_window_between_2015_and_2215_taipei() -> None:
    """Between 20:15 and 22:14 Taipei on T, TPEx 3insti has published T while TPEx margin still serves T-1."""
    probe_2159_utc = datetime(2026, 11, 18, 13, 59, 45, tzinfo=timezone.utc)  # 21:59:45 Taipei
    assert (
        latest_published_chip_session(
            "tw-official-tpex-institutional-flow", probe_2159_utc, is_session_day=_twse_session_day
        )
        == date(2026, 11, 18)
    )
    assert (
        latest_published_chip_session(
            "tw-official-tpex-margin-balances", probe_2159_utc, is_session_day=_twse_session_day
        )
        == date(2026, 11, 17)
    )
    for next_morning_cid in (
        "tw-official-twse-margin-balances",
        "tw-taifex-institutional-positions",
        "tw-taifex-put-call",
        "tw-taifex-tx-daily",
    ):
        assert (
            latest_published_chip_session(next_morning_cid, probe_2159_utc, is_session_day=_twse_session_day)
            == date(2026, 11, 17)
        )


@pytest.mark.parametrize("connector_id", tuple(CHIP_PUBLICATION_SCHEDULES))
def test_friday_to_monday_and_evidenced_holidays(connector_id: str) -> None:
    # Friday 2026-11-13 at 18:50 Taipei -> Thursday 2026-11-12 for all 6 profiles.
    fri_afternoon_utc = _taipei_dt(2026, 11, 13, 18, 50).astimezone(timezone.utc)
    assert (
        latest_published_chip_session(connector_id, fri_afternoon_utc, is_session_day=_twse_session_day)
        == date(2026, 11, 12)
    )

    # Saturday 2026-11-14 at 11:18 Taipei (2026-11-14T03:18:52Z probe) -> Friday 2026-11-13 for all 6 profiles.
    sat_morning_utc = datetime(2026, 11, 14, 3, 18, 52, tzinfo=timezone.utc)
    assert (
        latest_published_chip_session(connector_id, sat_morning_utc, is_session_day=_twse_session_day)
        == date(2026, 11, 13)
    )

    # Monday 2026-11-16 at 04:00 Taipei (UTC still Sunday 2026-11-15T20:00:00Z) -> Friday 2026-11-13.
    mon_early_utc = datetime(2026, 11, 15, 20, 0, 0, tzinfo=timezone.utc)
    assert (
        latest_published_chip_session(connector_id, mon_early_utc, is_session_day=_twse_session_day)
        == date(2026, 11, 13)
    )

    # Monday 2026-11-16 at 15:40 Taipei -> still Friday 2026-11-13 (Monday not yet published).
    mon_afternoon_utc = _taipei_dt(2026, 11, 16, 15, 40).astimezone(timezone.utc)
    assert (
        latest_published_chip_session(connector_id, mon_afternoon_utc, is_session_day=_twse_session_day)
        == date(2026, 11, 13)
    )

    # Evidenced holiday: Friday 2026-10-09 (Double Tenth Day observed).
    # On Monday 2026-10-12 at 08:30 Taipei, latest published session is Thursday 2026-10-08.
    after_double_tenth_utc = _taipei_dt(2026, 10, 12, 8, 30).astimezone(timezone.utc)
    assert (
        latest_published_chip_session(connector_id, after_double_tenth_utc, is_session_day=_twse_session_day)
        == date(2026, 10, 8)
    )

    # Evidenced holiday: Friday 2026-10-23 (Retrocession Day compensatory holiday in TWSE calendar).
    # On Monday 2026-10-26 at 08:30 Taipei, latest published session is Thursday 2026-10-22.
    after_retrocession_utc = _taipei_dt(2026, 10, 26, 8, 30).astimezone(timezone.utc)
    assert (
        latest_published_chip_session(connector_id, after_retrocession_utc, is_session_day=_twse_session_day)
        == date(2026, 10, 22)
    )


@pytest.mark.parametrize("connector_id", tuple(CHIP_PUBLICATION_SCHEDULES))
def test_evaluate_chip_provider_session_distinguishes_not_yet_published_from_late(connector_id: str) -> None:
    session_t = date(2026, 11, 18)
    session_t_minus_1 = date(2026, 11, 17)
    pub_dt = expected_chip_publication_datetime(connector_id, session_t)
    schedule = get_chip_publication_schedule(connector_id)

    # 1. Provider serves session T after publication time -> published (ok).
    at_30m = (pub_dt + timedelta(minutes=30)).astimezone(timezone.utc)
    ok_verdict = evaluate_chip_provider_session(
        connector_id,
        provider_session=session_t.isoformat(),
        now=at_30m,
        is_session_day=_twse_session_day,
    )
    assert ok_verdict.status == "published"
    assert ok_verdict.reason == "published"
    assert ok_verdict.is_failure is False
    assert ok_verdict.expected_session == session_t
    assert ok_verdict.provider_session == "2026-11-18"

    # 2. Provider still serves T-1 within max_publication_delay (e.g. +40m and exact +150m bound) -> not_yet_published.
    for delta in (timedelta(minutes=40), schedule.max_publication_delay):
        within_bound = (pub_dt + delta).astimezone(timezone.utc)
        pending = evaluate_chip_provider_session(
            connector_id,
            provider_session=session_t_minus_1.isoformat(),
            now=within_bound,
            is_session_day=_twse_session_day,
        )
        assert pending.status == "not_yet_published"
        assert pending.reason == "not_yet_published"
        assert pending.is_failure is False
        assert pending.provider_session == "2026-11-17"
        assert pending.expected_session == session_t
        assert "2026-11-17" in pending.detail and "2026-11-18" in pending.detail
        assert "not_yet_published" in pending.detail

    # 3. Provider still serves T-1 past max_publication_delay (+150m + 1s) -> provider_publication_late (red failure).
    past_bound = (pub_dt + schedule.max_publication_delay + timedelta(seconds=1)).astimezone(timezone.utc)
    late = evaluate_chip_provider_session(
        connector_id,
        provider_session=session_t_minus_1.isoformat(),
        now=past_bound,
        is_session_day=_twse_session_day,
    )
    assert late.status == "provider_publication_late"
    assert late.reason == "provider_publication_late"
    assert late.is_failure is True
    assert late.provider_session == "2026-11-17"
    assert late.expected_session == session_t
    assert late.delay_seconds == int(schedule.max_publication_delay.total_seconds()) + 1
    assert "provider_publication_late" in late.detail
    assert "2026-11-17" in late.detail and "2026-11-18" in late.detail

    # 4. Missing or future provider session -> session_mismatch (red failure).
    for bad_session in ("", None, "2026-11-19"):
        mismatch = evaluate_chip_provider_session(
            connector_id,
            provider_session=bad_session,
            now=at_30m,
            is_session_day=_twse_session_day,
        )
        assert mismatch.status == "session_mismatch"
        assert mismatch.is_failure is True
        assert "2026-11-18" in mismatch.detail
