# MGMT-BROKER-005 Review

Reviewer: Codex
Owner: Codex2
Task: Shioaji fail-closed tests
Reviewed commit: ddcb0d76
Date: 2026-05-15

## Result

Approved.

The added regressions cover the requested fail-closed surfaces:

- closed sandbox gate rejects submit/cancel/readback before touching the injected Shioaji API double
- live rejection remains unconditional and does not touch SDK methods or in-memory order/trade books
- closed-gate smoke payloads and bundles keep place/cancel responses empty while still emitting live-disabled and no-real-capital evidence

## Verification

Commands run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.broker.shioaji.test_adapter -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.broker.shioaji.test_sandbox_smoke -q
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_run_ep5_canary_readiness.py -q
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_run_broker_sandbox_order_smoke.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/broker/shioaji/test_adapter.py services/broker/shioaji/test_sandbox_smoke.py services/broker/shioaji/adapter.py services/broker/shioaji/sandbox_smoke.py scripts/run_ep5_canary_readiness.py scripts/run_broker_sandbox_order_smoke.py
```

Results:

- Shioaji adapter tests: 40 passed.
- Shioaji sandbox smoke tests: 7 passed.
- EP5 canary readiness tests: 6 passed.
- Broker sandbox order smoke tests: 8 passed.
- Python compile check passed.

## Notes

The repository had unrelated dirty/generated state files and unrelated task artifacts before this review. This review only evaluates the scoped MGMT-BROKER-005 files changed by commit ddcb0d76.
