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
        self.assertEqual((net["basis"], net["periods_per_year"]), ("underlying_net_taiwan_fill_costs", 252))
        # entry pays commission; the later exit pays commission plus 0.3% equity tax
        self.assertAlmostEqual(net["return_series"][0], 0.10 - 0.001425)
        self.assertAlmostEqual(net["return_series"][1], 0.10)
        self.assertAlmostEqual(net["return_series"][2], -(0.001425 + 0.003))
