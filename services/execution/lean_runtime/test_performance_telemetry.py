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

    def test_mark_older_than_latest_fill_cannot_value_the_new_ledger_state(self):
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

        self.assertEqual(result.status, "marks_unavailable")
        self.assertIsNone(result.sample)
        self.assertEqual(result.diagnostic["code"], "market_marks_predate_ledger")
        self.assertEqual(result.diagnostic["missing_symbols"], ["AAPL"])


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


if __name__ == "__main__":
    unittest.main()
