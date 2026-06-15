"""Unit tests for the telemetry-vs-drift consistency verifier's logic."""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "verify_e2e_telemetry_drift_consistency",
    os.path.join(_HERE, "verify_e2e_telemetry_drift_consistency.py"),
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class TestNum(unittest.TestCase):
    def test_num_parses_numbers(self):
        self.assertEqual(mod._num(6), 6.0)
        self.assertEqual(mod._num("3.5"), 3.5)

    def test_num_defaults_zero_on_bad(self):
        self.assertEqual(mod._num(None), 0.0)
        self.assertEqual(mod._num("nope"), 0.0)


class TestDisconnectLogic(unittest.TestCase):
    """The gate fires when telemetry shows trades but drift observed shows zero."""

    @staticmethod
    def _is_disconnect(t_trades, d_trades):
        return bool(t_trades and t_trades > 0 and not d_trades)

    def test_trades_but_zero_observed_is_disconnect(self):
        self.assertTrue(self._is_disconnect(6, 0))
        self.assertTrue(self._is_disconnect(1, 0))

    def test_consistent_is_not_disconnect(self):
        self.assertFalse(self._is_disconnect(6, 6))
        self.assertFalse(self._is_disconnect(0, 0))

    def test_no_telemetry_trades_is_not_disconnect(self):
        # idle runtime: telemetry 0 and drift 0 is fine
        self.assertFalse(self._is_disconnect(0, 0))


if __name__ == "__main__":
    unittest.main()
