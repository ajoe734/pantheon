# APP-003-QLIB-ACTIVATION-001 Review

Date: 2026-04-24
Reviewer: Codex
Task: `APP-003-QLIB-ACTIVATION-001`
Owner: `Codex2`
Disposition: approved

## Findings

No blocking findings remain after review.

I corrected one implementation-level issue during review before approving:

- `services/research/qlib/adapter/qlib_adapter.py` now calls the real upstream
  `Qlib` LightGBM backend through a minimal dataset wrapper that matches the
  official `LGBModel.fit(dataset)` / `predict(dataset, segment=...)` contract,
  instead of incorrectly treating it like a plain sklearn-style
  `fit(X_train, y_train)` estimator.
- `services/research/qlib/test_adapter.py` now includes a regression test that
  mocks the upstream import surface and verifies the real-backend path builds
  `train` / `valid` segments in the format Qlib expects.
- `integrations/qlib/smoke_test.md` now truthfully records the 2026-04-24
  revalidation and no longer hard-codes a stale checksum value that changes per
  run.
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` now carries the same
  2026-04-24 update date as the refreshed Qlib activation truth.

## Scope Reviewed

- `services/research/qlib/adapter/qlib_adapter.py`
- `services/research/qlib/test_adapter.py`
- `services/research/qlib/smoke_test.py`
- `integrations/qlib/activation_packet.md`
- `integrations/qlib/integration.md`
- `integrations/qlib/smoke_test.md`
- `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `OSS_INTEGRATION_CHECKLIST.md`

## Verification

Executed locally:

```bash
python3 services/research/qlib/smoke_test.py
python3 -m unittest discover -s services/research/qlib -p 'test_*.py'
```

Result:

- smoke test passed with `assertions: OK`
- unit coverage passed with `14` tests
- canonical Qlib docs still agree that the row remains `smoke-tested`, the
  first governed LightGBM activation packet is prepared, and production
  activation is still blocked on the RS-003 candidate, governed dataset proof,
  and target StrategySpec binding
