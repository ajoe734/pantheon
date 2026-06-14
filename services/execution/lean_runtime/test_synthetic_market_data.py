"""Tests for the opt-in paper SyntheticMarketData price source (right-half B)."""
import types
import unittest

from services.execution.lean_runtime.paper_runtime import SyntheticMarketData


class _FakeSecurity:
    def __init__(self, price: float) -> None:
        self.Price = price


class _FakeAlgo:
    """Minimal stand-in for PaperExecutionAlgorithm's price/portfolio surface."""

    def __init__(self, holdings: dict[str, float], fill_price: float = 100.0) -> None:
        self._sec = {s: _FakeSecurity(fill_price) for s in holdings}
        self._cost = {s: fill_price for s in holdings}
        self.Portfolio = {
            s: types.SimpleNamespace(Quantity=q) for s, q in holdings.items()
        }

    def _security(self, symbol: str) -> _FakeSecurity:
        return self._sec.setdefault(symbol, _FakeSecurity(100.0))

    def SetSecurityPrice(self, symbol: str, price: float) -> None:
        self._security(symbol).Price = price

    def pnl(self) -> float:
        return sum(
            self.Portfolio[s].Quantity * (self._security(s).Price - self._cost[s])
            for s in self.Portfolio
        )


class TestSyntheticMarketData(unittest.TestCase):
    def test_advance_moves_held_symbol_prices_within_bounds(self):
        algo = _FakeAlgo({"AAPL.US": 7.0})
        smd = SyntheticMarketData(amplitude=0.05)
        # advance several steps; at least one must move the price off the 100 anchor
        moved = False
        for _ in range(8):
            prices = smd.advance(algo)
            self.assertIn("AAPL.US", prices)
            # bounded within +/-5% of the 100 anchor
            self.assertGreaterEqual(prices["AAPL.US"], 95.0 - 1e-6)
            self.assertLessEqual(prices["AAPL.US"], 105.0 + 1e-6)
            if abs(prices["AAPL.US"] - 100.0) > 1e-6:
                moved = True
        self.assertTrue(moved, "synthetic market data must move the price off the anchor")

    def test_pnl_becomes_nonzero_after_advance(self):
        algo = _FakeAlgo({"AAPL.US": 7.0})
        self.assertEqual(algo.pnl(), 0.0)  # flat at fill price
        smd = SyntheticMarketData(amplitude=0.05)
        nonzero_seen = any(
            (smd.advance(algo), algo.pnl())[1] != 0.0 for _ in range(8)
        )
        self.assertTrue(nonzero_seen, "PnL must move once synthetic prices move")

    def test_deterministic(self):
        a1, a2 = _FakeAlgo({"AAPL.US": 1.0}), _FakeAlgo({"AAPL.US": 1.0})
        s1, s2 = SyntheticMarketData(), SyntheticMarketData()
        for _ in range(5):
            self.assertEqual(s1.advance(a1), s2.advance(a2))

    def test_only_held_symbols_touched(self):
        algo = _FakeAlgo({"AAPL.US": 1.0})
        smd = SyntheticMarketData()
        prices = smd.advance(algo)
        self.assertEqual(set(prices.keys()), {"AAPL.US"})


if __name__ == "__main__":
    unittest.main()
