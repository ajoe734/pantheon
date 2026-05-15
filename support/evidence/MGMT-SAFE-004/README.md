# MGMT-SAFE-004 Canary Human Gate Smoke

Scope:

- Canary human-gate packet readiness requires explicit risk-owner approval, operator approval, persona-capital binding, canary scale refs, and a Shioaji broker sandbox smoke ref.
- Missing operator approval keeps the packet `incomplete`.
- Missing Shioaji broker sandbox smoke keeps the packet `incomplete`.
- A live target plan is rejected by the canary human-gate smoke.
- Broker smoke that does not prove the live boundary is fail-closed keeps the packet `incomplete`.
- The smoke uses fixture artifacts only and asserts no production live order or real capital side effects.

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_canary_human_gate_smoke.py --json-out support/evidence/MGMT-SAFE-004/canary-human-gate-smoke.json
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_ep5_canary_readiness.py scripts/run_canary_human_gate_smoke.py scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py
```

Closeout verification on 2026-05-15:

- Smoke evidence refreshed: 5/5 passed.
- Focused pytest: 10 passed.
- py_compile: passed for readiness, smoke, and focused tests.
