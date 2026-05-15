# MGMT-BROKER-001 Evidence Note

Task: Shioaji sandbox adapter facade
Owner: Codex
Reviewer: Gemini2

## Scope

This task adds a Management-facing facade over the existing Shioaji sandbox
adapter. The facade composes:

- connect
- redacted account readiness
- place test order
- cancel test order
- readback
- reconcile
- live-disabled summary

The output keeps the SD 6.4 management shape explicit:

- `broker`: `shioaji`
- `environment`: `sandbox`
- `account_status`: `ready | missing | unsigned`
- `production_live_enabled`: `false`
- `capital_binding_enabled`: `false`
- `human_gate_required`: `true`

This task does not produce the formal broker smoke evidence packet and does not
approve canary/live activation. Those remain in MGMT-BROKER-003,
MGMT-BROKER-004, and MGMT-BROKER-006.

## Changed Files

- `services/broker/shioaji/adapter.py`
- `services/broker/shioaji/facade.py`
- `services/broker/shioaji/__init__.py`
- `services/broker/shioaji/test_facade.py`
- `services/broker/shioaji/README.md`

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.broker.shioaji.test_facade -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.broker.shioaji.test_adapter -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/broker/shioaji/adapter.py services/broker/shioaji/facade.py services/broker/shioaji/test_facade.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/broker/shioaji -q
```

Observed result:

- `test_facade`: 7 tests passed
- `test_adapter`: 40 tests passed
- `py_compile`: passed
- `pytest services/broker/shioaji -q`: 54 tests passed
