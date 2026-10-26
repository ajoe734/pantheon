from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.parse
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm
from services.execution.lean_runtime.performance_telemetry import (
    MarketMark,
    PerformanceSample,
    RollingDrawdownTracker,
    SourceIngestMarkProvider,
    value_portfolio,
)
from services.execution.market_snapshot_admission import (
    TW_GOVERNED_CALENDAR_PINS,
    evaluate_taiwan_market_freshness,
    is_taiwan_symbol,
)


_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
_UNSET = object()

_RECORDED_2330_CALENDAR_EVIDENCE = {
    "authority": "Taiwan Stock Exchange 115 年10月23日休市公告 (2026-10-22)",
    "checksum": "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99",
    "coverage_end": "2026-10-28",
    "coverage_start": "2026-10-22",
    "fetched_at": "2026-10-23T07:00:39Z",
    "holidays": {
        "2026-10-23": {
            "name": "颱風全日休市 (TWSE 臨時休市公告)",
        },
        "2026-10-25": {
            "name": "臺灣光復暨金門古寧頭大捷紀念日",
        },
        "2026-10-26": {
            "name": "臺灣光復暨金門古寧頭大捷紀念日",
        },
    },
    "market": "TW",
    "source_url": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf",
    "timezone": "Asia/Taipei",
    "trading_days": [
        "2026-10-22",
        "2026-10-27",
        "2026-10-28",
    ],
    "venue": "TWSE",
    "version": "twse-2026-emergency-closure-20261023-v2",
}


def _recorded_2330_oct22_snapshot() -> dict:
    return {
        "schema_version": "source_ingest_latest_market_snapshot.v1",
        "snapshot_id": "mss-ff49b0e9e567bba38063946a",
        "symbol": "2330.TW",
        "event_time": "2026-10-22T00:00:00Z",
        "observed_at": "2026-10-23T12:29:54Z",
        "closes": [2405.0, 2420.0, 2385.0],
        "lineage": {
            "source_ids": [
                "tw-official:tw_price_daily:TWSE:2330:9691b8ad1b066a80",
            ],
            "connector_ids": [
                "tw-twse-tpex-official-market",
            ],
            "content_refs": [
                "tw-official://tw_price_daily/TWSE/2330/2026-10-22/9691b8ad1b066a80",
            ],
            "ingest_run_ids": [
                "ingest-e53a86463995",
            ],
        },
        "source_ref": "source-ingest://snapshots/mss-ff49b0e9e567bba38063946a",
        "calendar_evidence": json.loads(json.dumps(_RECORDED_2330_CALENDAR_EVIDENCE)),
        "market": "TW",
    }


def _snapshot(
    symbol: str,
    price: float,
    event_time: str = "2026-07-14T11:00:00Z",
    *,
    observed_at: object = _UNSET,
    lineage: object = _UNSET,
    calendar_evidence: object = _UNSET,
    source_ref: str | None = None,
) -> dict:
    default_lineage = (
        {
            "source_ids": [
                f"tw-official:tw_price_daily:TWSE:{symbol.split('.', 1)[0]}:test"
            ],
            "connector_ids": ["tw-twse-tpex-official-market"],
        }
        if is_taiwan_symbol(symbol)
        else {}
    )
    payload = {
        "schema_version": "market-snapshot/v1",
        "snapshot_id": f"mss-{symbol}",
        "symbol": symbol,
        "event_time": event_time,
        "observed_at": event_time if observed_at is _UNSET else observed_at,
        "closes": [price - 1.0, price],
        "lineage": default_lineage if lineage is _UNSET else lineage,
        "source_ref": source_ref or f"source-ingest://snapshots/mss-{symbol}",
    }
    if calendar_evidence is not _UNSET:
        payload["calendar_evidence"] = calendar_evidence
    return payload


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "x", {}, None)


def _provider(
    snapshots: dict[str, dict | Exception], urls: list[str] | None = None
) -> SourceIngestMarkProvider:
    def fetch(url, _timeout):
        if urls is not None:
            urls.append(url)
        symbol = urllib.parse.unquote(url.split("symbol=", 1)[1])
        response = snapshots.get(symbol)
        if response is None:
            raise _http_error(url, 404)
        if isinstance(response, Exception):
            raise response
        return response

    return SourceIngestMarkProvider(
        "http://source-ingest:8097",
        cache_ttl_seconds=0,
        max_mark_age_seconds=172800,
        future_tolerance_seconds=300,
        fetch_json=fetch,
        now=lambda: _NOW,
    )


def _sample(value: float, as_of: datetime, *, fill_count: int = 1) -> PerformanceSample:
    return PerformanceSample(
        pnl=value - 100.0,
        portfolio_value=value,
        initial_cash=100.0,
        cash=value,
        as_of=as_of.isoformat().replace("+00:00", "Z"),
        fill_count=fill_count,
        marks=(),
    )


class SourceIngestMarkProviderTest(unittest.TestCase):
    def test_default_fetch_reads_per_symbol_snapshot_not_source_records(self):
        requested_urls: list[str] = []

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return json.dumps(_snapshot("BTC/USD.KRAKEN", 68_500.0)).encode()

        def urlopen(request, timeout):
            requested_urls.append(request.full_url)
            return _Response()

        provider = SourceIngestMarkProvider(
            "http://source-ingest:8097", cache_ttl_seconds=0, now=lambda: _NOW
        )
        with patch("urllib.request.urlopen", urlopen):
            marks, _ = provider.resolve(["BTC/USD.KRAKEN"])

        self.assertEqual(
            requested_urls,
            [
                "http://source-ingest:8097/api/source-ingest/snapshots/latest"
                "?symbol=BTC%2FUSD.KRAKEN"
            ],
        )
        self.assertIn("BTC/USD.KRAKEN", marks)

    def test_200_snapshot_becomes_mark_from_latest_close(self):
        urls: list[str] = []
        snapshot = _snapshot("AAPL.US", 211.5, source_ref="source-ingest://snapshots/s1")
        marks, diagnostic = _provider({"AAPL.US": snapshot}, urls).resolve(["AAPL.US"])

        mark = marks["AAPL.US"]
        self.assertEqual(mark.price, 211.5)
        self.assertEqual(mark.as_of, "2026-07-14T11:00:00Z")
        self.assertEqual(mark.source_ref, "source-ingest://snapshots/s1")
        self.assertEqual(diagnostic["missing_symbols"], [])
        self.assertIsNone(diagnostic["last_error"])
        self.assertTrue(all("source-records" not in url for url in urls))

    def test_404_snapshot_is_missing_and_reported(self):
        marks, diagnostic = _provider({}).resolve(["AAPL.US"])

        self.assertEqual(marks, {})
        self.assertEqual(diagnostic["missing_symbols"], ["AAPL.US"])
        self.assertIn("HTTPError", diagnostic["last_error"])
        self.assertIn("404", diagnostic["last_error"])

    def test_503_snapshot_is_missing_and_reported(self):
        provider = _provider(
            {"AAPL.US": _http_error("http://source-ingest:8097/x", 503)}
        )
        marks, diagnostic = provider.resolve(["AAPL.US"])

        self.assertEqual(marks, {})
        self.assertEqual(diagnostic["missing_symbols"], ["AAPL.US"])
        self.assertIn("503", diagnostic["last_error"])

    def test_stale_and_future_snapshots_fail_closed(self):
        provider = _provider(
            {
                "FRESH.US": _snapshot("FRESH.US", 100.0, "2026-07-14T11:00:00Z"),
                "STALE.US": _snapshot("STALE.US", 100.0, "2026-07-12T11:59:59Z"),
                "FUTURE.US": _snapshot("FUTURE.US", 100.0, "2026-07-14T12:05:01Z"),
            }
        )
        marks, diagnostic = provider.resolve(["FRESH.US", "STALE.US", "FUTURE.US"])

        self.assertEqual(set(marks), {"FRESH.US"})
        self.assertEqual(diagnostic["missing_symbols"], ["FUTURE.US", "STALE.US"])

    def test_snapshot_for_a_different_instrument_resolves_to_nothing(self):
        provider = _provider({"AAPL.US": _snapshot("AAPL.TW", 812.0)})
        marks, diagnostic = provider.resolve(["AAPL.US"])

        self.assertEqual(marks, {})
        self.assertEqual(diagnostic["missing_symbols"], ["AAPL.US"])
        self.assertIn("does not match requested symbol", diagnostic["last_error"])

    def test_snapshot_without_price_time_or_source_ref_is_rejected(self):
        no_ref = _snapshot("NOREF.US", 10.0)
        no_ref.pop("source_ref")
        no_time = _snapshot("NOTIME.US", 10.0)
        no_time["event_time"] = None
        bad_price = _snapshot("BAD.US", float("nan"))
        empty = _snapshot("EMPTY.US", 10.0)
        empty["closes"] = []
        provider = _provider(
            {s["symbol"]: s for s in (no_ref, no_time, bad_price, empty)}
        )
        marks, diagnostic = provider.resolve(
            ["NOREF.US", "NOTIME.US", "BAD.US", "EMPTY.US"]
        )

        self.assertEqual(marks, {})
        self.assertEqual(len(diagnostic["missing_symbols"]), 4)

    def test_cache_ttl_is_per_symbol(self):
        urls: list[str] = []
        provider = _provider(
            {"A.US": _snapshot("A.US", 1.0), "B.US": _snapshot("B.US", 2.0)}, urls
        )
        provider._cache_ttl = 3600.0
        provider.resolve(["A.US"])
        provider.resolve(["A.US", "B.US"])

        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[0].endswith("symbol=A.US"))
        self.assertTrue(urls[1].endswith("symbol=B.US"))

    def test_failed_refresh_does_not_reuse_a_previously_cached_mark(self):
        responses = iter(
            [_snapshot("AAPL.US", 211.5), OSError("source-ingest unavailable")]
        )

        def fetch(_url, _timeout):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            fetch_json=fetch,
            now=lambda: _NOW,
        )
        first, _ = provider.resolve(["AAPL.US"])
        second, diagnostic = provider.resolve(["AAPL.US"])

        self.assertIn("AAPL.US", first)
        self.assertEqual(second, {})
        self.assertEqual(diagnostic["missing_symbols"], ["AAPL.US"])
        self.assertIn("source-ingest unavailable", diagnostic["last_error"])

    def test_taiwan_governed_emergency_closure_calendar_admits_oct22_mark_on_oct25_without_relabeling_time(self):
        now_oct25 = datetime.fromisoformat("2026-10-25T19:19:42+00:00")
        recorded_snapshot = _recorded_2330_oct22_snapshot()
        event_dt = datetime.fromisoformat("2026-10-22T00:00:00+00:00")
        observed_dt = datetime.fromisoformat("2026-10-23T12:29:54+00:00")

        # Raw 48h (172800s) filter rejected this snapshot on 2026-10-25T19:19:42Z
        # even though evaluate_taiwan_market_freshness accepts the governed closure calendar.
        raw_age_seconds = (now_oct25 - event_dt).total_seconds()
        self.assertGreater(raw_age_seconds, 172800.0)
        self.assertEqual(
            TW_GOVERNED_CALENDAR_PINS["twse-2026-emergency-closure-20261023-v2"],
            "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99",
        )
        self.assertEqual(
            evaluate_taiwan_market_freshness(
                event_time_dt=event_dt,
                now_dt=now_oct25,
                refresh_receipt_dt=observed_dt,
                lineage=recorded_snapshot["lineage"],
                max_refresh_age_seconds=172800,
                calendar_evidence=recorded_snapshot["calendar_evidence"],
            ),
            (True, None, None),
        )

        provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            max_mark_age_seconds=172800,
            future_tolerance_seconds=300,
            fetch_json=lambda _url, _timeout: recorded_snapshot,
            now=lambda: now_oct25,
        )
        marks, diagnostic = provider.resolve(["2330.TW"])

        self.assertEqual(diagnostic["indexed_mark_count"], 1)
        self.assertEqual(diagnostic["max_mark_age_seconds"], 172800.0)
        self.assertEqual(diagnostic["resolved_symbols"], ["2330.TW"])
        self.assertEqual(diagnostic["missing_symbols"], [])
        self.assertIsNone(diagnostic["last_error"])

        mark = marks["2330.TW"]
        self.assertEqual(mark.symbol, "2330.TW")
        self.assertEqual(mark.price, 2385.0)
        # Retain true mark as_of and source_ref; never relabel now or observed_at as market time.
        self.assertEqual(mark.as_of, "2026-10-22T00:00:00Z")
        self.assertEqual(
            mark.source_ref,
            "source-ingest://snapshots/mss-ff49b0e9e567bba38063946a",
        )
        self.assertEqual(
            mark.to_dict(),
            {
                "symbol": "2330.TW",
                "price": 2385.0,
                "as_of": "2026-10-22T00:00:00Z",
                "source_ref": "source-ingest://snapshots/mss-ff49b0e9e567bba38063946a",
                "observed_at": "2026-10-23T12:29:54Z",
                "lineage": recorded_snapshot["lineage"],
                "calendar_evidence": recorded_snapshot["calendar_evidence"],
            },
        )

        # Portfolio valuation on the same open 2330.TW book also reuses the session rule.
        valuation = value_portfolio(
            initial_cash=100_000.0,
            cash=104_800.0,
            positions=[{"symbol": "2330.TW", "quantity": -2.0}],
            marks=marks,
            fill_count=2,
            last_fill_at="2026-10-22T06:00:00Z",
            mark_diagnostic=diagnostic,
            max_mark_age_seconds=172800.0,
            now=now_oct25,
        )
        self.assertEqual(valuation.status, "valued")
        self.assertIsNotNone(valuation.sample)
        self.assertAlmostEqual(valuation.sample.portfolio_value, 100_030.0)
        self.assertAlmostEqual(valuation.sample.pnl, 30.0)
        self.assertEqual(valuation.sample.as_of, "2026-10-22T06:00:00Z")
        self.assertEqual(valuation.sample.marks[0].as_of, "2026-10-22T00:00:00Z")
        self.assertEqual(
            valuation.sample.marks[0].source_ref,
            "source-ingest://snapshots/mss-ff49b0e9e567bba38063946a",
        )

        # Also accepts governed calendar evidence carried inside lineage.calendar_evidence.
        lineage_embedded = _recorded_2330_oct22_snapshot()
        embedded_cal = lineage_embedded.pop("calendar_evidence")
        lineage_embedded["lineage"] = {
            **lineage_embedded["lineage"],
            "calendar_evidence": embedded_cal,
        }
        embedded_provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            max_mark_age_seconds=172800,
            fetch_json=lambda _url, _timeout: lineage_embedded,
            now=lambda: now_oct25,
        )
        embedded_marks, embedded_diag = embedded_provider.resolve(["2330.TW"])
        self.assertIn("2330.TW", embedded_marks)
        self.assertEqual(embedded_diag["missing_symbols"], [])

    def test_taiwan_mark_refuses_missed_trading_session_stale_untrusted_malformed_and_future_snapshots(self):
        now_oct25 = datetime.fromisoformat("2026-10-25T19:19:42+00:00")

        # 1. Genuine missed trading session: after 2026-10-27 13:30 Asia/Taipei (05:30Z) closes,
        # the 2026-10-22 close is rejected even if observed_at was refreshed 15 minutes ago.
        now_after_oct27_close = datetime.fromisoformat("2026-10-27T06:00:00+00:00")
        missed_session_snap = _recorded_2330_oct22_snapshot()
        missed_session_snap["observed_at"] = "2026-10-27T05:45:00Z"
        missed_provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            max_mark_age_seconds=172800,
            fetch_json=lambda _url, _timeout: missed_session_snap,
            now=lambda: now_after_oct27_close,
        )
        missed_marks, missed_diag = missed_provider.resolve(["2330.TW"])
        self.assertEqual(missed_marks, {})
        self.assertEqual(missed_diag["missing_symbols"], ["2330.TW"])

        # Value portfolio also rejects a mark that became stale after the 2026-10-27 session closed.
        oct25_provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            max_mark_age_seconds=172800,
            fetch_json=lambda _url, _timeout: _recorded_2330_oct22_snapshot(),
            now=lambda: now_oct25,
        )
        admitted_on_oct25, _ = oct25_provider.resolve(["2330.TW"])
        stale_on_oct27 = value_portfolio(
            initial_cash=100_000.0,
            cash=104_800.0,
            positions=[{"symbol": "2330.TW", "quantity": -2.0}],
            marks=admitted_on_oct25,
            fill_count=2,
            last_fill_at="2026-10-22T06:00:00Z",
            max_mark_age_seconds=172800.0,
            now=now_after_oct27_close,
        )
        self.assertEqual(stale_on_oct27.status, "marks_unavailable")
        self.assertIsNone(stale_on_oct27.sample)
        self.assertEqual(stale_on_oct27.diagnostic["code"], "stale_or_future_market_marks")

        # 2. Missing calendar evidence across a completed weekday (2026-10-23) fails closed.
        no_calendar_snap = _recorded_2330_oct22_snapshot()
        no_calendar_snap.pop("calendar_evidence")
        # 3. Tampered calendar evidence (holiday injected without matching trusted pin) fails closed.
        tampered_calendar_snap = _recorded_2330_oct22_snapshot()
        tampered_calendar_snap["calendar_evidence"]["holidays"]["2026-10-27"] = {"name": "Fake"}
        # 4. Untrusted calendar version pin fails closed.
        untrusted_pin_snap = _recorded_2330_oct22_snapshot()
        untrusted_pin_snap["calendar_evidence"]["version"] = "twse-2026-untrusted-v99"
        # 5. Malformed calendar evidence (non-object) fails closed.
        malformed_calendar_snap = _recorded_2330_oct22_snapshot()
        malformed_calendar_snap["calendar_evidence"] = "not-a-mapping"
        # 6. Untrusted / non-official lineage fails closed even within flat age window.
        untrusted_lineage_snap = _recorded_2330_oct22_snapshot()
        untrusted_lineage_snap["lineage"] = {
            "source_ids": ["yahoo:2330"],
            "connector_ids": ["untrusted-connector"],
        }
        # 7. Missing or pre-close refresh receipt (observed_at) fails closed.
        missing_receipt_snap = _recorded_2330_oct22_snapshot()
        missing_receipt_snap["observed_at"] = None
        pre_close_receipt_snap = _recorded_2330_oct22_snapshot()
        pre_close_receipt_snap["observed_at"] = "2026-10-22T05:00:00Z"
        # 8. Future event_time or future observed_at fails closed.
        future_event_snap = _recorded_2330_oct22_snapshot()
        future_event_snap["event_time"] = "2026-10-25T19:20:00Z"
        future_observed_snap = _recorded_2330_oct22_snapshot()
        future_observed_snap["observed_at"] = "2026-10-25T19:20:00Z"

        for case_name, bad_snap in (
            ("no_calendar", no_calendar_snap),
            ("tampered_calendar", tampered_calendar_snap),
            ("untrusted_pin", untrusted_pin_snap),
            ("malformed_calendar", malformed_calendar_snap),
            ("untrusted_lineage", untrusted_lineage_snap),
            ("missing_receipt", missing_receipt_snap),
            ("pre_close_receipt", pre_close_receipt_snap),
            ("future_event", future_event_snap),
            ("future_observed", future_observed_snap),
        ):
            with self.subTest(case=case_name):
                provider = SourceIngestMarkProvider(
                    "http://source-ingest:8097",
                    cache_ttl_seconds=0,
                    max_mark_age_seconds=172800,
                    fetch_json=lambda _url, _timeout, s=bad_snap: s,
                    now=lambda: now_oct25,
                )
                marks, diag = provider.resolve(["2330.TW"])
                self.assertEqual(marks, {})
                self.assertEqual(diag["missing_symbols"], ["2330.TW"])

        # 9. Non-Taiwan symbol behavior is preserved: AAPL.US older than 172800s is rejected
        # even if its snapshot carries valid Taiwan calendar evidence and lineage.
        non_tw_old = _recorded_2330_oct22_snapshot()
        non_tw_old["symbol"] = "AAPL.US"
        non_tw_provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            max_mark_age_seconds=172800,
            fetch_json=lambda _url, _timeout: non_tw_old,
            now=lambda: now_oct25,
        )
        non_tw_marks, non_tw_diag = non_tw_provider.resolve(["AAPL.US"])
        self.assertEqual(non_tw_marks, {})
        self.assertEqual(non_tw_diag["missing_symbols"], ["AAPL.US"])

        # 10. Cached-failure behavior is preserved for Taiwan symbols on subsequent refresh errors.
        tw_responses = iter(
            [_recorded_2330_oct22_snapshot(), OSError("source-ingest timeout")]
        )
        tw_fail_provider = SourceIngestMarkProvider(
            "http://source-ingest:8097",
            cache_ttl_seconds=0,
            max_mark_age_seconds=172800,
            fetch_json=lambda _url, _timeout: (
                (_ for _ in ()).throw(r)
                if isinstance((r := next(tw_responses)), Exception)
                else r
            ),
            now=lambda: now_oct25,
        )
        first_tw, _ = tw_fail_provider.resolve(["2330.TW"])
        second_tw, second_tw_diag = tw_fail_provider.resolve(["2330.TW"])
        self.assertIn("2330.TW", first_tw)
        self.assertEqual(second_tw, {})
        self.assertEqual(second_tw_diag["missing_symbols"], ["2330.TW"])
        self.assertIn("source-ingest timeout", second_tw_diag["last_error"])


class PortfolioValuationTest(unittest.TestCase):
    def test_long_and_short_books_use_fill_cash_ledger_and_real_marks(self):
        mark_time = "2026-07-14T11:00:00Z"
        long_result = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={"AAPL": MarketMark("AAPL", 110.0, mark_time, "source-ingest://aapl")},
            fill_count=1,
            last_fill_at="2026-07-14T10:00:00Z",
            now=_NOW,
        )
        short_result = value_portfolio(
            initial_cash=100_000,
            cash=101_000,
            positions=[{"symbol": "TSLA", "quantity": -10}],
            marks={"TSLA": MarketMark("TSLA", 90.0, mark_time, "source-ingest://tsla")},
            fill_count=1,
            last_fill_at="2026-07-14T10:00:00Z",
            now=_NOW,
        )

        self.assertEqual(long_result.status, "valued")
        self.assertIsNotNone(long_result.sample)
        self.assertAlmostEqual(long_result.sample.portfolio_value, 100_100.0)
        self.assertAlmostEqual(long_result.sample.pnl, 100.0)
        self.assertEqual(short_result.status, "valued")
        self.assertIsNotNone(short_result.sample)
        self.assertAlmostEqual(short_result.sample.portfolio_value, 100_100.0)
        self.assertAlmostEqual(short_result.sample.pnl, 100.0)

    def test_any_missing_open_position_mark_suppresses_the_entire_snapshot(self):
        result = value_portfolio(
            initial_cash=100_000,
            cash=98_000,
            positions=[
                {"symbol": "AAPL", "quantity": 10},
                {"symbol": "MSFT", "quantity": 2},
            ],
            marks={
                "AAPL": MarketMark(
                    "AAPL", 110.0, "2026-07-14T11:00:00Z", "source-ingest://aapl"
                )
            },
            fill_count=2,
            last_fill_at="2026-07-14T10:00:00Z",
            now=_NOW,
        )

        self.assertEqual(result.status, "marks_unavailable")
        self.assertIsNone(result.sample)
        self.assertEqual(result.diagnostic["code"], "missing_market_marks")
        self.assertEqual(result.diagnostic["missing_symbols"], ["MSFT"])

    def test_mark_older_than_latest_fill_can_value_the_new_ledger_state(self):
        # Before: value_portfolio rejected any still-age-eligible mark whose timestamp
        # predated the latest fill with marks_unavailable / market_marks_predate_ledger,
        # preventing fresh daily snapshot quotes from valuing same-day filled books.
        # After: an age-eligible admitted mark preceding the fill is accepted;
        # the portfolio sample as_of advances to the latest intrinsic timestamp across
        # the fill and all used marks (never earlier than a used mark or ledger),
        # while keeping the mark's original as_of and source_ref unchanged.
        result = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={
                "AAPL": MarketMark(
                    "AAPL",
                    110.0,
                    "2026-07-14T10:59:59Z",
                    "source-ingest://aapl",
                )
            },
            fill_count=1,
            last_fill_at="2026-07-14T11:00:00Z",
            now=_NOW,
        )

        self.assertEqual(result.status, "valued")
        self.assertIsNotNone(result.sample)
        self.assertAlmostEqual(result.sample.portfolio_value, 100_100.0)
        self.assertAlmostEqual(result.sample.pnl, 100.0)
        self.assertEqual(result.sample.as_of, "2026-07-14T11:00:00Z")
        self.assertEqual(result.sample.marks[0].as_of, "2026-07-14T10:59:59Z")
        self.assertEqual(result.sample.marks[0].source_ref, "source-ingest://aapl")

    def test_quote_older_than_max_age_or_future_quote_or_missing_fill_time_fails_closed(self):
        # Quote older than max age returns no sample (stale_or_future_market_marks)
        stale_result = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={
                "AAPL": MarketMark(
                    "AAPL",
                    110.0,
                    (_NOW - timedelta(seconds=172801)).isoformat(),
                    "source-ingest://aapl",
                )
            },
            fill_count=1,
            last_fill_at="2026-07-14T11:00:00Z",
            now=_NOW,
        )
        self.assertEqual(stale_result.status, "marks_unavailable")
        self.assertIsNone(stale_result.sample)
        self.assertEqual(stale_result.diagnostic["code"], "stale_or_future_market_marks")

        # Future quote fails closed (stale_or_future_market_marks)
        future_result = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={
                "AAPL": MarketMark(
                    "AAPL",
                    110.0,
                    (_NOW + timedelta(seconds=301)).isoformat(),
                    "source-ingest://aapl",
                )
            },
            fill_count=1,
            last_fill_at="2026-07-14T11:00:00Z",
            now=_NOW,
        )
        self.assertEqual(future_result.status, "marks_unavailable")
        self.assertIsNone(future_result.sample)
        self.assertEqual(future_result.diagnostic["code"], "stale_or_future_market_marks")

        # Missing latest-fill time fails closed (open_book_missing_fill_as_of)
        missing_fill_result = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={
                "AAPL": MarketMark(
                    "AAPL",
                    110.0,
                    "2026-07-14T11:00:00Z",
                    "source-ingest://aapl",
                )
            },
            fill_count=1,
            last_fill_at=None,
            now=_NOW,
        )
        self.assertEqual(missing_fill_result.status, "invalid_ledger")
        self.assertIsNone(missing_fill_result.sample)
        self.assertEqual(missing_fill_result.diagnostic["code"], "open_book_missing_fill_as_of")


class RollingDrawdownTrackerTest(unittest.TestCase):
    def test_first_loss_is_measured_against_initial_funded_equity(self):
        tracker = RollingDrawdownTracker(window_days=20)

        metrics = tracker.observe(
            _sample(80.0, _NOW),
            initial_equity_as_of=(_NOW - timedelta(hours=1)).isoformat(),
        )

        self.assertAlmostEqual(metrics["drawdown_pct"], 0.2)
        self.assertEqual(metrics["peak_portfolio_value"], 100.0)
        self.assertEqual(metrics["window_observations"], 2)

    def test_twenty_day_window_deduplicates_and_ignores_out_of_order_samples(self):
        tracker = RollingDrawdownTracker(window_days=20)
        first = _sample(100.0, _NOW)
        trough = _sample(80.0, _NOW + timedelta(days=1))

        self.assertEqual(tracker.observe(first)["drawdown_pct"], 0.0)
        trough_metrics = tracker.observe(trough)
        self.assertAlmostEqual(trough_metrics["drawdown_pct"], 0.2)
        self.assertEqual(trough_metrics["window_observations"], 2)
        self.assertIsNone(tracker.observe(trough), "same fill/mark fingerprint must be suppressed")
        self.assertIsNone(
            tracker.observe(_sample(70.0, _NOW + timedelta(hours=12))),
            "late samples must not regress the high-water series",
        )

        expired_peak = tracker.observe(_sample(90.0, _NOW + timedelta(days=22)))
        self.assertEqual(expired_peak["drawdown_pct"], 0.0)
        self.assertEqual(expired_peak["peak_portfolio_value"], 90.0)
        self.assertEqual(expired_peak["window_observations"], 1)

    def test_same_as_of_revision_replaces_the_superseded_high_water_sample(self):
        tracker = RollingDrawdownTracker(window_days=20)
        seed_as_of = (_NOW - timedelta(hours=1)).isoformat()

        self.assertEqual(
            tracker.observe(
                _sample(120.0, _NOW),
                initial_equity_as_of=seed_as_of,
            )["peak_portfolio_value"],
            120.0,
        )
        revised = tracker.observe(
            _sample(80.0, _NOW, fill_count=2),
            initial_equity_as_of=seed_as_of,
        )

        self.assertEqual(revised["peak_portfolio_value"], 100.0)
        self.assertAlmostEqual(revised["drawdown_pct"], 0.2)
        self.assertEqual(revised["window_observations"], 2)

    def test_restored_window_preserves_high_water_across_process_restart(self):
        tracker = RollingDrawdownTracker(window_days=20)
        tracker.observe(
            _sample(120.0, _NOW),
            initial_equity_as_of=(_NOW - timedelta(hours=1)).isoformat(),
        )
        persisted = json.loads(json.dumps(tracker.export_state()))

        restored = RollingDrawdownTracker(window_days=20)
        restored.restore(persisted)
        metrics = restored.observe(_sample(90.0, _NOW + timedelta(days=1)))

        self.assertAlmostEqual(metrics["drawdown_pct"], 0.25)
        self.assertEqual(metrics["peak_portfolio_value"], 120.0)

    def test_restore_rejects_latest_timestamp_without_matching_window_sample(self):
        tracker = RollingDrawdownTracker(window_days=20)

        with self.assertRaisesRegex(ValueError, "has no window sample"):
            tracker.restore(
                {
                    "schema_version": "rolling_drawdown.v1",
                    "window_days": 20,
                    "values": [],
                    "last_fingerprint": None,
                    "latest_as_of": "2099-01-01T00:00:00Z",
                }
            )

    def test_identical_ledger_mark_evaluations_at_different_wall_clocks_prevent_drawdown_duplication(self):
        # Two identical evaluations at different wall-clock now values have identical fingerprints
        now1 = _NOW
        now2 = _NOW + timedelta(hours=2)
        mark = MarketMark("AAPL", 110.0, "2026-07-14T10:00:00Z", "source-ingest://aapl")
        res1 = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={"AAPL": mark},
            fill_count=1,
            last_fill_at="2026-07-14T10:30:00Z",
            now=now1,
        )
        res2 = value_portfolio(
            initial_cash=100_000,
            cash=99_000,
            positions=[{"symbol": "AAPL", "quantity": 10}],
            marks={"AAPL": mark},
            fill_count=1,
            last_fill_at="2026-07-14T10:30:00Z",
            now=now2,
        )
        self.assertEqual(res1.status, "valued")
        self.assertEqual(res2.status, "valued")
        self.assertIsNotNone(res1.sample)
        self.assertIsNotNone(res2.sample)
        self.assertEqual(res1.sample.fingerprint, res2.sample.fingerprint)

        # RollingDrawdownTracker records initial equity seed and first sample point, but suppresses second point
        tracker = RollingDrawdownTracker(window_days=20)
        first_metrics = tracker.observe(res1.sample)
        self.assertIsNotNone(first_metrics)
        self.assertEqual(first_metrics["window_observations"], 2)

        second_metrics = tracker.observe(res2.sample)
        self.assertIsNone(second_metrics, "Advancing wall clock now without ledger or mark change must record no second point")

        # Restored tracker plus unchanged sample also records no extra point
        persisted = tracker.export_state()
        restored = RollingDrawdownTracker(window_days=20)
        restored.restore(persisted)
        self.assertIsNone(restored.observe(res2.sample), "Restored tracker with unchanged sample must record no extra point")

        # A real new committed fill is a different intrinsic sample and is observed
        res_new_fill = value_portfolio(
            initial_cash=100_000,
            cash=97_900,
            positions=[{"symbol": "AAPL", "quantity": 20}],
            marks={"AAPL": mark},
            fill_count=2,
            last_fill_at="2026-07-14T11:00:00Z",
            now=now2,
        )
        self.assertNotEqual(res_new_fill.sample.fingerprint, res1.sample.fingerprint)
        obs_new_fill = tracker.observe(res_new_fill.sample)
        self.assertIsNotNone(obs_new_fill)
        self.assertEqual(obs_new_fill["window_observations"], 3)

        # A changed eligible quote is a different intrinsic sample and is observed
        mark_updated = MarketMark("AAPL", 115.0, "2026-07-14T11:30:00Z", "source-ingest://aapl-v2")
        res_new_quote = value_portfolio(
            initial_cash=100_000,
            cash=97_900,
            positions=[{"symbol": "AAPL", "quantity": 20}],
            marks={"AAPL": mark_updated},
            fill_count=2,
            last_fill_at="2026-07-14T11:00:00Z",
            now=now2,
        )
        self.assertNotEqual(res_new_quote.sample.fingerprint, res_new_fill.sample.fingerprint)
        obs_new_quote = tracker.observe(res_new_quote.sample)
        self.assertIsNotNone(obs_new_quote)
        self.assertEqual(obs_new_quote["window_observations"], 4)


class PaperPerformanceLedgerTest(unittest.TestCase):
    def test_partial_close_combines_realized_cash_with_remaining_mark_to_market(self):
        algorithm = PaperExecutionAlgorithm(initial_cash=100_000.0)
        algorithm.SetSecurityPrice(
            "AAPL",
            100.0,
            as_of="2026-07-14T09:00:00Z",
            source="execution_price",
            authoritative=False,
        )
        algorithm.MarketOrder("AAPL", 10)
        algorithm.SetSecurityPrice(
            "AAPL",
            120.0,
            as_of="2026-07-14T10:00:00Z",
            source="execution_price",
            authoritative=False,
        )
        algorithm.MarketOrder("AAPL", -4)
        algorithm.SetSecurityMark(
            "AAPL",
            115.0,
            as_of="2026-07-14T11:00:00Z",
            source="source-ingest://aapl",
        )
        ledger = algorithm.performance_ledger()

        result = value_portfolio(
            initial_cash=ledger["initial_cash"],
            cash=ledger["cash"],
            positions=ledger["positions"],
            marks=algorithm.authoritative_marks(),
            fill_count=ledger["fill_count"],
            last_fill_at="2026-07-14T10:00:00Z",
            now=_NOW,
        )

        self.assertEqual(ledger["cash"], 99_480.0)
        self.assertEqual(ledger["positions"][0]["quantity"], 6.0)
        self.assertEqual(result.status, "valued")
        self.assertAlmostEqual(result.sample.portfolio_value, 100_170.0)
        self.assertAlmostEqual(result.sample.pnl, 170.0)
        self.assertEqual(result.sample.as_of, "2026-07-14T11:00:00Z")

    def test_flat_book_reports_realized_pnl_at_last_fill_time_without_a_mark(self):
        algorithm = PaperExecutionAlgorithm(initial_cash=100_000.0)
        algorithm.SetSecurityPrice(
            "AAPL",
            100.0,
            as_of="2026-07-14T09:00:00Z",
            source="execution_price",
            authoritative=False,
        )
        algorithm.MarketOrder("AAPL", 10)
        algorithm.SetSecurityPrice(
            "AAPL",
            120.0,
            as_of="2026-07-14T10:00:00Z",
            source="execution_price",
            authoritative=False,
        )
        algorithm.MarketOrder("AAPL", -10)
        ledger = algorithm.performance_ledger()

        result = value_portfolio(
            initial_cash=ledger["initial_cash"],
            cash=ledger["cash"],
            positions=ledger["positions"],
            marks={},
            fill_count=ledger["fill_count"],
            last_fill_at=ledger["last_fill_at"],
            now=_NOW,
        )

        self.assertEqual(ledger["positions"], [])
        self.assertEqual(ledger["cash"], 100_200.0)
        self.assertEqual(result.status, "valued")
        self.assertAlmostEqual(result.sample.pnl, 200.0)
        self.assertEqual(result.sample.as_of, ledger["last_fill_at"])
        self.assertEqual(result.sample.marks, ())

    def test_restart_restores_fill_ledger_but_requires_a_fresh_authoritative_mark(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            first = PaperExecutionAlgorithm(initial_cash=100_000, state_path=str(state_path))
            self.assertTrue(first.BindPerformanceBinding("binding-a"))
            first.SetSecurityPrice(
                "AAPL",
                100.0,
                as_of="2026-07-14T10:00:00Z",
                source="execution_price",
                authoritative=False,
            )
            first.MarketOrder("AAPL", 10)

            restored = PaperExecutionAlgorithm(initial_cash=1.0, state_path=str(state_path))
            ledger = restored.performance_ledger()

            self.assertEqual(ledger["initial_cash"], 100_000.0)
            self.assertEqual(ledger["cash"], 99_000.0)
            self.assertEqual(ledger["fill_count"], 1)
            self.assertEqual(ledger["positions"][0]["quantity"], 10.0)
            self.assertEqual(restored.authoritative_marks(), {})

            restored.SetSecurityMark(
                "AAPL",
                110.0,
                as_of="2026-07-14T11:00:00Z",
                source="source-ingest://aapl",
            )
            result = value_portfolio(
                initial_cash=ledger["initial_cash"],
                cash=ledger["cash"],
                positions=ledger["positions"],
                marks=restored.authoritative_marks(),
                fill_count=ledger["fill_count"],
                last_fill_at="2026-07-14T10:00:00Z",
                now=_NOW,
            )

            self.assertEqual(result.status, "valued")
            self.assertAlmostEqual(result.sample.pnl, 100.0)

    def test_restart_restores_the_persisted_drawdown_window(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            first = PaperExecutionAlgorithm(initial_cash=100.0, state_path=str(state_path))
            self.assertTrue(first.BindPerformanceBinding("binding-a"))
            first.SetSecurityPrice("AAPL", 10.0)
            first.MarketOrder("AAPL", 1)
            tracker = RollingDrawdownTracker(window_days=20)
            tracker.observe(
                _sample(120.0, _NOW),
                initial_equity_as_of=first.performance_ledger()["first_fill_at"],
            )
            self.assertTrue(first.save_performance_window(tracker.export_state()))

            restored_algorithm = PaperExecutionAlgorithm(state_path=str(state_path))
            restored_tracker = RollingDrawdownTracker(window_days=20)
            restored_tracker.restore(restored_algorithm.performance_window_state())
            metrics = restored_tracker.observe(_sample(90.0, _NOW + timedelta(days=1)))

            self.assertAlmostEqual(metrics["drawdown_pct"], 0.25)
            self.assertEqual(metrics["peak_portfolio_value"], 120.0)

    def test_persisted_ledger_refuses_a_different_runtime_binding(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            first = PaperExecutionAlgorithm(initial_cash=100.0, state_path=str(state_path))
            self.assertTrue(first.BindPerformanceBinding("binding-a"))
            first.SetSecurityPrice("AAPL", 10.0)
            first.MarketOrder("AAPL", 1)

            restored = PaperExecutionAlgorithm(state_path=str(state_path))

            self.assertFalse(restored.BindPerformanceBinding("binding-b"))
            ledger = restored.performance_ledger()
            self.assertEqual(ledger["binding_id"], "binding-a")
            self.assertIn("binding mismatch", ledger["state_binding_error"])

    def test_corrupt_ledger_is_not_overwritten_when_binding_is_attached(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            state_path.write_text("not-json", encoding="utf-8")
            algorithm = PaperExecutionAlgorithm(state_path=str(state_path))

            self.assertFalse(algorithm.BindPerformanceBinding("binding-a"))
            self.assertEqual(state_path.read_text(encoding="utf-8"), "not-json")
            self.assertIn("JSONDecodeError", algorithm.performance_ledger()["state_load_error"])

    def test_loaded_unscoped_ledger_cannot_be_claimed_by_a_binding(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "paper-ledger.json"
            unscoped = PaperExecutionAlgorithm(initial_cash=100.0, state_path=str(state_path))
            unscoped.SetSecurityPrice("AAPL", 10.0)
            unscoped.MarketOrder("AAPL", 1)

            restored = PaperExecutionAlgorithm(state_path=str(state_path))

            self.assertFalse(restored.BindPerformanceBinding("binding-a"))
            self.assertIn(
                "missing binding identity",
                restored.performance_ledger()["state_binding_error"],
            )

    def test_taiwan_sell_fill_is_signed_and_credits_cash(self):
        events = []
        algorithm = PaperExecutionAlgorithm(initial_cash=1_000.0, event_sink=events.append)
        broker_fill = {
            "order_id": "tw-order-001",
            "fill_qty": 3,
            "fill_price": 50.0,
            "filled_at": "2026-07-14T11:00:00Z",
            "quote_source": "shioaji-paper-quote",
        }

        algorithm.SetCurrentSignalContext(
            {
                "market_price": 50.0,
                "market_price_as_of": "2026-07-14T11:00:00Z",
                "market_price_source": "source-ingest://snapshots/test-2330",
            }
        )
        with patch.object(algorithm, "_post_broker_paper_order", return_value=broker_fill):
            algorithm.SubmitTaiwanBrokerOrder(
                "2330.TW",
                signal_id="signal-tw-sell-001",
                side="sell",
                quantity=3,
                quantity_type="SHARES",
                action="SELL",
            )

        ledger = algorithm.performance_ledger()
        self.assertEqual(ledger["positions"][0]["quantity"], -3.0)
        self.assertEqual(ledger["cash"], 1_150.0)
        self.assertEqual(ledger["fill_count"], 1)
        self.assertEqual(events[0].quantity, -3.0)
        self.assertEqual(events[0].fill_price, 50.0)

    def test_e2e_paper_execution_algorithm_ledger_provider_valuator(self):
        algorithm = PaperExecutionAlgorithm(initial_cash=100_000.0)

        # Committed fills with governed daily-close shape and realistic sequence
        broker_fill_tw = {
            "order_id": "tw-order-001",
            "fill_qty": 10,
            "fill_price": 1000.0,
            "filled_at": "2026-07-14T06:00:00Z",
            "quote_source": "shioaji-paper-quote",
        }
        algorithm.SetCurrentSignalContext(
            {
                "market_price": 1000.0,
                "market_price_as_of": "2026-07-14T05:30:00Z",
                "market_price_source": "source-ingest://snapshots/mss-2330",
            }
        )
        with patch("services.execution.lean_runtime.paper_runtime._iso_now", return_value="2026-07-14T06:00:00Z"):
            with patch.object(algorithm, "_post_broker_paper_order", return_value=broker_fill_tw):
                algorithm.SubmitTaiwanBrokerOrder(
                    "2330.TW",
                    signal_id="sig-tw-001",
                    side="buy",
                    quantity=10,
                    quantity_type="SHARES",
                    action="BUY",
                )

        broker_fill_tw2 = {
            "order_id": "tw-order-002",
            "fill_qty": 5,
            "fill_price": 200.0,
            "filled_at": "2026-07-14T06:15:00Z",
            "quote_source": "shioaji-paper-quote",
        }
        algorithm.SetCurrentSignalContext(
            {
                "market_price": 200.0,
                "market_price_as_of": "2026-07-14T06:10:00Z",
                "market_price_source": "source-ingest://snapshots/mss-2454-morning",
            }
        )
        with patch("services.execution.lean_runtime.paper_runtime._iso_now", return_value="2026-07-14T06:15:00Z"):
            with patch.object(algorithm, "_post_broker_paper_order", return_value=broker_fill_tw2):
                algorithm.SubmitTaiwanBrokerOrder(
                    "2454.TW",
                    signal_id="sig-tw-002",
                    side="buy",
                    quantity=5,
                    quantity_type="SHARES",
                    action="BUY",
                )

        ledger = algorithm.performance_ledger()
        self.assertEqual(ledger["initial_cash"], 100_000.0)
        self.assertEqual(ledger["cash"], 89_000.0)
        self.assertEqual(ledger["fill_count"], 2)
        self.assertEqual(ledger["last_fill_at"], "2026-07-14T06:15:00Z")

        # Two marks with distinct quote times:
        # 2330.TW quote time (05:30:00Z) predates both fills (06:00:00Z and 06:15:00Z)
        # and has immutable identical price/time/source_ref as fill context.
        # 2454.TW has an actual later differently identified eligible quote (07:30:00Z)
        # newer than both fills, yielding truthful positive PnL.
        snap_2330 = _snapshot(
            "2330.TW",
            1000.0,
            event_time="2026-07-14T05:30:00Z",
            source_ref="source-ingest://snapshots/mss-2330",
        )
        snap_2454 = _snapshot(
            "2454.TW",
            210.0,
            event_time="2026-07-14T07:30:00Z",
            source_ref="source-ingest://snapshots/mss-2454-afternoon",
        )
        provider = _provider({"2330.TW": snap_2330, "2454.TW": snap_2454})
        marks, mark_diag = provider.resolve(algorithm.mark_symbols())
        self.assertEqual(len(marks), 2)
        self.assertEqual(mark_diag["missing_symbols"], [])

        # Valuator produces truthful cash + quantity * mark and PnL
        result = value_portfolio(
            initial_cash=ledger["initial_cash"],
            cash=ledger["cash"],
            positions=ledger["positions"],
            marks=marks,
            fill_count=ledger["fill_count"],
            last_fill_at=ledger["last_fill_at"],
            mark_diagnostic=mark_diag,
            now=_NOW,
        )

        self.assertEqual(result.status, "valued")
        self.assertIsNotNone(result.sample)
        # Cash: 89,000 + 10 * 1000 (10,000) + 5 * 210 (1,050) = 100,050 (PnL: +50.0)
        self.assertAlmostEqual(result.sample.cash, 89_000.0)
        self.assertAlmostEqual(result.sample.portfolio_value, 100_050.0)
        self.assertAlmostEqual(result.sample.pnl, 50.0)

        # Sample date is at least the latest fill and every used mark
        last_fill_dt = datetime.fromisoformat(ledger["last_fill_at"].replace("Z", "+00:00"))
        sample_as_of_dt = datetime.fromisoformat(result.sample.as_of.replace("Z", "+00:00"))
        mark_2330_dt = datetime.fromisoformat(marks["2330.TW"].as_of.replace("Z", "+00:00"))
        mark_2454_dt = datetime.fromisoformat(marks["2454.TW"].as_of.replace("Z", "+00:00"))

        # Old quote before ledger valuation: 2330 mark predates last fill and is successfully valued
        self.assertGreater(last_fill_dt, mark_2330_dt)
        self.assertGreaterEqual(sample_as_of_dt, last_fill_dt)
        self.assertGreaterEqual(sample_as_of_dt, mark_2330_dt)
        self.assertGreaterEqual(sample_as_of_dt, mark_2454_dt)
        # Distinct quote times: sample as_of does not backdate the newer quote (07:30:00Z)
        self.assertEqual(result.sample.as_of, "2026-07-14T07:30:00Z")

        # Each original quote date and source reference is preserved unchanged
        sample_marks = {m.symbol: m for m in result.sample.marks}
        self.assertEqual(sample_marks["2330.TW"].as_of, "2026-07-14T05:30:00Z")
        self.assertEqual(sample_marks["2330.TW"].source_ref, "source-ingest://snapshots/mss-2330")
        self.assertEqual(sample_marks["2454.TW"].as_of, "2026-07-14T07:30:00Z")
        self.assertEqual(sample_marks["2454.TW"].source_ref, "source-ingest://snapshots/mss-2454-afternoon")

    def test_unchanged_sample_provenance_enrichment_and_idempotence(self):
        snap = _recorded_2330_oct22_snapshot()
        mark_without_prov = MarketMark(
            symbol="2330.TW",
            price=2385.0,
            as_of="2026-10-22T00:00:00Z",
            source_ref=snap["source_ref"],
        )
        mark_with_prov = MarketMark(
            symbol="2330.TW",
            price=2385.0,
            as_of="2026-10-22T00:00:00Z",
            source_ref=snap["source_ref"],
            observed_at=snap["observed_at"],
            lineage=snap["lineage"],
            calendar_evidence=snap["calendar_evidence"],
        )
        sample_without_prov = PerformanceSample(
            pnl=-4770.0,
            portfolio_value=95230.0,
            initial_cash=100000.0,
            cash=100000.0,
            as_of="2026-10-22T00:00:00Z",
            fill_count=2,
            marks=(mark_without_prov,),
        )
        sample_with_prov = PerformanceSample(
            pnl=-4770.0,
            portfolio_value=95230.0,
            initial_cash=100000.0,
            cash=100000.0,
            as_of="2026-10-22T00:00:00Z",
            fill_count=2,
            marks=(mark_with_prov,),
        )
        self.assertEqual(sample_without_prov.fingerprint, sample_with_prov.fingerprint)

        tracker = RollingDrawdownTracker()
        first_metrics = tracker.observe(
            sample_without_prov,
            initial_equity_as_of="2026-10-20T00:00:00Z",
        )
        self.assertIsNotNone(first_metrics)
        self.assertEqual(first_metrics["drawdown_as_of"], "2026-10-22T00:00:00Z")
        self.assertEqual(first_metrics["window_observations"], 2)

        # Standard observe returns None because value fingerprint is unchanged.
        self.assertIsNone(tracker.observe(sample_with_prov))
        # Newly available governed provenance on the same sample returns metrics once
        # without adding duplicate window observations or changing drawdown_pct/as_of.
        enriched_metrics = tracker.observe_unchanged_provenance(sample_with_prov)
        self.assertEqual(enriched_metrics, first_metrics)

        # Repeated identical provenance is idempotent, including across state restore.
        self.assertIsNone(tracker.observe_unchanged_provenance(sample_with_prov))
        exported = tracker.export_state()
        self.assertIn("last_mark_provenance", exported)
        restored = RollingDrawdownTracker()
        restored.restore(exported)
        self.assertIsNone(restored.observe(sample_with_prov))
        self.assertIsNone(restored.observe_unchanged_provenance(sample_with_prov))

        # Regression attempts (dropping provenance, older observed_at, or invalid pin) are rejected.
        self.assertIsNone(restored.observe_unchanged_provenance(sample_without_prov))
        older_receipt_mark = MarketMark(
            symbol="2330.TW",
            price=2385.0,
            as_of="2026-10-22T00:00:00Z",
            source_ref=snap["source_ref"],
            observed_at="2026-10-23T08:00:00Z",
            lineage=snap["lineage"],
            calendar_evidence=snap["calendar_evidence"],
        )
        older_sample = PerformanceSample(
            pnl=-4770.0,
            portfolio_value=95230.0,
            initial_cash=100000.0,
            cash=100000.0,
            as_of="2026-10-22T00:00:00Z",
            fill_count=2,
            marks=(older_receipt_mark,),
        )
        self.assertIsNone(restored.observe_unchanged_provenance(older_sample))

        bad_pin_cal = json.loads(json.dumps(snap["calendar_evidence"]))
        bad_pin_cal["version"] = "twse-2026-untrusted-v99"
        bad_pin_mark = MarketMark(
            symbol="2330.TW",
            price=2385.0,
            as_of="2026-10-22T00:00:00Z",
            source_ref=snap["source_ref"],
            observed_at="2026-10-24T08:00:00Z",
            lineage=snap["lineage"],
            calendar_evidence=bad_pin_cal,
        )
        bad_pin_sample = PerformanceSample(
            pnl=-4770.0,
            portfolio_value=95230.0,
            initial_cash=100000.0,
            cash=100000.0,
            as_of="2026-10-22T00:00:00Z",
            fill_count=2,
            marks=(bad_pin_mark,),
        )
        self.assertIsNone(restored.observe_unchanged_provenance(bad_pin_sample))


if __name__ == "__main__":
    unittest.main()
