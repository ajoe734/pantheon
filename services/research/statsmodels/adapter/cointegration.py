"""
Statsmodels Engle-Granger cointegration test for the Pantheon research plane.

Provides cointegration_test(prices_a, prices_b) implementing the
Engle-Granger two-step test and spread/half-life estimation for
stat-arb style signal generation.

Governance: outputs are signal_snapshot research artifacts only;
no direct live-trading influence.
"""
from __future__ import annotations

import math
from typing import Any


def cointegration_test(
    prices_a: list[float],
    prices_b: list[float],
) -> dict[str, Any]:
    """Run Engle-Granger cointegration test on two price series.

    Parameters
    ----------
    prices_a, prices_b:
        Equal-length lists of numeric prices (at least 20 observations).

    Returns
    -------
    dict with keys:
        p_value   - Engle-Granger p-value (float, lower = more evidence of cointegration)
        spread    - OLS residuals: prices_a − (alpha + beta * prices_b)  (list[float])
        half_life - estimated mean-reversion half-life in periods (float)
    """
    import numpy as np
    from statsmodels.tsa.stattools import coint

    if len(prices_a) != len(prices_b):
        raise ValueError(
            f"prices_a and prices_b must have the same length; "
            f"got {len(prices_a)} and {len(prices_b)}"
        )
    if len(prices_a) < 20:
        raise ValueError(
            f"At least 20 observations required; got {len(prices_a)}"
        )

    y = np.array(prices_a, dtype=float)
    x = np.array(prices_b, dtype=float)

    # Engle-Granger p-value
    _, p_value, _ = coint(y, x)

    # Spread via OLS: y = alpha + beta * x + residual
    n = len(y)
    x_mat = np.column_stack([np.ones(n), x])
    coeffs, _, _, _ = np.linalg.lstsq(x_mat, y, rcond=None)
    alpha, beta = float(coeffs[0]), float(coeffs[1])
    spread_arr = y - alpha - beta * x

    # Half-life via AR(1) on spread differences:
    # Δs_t = c + gamma * s_{t-1} + ε → half_life = −log(2) / log(1 + gamma)
    delta_s = spread_arr[1:] - spread_arr[:-1]
    lag_s = spread_arr[:-1]
    lag_mat = np.column_stack([np.ones(len(lag_s)), lag_s])
    ar_coeffs, _, _, _ = np.linalg.lstsq(lag_mat, delta_s, rcond=None)
    gamma = float(ar_coeffs[1])
    rho = 1.0 + gamma
    if 0.0 < rho < 1.0:
        half_life = -math.log(2.0) / math.log(rho)
    else:
        half_life = float("inf")

    return {
        "p_value": float(p_value),
        "spread": spread_arr.tolist(),
        "half_life": half_life,
    }
