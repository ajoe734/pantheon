import tempfile
import unittest
from pathlib import Path

from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks


class MemberVirtualBookTests(unittest.TestCase):
    def test_previous_view_prices_trial_returns_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virtual.json"
            books = MemberVirtualBooks(path)
            def observe(day, price, direction="LONG"):
                return books.observe(binding_id="binding", envelope_checksum="sha256:envelope",
                    symbol="2330.TWSE", event_time=f"2024-10-{day:02}T05:30:00Z",
                    price=price, source_ref="recorded-twse", bar_frequency="1d", members={
                        "trial": {"artifact_id": "a", "version": "1.0.0", "checksum": "sha256:member",
                                  "virtual_only": True, "view": {"direction": direction, "strength": 1}},
                    })
            observe(1, 100)
            observe(3, 110, "SHORT")
            [summary] = observe(5, 121, "FLAT")
            self.assertAlmostEqual(summary["return_series"][0], .1)
            self.assertAlmostEqual(summary["return_series"][1], -.1)
            self.assertAlmostEqual(summary["total_return"], -.01)
            self.assertAlmostEqual(summary["max_drawdown"], .1)
            self.assertAlmostEqual(summary["sharpe_ratio"], 0)
            books = MemberVirtualBooks(path)
            self.assertEqual(books.snapshot(), [summary])
            self.assertEqual(observe(5, 121, "FLAT"), [summary])
            self.assertEqual(observe(1, 100), [summary])
            with self.assertRaisesRegex(ValueError, "conflicting"):
                observe(5, 122)
            with self.assertRaisesRegex(ValueError, "out-of-order"):
                observe(2, 100)
            with self.assertRaises(ValueError):
                observe(7, float("nan"))
            [summary] = observe(7, 90)
            self.assertEqual(summary["return_series"][-1], 0)
            self.assertFalse(summary["is_real_order"])

    def test_member_aggregate_waits_for_observed_intervals_on_every_underlying(self):
        books = MemberVirtualBooks()
        member = {"artifact_id": "a", "version": "1.0.0", "checksum": "sha256:member",
                  "virtual_only": True, "underlying_count": 2,
                  "view": {"direction": "LONG", "strength": 1}}
        def mark(symbol, day, price):
            return books.observe(binding_id="binding", envelope_checksum="sha256:envelope",
                symbol=symbol, event_time=f"2024-10-{day:02}T05:30:00Z", price=price,
                source_ref="recorded", members={"member": member}, bar_frequency="1d")[0]
        mark("2330.TW", 1, 100)
        summary = mark("2330.TW", 2, 110)
        self.assertFalse(summary["marks_complete"])
        self.assertEqual(summary["return_series"], [])
        mark("2317.TW", 1, 200)
        summary = mark("2317.TW", 2, 180)
        self.assertTrue(summary["marks_complete"])
        self.assertAlmostEqual(summary["return_series"][0], 0)

    def test_net_of_costs_block_charges_taiwan_fill_costs_and_keeps_gross_headline(self):
        books = MemberVirtualBooks()
        member = {"m": {"artifact_id": "m", "version": "1", "checksum": "sha256:m", "virtual_only": False,
                        "view": {"direction": "LONG", "strength": 1}}}
        flat = {"m": {**member["m"], "view": {"direction": "FLAT", "strength": 1}}}
        for day, (price, members) in enumerate(((100, member), (110, member), (121, flat), (121, flat)), 1):
            summary = books.observe(
                binding_id="b", envelope_checksum="e", symbol="2330.TW",
                event_time=f"2024-10-0{day}T05:30:00Z", price=price, source_ref="r",
                members=members, bar_frequency="1d")
        [row] = summary if isinstance(summary, list) else books.snapshot()
        self.assertEqual(row["basis"], "underlying_gross_virtual")
        self.assertAlmostEqual(row["return_series"][0], 0.10)
        net = row["net_of_costs"]
        self.assertEqual((net["basis"], net["periods_per_year"]), ("underlying_exposure_fraction_net_taiwan_costs_equal_notional", 252))
        # entry pays commission; the later exit pays commission plus 0.3% equity tax
        self.assertAlmostEqual(net["return_series"][0], 0.10 - 0.001425)
        # the exit is charged on the interval that ends at the exit mark
        self.assertAlmostEqual(net["return_series"][1], 0.10 - (0.001425 + 0.003))
        self.assertAlmostEqual(net["return_series"][2], 0.0)

    def test_terminal_exit_is_charged_without_a_later_mark(self):
        books = MemberVirtualBooks()
        base = {"artifact_id": "m", "version": "1", "checksum": "sha256:m", "virtual_only": False}
        for day, direction in ((1, "LONG"), (2, "FLAT")):
            books.observe(binding_id="b", envelope_checksum="e", symbol="2330.TW",
                          event_time=f"2024-10-0{day}T05:30:00Z", price=100, source_ref="r",
                          members={"m": {**base, "view": {"direction": direction, "strength": 1}}},
                          bar_frequency="1d")
        [row] = books.snapshot()
        # entry commission + exit commission + 0.3% equity tax, none omitted
        self.assertAlmostEqual(row["net_of_costs"]["total_return"], -(0.001425 * 2 + 0.003), places=6)


class UnalignedHistoryTests(unittest.TestCase):
    def test_incomplete_or_unaligned_underlying_histories_are_never_compared(self):
        """Reviewer counterexample: 2330 through Oct03, 2317 through Oct02 is not 'compared'."""
        approved = {"status": "approved", "baseline_key": "k", "oos_sharpe": 0.5, "oos_max_drawdown": 0.1,
                    "return_basis": "gate_share_sized_per_instrument_mean_sharpe_taiwan_fill_costs",
                    "periods_per_year": 365, "order_quantity": 10}
        member = {"artifact_id": "a", "version": "1.0.0", "checksum": "sha256:member",
                  "virtual_only": False, "underlying_count": 2,
                  "view": {"direction": "LONG", "strength": 1}}

        def snapshot(marks):
            books = MemberVirtualBooks()
            for symbol, day, price in marks:
                books.observe(binding_id="b", envelope_checksum="sha256:e", symbol=symbol,
                              event_time=f"2024-10-{day:02}T05:30:00Z", price=price, source_ref="r",
                              members={"m": member}, bar_frequency="1d")
            return books.snapshot(baseline_resolver=lambda *_: approved)[0]
        short = snapshot([("2330.TW", 1, 100), ("2330.TW", 2, 101), ("2330.TW", 3, 99),
                          ("2317.TW", 1, 50), ("2317.TW", 2, 52)])
        self.assertFalse(short["marks_complete"])
        self.assertEqual(short["baseline_comparison"]["status"], "member_metric_missing")
        self.assertEqual(short["gate_basis"]["unavailable_reason"], "histories_not_aligned_to_common_window")
        one_side = snapshot([("2330.TW", 1, 100), ("2330.TW", 2, 101), ("2330.TW", 3, 99)])
        self.assertEqual(one_side["gate_basis"]["unavailable_reason"], "underlying_marks_missing")
        self.assertEqual(one_side["baseline_comparison"]["status"], "member_metric_missing")


class GateBasisTests(unittest.TestCase):
    def _book(self, directions, prices, symbol="2330.TW"):
        return {"symbol": symbol, "observations": [
            {"price": p, "direction": d} for d, p in zip(directions, prices)]}

    def test_share_sized_cash_portfolio_hand_computed(self):
        from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
        # Buy 10 shares at 100, hold, sell 10 at 110 (commission + 0.3% tax).
        metrics, reason = gate_basis_metrics(
            [self._book((1, 0, -1), (100, 105, 110))], 10)
        self.assertIsNone(reason)
        e0 = 100000 - 1000 * 1.001425 + 1000
        e2 = 100000 - 1000 * 1.001425 + 1100 * (1 - 0.001425 - 0.003)
        r = [e0 / 100000 - 1, (100000 - 1001.425 + 1050) / e0 - 1, e2 / (100000 - 1001.425 + 1050) - 1]
        import math, statistics
        self.assertAlmostEqual(metrics["sharpe_ratio"],
                               statistics.mean(r) / statistics.stdev(r) * math.sqrt(365), places=9)
        self.assertEqual(metrics["basis"],
                         "gate_share_sized_per_instrument_mean_sharpe_taiwan_fill_costs")
        self.assertEqual(metrics["periods_per_year"], 365)

    def test_numerical_parity_with_genuine_vectorbt_gate_path(self):
        try:
            import pandas as pd
            import vectorbt as vbt
        except ImportError:
            self.skipTest("vectorbt not installed")
        from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
        cases = [((1, 0, -1), (100, 105, 110)),
                 ((1, 0, 0, -1, 1, -1), (100, 96, 103, 99, 101, 95)),
                 ((1, -1, 1, 0, -1), (50, 52, 49, 51, 47))]
        for directions, prices in cases:
            for symbol, tax in (("2330.TW", 0.003), ("0050.TW", 0.001)):
                with self.subTest(directions=directions, symbol=symbol):
                    sizes = pd.Series([10.0 * d for d in directions])
                    fees = pd.Series([0.001425 + (tax if d < 0 else 0.0) for d in directions])
                    pf = vbt.Portfolio.from_orders(
                        pd.Series([float(p) for p in prices]), size=sizes, size_type="amount",
                        direction="both", init_cash=100000.0, fees=fees, freq="1D")
                    metrics, reason = gate_basis_metrics([self._book(directions, prices, symbol)], 10)
                    self.assertIsNone(reason)
                    self.assertAlmostEqual(metrics["sharpe_ratio"], float(pf.sharpe_ratio()), places=6)
                    self.assertAlmostEqual(metrics["max_drawdown"], abs(float(pf.max_drawdown())), places=9)

    def test_unavailable_when_unsized_or_direction_not_recorded_or_constant(self):
        from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
        good = self._book((1, 0, -1), (100, 105, 110))
        self.assertEqual(gate_basis_metrics([good], None)[1], "order_quantity_unavailable")
        legacy = {"symbol": "2330.TW", "observations": [{"price": 100, "exposure": 1}] * 3}
        self.assertEqual(gate_basis_metrics([legacy], 10)[1], "direction_not_recorded")
        flat = self._book((0, 0, 0), (100, 105, 110))
        self.assertEqual(gate_basis_metrics([flat], 10)[1], "insufficient_or_constant_returns")
