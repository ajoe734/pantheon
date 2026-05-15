# Pytest Harness

The root pytest harness is intended to make full-suite collection deterministic
across Pantheon's service-style Python layout.

## Canonical commands

```bash
python3 -m pytest -q --collect-only
python3 -m pytest -q
```

For the broader command order across pytest, direct smoke entrypoints, compose,
and gated production-posture checks, see
[`docs/testing/full-suite-runbook.md`](full-suite-runbook.md).

## Collection contract

- Root collection uses `--import-mode=importlib` so duplicate test basenames in
  different services do not collide.
- The default suite collects `test_*.py` files under `scripts/` and `services/`.
- Direct smoke files such as `smoke_test.py`, `http_smoke_test.py`, and
  `ray_tune_smoke_test.py` are not part of the default suite. Run them as their
  documented Python entrypoints when a smoke run is intended.
- Before each test module is imported, the root harness activates that module's
  service-local import roots and clears transient top-level modules loaded from
  `services/` or `scripts/`, preventing names such as `main`, `adapter`,
  `models`, and `feedback` from leaking across service boundaries.

## Verification notes

2026-04-30 validation:

- `python3 -m pytest -q --collect-only` collected 2214 tests without import
  mismatch or module shadowing failures.
- `python3 -m pytest -q` reached normal test execution and reported 3 runtime
  failures in `services/capital/test_service.py`, with 2210 passed and 1
  skipped; the failures are route/domain exception handling issues, not pytest
  collection pollution.
- `python3 -m pytest -q --collect-only services/runtime-manager/smoke_test.py`
  and `python3 -m pytest -q --collect-only services/capital/smoke_test.py`
  reported no tests collected, confirming direct smoke scripts are outside the
  default pytest collection contract.
