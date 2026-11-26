import tempfile
import unittest
from pathlib import Path

from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION, MemberVirtualBooks


class MemberVirtualBookTests(unittest.TestCase):
    def test_next_open_fills_trial_returns_and_restart(self):
        # A view decided from the close of one mark is filled at the NEXT mark's open.
        opens = {1: 100, 3: 104, 5: 115, 7: 118}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "virtual.json"
            books = MemberVirtualBooks(path)
            def observe(day, price, direction="LONG"):
                return books.observe(binding_id="binding", envelope_checksum="sha256:envelope",
                    symbol="2330.TWSE", event_time=f"2024-10-{day:02}T05:30:00Z",
                    price=price, open_price=opens.get(day), source_ref="recorded-twse",
                    bar_frequency="1d", members={
                        "trial": {"artifact_id": "a", "version": "1.0.0", "checksum": "sha256:member",
                                  "virtual_only": True, "view": {"direction": direction, "strength": 1}},
                    })
            observe(1, 100)
            observe(3, 110, "SHORT")
            [summary] = observe(5, 121, "FLAT")
            first = 110 / 104 - 1          # LONG decided at mark 1, filled at the open of mark 3
            second = (115 / 110 - 1) - (121 / 115 - 1)  # carried LONG to the open, then SHORT filled there
            self.assertEqual(len(summary["return_series"]), 2)
            self.assertAlmostEqual(summary["return_series"][0], first)
            self.assertAlmostEqual(summary["return_series"][1], second)
            self.assertAlmostEqual(summary["total_return"], (1 + first) * (1 + second) - 1)
            self.assertEqual(summary["fill_convention"], FILL_CONVENTION)
            self.assertEqual([(f["decision_time"][:10], f["fill_time"][:10], f["price"]) for f in summary["fills"]],
                             [("2024-10-01", "2024-10-03", 104.0), ("2024-10-03", "2024-10-05", 115.0)])
            self.assertEqual(summary["fill_gaps"], [])
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
            # FLAT decided at mark 5 is filled at the open of mark 7 against the carried SHORT.
            self.assertAlmostEqual(summary["return_series"][-1], -(118 / 121 - 1))
            self.assertFalse(summary["is_real_order"])

    def test_session_without_open_gives_no_fill_and_never_falls_back_to_the_close(self):
        books = MemberVirtualBooks()
        member = {"artifact_id": "a", "version": "1.0.0", "checksum": "sha256:member",
                  "virtual_only": True, "view": {"direction": "LONG", "strength": 1}}
        for day, price, open_price in ((1, 100, 100), (2, 110, None), (3, 121, 112)):
            [summary] = books.observe(binding_id="b", envelope_checksum="e", symbol="2330.TW",
                event_time=f"2024-10-{day:02}T05:30:00Z", price=price, open_price=open_price,
                source_ref="r", members={"m": member}, bar_frequency="1d")
        # mark 2 has no open: the LONG decided at mark 1 is not filled there (no close fill),
        # the exposure stays flat for that interval and the gap is recorded with its reason.
        self.assertAlmostEqual(summary["return_series"][0], 0.0)
        self.assertEqual([(g["fill_time"][:10], g["reason"]) for g in summary["fill_gaps"]],
                         [("2024-10-02", "no_open_price")])
        # the LONG decided at mark 2 fills at the open of mark 3
        self.assertAlmostEqual(summary["return_series"][1], 121 / 112 - 1)

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
        opens = (100, 108, 110, 121)
        for day, (price, members) in enumerate(((100, member), (110, member), (121, flat), (121, flat)), 1):
            summary = books.observe(
                binding_id="b", envelope_checksum="e", symbol="2330.TW",
                event_time=f"2024-10-0{day}T05:30:00Z", price=price, open_price=opens[day - 1],
                source_ref="r", members=members, bar_frequency="1d")
        [row] = summary if isinstance(summary, list) else books.snapshot()
        self.assertEqual(row["basis"], "underlying_gross_virtual")
        self.assertAlmostEqual(row["return_series"][0], 110 / 108 - 1)
        net = row["net_of_costs"]
        self.assertEqual((net["basis"], net["periods_per_year"]), ("underlying_exposure_fraction_net_taiwan_costs_equal_notional", 252))
        # the entry is filled at the open of mark 2 and pays commission
        self.assertAlmostEqual(net["return_series"][0], 110 / 108 - 1 - 0.001425)
        self.assertAlmostEqual(net["return_series"][1], 121 / 110 - 1)
        # the FLAT decided at mark 3 is filled at the open of mark 4: commission plus 0.3% equity tax
        self.assertAlmostEqual(net["return_series"][2], -(0.001425 + 0.003))

    def test_exit_is_charged_when_filled_at_the_next_open_not_before(self):
        books = MemberVirtualBooks()
        base = {"artifact_id": "m", "version": "1", "checksum": "sha256:m", "virtual_only": False}
        def mark(day, direction):
            books.observe(binding_id="b", envelope_checksum="e", symbol="2330.TW",
                          event_time=f"2024-10-0{day}T05:30:00Z", price=100, open_price=100, source_ref="r",
                          members={"m": {**base, "view": {"direction": direction, "strength": 1}}},
                          bar_frequency="1d")
        for day, direction in ((1, "LONG"), (2, "FLAT")):
            mark(day, direction)
        [row] = books.snapshot()
        # the FLAT decision has no next session yet: only the entry commission is charged
        self.assertAlmostEqual(row["net_of_costs"]["total_return"], -0.001425, places=6)
        mark(3, "FLAT")
        [row] = books.snapshot()
        expected = (1 - 0.001425) * (1 - (0.001425 + 0.003)) - 1
        self.assertAlmostEqual(row["net_of_costs"]["total_return"], expected, places=6)


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
    def _book(self, directions, prices, symbol="2330.TW", opens=None):
        return {"symbol": symbol, "observations": [
            {"price": p, "open": o, "direction": d}
            for d, p, o in zip(directions, prices, opens or prices)]}

    def test_share_sized_cash_portfolio_hand_computed(self):
        from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
        # Buy 10 shares decided at row 0, filled at the open of row 1 (102); the SELL decided at
        # row 2 is filled at the open of row 3 (109) with commission + 0.3% tax.
        metrics, reason = gate_basis_metrics(
            [self._book((1, 0, -1, 0), (100, 105, 110, 108), opens=(100, 102, 107, 109))], 10)
        self.assertIsNone(reason)
        cash = 100000 - 1020 * 1.001425
        equities = [100000.0, cash + 1050, cash + 1100, cash + 1090 * (1 - 0.001425 - 0.003)]
        r = [equities[i] / equities[i - 1] - 1 if i else equities[0] / 100000 - 1 for i in range(4)]
        import math, statistics
        self.assertAlmostEqual(metrics["sharpe_ratio"],
                               statistics.mean(r) / statistics.stdev(r) * math.sqrt(365), places=9)
        self.assertEqual(metrics["basis"],
                         "gate_share_sized_per_instrument_mean_sharpe_taiwan_fill_costs")
        self.assertEqual(metrics["periods_per_year"], 365)
        self.assertEqual(metrics["fill_convention"], FILL_CONVENTION)

    def test_row_without_open_places_no_order(self):
        from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
        full, _ = gate_basis_metrics([self._book((1, 0, 0, 0), (100, 105, 110, 108),
                                                 opens=(100, 102, 107, 109))], 10)
        self.assertIsNotNone(full)
        # no open at row 1: the BUY is not filled at the close, so the book never trades and
        # its equity is constant (never a fabricated close fill)
        gap, reason = gate_basis_metrics([self._book((1, 0, 0, 0), (100, 105, 110, 108),
                                                     opens=(100, None, 107, 109))], 10)
        self.assertIsNone(gap)
        self.assertEqual(reason, "insufficient_or_constant_returns")

    def test_numerical_parity_with_genuine_vectorbt_gate_path(self):
        try:
            import pandas as pd
            import vectorbt as vbt
        except ImportError:
            self.skipTest("vectorbt not installed")
        from services.execution.lean_runtime.member_virtual_book import gate_basis_metrics
        cases = [((1, 0, -1, 0), (100, 105, 110, 108), (100, 102, 107, 109)),
                 ((1, 0, 0, -1, 1, -1), (100, 96, 103, 99, 101, 95), (100, 99, 97, 104, 98, 102)),
                 ((1, -1, 1, 0, -1), (50, 52, 49, 51, 47), (50, 51, 53, 48, 52))]
        for directions, prices, opens in cases:
            for symbol, tax in (("2330.TW", 0.003), ("0050.TW", 0.001)):
                with self.subTest(directions=directions, symbol=symbol):
                    # the gate path: orders shifted one bar, filled at that bar's open
                    shifted = (0,) + directions[:-1]
                    sizes = pd.Series([10.0 * d for d in shifted])
                    fees = pd.Series([0.001425 + (tax if d < 0 else 0.0) for d in shifted])
                    pf = vbt.Portfolio.from_orders(
                        pd.Series([float(p) for p in prices]), size=sizes, size_type="amount",
                        price=pd.Series([float(o) for o in opens]),
                        direction="both", init_cash=100000.0, fees=fees, freq="1D")
                    metrics, reason = gate_basis_metrics(
                        [self._book(directions, prices, symbol, opens=opens)], 10)
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


def test_exact_member_history_survives_envelope_replacement(tmp_path):
    from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
    path = tmp_path / 'persona-books.json'
    member = {'artifact_id': 'a', 'version': '1.0.0', 'checksum': 'sha256:member',
              'virtual_only': False, 'view': {'direction': 'LONG', 'strength': 1}}
    first = MemberVirtualBooks(path)
    for day, price in ((1, 100), (2, 101)):
        first.observe(binding_id='old', envelope_checksum='env1', symbol='2330.TW',
            event_time=f'2024-10-{day:02}T05:30:00Z', price=price, source_ref='recorded-unit',
            bar_frequency='1d', members={'member': member})
    second = MemberVirtualBooks(path)
    snapshot = second.observe(binding_id='new', envelope_checksum='env2', symbol='2330.TW',
        event_time='2024-10-03T05:30:00Z', price=102, source_ref='recorded-unit',
        bar_frequency='1d', members={'member': member})
    current = next(b for b in snapshot if b['binding_id'] == 'new')
    assert current['dates'] == ['2024-10-02', '2024-10-03']
    assert len(current['net_of_costs']['returns']) == 2
    # A stale instance reloads the durable store before adding a new envelope.
    newer = first.observe(binding_id='newer', envelope_checksum='env3', symbol='2330.TW',
        event_time='2024-10-04T05:30:00Z', price=103, source_ref='recorded-unit',
        bar_frequency='1d', members={'member': member})
    assert len(next(b for b in newer if b['binding_id'] == 'newer')['dates']) == 3
    changed = {**member, 'checksum': 'sha256:different'}
    last = second.observe(binding_id='other', envelope_checksum='env4', symbol='2330.TW',
        event_time='2024-10-05T05:30:00Z', price=104, source_ref='recorded-unit',
        bar_frequency='1d', members={'member': changed})
    assert next(b for b in last if b['binding_id'] == 'other')['dates'] == []


def test_baseline_lookup_uses_the_pool_member_registry_ref():
    from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
    books = MemberVirtualBooks()
    books.observe(binding_id='binding', envelope_checksum='envelope', symbol='2330.TW',
        event_time='2024-10-01T05:30:00Z', price=100, source_ref='unit-book-source', bar_frequency='1d',
        members={'registry-alias': {'artifact_id': 'embedded-a', 'version': '1.0.0', 'checksum': 'sha256:a',
            'virtual_only': False, 'view': {'direction': 'LONG', 'strength': 1}}})
    looked_up = []
    books.snapshot(baseline_resolver=lambda ref, *_: looked_up.append(ref))
    assert looked_up == ['registry-alias']


def test_idle_pool_observations_are_known_zero_and_invalid_inputs_fail_closed():
    import importlib
    import pytest
    from services.execution.lean_runtime.member_virtual_book import idle_pool_observations
    policy = importlib.import_module("services.control-plane.persona.pool_policy")
    p = policy.load_policy()
    days = [f"2026-10-{d:02d}" for d in range(1, p.health_window_days + 1)]
    rows = idle_pool_observations(days, source_ref="unit:risk", gross_limit=100, drawdown_limit=.1)
    health = policy.pool_health([], {}, rows, p)
    assert health["gross_utilisation"]["value"] == 0 and health["drawdown_utilisation"]["value"] == 0
    assert health["idle_day_share"]["value"] == 1 and health["effective_bets"]["reason"] == "no_live_members"
    assert "low_risk_budget_utilisation" in health["demand_reasons"]
    for bad in ({"source_ref": ""}, {"gross_limit": 0}, {"drawdown_limit": -1}):
        with pytest.raises(ValueError):
            idle_pool_observations(days, **{"source_ref": "x", "gross_limit": 1, "drawdown_limit": 1, **bad})


class SinceInceptionTests(unittest.TestCase):
    def _mark(self, books, day, joined_at, member_id="m"):
        return books.observe(binding_id="b", envelope_checksum="e", symbol="2330.TW",
            event_time=f"2024-10-{day:02}T05:30:00+00:00", price=100 + day, open_price=100 + day,
            source_ref="r", bar_frequency="1d", members={member_id: {
                "artifact_id": "a", "version": "1", "checksum": "c", "virtual_only": False,
                "view": {"direction": "LONG", "strength": 1},
                **({"joined_at": joined_at} if joined_at else {})}})[0]

    def test_true_only_when_history_starts_at_admission(self):
        self.assertTrue(self._mark(MemberVirtualBooks(), 2, "2024-10-01T00:00:00+00:00")["since_inception"])
        self.assertFalse(self._mark(MemberVirtualBooks(), 2, None)["since_inception"])
        self.assertFalse(self._mark(MemberVirtualBooks(), 20, "2024-10-01T00:00:00+00:00")["since_inception"])
        self.assertFalse(self._mark(MemberVirtualBooks(), 2, "2024-10-09T00:00:00+00:00")["since_inception"])

    def test_survives_restart_and_a_later_envelope_carries_the_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v.json"
            self._mark(MemberVirtualBooks(path), 2, "2024-10-01T00:00:00+00:00")
            self.assertTrue(MemberVirtualBooks(path).snapshot()[0]["since_inception"])

    def test_book_without_admission_records_the_first_carried_joined_at_once(self):
        # Hosted 2026-11-26: books created 2026-11-19 by views without joined_at hold admitted_at null.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v.json"
            self._mark(MemberVirtualBooks(path), 2, None)
            self.assertIsNone(next(iter(MemberVirtualBooks(path).books.values()))["admitted_at"])
            book = self._mark(MemberVirtualBooks(path), 3, "2024-10-01T00:00:00+00:00")
            self.assertEqual(book["admitted_at"], "2024-10-01T00:00:00+00:00")
            self.assertTrue(book["since_inception"])  # first mark 10-02 is within the grace of 10-01
            # A different later value never rewrites the recorded admission; a missing one never clears it.
            book = self._mark(MemberVirtualBooks(path), 4, "2024-09-01T00:00:00+00:00")
            self.assertEqual(book["admitted_at"], "2024-10-01T00:00:00+00:00")
            book = self._mark(MemberVirtualBooks(path), 7, None)
            self.assertEqual(book["admitted_at"], "2024-10-01T00:00:00+00:00")
            self.assertEqual(len(book["dates"]), 3)  # one observation per session, no extra rows

    def test_recorded_admission_too_far_before_the_first_mark_stays_not_since_inception(self):
        books = MemberVirtualBooks()
        self._mark(books, 20, None)
        book = self._mark(books, 21, "2024-10-01T00:00:00+00:00")
        self.assertEqual(book["admitted_at"], "2024-10-01T00:00:00+00:00")
        self.assertFalse(book["since_inception"])
