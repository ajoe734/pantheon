# MGMT-SAFE-005 No Live Side Effects Assertion

Scope:

- Recursively scans current Track E paper/sandbox/safety evidence for live order,
  live capital, and production-broker side-effect flags.
- Revalidates discovered non-live OODA packets with the OODA packet validator.
- Exercises the OODA guard with a synthetic paper packet that forces
  `act.live_capital_side_effects=true` and expects rejection.
- Consumes optional safety-smoke summaries when present, without requiring live
  broker credentials or opening any broker session.

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_no_live_side_effects_assertion.py --json-out support/evidence/MGMT-SAFE-005/no-live-side-effects-assertion.json
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_no_live_side_effects_assertion.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_no_live_side_effects_assertion.py scripts/test_run_no_live_side_effects_assertion.py
```
