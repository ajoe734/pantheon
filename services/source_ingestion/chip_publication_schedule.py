"""Measured provider publication schedule for Taiwan official & TAIFEX chip profiles.

Each of the six governed Taiwan chip refresh profiles in ``TW_CHIP_REFRESH_PROFILES``
fetches an unparameterized "current snapshot" endpoint from TWSE OpenAPI, TPEx OpenAPI,
or TAIFEX OpenAPI. None of those endpoints publishes session ``T`` at the ``13:30``
Asia/Taipei cash close:

- TPEx ``tpex_3insti_daily_trading`` publishes session ``T`` around ``20:00:17`` Taipei
  on day ``T`` (scheduled with a 15-minute margin at ``20:15`` Taipei on ``T``).
- TPEx ``tpex_mainboard_margin_balance`` publishes session ``T`` around ``22:00:07``
  Taipei on day ``T`` (at ``20:00:07`` Taipei its HTTP ``Last-Modified`` header rotates
  while the JSON body still carries ``Date == T-1``; scheduled with a 15-minute margin
  at ``22:15`` Taipei on ``T``).
- TWSE ``MI_MARGN`` publishes session ``T`` in the next-morning OpenAPI rebuild around
  ``05:23:16`` Taipei on ``T+1`` (scheduled with a 17-minute margin at ``05:40`` Taipei
  on ``T+1``).
- TAIFEX ``MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate``,
  ``PutCallRatio``, and ``DailyMarketReportFut`` publish session ``T`` in the
  next-morning OpenAPI rebuild around ``06:36:02``–``06:36:26`` Taipei on ``T+1``
  (scheduled with a 14-minute margin at ``06:50`` Taipei on ``T+1``).

Asking these providers for session ``T`` during the afternoon (``14:40``, ``15:40``,
``18:50`` Taipei) guaranteed a session mismatch after stopping the steady collector
controller and recreating ``source-ingest``. By resolving the latest session each
provider has actually published at ``now``, preflight returns ``noop`` (``already_fresh``)
before any container mutation whenever the latest published session is already stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from .connector_definitions import TW_CHIP_REFRESH_PROFILES

TAIPEI_TZ = timezone(timedelta(hours=8))
DEFAULT_MAX_PUBLICATION_DELAY = timedelta(hours=2, minutes=30)

OFFICIAL_CHIP_REFRESH_PROFILES: dict[str, dict[str, Any]] = {
    connector_id: dict(profile)
    for connector_id, profile in TW_CHIP_REFRESH_PROFILES.items()
    if profile.get("base") != "tw-finmind-fundamentals"
}


@dataclass(frozen=True)
class ChipPublicationSchedule:
    """Measured publication schedule and bounded delay tolerance for one chip profile."""

    connector_id: str
    dataset: str
    provider: str
    endpoint: str
    earliest_observed_publication_time_taipei: time
    safety_margin_minutes: int
    publication_time_taipei: time
    publication_day_offset: int
    max_publication_delay: timedelta = DEFAULT_MAX_PUBLICATION_DELAY
    body_date_field: str | None = None
    notes: str = ""

    def expected_publication_datetime(self, session_date: date) -> datetime:
        """Return the Taipei datetime when ``session_date`` is scheduled to be published."""
        publish_calendar_day = session_date + timedelta(days=self.publication_day_offset)
        return datetime.combine(publish_calendar_day, self.publication_time_taipei, tzinfo=TAIPEI_TZ)


@dataclass(frozen=True)
class ChipPublicationVerdict:
    """Result of comparing a provider's served session date against the expected published session."""

    connector_id: str
    status: str
    reason: str
    is_failure: bool
    provider_session: str
    expected_session: date
    expected_published_at: datetime
    checked_at: datetime
    delay_seconds: int
    max_delay_seconds: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "status": self.status,
            "reason": self.reason,
            "reason_code": self.reason,
            "is_failure": self.is_failure,
            "provider_session": self.provider_session,
            "expected_session": self.expected_session.isoformat(),
            "expected_published_at": self.expected_published_at.isoformat(),
            "checked_at": self.checked_at.isoformat(),
            "delay_seconds": self.delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
            "detail": self.detail,
        }


CHIP_PUBLICATION_SCHEDULES: dict[str, ChipPublicationSchedule] = {
    "tw-official-twse-margin-balances": ChipPublicationSchedule(
        connector_id="tw-official-twse-margin-balances",
        dataset="tw_margin_short_balance",
        provider="TWSE",
        endpoint="https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN",
        earliest_observed_publication_time_taipei=time(5, 23, 16),
        safety_margin_minutes=17,
        publication_time_taipei=time(5, 40),
        publication_day_offset=1,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field=None,
        notes=(
            "TWSE OpenAPI MI_MARGN rebuilds overnight at ~05:23 Taipei on calendar day T+1 "
            "(Last-Modified: Tue, 17 Nov 2026 21:23:16 GMT for session 2026-11-17). "
            "Payload rows carry no Date field."
        ),
    ),
    "tw-official-tpex-margin-balances": ChipPublicationSchedule(
        connector_id="tw-official-tpex-margin-balances",
        dataset="tw_margin_short_balance",
        provider="TPEx",
        endpoint="https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance",
        earliest_observed_publication_time_taipei=time(22, 0, 7),
        safety_margin_minutes=15,
        publication_time_taipei=time(22, 15),
        publication_day_offset=0,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field="Date",
        notes=(
            "At 20:00:07 Taipei (12:00:07 GMT) TPEx updates Last-Modified while the JSON body "
            "still serves Date=T-1 (668 rows on 2026-11-18). The body switches to Date=T "
            "(1151118, 923 rows) at 22:00:07 Taipei (14:00:07 GMT)."
        ),
    ),
    "tw-official-tpex-institutional-flow": ChipPublicationSchedule(
        connector_id="tw-official-tpex-institutional-flow",
        dataset="tw_institutional_flow",
        provider="TPEx",
        endpoint="https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
        earliest_observed_publication_time_taipei=time(20, 0, 17),
        safety_margin_minutes=15,
        publication_time_taipei=time(20, 15),
        publication_day_offset=0,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field="Date",
        notes=(
            "TPEx 3insti OpenAPI publishes Date=T (ROC YYYMMDD) in the 20:00 Taipei batch "
            "(Last-Modified: Wed, 18 Nov 2026 12:00:17 GMT with Date=1151118)."
        ),
    ),
    "tw-taifex-institutional-positions": ChipPublicationSchedule(
        connector_id="tw-taifex-institutional-positions",
        dataset="taifex_futures_chip",
        provider="TAIFEX",
        endpoint=(
            "https://openapi.taifex.com.tw/v1/"
            "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
        ),
        earliest_observed_publication_time_taipei=time(6, 36, 26),
        safety_margin_minutes=14,
        publication_time_taipei=time(6, 50),
        publication_day_offset=1,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field="Date",
        notes=(
            "TAIFEX OpenAPI rebuilds all daily futures/options snapshots at ~06:36 Taipei on "
            "calendar day T+1 (Last-Modified: Tue, 17 Nov 2026 22:36:26 GMT; throughout "
            "2026-11-18 up to 22:12 Taipei the endpoint still served Date=20261117)."
        ),
    ),
    "tw-taifex-put-call": ChipPublicationSchedule(
        connector_id="tw-taifex-put-call",
        dataset="taifex_options_chip",
        provider="TAIFEX",
        endpoint="https://openapi.taifex.com.tw/v1/PutCallRatio",
        earliest_observed_publication_time_taipei=time(6, 36, 8),
        safety_margin_minutes=14,
        publication_time_taipei=time(6, 50),
        publication_day_offset=1,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field="Date",
        notes=(
            "TAIFEX OpenAPI PutCallRatio rebuilds at ~06:36:08 Taipei on calendar day T+1 "
            "(Last-Modified: Tue, 17 Nov 2026 22:36:08 GMT; throughout 2026-11-18 up to "
            "22:12 Taipei the endpoint still served Date=20261117)."
        ),
    ),
    "tw-taifex-tx-daily": ChipPublicationSchedule(
        connector_id="tw-taifex-tx-daily",
        dataset="taifex_tx_daily",
        provider="TAIFEX",
        endpoint="https://openapi.taifex.com.tw/v1/DailyMarketReportFut",
        earliest_observed_publication_time_taipei=time(6, 36, 2),
        safety_margin_minutes=14,
        publication_time_taipei=time(6, 50),
        publication_day_offset=1,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field="Date",
        notes=(
            "TAIFEX OpenAPI DailyMarketReportFut rebuilds at ~06:36:02 Taipei on calendar day T+1 "
            "(Last-Modified: Tue, 17 Nov 2026 22:36:02 GMT; throughout 2026-11-18 up to "
            "22:12 Taipei the endpoint still served Date=20261117)."
        ),
    ),
}

if set(CHIP_PUBLICATION_SCHEDULES) != set(OFFICIAL_CHIP_REFRESH_PROFILES):
    raise RuntimeError(
        "CHIP_PUBLICATION_SCHEDULES must cover every non-fundamental profile in TW_CHIP_REFRESH_PROFILES: "
        f"scheduled={sorted(CHIP_PUBLICATION_SCHEDULES)} != expected={sorted(OFFICIAL_CHIP_REFRESH_PROFILES)}"
    )

MEASURED_CHIP_PROVIDER_PROBES: tuple[dict[str, Any], ...] = (
    {
        "probe_id": "2026-11-14-morning-post-publication",
        "probed_at_utc": "2026-11-14T03:18:52Z",
        "probed_at_taipei": "2026-11-14T11:18:52+08:00",
        "latest_closed_session": "2026-11-13",
        "observations": {
            "tw-official-twse-margin-balances": {
                "http_status": 200,
                "row_count": 1309,
                "body_date_raw": None,
                "resolved_session": "2026-11-13",
            },
            "tw-official-tpex-margin-balances": {
                "http_status": 200,
                "row_count": 922,
                "body_date_raw": "1151113",
                "resolved_session": "2026-11-13",
            },
            "tw-official-tpex-institutional-flow": {
                "http_status": 200,
                "row_count": 896,
                "body_date_raw": "1151113",
                "resolved_session": "2026-11-13",
            },
            "tw-taifex-institutional-positions": {
                "http_status": 200,
                "row_count": 66,
                "body_date_raw": "20261113",
                "resolved_session": "2026-11-13",
            },
            "tw-taifex-put-call": {
                "http_status": 200,
                "row_count": 21,
                "body_date_raw": "20261113",
                "resolved_session": "2026-11-13",
            },
            "tw-taifex-tx-daily": {
                "http_status": 200,
                "row_count": 2305,
                "body_date_raw": "20261113",
                "resolved_session": "2026-11-13",
            },
        },
    },
    {
        "probe_id": "2026-11-18-morning-post-overnight-rebuild",
        "probed_at_utc": "2026-11-18T00:26:25Z",
        "probed_at_taipei": "2026-11-18T08:26:25+08:00",
        "latest_closed_session": "2026-11-17",
        "observations": {
            "tw-official-twse-margin-balances": {
                "http_status": 200,
                "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT",
                "last_modified_taipei": "2026-11-18T05:23:16+08:00",
                "row_count": 1310,
                "body_date_raw": None,
                "resolved_session": "2026-11-17",
            },
            "tw-official-tpex-margin-balances": {
                "http_status": 200,
                "row_count": 922,
                "body_date_raw": "1151117",
                "resolved_session": "2026-11-17",
            },
            "tw-official-tpex-institutional-flow": {
                "http_status": 200,
                "row_count": 896,
                "body_date_raw": "1151117",
                "resolved_session": "2026-11-17",
            },
            "tw-taifex-institutional-positions": {
                "http_status": 200,
                "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT",
                "last_modified_taipei": "2026-11-18T06:36:26+08:00",
                "row_count": 72,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
            },
            "tw-taifex-put-call": {
                "http_status": 200,
                "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT",
                "last_modified_taipei": "2026-11-18T06:36:08+08:00",
                "row_count": 20,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
            },
            "tw-taifex-tx-daily": {
                "http_status": 200,
                "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT",
                "last_modified_taipei": "2026-11-18T06:36:02+08:00",
                "row_count": 2342,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
            },
        },
    },
    {
        "probe_id": "2026-11-18-evening-pre-2200-taipei",
        "probed_at_utc": "2026-11-18T13:59:45Z",
        "probed_at_taipei": "2026-11-18T21:59:45+08:00",
        "latest_closed_session": "2026-11-18",
        "observations": {
            "tw-official-twse-margin-balances": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 13:59:45 GMT",
                "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT",
                "last_modified_taipei": "2026-11-18T05:23:16+08:00",
                "row_count": 1310,
                "body_date_raw": None,
                "resolved_session": "2026-11-17",
                "sha256": "115f0e21f7e58d2bd4b1e8ef6f73e9e4c8dc09e8f131f73c76da24d510a29140",
            },
            "tw-official-tpex-margin-balances": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 13:59:46 GMT",
                "last_modified": "Wed, 18 Nov 2026 12:00:07 GMT",
                "last_modified_taipei": "2026-11-18T20:00:07+08:00",
                "row_count": 668,
                "body_date_raw": "1151117",
                "resolved_session": "2026-11-17",
                "sha256": "c6f000b4011ffdded346367da05c9f1a611e1d4a4c0969be76601a4a55f235cf",
            },
            "tw-official-tpex-institutional-flow": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 13:59:46 GMT",
                "last_modified": "Wed, 18 Nov 2026 12:00:17 GMT",
                "last_modified_taipei": "2026-11-18T20:00:17+08:00",
                "row_count": 899,
                "body_date_raw": "1151118",
                "resolved_session": "2026-11-18",
                "sha256": "0da8526a5ff567e58d6d71b820d1f77383a38a623645123e244c50dad4026828",
            },
            "tw-taifex-institutional-positions": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 13:59:47 GMT",
                "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT",
                "last_modified_taipei": "2026-11-18T06:36:26+08:00",
                "row_count": 72,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
                "sha256": "be8c544b86debbba683ce7195079d6598f614c17a78e67bcba3dc8a316db583a",
            },
            "tw-taifex-put-call": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 13:59:47 GMT",
                "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT",
                "last_modified_taipei": "2026-11-18T06:36:08+08:00",
                "row_count": 20,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
                "sha256": "f053b1661d507dd6b235e9d918b5f5c0020816f617ca3a8bbbc169a38513c8eb",
            },
            "tw-taifex-tx-daily": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 13:59:47 GMT",
                "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT",
                "last_modified_taipei": "2026-11-18T06:36:02+08:00",
                "row_count": 2342,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
                "sha256": "43343da3fc5dd9f3c3363b81fb4cf16060b0f6e2793de1de0fc4c7bc4c587de3",
            },
        },
    },
    {
        "probe_id": "2026-11-18-evening-post-2200-taipei",
        "probed_at_utc": "2026-11-18T14:12:52Z",
        "probed_at_taipei": "2026-11-18T22:12:52+08:00",
        "latest_closed_session": "2026-11-18",
        "observations": {
            "tw-official-twse-margin-balances": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 14:12:52 GMT",
                "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT",
                "last_modified_taipei": "2026-11-18T05:23:16+08:00",
                "row_count": 1310,
                "body_date_raw": None,
                "resolved_session": "2026-11-17",
                "sha256": "115f0e21f7e58d2bd4b1e8ef6f73e9e4c8dc09e8f131f73c76da24d510a29140",
            },
            "tw-official-tpex-margin-balances": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 14:12:53 GMT",
                "last_modified": "Wed, 18 Nov 2026 14:00:07 GMT",
                "last_modified_taipei": "2026-11-18T22:00:07+08:00",
                "row_count": 923,
                "body_date_raw": "1151118",
                "resolved_session": "2026-11-18",
                "sha256": "a1da5bd1bc2f77ac41d15cabb9e551c46c77d70fa957b1edd87487eb490d07d4",
            },
            "tw-official-tpex-institutional-flow": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 14:12:54 GMT",
                "last_modified": "Wed, 18 Nov 2026 14:00:18 GMT",
                "last_modified_taipei": "2026-11-18T22:00:18+08:00",
                "row_count": 899,
                "body_date_raw": "1151118",
                "resolved_session": "2026-11-18",
                "sha256": "0da8526a5ff567e58d6d71b820d1f77383a38a623645123e244c50dad4026828",
            },
            "tw-taifex-institutional-positions": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 14:12:54 GMT",
                "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT",
                "last_modified_taipei": "2026-11-18T06:36:26+08:00",
                "row_count": 72,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
                "sha256": "be8c544b86debbba683ce7195079d6598f614c17a78e67bcba3dc8a316db583a",
            },
            "tw-taifex-put-call": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 14:12:54 GMT",
                "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT",
                "last_modified_taipei": "2026-11-18T06:36:08+08:00",
                "row_count": 20,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
                "sha256": "f053b1661d507dd6b235e9d918b5f5c0020816f617ca3a8bbbc169a38513c8eb",
            },
            "tw-taifex-tx-daily": {
                "http_status": 200,
                "date_header": "Wed, 18 Nov 2026 14:12:54 GMT",
                "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT",
                "last_modified_taipei": "2026-11-18T06:36:02+08:00",
                "row_count": 2342,
                "body_date_raw": "20261117",
                "resolved_session": "2026-11-17",
                "sha256": "43343da3fc5dd9f3c3363b81fb4cf16060b0f6e2793de1de0fc4c7bc4c587de3",
            },
        },
    },
)


def get_chip_publication_schedule(connector_id: str) -> ChipPublicationSchedule:
    """Return the governed publication schedule for ``connector_id``."""
    clean_id = str(connector_id or "").strip()
    if clean_id not in CHIP_PUBLICATION_SCHEDULES:
        raise KeyError(f"unknown chip refresh profile: {clean_id!r}")
    return CHIP_PUBLICATION_SCHEDULES[clean_id]


def expected_chip_publication_datetime(connector_id: str, session_date: date) -> datetime:
    """Return the Taipei datetime at which ``session_date`` is scheduled to be published."""
    schedule = get_chip_publication_schedule(connector_id)
    return schedule.expected_publication_datetime(session_date)


def _resolve_connector_and_now(
    connector_id_or_now: str | datetime,
    now_or_connector_id: datetime | str | None = None,
    *,
    connector_id: str | None = None,
) -> tuple[str, datetime]:
    if isinstance(connector_id_or_now, datetime):
        resolved_now = connector_id_or_now
        resolved_connector = connector_id if connector_id is not None else now_or_connector_id
    else:
        resolved_connector = connector_id_or_now
        resolved_now = now_or_connector_id  # type: ignore[assignment]
    if not isinstance(resolved_connector, str) or not resolved_connector.strip():
        raise ValueError("connector_id is required")
    if not isinstance(resolved_now, datetime) or resolved_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return resolved_connector.strip(), resolved_now


def latest_published_chip_session(
    connector_id_or_now: str | datetime,
    now_or_connector_id: datetime | str | None = None,
    *,
    connector_id: str | None = None,
    is_session_day: Callable[[date], bool] | None = None,
    max_lookback_days: int = 20,
) -> date:
    """Latest trading session whose dataset for ``connector_id`` is already published at ``now``.

    ``now`` must be timezone-aware; the Taipei calendar date/time is derived from it so
    UTC/Taipei date boundaries are handled accurately. Walks back over weekends and
    ``is_session_day`` closures until finding the most recent trading session ``S`` whose
    scheduled publication datetime ``expected_chip_publication_datetime(connector_id, S) <= now``.
    """
    resolved_id, resolved_now = _resolve_connector_and_now(
        connector_id_or_now, now_or_connector_id, connector_id=connector_id
    )
    schedule = get_chip_publication_schedule(resolved_id)
    now_t = resolved_now.astimezone(TAIPEI_TZ)
    cursor = now_t.date()
    for _ in range(max_lookback_days):
        if cursor.weekday() < 5 and (is_session_day is None or bool(is_session_day(cursor))):
            if now_t >= schedule.expected_publication_datetime(cursor):
                return cursor
        cursor -= timedelta(days=1)
    raise ValueError(f"no published chip session found for {resolved_id} within lookback window")


def evaluate_chip_provider_session(
    connector_id: str,
    *,
    provider_session: date | str | None,
    now: datetime,
    is_session_day: Callable[[date], bool] | None = None,
    max_lookback_days: int = 20,
) -> ChipPublicationVerdict:
    """Compare the session date served by the provider against the expected published session.

    - When ``provider_session == expected_session``: returns ``status="published"``, ``is_failure=False``.
    - When ``provider_session < expected_session`` (provider still serving a prior session after
      ``expected_session``'s scheduled publication time):
      - If ``now <= expected_published_at + schedule.max_publication_delay``: returns
        ``status="not_yet_published"``, ``reason="not_yet_published"``, ``is_failure=False``.
      - If ``now > expected_published_at + schedule.max_publication_delay``: returns
        ``status="provider_publication_late"``, ``reason="provider_publication_late"``, ``is_failure=True``.
    - Otherwise (missing, malformed, or future session date): returns ``status="session_mismatch"``,
      ``reason="provider_session_mismatch"``, ``is_failure=True``.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    schedule = get_chip_publication_schedule(connector_id)
    expected_session = latest_published_chip_session(
        connector_id,
        now,
        is_session_day=is_session_day,
        max_lookback_days=max_lookback_days,
    )
    expected_published_at = schedule.expected_publication_datetime(expected_session)
    now_t = now.astimezone(TAIPEI_TZ)
    delay_seconds = max(0, int((now_t - expected_published_at).total_seconds()))
    max_delay_seconds = int(schedule.max_publication_delay.total_seconds())

    parsed_provider: date | None = None
    if isinstance(provider_session, date) and not isinstance(provider_session, datetime):
        parsed_provider = provider_session
        provider_text = provider_session.isoformat()
    else:
        provider_text = str(provider_session or "").strip()[:10]
        if provider_text:
            try:
                parsed_provider = date.fromisoformat(provider_text)
            except ValueError:
                parsed_provider = None

    expected_text = expected_session.isoformat()
    if parsed_provider == expected_session:
        return ChipPublicationVerdict(
            connector_id=connector_id,
            status="published",
            reason="published",
            is_failure=False,
            provider_session=expected_text,
            expected_session=expected_session,
            expected_published_at=expected_published_at,
            checked_at=now_t,
            delay_seconds=delay_seconds,
            max_delay_seconds=max_delay_seconds,
            detail=(
                f"bounded chip provider session {expected_text!r} == expected {expected_text!r} "
                f"for {connector_id}"
            ),
        )

    if parsed_provider is not None and parsed_provider < expected_session:
        if delay_seconds <= max_delay_seconds:
            return ChipPublicationVerdict(
                connector_id=connector_id,
                status="not_yet_published",
                reason="not_yet_published",
                is_failure=False,
                provider_session=provider_text,
                expected_session=expected_session,
                expected_published_at=expected_published_at,
                checked_at=now_t,
                delay_seconds=delay_seconds,
                max_delay_seconds=max_delay_seconds,
                detail=(
                    f"not_yet_published: bounded chip provider session {provider_text!r} != expected "
                    f"{expected_text!r} for {connector_id} (provider_session={provider_text}, "
                    f"expected_session={expected_text}, expected_published_at={expected_published_at.isoformat()}, "
                    f"delay_seconds={delay_seconds} <= max_delay_seconds={max_delay_seconds})"
                ),
            )
        return ChipPublicationVerdict(
            connector_id=connector_id,
            status="provider_publication_late",
            reason="provider_publication_late",
            is_failure=True,
            provider_session=provider_text,
            expected_session=expected_session,
            expected_published_at=expected_published_at,
            checked_at=now_t,
            delay_seconds=delay_seconds,
            max_delay_seconds=max_delay_seconds,
            detail=(
                f"provider_publication_late: bounded chip provider session {provider_text!r} != requested "
                f"{expected_text!r} for {connector_id} (provider_session={provider_text}, "
                f"expected_session={expected_text}, expected_published_at={expected_published_at.isoformat()}, "
                f"delay_seconds={delay_seconds} > max_delay_seconds={max_delay_seconds})"
            ),
        )

    return ChipPublicationVerdict(
        connector_id=connector_id,
        status="session_mismatch",
        reason="provider_session_mismatch",
        is_failure=True,
        provider_session=provider_text,
        expected_session=expected_session,
        expected_published_at=expected_published_at,
        checked_at=now_t,
        delay_seconds=delay_seconds,
        max_delay_seconds=max_delay_seconds,
        detail=(
            f"bounded chip provider session {provider_text!r} != requested {expected_text!r} "
            f"for {connector_id} (provider_session={provider_text!r}, expected_session={expected_text!r})"
        ),
    )


__all__ = [
    "CHIP_PUBLICATION_SCHEDULES",
    "ChipPublicationSchedule",
    "ChipPublicationVerdict",
    "DEFAULT_MAX_PUBLICATION_DELAY",
    "MEASURED_CHIP_PROVIDER_PROBES",
    "OFFICIAL_CHIP_REFRESH_PROFILES",
    "TAIPEI_TZ",
    "evaluate_chip_provider_session",
    "expected_chip_publication_datetime",
    "get_chip_publication_schedule",
    "latest_published_chip_session",
]
