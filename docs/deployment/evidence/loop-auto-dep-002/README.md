# LOOP-AUTO-DEP-002 Evidence

Task: `LOOP-AUTO-DEP-002`
Owner: `Claude2`
Reviewer: `Codex`
Wave: Wave 3 Deployment Saga

## Scope

Add idempotent plan-to-binding adapter so that the deployment saga can safely
call runtime-manager to create or verify a `RuntimeBinding`.

## Delivered Artifacts

- `services/deployment/runtime_manager_dispatch_adapter.py` — idempotent
  dispatch adapter (DispatchResult, dispatch_to_runtime_manager)
- `services/deployment/test_runtime_manager_dispatch_adapter.py` — 34 unit tests
- `docs/deployment/evidence/loop-auto-dep-002/README.md` — this file

## Acceptance Criteria Coverage

| Criterion | How satisfied |
|---|---|
| Approved immutable plan dispatch creates or verifies one binding | `dispatch_to_runtime_manager()` builds a deploy request from saga + context and calls `RuntimeManagerClient.deploy()`. On success, returns `DispatchResult(outcome="success", binding_id=..., binding=...)`. Verified by `TestNewDispatch` (3 tests). |
| Duplicate dispatch does not duplicate bindings | When `saga["binding_id"]` is set, adapter calls `client.get()` instead of `client.deploy()`. Returns `idempotent_replay=True` on success. Verified by `TestIdempotentReplay` (5 tests). |
| Runtime-manager failures return retryable or terminal saga state | HTTP 5xx/429 and `RUNTIME_MANAGER_UNAVAILABLE` → `RETRYABLE_ERROR`. HTTP 4xx and pre-condition keyword errors → `TERMINAL_ERROR`. Verified by `TestErrorClassification` (13 tests). |

## Validation

Run on 2026-06-27:

```bash
python3 -m pytest services/deployment/test_runtime_manager_dispatch_adapter.py -v
```

Result:

```
34 passed in 10.02s
```

Full suite (no regressions):

```bash
python3 -m pytest services/deployment/ -v
```

Result:

```
90 passed in 35.72s
```

Test classes:
- `TestNewDispatch` — deploy() called once for saga with no binding_id; request
  fields traced from saga + context; get() not called
- `TestIdempotentReplay` — existing binding verified via get(); deploy() not
  called; binding-not-found inconsistency classified as terminal
- `TestErrorClassification` — HTTP 5xx/429 retryable; 4xx terminal; unavailable
  error code retryable; pre-condition keyword errors terminal; generic exceptions
  retryable
- `TestBuildDeployRequest` — required/optional field mapping verified
- `TestDispatchResult` — helper predicates (succeeded, is_retryable, is_terminal)

## Design Notes

The adapter is deliberately narrow:

- It does **not** write saga state.  Callers (the DEP-001 outbox consumer or a
  future step in the saga worker) must call `record_binding_created` or
  `record_failure` on the `DeploymentOrchestrationService` based on the returned
  `DispatchResult.outcome`.
- The `deploy_context` dict separates caller-supplied approval context
  (persona binding id, loader checks, deployment scope) from the saga's own
  identity fields.  This keeps the adapter composable without importing the
  full saga domain.
- Error classification is keyword-based for local `RuntimeManagerError`
  (raised on the non-HTTP code path) and HTTP-status-based for
  `RuntimeManagerClientError` from the HTTP path.

## Maturity Claim

This task advances `promotion_deployment` and `capital_pool_execution` loops
from `api-only` toward `reconciled`: the deployment saga now has an idempotent,
observable integration point into the runtime-manager binding surface.
Full reconciled status requires the outbox consumer (DEP-001) to be wired to
call this adapter for each consumed event, which is addressed in subsequent
tasks.
