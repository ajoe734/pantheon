# Execution Sandbox Canary Activation-Ready Evidence

Task: `SVC-EXECUTION-SANDBOX-CANARY-ACTIVATION-READY`  
Generated at: `2026-05-04T04:59:36Z`  
Status: sandbox/test-key lifecycle evidence only; no production-live broker or real-capital side effects

## Providers

| Provider | Mode | Packet |
|---|---|---|
| IBKR | `test_key_validate_only` | `ibkr/` |
| Shioaji | `test_key_simulation` | `shioaji/` |
| Kraken | `test_key_validate_only` | `kraken/` |

Each provider packet contains:

- `place.request.json`
- `cancel-replace.request.json`
- `readback.json`
- `order-lifecycle.json`
- `execution.json`
- `reconciliation.json`
- `no-real-capital-evidence.json`
- `summary.json`

## Commands

```bash
python3 scripts/run_broker_sandbox_order_smoke.py --provider ibkr --mode test_key_validate_only --symbol AAPL.US --side buy --quantity 1 --limit-price 120 --replace-limit-price 119 --account-ref DU-SANDBOX-001 --credential-ref secret://pantheon/ibkr-paper-test-key --host ibkr-paper-gateway --port 7497 --client-id 42 --output-dir docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/ibkr

python3 scripts/run_broker_sandbox_order_smoke.py --provider shioaji --mode test_key_simulation --symbol 2330.TW --side buy --quantity 1 --limit-price 950 --replace-limit-price 949 --account-ref SHIOAJI-SIM-001 --credential-ref secret://pantheon/shioaji-sim-test-key --output-dir docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/shioaji

python3 scripts/run_broker_sandbox_order_smoke.py --provider kraken --mode test_key_validate_only --symbol BTC/USD.KRAKEN --side buy --quantity 1 --limit-price 64000 --replace-limit-price 63900 --account-ref KRAKEN-TEST-KEY-001 --credential-ref secret://pantheon/kraken-test-key --output-dir docs/deployment/evidence/execution-sandbox-canary-activation-ready/20260504T045936Z/kraken
```

## Boundary

All packets record:

- `production_live.enabled = false`
- `production_live.order_side_effects_allowed = false`
- `order_lifecycle.capital_boundary.real_capital_used = false`
- `order_lifecycle.capital_boundary.production_live_order_submitted = false`
- `no_real_capital.broker_secret_boundary.raw_secret_material_present = false`

This packet is suitable as the broker sandbox/test-key smoke ref for the
runtime-manager canary promotion gate. It is not a canary/live proof packet.
