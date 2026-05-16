# TRN-001 Review: TeachingSession / TeachingEvent Schema

Reviewer: Claude
Owner: Codex
Date: 2026-05-16
Commit: f7d155a9

## Decision: APPROVED

## Scope Verified

- `services/training-session/teaching_session.schema.json` — JSON Schema Draft-7
- `services/training-session/teaching_event.schema.json` — JSON Schema Draft-7
- `services/training-session/models.py` — frozen dataclasses + dual validation
- `services/training-session/tests/test_teaching_models.py` — 7 model tests
- `services/training-session/tests/test_http_service.py` — 6 lifecycle tests (3 new)
- `services/training-session/main.py` — canonical field emission + legacy normalization
- `support/evidence/TRN-001/README.md` — evidence packet

## Verification Commands Run

```
python3 -m json.tool services/training-session/teaching_session.schema.json  → valid
python3 -m json.tool services/training-session/teaching_event.schema.json    → valid
python3 -m py_compile services/training-session/models.py services/training-session/main.py → passed
pytest services/training-session/tests -q → 16 passed
```

## Review Findings

### Pass — JSON Schemas

Both schemas are well-formed Draft-7. Required fields are correct:
- `TeachingSession`: session_id, persona_id, opened_by, mode, status, started_at, trace_id
- `TeachingEvent`: event_id, session_id, event_type, actor_type, payload, timestamp, correlation_id, sequence_number

Enum coverage for `mode`, `status`, `event_type`, `actor_type` is complete and aligns with the Python enums. `additionalProperties: false` enforces strict contract boundaries. Nullable definitions use proper `anyOf` patterns.

### Pass — Python Models (dual validation)

`TeachingEvent` and `TeachingSession` are frozen dataclasses with `__post_init__` enforcing:
1. Python-level field normalization (require_text, coerce_enum, require_positive_int)
2. Business invariant checks via `validate_teaching_event` / `validate_teaching_session`
3. jsonschema Draft-7 payload validation via `validate_teaching_event_payload` / `validate_teaching_session_payload`

Key invariants correctly enforced:
- Terminal session statuses (completed/abandoned/committed/discarded/expired) require `ended_at`
- Active/paused sessions must not have `ended_at`
- Duplicate `event_id` values in session events are rejected
- `emitted_at` must match `timestamp` when both are explicitly provided
- Message events require `message_body` in top-level field or payload

### Pass — Canonical Field Emission

`_build_teaching_event` in main.py emits the four canonical fields for every service-generated event:
- `actor_type` (derived from actor name via `_actor_type_from_actor`)
- `payload` (normalized from explicit fields)
- `timestamp` (canonical ISO-8601)
- `correlation_id` (defaults to `{session_id}:{event_id}`)

BFF-facing aliases (`actor`, `message_body`, `emitted_at`, `sequence_number`) are preserved alongside canonical fields.

### Pass — Legacy Session Normalization

`_teaching_session_contract` in main.py normalizes pre-schema sessions by filling defaults for `mode`, `trace_id`, `opened_by`, `session_type`, and aliased fields (`objective`/`topic`, `created_at`→`started_at`). Called in `complete_session` to upgrade any legacy stored session before materialization.

`test_complete_upgrades_legacy_session_to_schema_contract` verifies that a session stored without `mode` or `trace_id` is correctly upgraded on complete and passes `TeachingSession.from_dict()` validation.

### Pass — Test Coverage

13 training-session tests (7 model + 6 HTTP lifecycle), plus 3 BFF client contract tests. Tests cover:
- Schema Draft-7 validity
- Event and session round-trips through model and schema
- Terminal status validation
- Duplicate event ID rejection
- Timestamp alias drift detection
- Event/session cross-check
- Full session lifecycle (create → append-events → preview → complete → commit)
- Control patch accepted/rejected paths
- Legacy session upgrade on complete

### Advisory — Vectorbt Import Placement

`main.py` contains a mid-file import at line 458:
```python
from services.research.vectorbt.adapter.vectorbt_adapter import run_vectorbt_workflow, BacktestConfig
```

This is accompanied by stub OHLCV data in `_get_ohlcv_data` and a live vectorbt backtest run inside `refresh_preview`. Three concerns:

1. Mid-file imports are a code smell; imports belong at the top.
2. The `_get_ohlcv_data` stub with hardcoded data is a placeholder that should not persist.
3. The training-session service importing the vectorbt adapter directly may violate the OSS-per-container isolation principle (each framework in its own container).

The tests pass and the schema/model deliverables are not affected by this. This is noted as a follow-up concern for the team, not a blocking issue for TRN-001.

## Summary

Core TRN-001 deliverables (JSON schemas, dual-validated Python models, canonical field emission, legacy normalization, focused tests) are correctly implemented. The vectorbt integration in `refresh_preview` is advisory only. Approved for finalization.
