"""
Smoke test for the statsmodels cointegration adapter.

Generates a deterministic synthetic cointegrated pair, asserts p_value < 0.05,
and emits a signal_snapshot artifact with a deterministic checksum.

Run: pytest -q services/research/statsmodels/smoke_test.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from adapter import cointegration_test


_SEED = 42
_N = 120


def _make_cointegrated_series(n: int = _N, seed: int = _SEED):
    """Return two deterministically cointegrated price series."""
    import numpy as np

    rng = np.random.default_rng(seed)
    common_walk = np.cumsum(rng.standard_normal(n))
    noise_a = rng.standard_normal(n) * 0.5
    noise_b = rng.standard_normal(n) * 0.5
    prices_a = (100.0 + common_walk + noise_a).tolist()
    prices_b = (100.0 + 1.5 * common_walk + noise_b).tolist()
    return prices_a, prices_b


def test_cointegration_smoke():
    prices_a, prices_b = _make_cointegrated_series()
    result = cointegration_test(prices_a, prices_b)

    assert "p_value" in result, "result must contain p_value"
    assert "spread" in result, "result must contain spread"
    assert "half_life" in result, "result must contain half_life"
    assert result["p_value"] < 0.05, (
        f"Expected cointegrated series to yield p_value < 0.05, got {result['p_value']:.6f}"
    )
    assert len(result["spread"]) == _N, (
        f"spread length must equal series length ({_N}), got {len(result['spread'])}"
    )
    assert result["half_life"] > 0, (
        f"half_life must be positive, got {result['half_life']}"
    )

    # Build signal_snapshot artifact with a deterministic checksum.
    # Round to 8 decimal places so the checksum is stable across runs.
    signal_snapshot = {
        "artifact_type": "signal_snapshot",
        "test": "engle_granger",
        "seed": _SEED,
        "series_length": _N,
        "spread_length": len(result["spread"]),
        "p_value": round(result["p_value"], 8),
        "half_life": round(result["half_life"], 4),
    }
    snapshot_bytes = json.dumps(signal_snapshot, sort_keys=True).encode()
    checksum = hashlib.sha256(snapshot_bytes).hexdigest()
    signal_snapshot["checksum"] = checksum

    assert signal_snapshot["artifact_type"] == "signal_snapshot"
    assert len(checksum) == 64, f"checksum must be 64 hex chars, got {len(checksum)}"

    return signal_snapshot


if __name__ == "__main__":
    snap = test_cointegration_smoke()
    print("SMOKE TEST PASSED")
    print(json.dumps(snap, indent=2))
