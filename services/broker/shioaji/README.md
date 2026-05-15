# services/broker/shioaji — Shioaji TW Broker Adapter

Pantheon broker adapter for Taiwan markets via the Shioaji SDK (Sinopac Securities).

## Status

Scaffold — sandbox (simulation) mode only. Live orders are permanently disabled.

## Gate

| Env var | Default | Effect |
|---|---|---|
| `BROKER_SHIOAJI_SANDBOX_ENABLED` | `false` | Must be `true` to enable sandbox (simulation) orders |
| `BROKER_SHIOAJI_API_KEY` | — | Sinopac API key (required when gate is open) |
| `BROKER_SHIOAJI_SECRET_KEY` | — | Sinopac secret key (required when gate is open) |

Fail-closed by default: all calls raise `SHIOAJI_SANDBOX_DISABLED` (HTTP 503) until the gate is explicitly set.

Live orders **always** raise `SHIOAJI_LIVE_DISABLED` (HTTP 403), regardless of the gate state.

## Interface

```python
from services.broker.shioaji import ShioajiBrokerAdapter, ShioajiBrokerError

adapter = ShioajiBrokerAdapter()  # reads env gate

# Submit a sandbox order
order = adapter.submit(
    capital_pool_id="pool-1",
    strategy_id="strat-tw-001",
    symbol="2330",          # TSMC
    qty=1.0,                # lots (整數)
    side="buy",             # "buy" | "sell"
    order_type="market",    # "market" | "limit"
    limit_price=None,
)

# Cancel
cancelled = adapter.cancel(order.order_id)

# Status
refreshed = adapter.get_status(order.order_id)

# Live reject (always)
adapter.reject_live_order()  # raises ShioajiBrokerError(SHIOAJI_LIVE_DISABLED)
```

## Management Facade

`ShioajiSandboxFacade` wraps the same fail-closed adapter for Management/OODA
surfaces. It does not write evidence files, approve canary/live activation, or
enable capital binding.

```python
from services.broker.shioaji import ShioajiSandboxFacade

payload = ShioajiSandboxFacade().run_lifecycle(
    capital_pool_id="pool-mgmt-broker-001",
    strategy_id="strategy-mgmt-broker-001",
    symbol="2890",
    qty=1.0,
    side="buy",
    order_type="limit",
    limit_price=18.0,
    account_kind="stock",
)
```

The facade output includes:

- `broker="shioaji"` and `environment="sandbox"`
- `account_status` as `ready`, `missing`, or `unsigned`
- `place_result`, `cancel_result`, `readback_result`, and `reconcile_result`
- `production_live_enabled=false`
- `capital_binding_enabled=false`
- `human_gate_required=true`

## Order Shape

`ShioajiOrder` mirrors `PaperOrder` from `services/broker/paper_simulation.py`:

| Field | Type | Notes |
|---|---|---|
| `order_id` | str | UUID hex |
| `capital_pool_id` | str | |
| `strategy_id` | str | |
| `symbol` | str | TW ticker, e.g. `"2330"` |
| `qty` | float | lots |
| `side` | str | `"buy"` or `"sell"` |
| `order_type` | str | `"market"` or `"limit"` |
| `limit_price` | float\|None | |
| `created_at` | str | ISO 8601 UTC |
| `filled_at` | str\|None | |
| `fill_price` | float\|None | |
| `fill_qty` | float | |
| `status` | str | `submitted`, `cancelled` |
| `sim_fill_flag` | bool | always `True` |
| `is_real_order` | bool | always `False` |
| `is_real_capital` | bool | always `False` |
| `deployment_stage` | str | always `"sandbox"` |
| `reject_reason` | str\|None | |
| `shioaji_trade_id` | str\|None | Shioaji SDK trade reference |

## Requirements

```
shioaji>=1.2.0,<2.0.0
```

Install in the broker Docker image only — not in shared requirements.

## Running Tests

```bash
cd services/broker/shioaji
python -m pytest test_adapter.py -v
python -m pytest test_facade.py -v
```

Tests use a mock API and do not require the Shioaji SDK or credentials.

## Sandbox Smoke Evidence

The broker-side place/cancel/readback/reconcile smoke entrypoint is:

```bash
BROKER_SHIOAJI_SANDBOX_ENABLED=1 \
python3 services/broker/shioaji/sandbox_smoke.py \
  --account-kind stock \
  --symbol 2890 \
  --qty 1 \
  --side buy \
  --order-type limit \
  --limit-price 18.0 \
  --cancel-delay-seconds 1.0 \
  --output-file support/evidence/EP5-BROKER-TW-002-RERUN-REAL-FIX/stock-smoke.json
```

This RERUN-REAL-FIX smoke is stock-only. Do not run futures smoke for this
account, and do not run the old sleep/relogin signed-status audit here;
production-mode signed verification belongs to `EP5-BROKER-TW-002-PRODUCTION-VERIFY`.
Real SDK runs fail fast outside the 08:00-20:00 Asia/Taipei sandbox window.

Use `--mock-api` only for local/CI replay when the Shioaji SDK or sandbox
credentials are unavailable. Mock replay output is explicitly marked as
`run_mode=mock_api_replay`; real simulation-account proof must run without
`--mock-api`.

## Policy References

- `PAPER_CANARY_LIVE_POLICY.md` — deployment stage policy; broker live gate
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` — capital binding and fail-closed semantics
