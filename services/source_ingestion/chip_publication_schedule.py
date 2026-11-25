"""Measured provider publication schedule for Taiwan official & TAIFEX chip and contract profiles.

Each of the six governed Taiwan chip refresh profiles in ``TW_CHIP_REFRESH_PROFILES``
and the TAIFEX stock-futures contract refresh (``tw-taifex-futures-options-chip`` with
dataset ``taifex_ssf_contracts``) fetches an unparameterized "current snapshot" endpoint
from TWSE OpenAPI, TPEx OpenAPI, or TAIFEX OpenAPI. None of those endpoints publishes
session ``T`` at the ``13:30`` Asia/Taipei cash close:

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
- TAIFEX stock-futures contracts (``tw-taifex-futures-options-chip`` /
  ``taifex_ssf_contracts``) join ``DailyMarketReportFut`` (``06:36:02`` Taipei ``T+1``),
  ``SingleStockFuturesMargining`` (``06:37:00`` Taipei ``T+1``),
  ``SingleStockFuturesETFMargining`` (``06:37:02`` Taipei ``T+1``), and
  ``FinalSettlementPriceSSF`` (``06:39:14`` Taipei ``T+1``); scheduled with an
  11-minute margin after final settlement (13 minutes after margin tables) at ``06:50``
  Taipei on ``T+1``.

Asking these providers for session ``T`` during the afternoon (``14:40``, ``15:40``,
``18:50`` Taipei) guaranteed a session mismatch after stopping the steady collector
controller and recreating ``source-ingest``. By resolving the latest session each
provider has actually published at ``now``, preflight returns ``noop`` (``already_fresh``)
before any container mutation whenever the latest published session is already stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .connector_definitions import TW_CHIP_REFRESH_PROFILES

TAIPEI_TZ = timezone(timedelta(hours=8))
DEFAULT_MAX_PUBLICATION_DELAY = timedelta(hours=2, minutes=30)

OFFICIAL_CHIP_REFRESH_PROFILES: dict[str, dict[str, Any]] = {
    **{
        connector_id: dict(profile)
        for connector_id, profile in TW_CHIP_REFRESH_PROFILES.items()
        if profile.get("base") != "tw-finmind-fundamentals"
    },
    "tw-taifex-futures-options-chip": {
        "base": "tw-taifex-futures-options-chip",
        "dataset": "taifex_ssf_contracts",
        "schema_hash": "tw_taifex_ssf_contracts.v1",
    },
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
    "tw-taifex-futures-options-chip": ChipPublicationSchedule(
        connector_id="tw-taifex-futures-options-chip",
        dataset="taifex_ssf_contracts",
        provider="TAIFEX",
        endpoint="https://openapi.taifex.com.tw/v1/SingleStockFuturesMargining",
        earliest_observed_publication_time_taipei=time(6, 39, 14),
        safety_margin_minutes=11,
        publication_time_taipei=time(6, 50),
        publication_day_offset=1,
        max_publication_delay=DEFAULT_MAX_PUBLICATION_DELAY,
        body_date_field="Date",
        notes=(
            "TAIFEX OpenAPI rebuilds the stock-futures contract tables in the next-morning batch on "
            "calendar day T+1: DailyMarketReportFut at 06:36:02 Taipei (22:36:02 GMT), "
            "SingleStockFuturesMargining at 06:37:00 Taipei (22:37:00 GMT, Date=20261117, 301 rows), "
            "SingleStockFuturesETFMargining at 06:37:02 Taipei (22:37:02 GMT, Date=20261117, 25 rows), "
            "and FinalSettlementPriceSSF at 06:39:14 Taipei (22:39:14 GMT, 330 rows). "
            "Scheduled at 06:50 Taipei on T+1 (13m margin after margin tables, 11m after final settlement)."
        ),
    ),
}

if set(CHIP_PUBLICATION_SCHEDULES) != set(OFFICIAL_CHIP_REFRESH_PROFILES):
    raise RuntimeError(
        "CHIP_PUBLICATION_SCHEDULES must cover every official/TAIFEX chip and contract refresh profile: "
        f"scheduled={sorted(CHIP_PUBLICATION_SCHEDULES)} != expected={sorted(OFFICIAL_CHIP_REFRESH_PROFILES)}"
    )

MEASURED_CHIP_PROVIDER_PROBES: tuple[dict[str, Any], ...] = (
    {
        "probe_id": "2026-11-14-morning-post-publication",
        "probed_at_utc": "2026-11-14T03:18:52Z",
        "probed_at_taipei": "2026-11-14T11:18:52+08:00",
        "latest_closed_session": "2026-11-13",
        "observations": {
            "tw-official-twse-margin-balances": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 21:23:00 GMT", "row_count": 1309, "body_date_raw": None, "resolved_session": "2026-11-13"},
            "tw-official-tpex-margin-balances": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 14:00:07 GMT", "row_count": 922, "body_date_raw": "1151113", "resolved_session": "2026-11-13"},
            "tw-official-tpex-institutional-flow": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 12:00:17 GMT", "row_count": 896, "body_date_raw": "1151113", "resolved_session": "2026-11-13"},
            "tw-taifex-institutional-positions": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 22:36:26 GMT", "row_count": 66, "body_date_raw": "20261113", "resolved_session": "2026-11-13"},
            "tw-taifex-put-call": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 22:36:08 GMT", "row_count": 21, "body_date_raw": "20261113", "resolved_session": "2026-11-13"},
            "tw-taifex-tx-daily": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 22:36:02 GMT", "row_count": 2305, "body_date_raw": "20261113", "resolved_session": "2026-11-13"},
            "tw-taifex-futures-options-chip": {"http_status": 200, "last_modified": "Fri, 13 Nov 2026 22:37:00 GMT", "etf_margin_last_modified": "Fri, 13 Nov 2026 22:37:02 GMT", "final_settlement_last_modified": "Fri, 13 Nov 2026 22:39:14 GMT", "row_count": 301, "body_date_raw": "20261113", "resolved_session": "2026-11-13"},
        },
    },
    {
        "probe_id": "2026-11-18-morning-post-overnight-rebuild",
        "probed_at_utc": "2026-11-18T00:26:25Z",
        "probed_at_taipei": "2026-11-18T08:26:25+08:00",
        "latest_closed_session": "2026-11-17",
        "observations": {
            "tw-official-twse-margin-balances": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT", "last_modified_taipei": "2026-11-18T05:23:16+08:00", "row_count": 1310, "body_date_raw": None, "resolved_session": "2026-11-17"},
            "tw-official-tpex-margin-balances": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 14:00:07 GMT", "row_count": 922, "body_date_raw": "1151117", "resolved_session": "2026-11-17"},
            "tw-official-tpex-institutional-flow": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 12:00:17 GMT", "row_count": 896, "body_date_raw": "1151117", "resolved_session": "2026-11-17"},
            "tw-taifex-institutional-positions": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT", "last_modified_taipei": "2026-11-18T06:36:26+08:00", "row_count": 72, "body_date_raw": "20261117", "resolved_session": "2026-11-17"},
            "tw-taifex-put-call": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT", "last_modified_taipei": "2026-11-18T06:36:08+08:00", "row_count": 20, "body_date_raw": "20261117", "resolved_session": "2026-11-17"},
            "tw-taifex-tx-daily": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT", "last_modified_taipei": "2026-11-18T06:36:02+08:00", "row_count": 2342, "body_date_raw": "20261117", "resolved_session": "2026-11-17"},
            "tw-taifex-futures-options-chip": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:37:00 GMT", "last_modified_taipei": "2026-11-18T06:37:00+08:00", "etf_margin_last_modified": "Tue, 17 Nov 2026 22:37:02 GMT", "final_settlement_last_modified": "Tue, 17 Nov 2026 22:39:14 GMT", "row_count": 301, "etf_margin_row_count": 25, "final_settlement_row_count": 330, "body_date_raw": "20261117", "final_settlement_day_raw": "20261021", "resolved_session": "2026-11-17"},
        },
    },
    {
        "probe_id": "2026-11-18-evening-2154-taipei-coordinator",
        "probed_at_utc": "2026-11-18T13:54:33Z",
        "probed_at_taipei": "2026-11-18T21:54:33+08:00",
        "latest_closed_session": "2026-11-18",
        "observations": {
            "tw-official-twse-margin-balances": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT", "last_modified_taipei": "2026-11-18T05:23:16+08:00", "row_count": 1310, "body_date_raw": None, "resolved_session": "2026-11-17"},
            "tw-official-tpex-margin-balances": {"http_status": 200, "last_modified": "Wed, 18 Nov 2026 12:00:07 GMT", "last_modified_taipei": "2026-11-18T20:00:07+08:00", "row_count": 668, "body_date_raw": "1151117", "resolved_session": "2026-11-17"},
            "tw-official-tpex-institutional-flow": {"http_status": 200, "last_modified": "Wed, 18 Nov 2026 12:00:17 GMT", "last_modified_taipei": "2026-11-18T20:00:17+08:00", "row_count": 899, "body_date_raw": "1151118", "resolved_session": "2026-11-18"},
            "tw-taifex-institutional-positions": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT", "last_modified_taipei": "2026-11-18T06:36:26+08:00", "row_count": 72, "body_date_raw": "20261117", "resolved_session": "2026-11-17"},
            "tw-taifex-put-call": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT", "last_modified_taipei": "2026-11-18T06:36:08+08:00", "row_count": 20, "body_date_raw": "20261117", "resolved_session": "2026-11-17"},
            "tw-taifex-tx-daily": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT", "last_modified_taipei": "2026-11-18T06:36:02+08:00", "row_count": 2342, "body_date_raw": "20261117", "resolved_session": "2026-11-17"},
            "tw-taifex-futures-options-chip": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:37:00 GMT", "last_modified_taipei": "2026-11-18T06:37:00+08:00", "etf_margin_last_modified": "Tue, 17 Nov 2026 22:37:02 GMT", "final_settlement_last_modified": "Tue, 17 Nov 2026 22:39:14 GMT", "row_count": 301, "etf_margin_row_count": 25, "final_settlement_row_count": 330, "body_date_raw": "20261117", "final_settlement_day_raw": "20261021", "resolved_session": "2026-11-17"},
        },
    },
    {
        "probe_id": "2026-11-18-evening-pre-2200-taipei",
        "probed_at_utc": "2026-11-18T13:59:45Z",
        "probed_at_taipei": "2026-11-18T21:59:45+08:00",
        "latest_closed_session": "2026-11-18",
        "observations": {
            "tw-official-twse-margin-balances": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 13:59:45 GMT", "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT", "last_modified_taipei": "2026-11-18T05:23:16+08:00", "row_count": 1310, "body_date_raw": None, "resolved_session": "2026-11-17", "sha256": "115f0e21f7e58d2bd4b1e8ef6f73e9e4c8dc09e8f131f73c76da24d510a29140"},
            "tw-official-tpex-margin-balances": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 13:59:46 GMT", "last_modified": "Wed, 18 Nov 2026 12:00:07 GMT", "last_modified_taipei": "2026-11-18T20:00:07+08:00", "row_count": 668, "body_date_raw": "1151117", "resolved_session": "2026-11-17", "sha256": "c6f000b4011ffdded346367da05c9f1a611e1d4a4c0969be76601a4a55f235cf"},
            "tw-official-tpex-institutional-flow": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 13:59:46 GMT", "last_modified": "Wed, 18 Nov 2026 12:00:17 GMT", "last_modified_taipei": "2026-11-18T20:00:17+08:00", "row_count": 899, "body_date_raw": "1151118", "resolved_session": "2026-11-18", "sha256": "0da8526a5ff567e58d6d71b820d1f77383a38a623645123e244c50dad4026828"},
            "tw-taifex-institutional-positions": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 13:59:47 GMT", "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT", "last_modified_taipei": "2026-11-18T06:36:26+08:00", "row_count": 72, "body_date_raw": "20261117", "resolved_session": "2026-11-17", "sha256": "be8c544b86debbba683ce7195079d6598f614c17a78e67bcba3dc8a316db583a"},
            "tw-taifex-put-call": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 13:59:47 GMT", "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT", "last_modified_taipei": "2026-11-18T06:36:08+08:00", "row_count": 20, "body_date_raw": "20261117", "resolved_session": "2026-11-17", "sha256": "f053b1661d507dd6b235e9d918b5f5c0020816f617ca3a8bbbc169a38513c8eb"},
            "tw-taifex-tx-daily": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 13:59:47 GMT", "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT", "last_modified_taipei": "2026-11-18T06:36:02+08:00", "row_count": 2342, "body_date_raw": "20261117", "resolved_session": "2026-11-17", "sha256": "43343da3fc5dd9f3c3363b81fb4cf16060b0f6e2793de1de0fc4c7bc4c587de3"},
            "tw-taifex-futures-options-chip": {"http_status": 200, "last_modified": "Tue, 17 Nov 2026 22:37:00 GMT", "last_modified_taipei": "2026-11-18T06:37:00+08:00", "etf_margin_last_modified": "Tue, 17 Nov 2026 22:37:02 GMT", "final_settlement_last_modified": "Tue, 17 Nov 2026 22:39:14 GMT", "row_count": 301, "etf_margin_row_count": 25, "final_settlement_row_count": 330, "body_date_raw": "20261117", "final_settlement_day_raw": "20261021", "resolved_session": "2026-11-17", "sha256": "961ddabbd98ba1c70e0f83fb59bad76d0f4c848444c593eb91a4614b9075762d"},
        },
    },
    {
        "probe_id": "2026-11-18-evening-post-2200-taipei",
        "probed_at_utc": "2026-11-18T14:12:52Z",
        "probed_at_taipei": "2026-11-18T22:12:52+08:00",
        "latest_closed_session": "2026-11-18",
        "observations": {
            "tw-official-twse-margin-balances": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 14:12:52 GMT", "last_modified": "Tue, 17 Nov 2026 21:23:16 GMT", "last_modified_taipei": "2026-11-18T05:23:16+08:00", "row_count": 1310, "body_date_raw": None, "resolved_session": "2026-11-17", "sha256": "115f0e21f7e58d2bd4b1e8ef6f73e9e4c8dc09e8f131f73c76da24d510a29140"},
            "tw-official-tpex-margin-balances": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 14:12:53 GMT", "last_modified": "Wed, 18 Nov 2026 14:00:07 GMT", "last_modified_taipei": "2026-11-18T22:00:07+08:00", "row_count": 923, "body_date_raw": "1151118", "resolved_session": "2026-11-18", "sha256": "a1da5bd1bc2f77ac41d15cabb9e551c46c77d70fa957b1edd87487eb490d07d4"},
            "tw-official-tpex-institutional-flow": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 14:12:54 GMT", "last_modified": "Wed, 18 Nov 2026 14:00:18 GMT", "last_modified_taipei": "2026-11-18T22:00:18+08:00", "row_count": 899, "body_date_raw": "1151118", "resolved_session": "2026-11-18", "sha256": "0da8526a5ff567e58d6d71b820d1f77383a38a623645123e244c50dad4026828"},
            "tw-taifex-institutional-positions": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 14:12:54 GMT", "last_modified": "Tue, 17 Nov 2026 22:36:26 GMT", "last_modified_taipei": "2026-11-18T06:36:26+08:00", "row_count": 72, "body_date_raw": "20261117", "resolved_session": "2026-11-17", "sha256": "be8c544b86debbba683ce7195079d6598f614c17a78e67bcba3dc8a316db583a"},
            "tw-taifex-put-call": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 14:12:54 GMT", "last_modified": "Tue, 17 Nov 2026 22:36:08 GMT", "last_modified_taipei": "2026-11-18T06:36:08+08:00", "row_count": 20, "body_date_raw": "20261117", "resolved_session": "2026-11-17", "sha256": "f053b1661d507dd6b235e9d918b5f5c0020816f617ca3a8bbbc169a38513c8eb"},
            "tw-taifex-tx-daily": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 14:12:54 GMT", "last_modified": "Tue, 17 Nov 2026 22:36:02 GMT", "last_modified_taipei": "2026-11-18T06:36:02+08:00", "row_count": 2342, "body_date_raw": "20261117", "resolved_session": "2026-11-17", "sha256": "43343da3fc5dd9f3c3363b81fb4cf16060b0f6e2793de1de0fc4c7bc4c587de3"},
            "tw-taifex-futures-options-chip": {"http_status": 200, "date_header": "Wed, 18 Nov 2026 16:39:26 GMT", "last_modified": "Tue, 17 Nov 2026 22:37:00 GMT", "last_modified_taipei": "2026-11-18T06:37:00+08:00", "etf_margin_last_modified": "Tue, 17 Nov 2026 22:37:02 GMT", "final_settlement_last_modified": "Tue, 17 Nov 2026 22:39:14 GMT", "row_count": 301, "etf_margin_row_count": 25, "final_settlement_row_count": 330, "body_date_raw": "20261117", "final_settlement_day_raw": "20261021", "resolved_session": "2026-11-17", "sha256": "961ddabbd98ba1c70e0f83fb59bad76d0f4c848444c593eb91a4614b9075762d"},
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
    - When ``provider_session > expected_session``:
      - If ``provider_session`` is after the Taipei date of ``now``, or equals the Taipei date of ``now``
        before that day's 13:30 close: returns ``status="future_session"``, ``reason="future_session"``,
        ``is_failure=True``.
      - If ``provider_session`` is a trading session day not after the latest closed Taipei date:
        returns ``status="published_early"``, ``reason="published_early"``, ``is_failure=False``.
    - When ``provider_session < expected_session`` (provider still serving a prior session after
      ``expected_session``'s scheduled publication time):
      - If ``now <= expected_published_at + schedule.max_publication_delay``: returns
        ``status="not_yet_published"``, ``reason="not_yet_published"``, ``is_failure=False``.
      - If ``now > expected_published_at + schedule.max_publication_delay``: returns
        ``status="provider_publication_late"``, ``reason="provider_publication_late"``, ``is_failure=True``.
    - Otherwise (missing, malformed, or non-session date): returns ``status="session_mismatch"``,
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
                f"provider_publication_late: bounded chip provider session {provider_text!r} != requested session "
                f"{expected_text!r} for {connector_id} (provider_session={provider_text}, "
                f"expected_session={expected_text}, expected_published_at={expected_published_at.isoformat()}, "
                f"delay_seconds={delay_seconds} > max_delay_seconds={max_delay_seconds})"
            ),
        )

    if parsed_provider is not None and parsed_provider > expected_session:
        latest_closed_taipei_date = (
            now_t.date() if now_t.time() >= time(13, 30) else now_t.date() - timedelta(days=1)
        )
        if parsed_provider > latest_closed_taipei_date:
            return ChipPublicationVerdict(
                connector_id=connector_id,
                status="future_session",
                reason="future_session",
                is_failure=True,
                provider_session=provider_text,
                expected_session=expected_session,
                expected_published_at=expected_published_at,
                checked_at=now_t,
                delay_seconds=delay_seconds,
                max_delay_seconds=max_delay_seconds,
                detail=(
                    f"future_session: bounded chip provider session {provider_text!r} != requested session "
                    f"{expected_text!r} for {connector_id} (provider_session={provider_text}, "
                    f"expected_session={expected_text}, checked_at={now_t.isoformat()})"
                ),
            )
        if parsed_provider.weekday() < 5 and (is_session_day is None or bool(is_session_day(parsed_provider))):
            return ChipPublicationVerdict(
                connector_id=connector_id,
                status="published_early",
                reason="published_early",
                is_failure=False,
                provider_session=provider_text,
                expected_session=expected_session,
                expected_published_at=expected_published_at,
                checked_at=now_t,
                delay_seconds=0,
                max_delay_seconds=max_delay_seconds,
                detail=(
                    f"published_early: bounded chip provider session {provider_text!r} > scheduled "
                    f"{expected_text!r} for {connector_id} (provider_session={provider_text}, "
                    f"expected_session={expected_text}, checked_at={now_t.isoformat()})"
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
            f"bounded chip provider session {provider_text!r} != requested session {expected_text!r} "
            f"for {connector_id} (provider_session={provider_text!r}, expected_session={expected_text!r})"
        ),
    )


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def expected_finmind_fundamental_period_ends(dataset: str, session_date: date) -> tuple[date, ...]:
    """Return the acceptable period-end date(s) for a FinMind fundamental dataset at ``session_date``.

    Compares values of the same kind:
    - ``tw_monthly_revenue`` uses month-end dates (statutory deadline: 10th of next month).
    - ``tw_financial_statement`` / ``tw_quarterly_statements`` uses quarter-end dates
      (statutory deadlines: May 15, Aug 14, Nov 14, Mar 31).
    - ``tw_valuation_bwibbu`` uses the trading session date ``session_date``.
    When ``session_date`` is between a completed period end and its statutory filing deadline,
    both the statutorily due period end and the newly closed period end (if filed early) are accepted.
    """
    from services.source_ingestion.feature_catalogue import statutory_available_date

    if dataset == "tw_valuation_bwibbu":
        return (session_date,)
    if dataset == "tw_monthly_revenue":
        year, month = session_date.year, session_date.month
        latest_closed: date | None = None
        statutory_due: date | None = None
        for _ in range(24):
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
            candidate = _month_end(year, month)
            if latest_closed is None:
                latest_closed = candidate
            deadline = statutory_available_date("tw_monthly_revenue", candidate)
            if deadline is not None and deadline <= session_date:
                statutory_due = candidate
                break
        if statutory_due is None or latest_closed is None:
            raise ValueError(f"no monthly revenue period end found for session {session_date.isoformat()}")
        return (statutory_due,) if statutory_due == latest_closed else (statutory_due, latest_closed)
    if dataset in ("tw_financial_statement", "tw_quarterly_statements"):
        latest_closed_q: date | None = None
        statutory_due_q: date | None = None
        for yr in (session_date.year, session_date.year - 1, session_date.year - 2):
            for qm, qd in ((12, 31), (9, 30), (6, 30), (3, 31)):
                candidate = date(yr, qm, qd)
                if candidate >= session_date:
                    continue
                if latest_closed_q is None:
                    latest_closed_q = candidate
                deadline = statutory_available_date("tw_quarterly_statements", candidate)
                if deadline is not None and deadline <= session_date:
                    statutory_due_q = candidate
                    break
            if statutory_due_q is not None:
                break
        if statutory_due_q is None or latest_closed_q is None:
            raise ValueError(f"no quarterly statement period end found for session {session_date.isoformat()}")
        return (statutory_due_q,) if statutory_due_q == latest_closed_q else (statutory_due_q, latest_closed_q)
    raise ValueError(f"unknown FinMind fundamental dataset {dataset!r}")

# Controller freshness for slow FinMind fundamentals is decided by publication,
# not by the generic cadence threshold. The stored ``source_timestamp`` of these
# connectors is the statutory publication time of the newest stored period.
FINMIND_FUNDAMENTAL_PUBLICATION_DATASETS = {
    "tw-finmind-monthly-revenue": "tw_monthly_revenue",
    "tw-finmind-quarterly-statements": "tw_quarterly_statements",
}
# Days after a statutory deadline during which the previous period is still accepted.
FINMIND_FUNDAMENTAL_PUBLICATION_GRACE_DAYS = 2


def finmind_fundamental_publication_fresh(
    dataset: str, source_timestamp: datetime, now: datetime
) -> bool:
    """True when the newest stored period is the newest one due as of ``now`` minus grace.

    Uses ``expected_finmind_fundamental_period_ends`` and ``statutory_available_date``;
    fails closed (False) when the schedule cannot be resolved.
    """
    from services.source_ingestion.feature_catalogue import statutory_available_date

    as_of = now.astimezone(TAIPEI_TZ).date() - timedelta(days=FINMIND_FUNDAMENTAL_PUBLICATION_GRACE_DAYS)
    try:
        required_end = min(expected_finmind_fundamental_period_ends(dataset, as_of))
        deadline = statutory_available_date(dataset, required_end)
    except ValueError:
        return False
    if deadline is None:
        return False
    return source_timestamp.astimezone(TAIPEI_TZ).date() >= deadline


__all__ = [
    "FINMIND_FUNDAMENTAL_PUBLICATION_DATASETS",
    "FINMIND_FUNDAMENTAL_PUBLICATION_GRACE_DAYS",
    "finmind_fundamental_publication_fresh",
    "CHIP_PUBLICATION_SCHEDULES",
    "ChipPublicationSchedule",
    "ChipPublicationVerdict",
    "DEFAULT_MAX_PUBLICATION_DELAY",
    "MEASURED_CHIP_PROVIDER_PROBES",
    "OFFICIAL_CHIP_REFRESH_PROFILES",
    "TAIPEI_TZ",
    "evaluate_chip_provider_session",
    "expected_chip_publication_datetime",
    "expected_finmind_fundamental_period_ends",
    "get_chip_publication_schedule",
    "latest_published_chip_session",
]


# --- TW-OFFICIAL-CHIP-FULL-MARKET-AND-SESSION-DATE-20261120 -------------------------------------------------
# Full-market storage contract (operator decision 2026-11-19; SD_MARKET_DATA_ONE_WRITE_PER_DAY section 6A):
# every listed TWSE/TPEx share and ETF of the session is stored once, keyed
#   tw_margin_short_balance: (symbol, trade_date)
#   tw_institutional_flow:   (symbol, trade_date, investor_class)
# and every row carries its provider session date.

FULL_MARKET_CHIP_CONNECTORS: frozenset[str] = frozenset(
    connector_id
    for connector_id, profile in TW_CHIP_REFRESH_PROFILES.items()
    if profile.get("full_market")
)

FULL_MARKET_CHIP_KEYS: dict[str, tuple[str, ...]] = {
    "tw_margin_short_balance": ("symbol", "trade_date"),
    "tw_institutional_flow": ("symbol", "trade_date", "investor_class"),
}

# Retention and size bounds of section 6A, used by the runtime-budget evidence.
FULL_MARKET_CHIP_RETENTION_DAYS = 2555
FULL_MARKET_CHIP_DAILY_MB_BOUND: dict[str, float] = {
    "tw_institutional_flow": 0.3,
    "tw_margin_short_balance": 0.2,
}


class ChipSessionUndecidable(ValueError):
    """A stored chip row has no provider date and the published-session rule cannot decide one."""


def is_full_market_chip_connector(connector_id: str) -> bool:
    return str(connector_id or "").strip() in FULL_MARKET_CHIP_CONNECTORS


def resolve_chip_row_session(
    connector_id: str,
    row_date: Any,
    fetch_time: datetime,
    *,
    is_session_day: Callable[[date], bool] | None = None,
) -> date:
    """Session date of one chip row.

    A provider-supplied row date wins. A row without one (TWSE ``MI_MARGN`` and TPEx margin carry none) gets
    the latest session the profile's schedule has published at the fetch time - the same
    ``latest_published_chip_session`` rule the refresh preflight and readback use. Raises
    ``ChipSessionUndecidable`` naming the profile when no session can be decided; never falls back to the
    fetch date.
    """
    text = str(row_date or "").strip()
    if text:
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ChipSessionUndecidable(f"{connector_id}: invalid row date {text!r}") from exc
    try:
        return latest_published_chip_session(connector_id, fetch_time, is_session_day=is_session_day)
    except (ValueError, KeyError) as exc:
        raise ChipSessionUndecidable(f"{connector_id}: cannot decide session for a row without a date: {exc}") from exc


def evaluate_chip_row_coverage(
    connector_id: str,
    *,
    provider_listed_count: int,
    stored_count: int,
    stored_session: date | str | None,
    expected_session: date | str,
) -> dict[str, Any]:
    """Compare stored rows of one session with the provider's listed count for that session.

    ``complete`` only when the stored session equals the expected one and every provider row is stored;
    ``partial`` (with ``missing_count``) when fewer are stored; ``session_mismatch`` when the stored rows carry
    another session. Never passes a short or mis-dated run.
    """
    expected = str(expected_session)[:10]
    stored = str(stored_session or "")[:10]
    listed = int(provider_listed_count)
    have = int(stored_count)
    base = {
        "connector_id": connector_id,
        "expected_session": expected,
        "stored_session": stored,
        "provider_listed_count": listed,
        "stored_count": have,
    }
    if stored != expected:
        return {**base, "status": "session_mismatch", "is_failure": True, "missing_count": max(listed - have, 0),
                "detail": f"{connector_id} stored session {stored!r} != expected {expected!r}"}
    missing = max(listed - have, 0)
    if missing:
        return {**base, "status": "partial", "is_failure": True, "missing_count": missing,
                "detail": f"{connector_id} stored {have} of {listed} provider rows; missing {missing}"}
    return {**base, "status": "complete", "is_failure": False, "missing_count": 0,
            "detail": f"{connector_id} stored all {listed} provider rows for {expected}"}


def previous_chip_session(session: date, *, is_session_day: Callable[[date], bool] | None = None) -> date:
    """The session directly before ``session`` (weekday-only when no calendar is given)."""
    check = is_session_day or (lambda d: d.weekday() < 5)
    cursor = session - timedelta(days=1)
    for _ in range(20):
        if check(cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise ChipSessionUndecidable(f"no session found before {session.isoformat()}")


# Hosted read-only measurements of stored TWSE margin rows. Right sessions: 498/498 and 500/500 (2026-11-20 sample),
# 1272 of 1311 = 97.0% (2026-11-24; the exchange revised the previous balance of 39 symbols) and 1304 of 1311 = 99.5%
# (2026-11-23). Wrong sessions: 102 of 500 = 20%, 186 of 1311 = 14.2%, 185 of 1311 = 14.1% (13.5 to 14.2% on the
# full market). The earlier 99% rule came from a 500-row sample and refused the correct 11/24 session, so the
# threshold sits between the two populations (0.97 and above accepted, 0.20 and below refused).
MARGIN_CHAIN_MIN_MATCH_RATIO = 0.90
MARGIN_CHAIN_MAX_MISMATCH_SYMBOLS = 10
MARGIN_CHAIN_GAP_LOOKBACK_SESSIONS = 10
MARGIN_DATASET = "tw_margin_short_balance"


def margin_chain_connector_ids() -> tuple[str, ...]:
    return tuple(c for c, p in TW_CHIP_REFRESH_PROFILES.items() if p.get("dataset") == MARGIN_DATASET)


def _margin_store_root() -> Path:
    import os

    explicit = os.environ.get("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("SOURCE_INGEST_DATA_DIR", "/data/source-ingest")) / "market_data_store"


def stored_margin_session_lookup(root: str | Path | None = None) -> Callable[[str, date], dict[str, dict[str, Any]] | None]:
    """Bounded read of one connector's stored margin rows for one session.

    Reads only ``normalized/tw_margin_short_balance/date=<session>/`` (the session-keyed partition written since
    PR 7655) and keeps the rows of ``connector_id`` whose recorded session equals that session. Returns
    ``{symbol: {margin_balance, short_balance}}``; ``None`` when the partition holds only rows stored before PR 7655
    (fetch-date partitions, date '') so the first load can be accepted explicitly; raises ``ChipSessionUndecidable``
    when the partition does not exist although the dataset has stored rows (a gap), which fails closed.
    """
    from .market_data_storage import _iter_normalized_rows

    def lookup(connector_id: str, session: date) -> dict[str, dict[str, Any]] | None:
        base = (Path(root) if root is not None else _margin_store_root()) / "normalized" / MARGIN_DATASET
        part = base / f"date={session.isoformat()}"
        files = sorted(part.glob("*.jsonl*")) if part.is_dir() else []
        if not files and not base.is_dir():
            return None  # nothing was ever stored for the dataset: the governed first load of a fresh store
        if not files:
            raise ChipSessionUndecidable(
                f"{connector_id}: previous session {session.isoformat()} has no stored margin partition (gap); "
                "refusing to accept it as a first load"
            )
        out: dict[str, dict[str, Any]] = {}
        saw_connector = False
        for path in files:
            for row in _iter_normalized_rows(path):
                if row.get("connector_id") != connector_id:
                    continue
                saw_connector = True
                meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
                nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
                if str(meta.get("date") or nrow.get("date") or "")[:10] != session.isoformat():
                    continue
                symbol = str(nrow.get("symbol") or meta.get("symbol") or "").strip().upper()
                if symbol:
                    out[symbol] = {"margin_balance": nrow.get("margin_balance"), "short_balance": nrow.get("short_balance")}
        if out:
            return out
        if saw_connector:
            return None  # only pre-7655 rows (date '') under a fetch-date partition: governed first load
        raise ChipSessionUndecidable(
            f"{connector_id}: previous session {session.isoformat()} has no stored rows for this connector (gap)"
        )

    return lookup


def stored_margin_earlier_session_lookup(
    root: str | Path | None = None,
) -> Callable[..., tuple[date, dict[str, dict[str, Any]]] | None]:
    """Newest stored session of one connector among the ``max_sessions`` sessions from ``start`` backwards.

    Reads at most the partitions of those sessions, newest first, and stops at the first one that holds rows of
    the connector with a recorded session equal to the partition. Returns ``(session, {symbol: balances})`` or
    ``None`` when none is stored in the window (the caller fails closed).
    """
    from .market_data_storage import _iter_normalized_rows

    def lookup(connector_id: str, start: date, *, is_session_day: Callable[[date], bool] | None = None,
               max_sessions: int = MARGIN_CHAIN_GAP_LOOKBACK_SESSIONS):
        base = (Path(root) if root is not None else _margin_store_root()) / "normalized" / MARGIN_DATASET
        session = start
        for index in range(max_sessions):
            if index:
                session = previous_chip_session(session, is_session_day=is_session_day)
            part = base / f"date={session.isoformat()}"
            if not part.is_dir():
                continue
            out: dict[str, dict[str, Any]] = {}
            for path in sorted(part.glob("*.jsonl*")):
                for row in _iter_normalized_rows(path):
                    if row.get("connector_id") != connector_id:
                        continue
                    meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
                    nrow = meta.get("normalized_row") if isinstance(meta.get("normalized_row"), Mapping) else {}
                    if str(meta.get("date") or nrow.get("date") or "")[:10] != session.isoformat():
                        continue
                    symbol = str(nrow.get("symbol") or meta.get("symbol") or "").strip().upper()
                    if symbol:
                        out[symbol] = {"margin_balance": nrow.get("margin_balance"),
                                       "short_balance": nrow.get("short_balance")}
            if out:
                return session, out
        return None

    return lookup


def margin_chain_wiring(connector_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    """Adapter kwargs that wire the chain check for a margin connector; empty for every other connector."""
    if connector_id not in margin_chain_connector_ids():
        return {}
    return {"previous_session_lookup": stored_margin_session_lookup(root), "accept_first_load": True,
            "earlier_session_lookup": stored_margin_earlier_session_lookup(root)}


def verify_margin_session_chain(
    connector_id: str,
    inferred_session: date,
    rows: Sequence[Mapping[str, Any]],
    stored_previous: Mapping[str, Mapping[str, Any]] | None,
    *,
    accept_first_load: bool = False,
    compared_session: date | None = None,
    missing_sessions: Sequence[date] = (),
) -> dict[str, Any]:
    """Verify an inferred margin session with the provider's own balance chain.

    For every symbol in both the payload and the stored session before ``inferred_session``, the payload's
    ``margin_previous_balance`` / ``short_previous_balance`` (前日餘額) must equal that stored session's
    ``margin_balance`` / ``short_balance``. A mismatch (early publication stores the next session under the
    previous key, late publication repeats the previous session) or a missing previous session fails closed with
    ``ChipSessionUndecidable`` naming the profile and the matched/mismatched counts. A first-ever load (no stored
    previous session) is accepted only with ``accept_first_load`` and is then recorded in the returned evidence.
    """
    if not stored_previous:
        if accept_first_load:
            return {"connector_id": connector_id, "session": inferred_session.isoformat(), "status": "first_load_accepted",
                    "matched_count": 0, "mismatched_count": 0, "first_load_accepted": True}
        raise ChipSessionUndecidable(
            f"{connector_id}: previous session of {inferred_session.isoformat()} is not stored; cannot verify the "
            "inferred session (matched 0, mismatched 0); first load needs an explicit accept_first_load flag"
        )
    if compared_session is not None:
        return _verify_gap_bridge(connector_id, inferred_session, rows, stored_previous, compared_session,
                                  missing_sessions)
    matched = mismatched = 0
    mismatched_symbols: list[str] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        prev = stored_previous.get(symbol)
        if prev is None:
            continue
        same = (
            row.get("margin_previous_balance") == prev.get("margin_balance")
            and row.get("short_previous_balance") == prev.get("short_balance")
        )
        if same:
            matched += 1
        else:
            mismatched += 1
            if len(mismatched_symbols) < MARGIN_CHAIN_MAX_MISMATCH_SYMBOLS:
                mismatched_symbols.append(symbol)
    total = matched + mismatched
    if not matched or matched < MARGIN_CHAIN_MIN_MATCH_RATIO * total:
        raise ChipSessionUndecidable(
            f"{connector_id}: inferred session {inferred_session.isoformat()} fails the provider balance chain "
            f"(matched {matched}, mismatched {mismatched}; need at least {MARGIN_CHAIN_MIN_MATCH_RATIO:.0%}); "
            "refusing to store"
        )
    return {"connector_id": connector_id, "session": inferred_session.isoformat(), "status": "chain_verified",
            "matched_count": matched, "mismatched_count": mismatched, "first_load_accepted": False,
            "mismatched_symbols": mismatched_symbols}


def _balance_match(rows: Sequence[Mapping[str, Any]], stored: Mapping[str, Mapping[str, Any]],
                   margin_key: str, short_key: str) -> tuple[int, int]:
    matched = mismatched = 0
    for row in rows:
        prev = stored.get(str(row.get("symbol") or "").strip().upper())
        if prev is None:
            continue
        if row.get(margin_key) == prev.get("margin_balance") and row.get(short_key) == prev.get("short_balance"):
            matched += 1
        else:
            mismatched += 1
    return matched, mismatched


def _verify_gap_bridge(connector_id, inferred_session, rows, stored, compared_session, missing_sessions):
    """The session before the inferred one is not stored: compare with the newest stored earlier session."""
    label = f"{connector_id}: inferred session {inferred_session.isoformat()} vs stored {compared_session.isoformat()}"
    for kind, keys, why in (
        ("previous", ("margin_previous_balance", "short_previous_balance"), "payload is an earlier session"),
        ("own", ("margin_balance", "short_balance"), "payload repeats the stored session"),
    ):
        matched, mismatched = _balance_match(rows, stored, *keys)
        if matched and matched >= MARGIN_CHAIN_MIN_MATCH_RATIO * (matched + mismatched):
            raise ChipSessionUndecidable(
                f"{label} ({why}: {kind} balances matched {matched}, mismatched {mismatched}); refusing to store"
            )
    return {"connector_id": connector_id, "session": inferred_session.isoformat(), "status": "gap_bridged",
            "compared_session": compared_session.isoformat(),
            "missing_sessions": [d.isoformat() for d in missing_sessions],
            "matched_count": 0, "mismatched_count": 0, "first_load_accepted": False}
