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


# ---- admission fill and book continuity (MEMBER-BOOK-ADMISSION-GAP-FILL-20261126) ----------------
# Hosted shape read 2026-11-26: member admitted 2026-11-08T18:10:02Z, book created 2026-11-19 with
# admitted_at null and four marks.  Governed daily bars in the shape governed_market_dataset serves
# (dataset_id = snapshot id); the registry artifact is the canonical Taiwan momentum artifact.
import importlib as _importlib
import json as _json
import math as _math
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone

import pytest as _pytest

_ADMITTED = "2026-11-08T18:10:02Z"
_LIVE_DAYS = ["2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24"]
_SNAPSHOT_ID = "gmd-2026-11-24-0123456789ab"
_TENANT = "tenant-a"


def _weekdays(start, end):
    day, out = _date.fromisoformat(start), []
    while day <= _date.fromisoformat(end):
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += _timedelta(days=1)
    return out


def _governed_dataset(*, drop=(), **_):
    records = []
    for symbol, base in (("2330.TW", 100.0), ("2317.TW", 50.0)):
        for i, day in enumerate(_weekdays("2026-07-01", "2026-11-24")):
            if day in drop and symbol == "2330.TW":
                continue
            close = base + 6 * _math.sin(i / 2.5) + 0.05 * i
            records.append({"instrument": symbol, "date": day, "open": round(close * 0.998, 4),
                            "high": round(close * 1.01, 4), "low": round(close * 0.99, 4),
                            "close": round(close, 4), "volume": 1_000_000.0})
    return {"dataset_id": _SNAPSHOT_ID, "strategy_id": "pool-admission", "data_frequency": "daily",
            "source_dataset_refs": [f"daily_bars:{_SNAPSHOT_ID}"], "records": records}


def _member_artifact():
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact
    artifact = build_canonical_tw_strategy_artifact(strategy_id="strat-member-fill", symbols=("2330.TW",),
                                                    lookback_bars=3, momentum_threshold=0.0)
    return artifact, strategy_artifact_checksum(artifact)


def _member(checksum, artifact_id, *, joined_at=None, direction="LONG"):
    return {"artifact_id": artifact_id, "version": "1.0.0", "checksum": checksum, "risk_weight": 1.0,
            "virtual_only": False, "underlying_count": 1,
            "view": {"direction": direction, "strength": 1},
            **({"joined_at": joined_at} if joined_at else {})}


def _fill_source(reads=None, **dataset_kwargs):
    from services.execution.lean_runtime.member_virtual_book import governed_admission_replay_source
    artifact, checksum = _member_artifact()
    entry = {"registry_id": artifact["artifact_id"], "checksum": checksum,
             "metadata": {"strategy_artifact": artifact}}

    def reader(ref, tenant):
        if reads is not None:
            reads.append((ref, tenant))
        return entry

    return governed_admission_replay_source(
        entry_reader=reader, tenant_id=_TENANT,
        dataset_resolver=lambda **kw: _governed_dataset(**dataset_kwargs, **kw)), artifact, checksum


def _live_marks(books, artifact_id, checksum, days=_LIVE_DAYS, *, joined_at=None, binding="rb-1", env="env-1"):
    dataset = {(r["instrument"], r["date"]): r for r in _governed_dataset()["records"]}
    snapshot = None
    for day in days:
        row = dataset.get(("2330.TW", day)) or {"close": 110.0, "open": 109.0}
        snapshot = books.observe(
            binding_id=binding, envelope_checksum=env, symbol="2330.TW",
            event_time=f"{day}T05:30:00+00:00", price=row["close"], open_price=row["open"],
            source_ref="recorded:" + day, bar_frequency="1d",
            members={artifact_id: _member(checksum, artifact_id, joined_at=joined_at)})
    return snapshot


def _reweight_accepts(book, checksum):
    policy = _importlib.import_module("services.control-plane.persona.pool_policy")
    live = [{"artifact_ref": book["member_id"], "checksum": checksum, "stage": "active", "risk_weight": 1.0}]
    return policy.reweight(live, {book["member_id"]: {**book, **book["net_of_costs"]}},
                           policy.load_policy(), "2026-11-26")[1][book["member_id"]]


def test_hosted_book_is_filled_from_admission_with_the_governed_replay_and_reweight_accepts_it(tmp_path):
    source, artifact, checksum = _fill_source()
    aid = artifact["artifact_id"]
    path = tmp_path / "persona-books.json"
    # The four live marks as hosted wrote them: views without joined_at, admitted_at null.
    [before] = _live_marks(MemberVirtualBooks(path), aid, checksum)
    assert before["admitted_at"] is None and before["since_inception"] is False
    live_rows = _json.loads(_json.dumps(next(iter(MemberVirtualBooks(path).books.values()))["observations"]))
    # The next mark arrives with the admission carried by the published envelope (joined_at).
    books = MemberVirtualBooks(path, history_source=source)
    [book] = _live_marks(books, aid, checksum, ["2026-11-25"], joined_at=_ADMITTED)
    stored = next(iter(MemberVirtualBooks(path).books.values()))
    rows = stored["observations"]
    sessions = _weekdays("2026-11-09", "2026-11-18")
    assert len(sessions) == 8
    assert [r["event_time"][:10] for r in rows] == sessions + _LIVE_DAYS + ["2026-11-25"]
    filled = rows[:8]
    assert {r["source_ref"] for r in filled} == {f"governed-replay:{_SNAPSHOT_ID}:pool_admission._replay"}
    assert all(r["open"] and r["price"] > 0 and r["direction"] in (-1, 0, 1) for r in filled)
    assert rows[8:12] == live_rows  # live observations are never changed
    assert book["admitted_at"] == _ADMITTED and book["since_inception"] is True
    assert book["since_inception_reason"] is None
    assert stored["admission_fill"]["gaps"] == [] and stored["admission_fill"]["filled"] == sessions
    assert f"governed-replay:{_SNAPSHOT_ID}:pool_admission._replay" in book["source_ref"]
    assert len(book["dates"]) == len(rows) - 1
    stats = _reweight_accepts(book, checksum)
    assert stats["days"] == len(rows) - 1


def test_fill_replays_the_existing_pool_admission_views_and_next_open_fills(tmp_path):
    from services.research.pool_admission import _replay
    from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter
    source, artifact, checksum = _fill_source()
    sessions = _weekdays("2026-11-09", "2026-11-18")
    result = source(artifact["artifact_id"], _member(checksum, artifact["artifact_id"]), "2330.TW", sessions)
    prepared = GovernedVectorbtInputAdapter().prepare(_governed_dataset())
    rows_out = []
    _replay(prepared, artifact, sessions, rows_out=rows_out)
    expected = {r["date"]: r for r in rows_out if r["symbol"] == "2330.TW"}
    assert set(result["rows"]) == set(sessions) == set(expected)
    for day in sessions:
        assert result["rows"][day]["exposure"] == expected[day]["exposure"]
        assert result["rows"][day]["close"] == expected[day]["close"]
    # Marks of the filled book use the shared convention: the decision of mark D fills at the open of D+1.
    books = MemberVirtualBooks(tmp_path / "b.json", history_source=source)
    [book] = _live_marks(books, artifact["artifact_id"], checksum, joined_at=_ADMITTED)
    assert book["fill_convention"] == FILL_CONVENTION and book["since_inception"] is True


def test_fill_runs_once_and_is_idempotent_across_restarts_and_envelope_replacements(tmp_path):
    reads = []
    source, artifact, checksum = _fill_source(reads)
    aid, path = artifact["artifact_id"], tmp_path / "persona-books.json"
    _live_marks(MemberVirtualBooks(path, history_source=source), aid, checksum, joined_at=_ADMITTED)
    first = _json.loads(path.read_text())
    assert len(reads) == 1
    # Restart, then a replacement of the envelope/binding: the carried book keeps the fill.
    restarted = MemberVirtualBooks(path, history_source=source)
    _live_marks(restarted, aid, checksum, ["2026-11-25"], joined_at=_ADMITTED, binding="rb-2", env="env-2")
    replaced = MemberVirtualBooks(path, history_source=source)
    [book] = _live_marks(replaced, aid, checksum, ["2026-11-26"], joined_at=_ADMITTED, binding="rb-3", env="env-3")
    assert len(reads) == 1  # no second registry read or dataset build: nothing refilled
    books = [b for b in _json.loads(path.read_text())["books"].values() if b["binding_id"] == "rb-3"]
    assert len(books) == 1 and len(books[0]["observations"]) == len(next(iter(first["books"].values()))["observations"]) + 2
    assert books[0]["admission_fill"] == next(iter(first["books"].values()))["admission_fill"]
    assert book["since_inception"] is True


def test_session_neither_source_produces_stays_a_gap_and_the_book_not_since_inception(tmp_path):
    source, artifact, checksum = _fill_source(drop=("2026-11-12",))
    aid, path = artifact["artifact_id"], tmp_path / "b.json"
    [book] = _live_marks(MemberVirtualBooks(path, history_source=source), aid, checksum, joined_at=_ADMITTED)
    stored = next(iter(MemberVirtualBooks(path).books.values()))
    assert stored["admission_fill"]["gaps"] == [{"date": "2026-11-12", "reason": "session_not_in_governed_dataset"}]
    assert "2026-11-12" not in {r["event_time"][:10] for r in stored["observations"]}  # nothing invented
    assert len(stored["admission_fill"]["filled"]) == 7
    assert book["since_inception"] is False
    assert book["since_inception_reason"] == "admission_gap_unfilled:session_not_in_governed_dataset"
    with _pytest.raises(ValueError, match="virtual_book_high_water_history_missing"):
        _reweight_accepts(book, checksum)


def test_unavailable_or_unconfigured_source_leaves_named_gaps_and_never_breaks_the_mark(tmp_path):
    aid = _member_artifact()[0]["artifact_id"]
    checksum = _member_artifact()[1]

    def unavailable(*_):
        raise RuntimeError("daily_bars_reader_unavailable: ImportError: no pyarrow")

    for name, kwargs, reason in (
            ("down", {"history_source": unavailable}, "admission_replay_unavailable:daily_bars_reader_unavailable"),
            ("none", {}, "admission_replay_source_unconfigured")):
        path = tmp_path / f"{name}.json"
        [book] = _live_marks(MemberVirtualBooks(path, **kwargs), aid, checksum, joined_at=_ADMITTED)
        stored = next(iter(MemberVirtualBooks(path).books.values()))
        assert [r["event_time"][:10] for r in stored["observations"]] == _LIVE_DAYS  # live rows only
        assert {g["reason"] for g in stored["admission_fill"]["gaps"]} == {reason}
        assert len(stored["admission_fill"]["gaps"]) == 8
        assert book["since_inception"] is False and book["since_inception_reason"].startswith("admission_gap_unfilled")


def test_gap_from_an_unavailable_source_is_filled_once_the_source_returns_in_a_later_process(tmp_path):
    source, artifact, checksum = _fill_source()
    aid, path = artifact["artifact_id"], tmp_path / "b.json"
    _live_marks(MemberVirtualBooks(path), aid, checksum, joined_at=_ADMITTED)  # no source: 8 gaps recorded
    assert len(next(iter(MemberVirtualBooks(path).books.values()))["admission_fill"]["gaps"]) == 8
    live = _json.loads(path.read_text())["books"]
    [book] = _live_marks(MemberVirtualBooks(path, history_source=source), aid, checksum, ["2026-11-25"],
                         joined_at=_ADMITTED)
    stored = next(iter(MemberVirtualBooks(path).books.values()))
    assert stored["admission_fill"]["gaps"] == [] and len(stored["admission_fill"]["filled"]) == 8
    assert stored["observations"][8:12] == next(iter(live.values()))["observations"][:4]
    assert book["since_inception"] is True


def test_book_inside_the_admission_grace_is_not_filled(tmp_path):
    reads = []
    source, artifact, checksum = _fill_source(reads)
    [book] = _live_marks(MemberVirtualBooks(tmp_path / "b.json", history_source=source),
                         artifact["artifact_id"], checksum, ["2026-11-09", "2026-11-10"],
                         joined_at=_ADMITTED)
    assert reads == [] and book["since_inception"] is True
    assert "admission_fill" not in next(iter(MemberVirtualBooks(tmp_path / "b.json").books.values()))


def test_runtime_book_survives_a_binding_replacement_with_the_reconcilers_state_path(tmp_path, monkeypatch):
    """PANTHEON_PERFORMANCE_STATE_PATH per binding (paper_fleet_reconciler line 1441) used to put the books
    at <binding state>.lifecycle-outbox.json.member-books.json: a published envelope started an empty book."""
    from services.execution.lean_runtime.test_paper_runtime import (
        InMemoryPendingSignalStore, PaperRuntimeService, PaperRuntimeServiceTest,
        _FakeRuntimeManagerClient, _FakeTelemetryEmitter,
    )
    from services.paper_fleet_reconciler.paper_fleet_reconciler import _binding_state_filename
    source, artifact, checksum = _fill_source()
    aid = artifact["artifact_id"]
    monkeypatch.setenv("PANTHEON_TENANT_ID", _TENANT)
    monkeypatch.delenv("PANTHEON_LIFECYCLE_OUTBOX_PATH", raising=False)

    def service(binding_id):
        monkeypatch.setenv("PANTHEON_PERFORMANCE_STATE_PATH", str(tmp_path / _binding_state_filename(binding_id)))
        built = PaperRuntimeService(
            store=InMemoryPendingSignalStore([]), identity=PaperRuntimeServiceTest._identity(None),
            runtime_manager_client=_FakeRuntimeManagerClient([]), telemetry_emitter=_FakeTelemetryEmitter(),
            poll_interval_seconds=3600)
        built._member_virtual_books.history_source = source
        return built

    # Books written by the earlier runtime version sit in the binding file, admitted_at null.
    legacy = MemberVirtualBooks(tmp_path / (_binding_state_filename("rb-old") + ".lifecycle-outbox.json.member-books.json"))
    _live_marks(legacy, aid, checksum, binding="rb-old", env="env-old")
    legacy_before = (tmp_path / (_binding_state_filename("rb-old") + ".lifecycle-outbox.json.member-books.json")).read_bytes()
    old = service("rb-old")
    _live_marks(old._member_virtual_books, aid, checksum, ["2026-11-25"], joined_at=_ADMITTED,
                binding="rb-old", env="env-old")
    new = service("rb-new")  # the publication moved the persona to a new RuntimeBinding and state file
    assert new._member_virtual_books.path == old._member_virtual_books.path  # one book file per persona
    assert new._member_virtual_books.path.parent.name == "member-books"
    [book] = _live_marks(new._member_virtual_books, aid, checksum, ["2026-11-26"], joined_at=_ADMITTED,
                         binding="rb-new", env="env-new")
    stored = [b for b in new._member_virtual_books.books.values() if b["binding_id"] == "rb-new"]
    assert len(stored) == 1
    assert stored[0]["admitted_at"] == _ADMITTED and book["since_inception"] is True
    assert [r["event_time"][:10] for r in stored[0]["observations"] if r["source_ref"].startswith("recorded:")] \
        == _LIVE_DAYS + ["2026-11-25", "2026-11-26"]
    assert len(stored[0]["observations"]) == 8 + 6  # growth is one row per session after the one-time fill
    # Old binding files are left in place, byte for byte.
    assert (tmp_path / (_binding_state_filename("rb-old") + ".lifecycle-outbox.json.member-books.json")).read_bytes() == legacy_before


def test_marks_recorded_under_earlier_binding_files_are_carried_in_once_without_changing_any_mark(tmp_path):
    source, artifact, checksum = _fill_source()
    aid = artifact["artifact_id"]
    first, second = tmp_path / "rb-a.json.lifecycle-outbox.json.member-books.json", \
        tmp_path / "rb-b.json.lifecycle-outbox.json.member-books.json"
    _live_marks(MemberVirtualBooks(first), aid, checksum, _LIVE_DAYS[:2], binding="rb-a", env="env-a")
    _live_marks(MemberVirtualBooks(second), aid, checksum, _LIVE_DAYS[2:], binding="rb-b", env="env-b")
    # A different member's book in the same directory is never carried.
    _live_marks(MemberVirtualBooks(tmp_path / "rb-c.json.lifecycle-outbox.json.member-books.json"),
                "other-member", "sha256:other", ["2026-11-10"], binding="rb-c", env="env-c")
    recorded = {r["event_time"]: r for path in (first, second)
                for b in _json.loads(path.read_text())["books"].values() for r in b["observations"]}
    persona = tmp_path / "member-books" / "persona.json"
    books = MemberVirtualBooks(persona, legacy_paths=sorted(tmp_path.glob("*.member-books.json")),
                               history_source=source)
    [book] = _live_marks(books, aid, checksum, ["2026-11-25"], joined_at=_ADMITTED, binding="rb-new", env="env-new")
    stored = next(b for b in books.books.values() if b["binding_id"] == "rb-new")
    assert sorted(stored["carried_from"]) == [first.name, second.name]
    live = {r["event_time"]: r for r in stored["observations"] if r["source_ref"].startswith("recorded:")}
    assert {t: live[t] for t in recorded} == recorded  # every recorded mark carried unchanged
    assert book["admitted_at"] == _ADMITTED and book["since_inception"] is True
    # Once: a new binding's book comes from the persona store, not from the legacy files again.
    first.write_text("{}")  # would raise or differ if it were read again
    again = MemberVirtualBooks(persona, legacy_paths=[first, second], history_source=source)
    [later] = _live_marks(again, aid, checksum, ["2026-11-26"], joined_at=_ADMITTED, binding="rb-next", env="env-next")
    assert later["since_inception"] is True and len(later["dates"]) == len(book["dates"]) + 1


def test_admission_fill_requests_only_member_symbols_and_needed_sessions(tmp_path):
    """Acceptance 2: the admission fill requests only the member's symbols (1 symbol, no companion) and
    only the sessions it needs (warmup + gap sessions, 10 bars total < 30), and replays identically."""
    from services.execution.lean_runtime.member_virtual_book import governed_admission_replay_source

    artifact, checksum = _member_artifact()
    aid = artifact["artifact_id"]
    entry = {"registry_id": aid, "checksum": checksum, "metadata": {"strategy_artifact": artifact}}
    sessions = _weekdays("2026-11-09", "2026-11-18")
    warmup = ["2026-11-05", "2026-11-06"]
    needed_days = set(warmup + sessions)
    resolver_calls = []

    def slim_resolver(**kwargs):
        resolver_calls.append(kwargs)
        records = [
            r for r in _governed_dataset()["records"]
            if r["instrument"] == "2330.TW" and r["date"] in needed_days
        ]
        assert len(records) == 10  # 2 warmup + 8 sessions of 1 symbol only
        return {
            "dataset_id": _SNAPSHOT_ID,
            "strategy_id": kwargs["strategy_id"],
            "data_frequency": "daily",
            "source_dataset_refs": [f"daily_bars:{_SNAPSHOT_ID}"],
            "records": records,
        }

    slim_source = governed_admission_replay_source(
        entry_reader=lambda ref, tenant: entry, tenant_id=_TENANT, dataset_resolver=slim_resolver
    )
    full_source, _, _ = _fill_source()
    slim_res = slim_source(aid, _member(checksum, aid), "2330.TW", sessions)
    full_res = full_source(aid, _member(checksum, aid), "2330.TW", sessions)
    assert len(resolver_calls) == 1
    assert resolver_calls[0]["symbols"] == ["2330.TW"]
    assert resolver_calls[0]["sessions"] == sessions
    assert resolver_calls[0]["warmup_days"] == 2
    assert resolver_calls[0]["min_instruments"] == 1
    assert slim_res == full_res


def test_reconciler_retired_sweep_before_first_new_mark_keeps_legacy_book_files_for_three_hosted_personas(tmp_path):
    """Hosted race (2026-11-27 10:00Z republish vs ~23:35Z mark): retiring the three hosted bindings
    removes their state and outbox files but keeps rb-{2b0df305,a2cb762c,eb74118d}*.member-books.json so
    the 3 recorded marks (2026-11-20..2026-11-24) are carried in at the new runtime's first mark."""
    from services.execution.lean_runtime.member_virtual_book import governed_admission_replay_source
    from services.paper_fleet_reconciler.paper_fleet_reconciler import (
        PaperFleetReconciler,
        _binding_state_filename,
    )
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact

    hosted_Recorded_days = ["2026-11-20", "2026-11-21", "2026-11-24"]
    hosted_personas = [
        ("persona-ee7112a829d38b9135ac", "rb-2b0df30559154a93902c209b0318d95c", "rb-2b0df305-new", "0050.TW"),
        ("persona-6507750312870d03a241", "rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4", "rb-a2cb762c-new", "2317.TW"),
        ("persona-2448c7053e33ea48da47", "rb-eb74118dd3ca4b169496b1c239087512", "rb-eb74118d-new", "2330.TW"),
    ]
    all_days = _weekdays("2026-10-15", "2026-11-27")
    entries = {}
    legacy_book_files = []
    excluded_bindings = []

    for persona_id, old_bid, _new_bid, sym in hosted_personas:
        art = build_canonical_tw_strategy_artifact(
            strategy_id=f"strat-{persona_id}", symbols=(sym,), lookback_bars=3, momentum_threshold=0.0
        )
        chk = strategy_artifact_checksum(art)
        aid = art["artifact_id"]
        entries[aid] = (art, chk, sym)

        state_file = tmp_path / _binding_state_filename(old_bid)
        outbox_file = state_file.with_name(f"{state_file.name}.lifecycle-outbox.json")
        book_file = outbox_file.with_name(f"{outbox_file.name}.member-books.json")
        state_file.write_text(_json.dumps({"pending_fill_publications": []}))
        outbox_file.write_text(_json.dumps({"pending": []}))

        legacy_books = MemberVirtualBooks(book_file)
        for i, day in enumerate(hosted_Recorded_days):
            legacy_books.observe(
                binding_id=old_bid, envelope_checksum="sha256:old-env", symbol=sym,
                event_time=f"{day}T23:35:00Z", price=100.0 + i, open_price=99.5 + i,
                source_ref=f"recorded:{day}", bar_frequency="1d",
                members={aid: _member(chk, aid, joined_at=None)},
            )
        legacy_book_files.append(book_file)
        excluded_bindings.append({"binding_id": old_bid, "status": "retired"})

    total_legacy_bytes = sum(p.stat().st_size for p in legacy_book_files)
    assert total_legacy_bytes < 10_000  # runtime budget: 3 legacy book files total under 10 KB

    # 10:00Z daily cycle retires the old bindings; reconciler sweep runs before the ~23:35Z mark.
    recon = PaperFleetReconciler(performance_state_root=str(tmp_path), poll_interval_seconds=999)
    removed = recon._cleanup_retired_state_files(
        excluded_bindings,
        desired_ids={new_bid for _, _, new_bid, _ in hosted_personas},
        active_ids=set(),
    )
    assert removed == [old_bid for _, old_bid, _, _ in hosted_personas]
    for _, old_bid, _, _ in hosted_personas:
        st = tmp_path / _binding_state_filename(old_bid)
        ob = st.with_name(f"{st.name}.lifecycle-outbox.json")
        bk = ob.with_name(f"{ob.name}.member-books.json")
        assert not st.exists() and not ob.exists()
        assert bk.exists()  # legacy book file survives retirement sweep

    # ~23:35Z: each persona's new runtime observes its first mark with joined_at carried in the envelope.
    def resolver(**kwargs):
        sym = kwargs["symbols"][0]
        records = [
            {
                "instrument": sym, "date": d, "open": round(100.0 + i * 0.2, 4),
                "high": round(101.0 + i * 0.2, 4), "low": round(99.0 + i * 0.2, 4),
                "close": round(100.5 + i * 0.2 + (0.6 if i % 2 == 0 else -0.4), 4),
                "volume": 1000.0,
            }
            for i, d in enumerate(all_days)
        ]
        return {
            "dataset_id": _SNAPSHOT_ID, "strategy_id": kwargs["strategy_id"],
            "data_frequency": "daily", "source_dataset_refs": [f"daily_bars:{_SNAPSHOT_ID}"],
            "records": records,
        }

    source = governed_admission_replay_source(
        entry_reader=lambda ref, tenant: {
            "registry_id": ref, "checksum": entries[ref][1],
            "metadata": {"strategy_artifact": entries[ref][0]},
        },
        tenant_id=_TENANT,
        dataset_resolver=resolver,
    )
    expected_fill_sessions = _weekdays("2026-11-09", "2026-11-19")  # 9 sessions before 2026-11-20
    assert len(expected_fill_sessions) == 9

    for (persona_id, _old_bid, new_bid, sym), (aid, (art, chk, _)) in zip(hosted_personas, entries.items()):
        persona_book_path = tmp_path / "member-books" / f"{persona_id}.json"
        books = MemberVirtualBooks(
            persona_book_path,
            legacy_paths=sorted(tmp_path.glob("*.member-books.json")),
            history_source=source,
            trading_day=lambda d: d.weekday() < 5,
        )
        [summary] = books.observe(
            binding_id=new_bid, envelope_checksum="sha256:new-env", symbol=sym,
            event_time="2026-11-27T23:35:00Z", price=105.0, open_price=104.5,
            source_ref="recorded:2026-11-27", bar_frequency="1d",
            members={aid: _member(chk, aid, joined_at=_ADMITTED)},
        )
        stored = next(b for b in books.books.values() if b["binding_id"] == new_bid)
        assert summary["admitted_at"] == _ADMITTED
        assert summary["since_inception"] is True
        assert summary["since_inception_reason"] is None
        assert stored["admission_fill"]["filled"] == expected_fill_sessions
        assert stored["admission_fill"]["gaps"] == []
        recorded_dates = [r["event_time"][:10] for r in stored["observations"] if r["source_ref"].startswith("recorded:")]
        assert recorded_dates == hosted_Recorded_days + ["2026-11-27"]
        stats = _reweight_accepts(summary, chk)
        assert stats["days"] == len(stored["observations"]) - 1

    # No new per-binding *.member-books.json file was created.
    assert sorted(tmp_path.glob("*.member-books.json")) == sorted(legacy_book_files)
