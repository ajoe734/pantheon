# OSS-QUANTLIB-001 Contract: QuantLib Option Pricing Adapter

Status: implemented
Task: OSS-QUANTLIB-001
Reviewer: Claude

## Purpose

Provides a narrow research-plane adapter for vanilla option pricing. The
adapter exposes European Black-Scholes and American CRR binomial pricing for
calls and puts and emits a `pricing_snapshot` artifact in the smoke test.

## Public Interface

### `adapter.price_european(spot, strike, rate, vol, tenor, option_type)`

Returns:

```json
{
  "price": 0.0,
  "delta": 0.0,
  "gamma": 0.0,
  "vega": 0.0
}
```

### `adapter.price_american_binomial(spot, strike, rate, vol, tenor, option_type, steps=512)`

Returns the same shape as `price_european`.

Inputs:

| Parameter | Type | Constraint |
| --- | --- | --- |
| `spot` | float | `> 0` |
| `strike` | float | `> 0` |
| `rate` | float | finite annual risk-free rate |
| `vol` | float | `> 0`, annualized volatility |
| `tenor` | float | `> 0`, years |
| `option_type` | string | `call` or `put` |
| `steps` | int | American binomial only, `>= 3` |

## Pricing Snapshot Artifact

The smoke test emits a deterministic `pricing_snapshot`:

```json
{
  "artifact_type": "pricing_snapshot",
  "framework": "quantlib",
  "model_suite": ["black_scholes", "crr_binomial"],
  "cases": {
    "itm_call": {
      "european": {"price": "...", "delta": "...", "gamma": "...", "vega": "..."},
      "american_binomial": {"price": "...", "delta": "...", "gamma": "...", "vega": "..."}
    },
    "otm_put": {
      "european": {"price": "...", "delta": "...", "gamma": "...", "vega": "..."},
      "american_binomial": {"price": "...", "delta": "...", "gamma": "...", "vega": "..."}
    }
  },
  "registry_entry": {
    "artifact_type": "pricing_snapshot",
    "artifact_state": "draft",
    "deployment_stage": "none"
  },
  "checksum": "<sha256>"
}
```

## Verification

`smoke_test.py` verifies:

- one in-the-money call against fixed Black-Scholes analytic reference values
- one out-of-the-money put against fixed Black-Scholes analytic reference values
- non-dividend American call convergence to the European analytic reference
- American put price is at least the European put price
- emitted artifact is registered as `artifact_type=pricing_snapshot`

## Governance

- Research artifact only; no broker, registry, or execution-plane writes.
- No deployment or live trading side effects.
- Dependency is explicitly pinned in `requirements.txt`: `QuantLib-Python==1.18`.
