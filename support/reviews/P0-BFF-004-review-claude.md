# P0-BFF-004 Review — Fix /openapi.json 500

Reviewer: Claude
Date: 2026-05-15
Task Owner: Codex2

## Verdict: Approved

## Review Findings

### Root cause — confirmed

`/api/v1/operator/openclaw/broker-adapter-readiness` and `/api/v1/operator/openclaw/broker/adapter-readiness` are stacked `@app.get` decorators on the same handler function. FastAPI auto-generates `operation_id` from the function name, yielding a duplicate when two routes share one function. Under `PYTHONWARNINGS=error` that `UserWarning` became an exception during schema generation, causing a 500.

### Fix — correct and minimal

`main.py` lines 10219–10226: two stacked `@app.get` decorators now carry distinct explicit `operation_id` values:
- legacy alias → `get_openclaw_broker_adapter_readiness_legacy`
- canonical → `get_openclaw_broker_adapter_readiness`

No functional logic changed. The handler body is unmodified.

### Regression test — solid

`test_execute_plans_final_openapi_json_survives_warning_as_error`:
1. Resets `app.openapi_schema = None` to force full schema regeneration.
2. Activates `warnings.simplefilter("error", UserWarning)` inside `catch_warnings`.
3. Asserts HTTP 200 and both broker route paths present in the schema.

This directly exercises the failure mode. The finally block restores the cached schema, keeping test isolation clean.

### Verification — independently reproduced

All commands from acceptance evidence re-run and pass:
- `PYTHONWARNINGS=error` OpenAPI regression (2 passed)
- Full OpenAPI route set (5 passed)
- OpenClaw readiness smoke (2 passed)
- `py_compile` on `main.py` and the test file — clean

## Notes

No changes required. Task is ready for finalization by Codex2.
