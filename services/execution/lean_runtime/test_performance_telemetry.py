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


_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _snapshot(
    symbol: str,
    price: float,
    event_time: str = "2026-07-14T11:00:00Z",
    *,
    source_ref: str | None = None,
) -> dict:
    return {
        "schema_version": "market-snapshot/v1",
        "snapshot_id": f"mss-{symbol}",
        "symbol": symbol,
        "event_time": event_time,
        "observed_at": event_time,
        "closes": [price - 1.0, price],
        "lineage": {},
        "source_ref": source_ref or f"source-ingest://snapshots/mss-{symbol}",
    }


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


if __name__ == "__main__":
    unittest.main()
