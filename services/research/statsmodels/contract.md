# OSS-STAT-001 Contract: statsmodels Cointegration Adapter

Status: implemented
Task: OSS-STAT-001
Reviewer: Codex

## Purpose

Provides a stat-arb style Engle-Granger cointegration check for pairs of price
series. Output is a `signal_snapshot` research artifact; it has no direct
live-trading influence.

## Public Interface

### `adapter.cointegration_test(prices_a, prices_b)`

```python
def cointegration_test(
    prices_a: list[float],
    prices_b: list[float],
) -> dict:
    ...
```

**Inputs:**

| Parameter  | Type         | Constraint                     |
|------------|--------------|--------------------------------|
| `prices_a` | list[float]  | ≥ 20 observations, same length |
| `prices_b` | list[float]  | ≥ 20 observations, same length |

**Output dict:**

| Key        | Type        | Description                                             |
|------------|-------------|---------------------------------------------------------|
| `p_value`  | float       | Engle-Granger test p-value (< 0.05 → cointegrated)     |
| `spread`   | list[float] | OLS residuals: `prices_a − (alpha + beta * prices_b)`  |
| `half_life`| float       | AR(1) mean-reversion half-life in periods               |

**Raises:**

- `ValueError` if series lengths differ or fewer than 20 observations.

## Signal Snapshot Artifact

The smoke test emits a `signal_snapshot` artifact:

```json
{
  "artifact_type": "signal_snapshot",
  "test": "engle_granger",
  "seed": 42,
  "series_length": 120,
  "spread_length": 120,
  "p_value": "<rounded to 8 dp>",
  "half_life": "<rounded to 4 dp>",
  "checksum": "<sha256 of the above sorted-key JSON>"
}
```

The checksum is SHA-256 of the JSON body (keys sorted, no checksum field
included in the hashed payload). It is deterministic for a fixed seed.

## Governance

- Output artifact type: `signal_snapshot` (research plane only)
- `direct_live_influence: false`
- No broker, registry, or execution-plane writes.
- Consumes: `statsmodels==0.14.2`, `numpy`, `pandas` (see `requirements.txt`).

## File Isolation

This adapter is self-contained in `services/research/statsmodels/`. It does
not import from any other `services/research/` subdirectory.
