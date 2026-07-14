from __future__ import annotations

import json
import tempfile
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


def _source_record(
    symbol: str,
    price: float,
    as_of: str | None,
    *,
    source_id: str | None = None,
    source_type: str = "market",
    status: str = "normalized",
    dataset: str = "daily_price",
    created_at: str = "2026-07-14T11:59:00Z",
) -> dict:
    row = {
        "symbol_canonical": symbol,
        "close": price,
        "dataset": dataset,
    }
    if as_of is not None:
        row["as_of"] = as_of
    return {
        "source_id": source_id or f"source-{symbol}",
        "content_ref": f"source-ingest://{source_id or symbol}",
        "source_type": source_type,
        "status": status,
        "created_at": created_at,
        "metadata": {"normalized_row": row},
    }


def _provider(records: list[dict]) -> SourceIngestMarkProvider:
    return SourceIngestMarkProvider(
        "http://source-ingest:8097",
        cache_ttl_seconds=0,
        max_mark_age_seconds=172800,
        future_tolerance_seconds=300,
        fetch_json=lambda _url, _timeout: {"source_records": records},
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
    def test_only_normalized_market_price_records_with_observation_time_are_admissible(self):
        missing_source_ref = _source_record(
            "NOREF.US", 42.0, "2026-07-14T11:00:00Z"
        )
        missing_source_ref.pop("source_id")
        missing_source_ref.pop("content_ref")
        records = [
            _source_record("AAPL.US", 211.5, "2026-07-14T11:00:00Z"),
            _source_record("MSFT.US", 501.0, "2026-07-14T11:00:00Z", source_type="news"),
            _source_record("NVDA.US", 175.0, "2026-07-14T11:00:00Z", status="rejected"),
            _source_record("TSLA.US", 320.0, "2026-07-14T11:00:00Z", dataset="fundamentals"),
            _source_record("BROKEN.US", float("nan"), "2026-07-14T11:00:00Z"),
            # Ingest availability time is not a market observation time.
            _source_record("AMZN.US", 230.0, None, created_at="2026-07-14T11:59:00Z"),
            missing_source_ref,
        ]

        marks, diagnostic = _provider(records).resolve(
            [
                "AAPL.US",
                "MSFT.US",
                "NVDA.US",
                "TSLA.US",
                "BROKEN.US",
                "AMZN.US",
                "NOREF.US",
            ]
        )

        self.assertEqual(set(marks), {"AAPL.US"})
        self.assertEqual(marks["AAPL.US"].price, 211.5)
        self.assertEqual(marks["AAPL.US"].as_of, "2026-07-14T11:00:00Z")
        self.assertEqual(
            diagnostic["missing_symbols"],
            ["AMZN.US", "BROKEN.US", "MSFT.US", "NOREF.US", "NVDA.US", "TSLA.US"],
        )

    def test_stale_and_future_marks_fail_closed(self):
        records = [
            _source_record("FRESH.US", 100.0, "2026-07-14T11:00:00Z"),
            _source_record("STALE.US", 100.0, "2026-07-12T11:59:59Z"),
            _source_record("FUTURE.US", 100.0, "2026-07-14T12:05:01Z"),
        ]

        marks, diagnostic = _provider(records).resolve(["FRESH.US", "STALE.US", "FUTURE.US"])

        self.assertEqual(set(marks), {"FRESH.US"})
        self.assertEqual(diagnostic["missing_symbols"], ["FUTURE.US", "STALE.US"])

    def test_base_symbol_alias_collision_fails_closed_without_hiding_canonical_marks(self):
        records = [
            _source_record("AAPL.US", 211.5, "2026-07-14T11:00:00Z", source_id="us-price"),
            _source_record("AAPL.TW", 812.0, "2026-07-14T11:30:00Z", source_id="tw-price"),
        ]

        marks, diagnostic = _provider(records).resolve(["AAPL", "AAPL.US", "AAPL.TW"])

        self.assertNotIn("AAPL", marks)
        self.assertEqual(marks["AAPL.US"].price, 211.5)
        self.assertEqual(marks["AAPL.TW"].price, 812.0)
        self.assertEqual(diagnostic["missing_symbols"], ["AAPL"])

    def test_conflicting_prices_at_the_same_observation_time_fail_closed(self):
        records = [
            _source_record(
                "AAPL.US",
                211.5,
                "2026-07-14T11:00:00Z",
                source_id="price-a",
            ),
            _source_record(
                "AAPL.US",
                212.5,
                "2026-07-14T11:00:00Z",
                source_id="price-b",
            ),
        ]

        marks, diagnostic = _provider(records).resolve(["AAPL.US"])

        self.assertEqual(marks, {})
        self.assertEqual(diagnostic["missing_symbols"], ["AAPL.US"])

    def test_newer_unambiguous_price_supersedes_an_older_conflict(self):
        records = [
            _source_record(
                "AAPL.US",
                210.0,
                "2026-07-14T10:00:00Z",
                source_id="older-a",
            ),
            _source_record(
                "AAPL.US",
                220.0,
                "2026-07-14T10:00:00Z",
                source_id="older-b",
            ),
            _source_record(
                "AAPL.US",
                215.0,
                "2026-07-14T11:00:00Z",
                source_id="latest",
            ),
        ]

        marks, diagnostic = _provider(records).resolve(["AAPL.US"])

        self.assertEqual(marks["AAPL.US"].price, 215.0)
        self.assertEqual(diagnostic["missing_symbols"], [])

    def test_crypto_canonical_symbol_resolves_only_the_matching_quote_pair(self):
        record = _source_record(
            "BTC.CRYPTO",
            68_500.0,
            "2026-07-14T11:00:00Z",
            dataset="crypto_spot_price",
        )
        record["metadata"]["normalized_row"]["vs_currency"] = "usd"

        marks, diagnostic = _provider([record]).resolve(
            ["BTC/USD.KRAKEN", "BTCUSD", "BTCUSDT"]
        )

        self.assertEqual(set(marks), {"BTC/USD.KRAKEN", "BTCUSD"})
        self.assertEqual(marks["BTC/USD.KRAKEN"].quote_currency, "USD")
        self.assertEqual(diagnostic["missing_symbols"], ["BTCUSDT"])

    def test_finmind_raw_price_shape_is_admissible_when_normalized_row_is_absent(self):
        record = {
            "source_id": "finmind:TaiwanStockPrice:2330",
            "content_ref": "finmind://data/TaiwanStockPrice/2330/2026-07-14",
            "source_type": "market",
            "status": "normalized",
            "metadata": {
                "dataset": "TaiwanStockPrice",
                "symbol": "2330",
                "event_time": "2026-07-14",
                "raw_row": {
                    "stock_id": "2330",
                    "date": "2026-07-14",
                    "close": 955.0,
                },
            },
        }

        marks, diagnostic = _provider([record]).resolve(["2330.TW"])

        self.assertEqual(marks["2330.TW"].price, 955.0)
        self.assertEqual(marks["2330.TW"].as_of, "2026-07-14T00:00:00Z")
        self.assertEqual(diagnostic["missing_symbols"], [])

    def test_failed_refresh_does_not_reuse_a_previously_cached_mark(self):
        responses = iter(
            [
                {"source_records": [_source_record("AAPL.US", 211.5, "2026-07-14T11:00:00Z")]},
                OSError("source-ingest unavailable"),
            ]
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
