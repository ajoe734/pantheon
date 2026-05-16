# CAP-002-RB Review

Task: `CAP-002-RB`
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Verdict

**Approved.**

## What was reviewed

- `services/deployment/models.py` — `PoolCompatibilityRequest` and `PoolCompatibilityResponse` models
- `services/deployment/service.py` — `PoolRuntimeCompatibilityService` and `POST /api/deployment/plans/compatibility-check`
- `services/deployment/test_service.py` — 3 new CAP-002-RB tests + 18 pre-existing tests
- `services/deployment/contract.md` — CAP-002-RB section added
- `support/evidence/CAP-002-RB/verification.md`

## Review findings

### Read-only invariant

`PoolRuntimeCompatibilityService.check()` reads from `capital_pools.json`,
`persona_capital_bindings.json`, and `runtime_bindings.json` through
`_load_records` / `_load_record` helpers and returns a response without writing
any of those stores. Correct.

### Response model coverage

All contract-required fields are present in `PoolCompatibilityResponse`:
`pool_found`, `pool_status`, `pool_active`, `single_runtime_enforced`,
`persona_binding_found`, `persona_scope_ok`, `persona_binding_id`,
`allowed_deployment_scope`, `active_runtime_binding_count`,
`active_runtime_binding_ids`, `single_runtime_ok`, `errors[]`, `warnings[]`.

### Single-runtime semantics

Exactly 1 active RuntimeBinding → warning only (correct; dispatch must use
replace/freeze/resume/rollback path). More than 1 → error. Zero → ok with no
warning. Matches the contract.

### Persona scope selection

When multiple bindings are present, the service picks the one with the highest
`allowed_deployment_scope` value via `_SCOPE_ORDER`. `persona_scope_ok = True`
only when at least one binding `permits_deployment_to(target_stage)` via the
canonical `PersonaCapitalBinding.from_dict().permits_deployment_to()` call.

### Missing sponsor_persona_id

Returns `ok = False` with a clear error; `target_stage` outside
paper/canary/live also returns `ok = False`. Both cases tested.

### Test coverage

3 targeted CAP-002-RB tests cover:
- happy path: active pool + binding with matching canary scope → ok
- rejected: suspended pool + binding with paper-only scope targeting canary → ok=false, two errors
- violation: 2 active RuntimeBindings → ok=false, single-runtime error

Pre-existing 18 tests are unaffected. All 21 pass.

### Verification commands run

```
python3 -m py_compile services/deployment/models.py services/deployment/service.py services/deployment/test_service.py
python3 -m pytest -q services/deployment/test_service.py
# 21 passed
```

## Cross-task note

No pre-existing failures introduced; worktree may contain DEP-002-RB in-progress
changes but those are unrelated and do not affect these test results.
