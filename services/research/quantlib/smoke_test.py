"""Smoke test for the QuantLib option pricing adapter.

Run: pytest -q services/research/quantlib/smoke_test.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import price_american_binomial, price_european


_ITM_CALL = {
    "spot": 105.0,
    "strike": 100.0,
    "rate": 0.03,
    "vol": 0.2,
    "tenor": 0.5,
    "option_type": "call",
    "expected_price": 9.553210963345876,
    "expected_delta": 0.6990865896301867,
    "expected_gamma": 0.02344701489334269,
    "expected_vega": 25.85033391991032,
}
_OTM_PUT = {
    "spot": 105.0,
    "strike": 95.0,
    "rate": 0.03,
    "vol": 0.2,
    "tenor": 0.5,
    "option_type": "put",
    "expected_price": 1.6422446904051533,
    "expected_delta": -0.1882202990311166,
    "expected_gamma": 0.0181690774564163,
    "expected_vega": 20.031407895698973,
}
_TOLERANCE = 1e-3


def _assert_close(actual: float, expected: float, *, label: str) -> None:
    assert abs(actual - expected) <= _TOLERANCE, (
        f"{label} expected {expected:.10f}, got {actual:.10f}"
    )


def _assert_european_case(case: dict[str, float | str]) -> dict[str, float]:
    result = price_european(
        float(case["spot"]),
        float(case["strike"]),
        float(case["rate"]),
        float(case["vol"]),
        float(case["tenor"]),
        str(case["option_type"]),
    )
    _assert_close(result["price"], float(case["expected_price"]), label="price")
    _assert_close(result["delta"], float(case["expected_delta"]), label="delta")
    _assert_close(result["gamma"], float(case["expected_gamma"]), label="gamma")
    _assert_close(result["vega"], float(case["expected_vega"]), label="vega")
    return result


def build_pricing_snapshot() -> dict[str, object]:
    itm_call = _assert_european_case(_ITM_CALL)
    otm_put = _assert_european_case(_OTM_PUT)
    american_call = price_american_binomial(
        105.0, 100.0, 0.03, 0.2, 0.5, "call", steps=512
    )
    american_put = price_american_binomial(
        105.0, 95.0, 0.03, 0.2, 0.5, "put", steps=512
    )

    # Non-dividend-paying American calls should converge to European calls.
    _assert_close(
        american_call["price"],
        float(_ITM_CALL["expected_price"]),
        label="american_call_price",
    )
    assert american_put["price"] >= otm_put["price"]

    pricing_snapshot = {
        "artifact_type": "pricing_snapshot",
        "framework": "quantlib",
        "model_suite": ["black_scholes", "crr_binomial"],
        "cases": {
            "itm_call": {
                "european": _rounded(itm_call),
                "american_binomial": _rounded(american_call),
            },
            "otm_put": {
                "european": _rounded(otm_put),
                "american_binomial": _rounded(american_put),
            },
        },
        "registry_entry": {
            "artifact_type": "pricing_snapshot",
            "artifact_state": "draft",
            "deployment_stage": "none",
        },
    }
    checksum_payload = json.dumps(pricing_snapshot, sort_keys=True).encode()
    pricing_snapshot["checksum"] = hashlib.sha256(checksum_payload).hexdigest()

    assert pricing_snapshot["artifact_type"] == "pricing_snapshot"
    assert pricing_snapshot["registry_entry"]["artifact_type"] == "pricing_snapshot"
    assert len(pricing_snapshot["checksum"]) == 64
    return pricing_snapshot


def test_quantlib_option_pricing_smoke() -> None:
    build_pricing_snapshot()


def _rounded(result: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 8) for key, value in result.items()}


if __name__ == "__main__":
    snapshot = build_pricing_snapshot()
    print("SMOKE TEST PASSED")
    print(json.dumps(snapshot, indent=2, sort_keys=True))
