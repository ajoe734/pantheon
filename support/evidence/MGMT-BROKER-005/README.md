# MGMT-BROKER-005 Shioaji Fail-Closed Test Evidence

Scope:

- Shioaji sandbox gate remains default-closed.
- Closed gate rejects submit/cancel/readback before SDK/API side effects.
- Live order rejection remains unconditional, including when the sandbox gate is open.
- Closed-gate smoke bundles still write `live-disabled.json` and `no-real-capital-evidence.json` without place/cancel responses.

Changed tests:

- `services/broker/shioaji/test_adapter.py`
- `services/broker/shioaji/test_sandbox_smoke.py`

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.broker.shioaji.test_adapter -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.broker.shioaji.test_sandbox_smoke -q
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_run_ep5_canary_readiness.py -q
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_run_broker_sandbox_order_smoke.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/broker/shioaji/test_adapter.py services/broker/shioaji/test_sandbox_smoke.py services/broker/shioaji/adapter.py services/broker/shioaji/sandbox_smoke.py scripts/run_ep5_canary_readiness.py scripts/run_broker_sandbox_order_smoke.py
```

Result: all focused verification commands passed.
