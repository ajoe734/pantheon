# ASK-002 Review — ConsultRequest / ConsultMemo Schema

Reviewer: Claude
Task: ASK-002
Owner: Codex
Commit: b23fb2e3
Date: 2026-05-16

## Decision: APPROVED

## Scope Verified

- Draft-07 JSON schemas added: `consult_request.schema.json`, `consult_memo.schema.json`
- Schema load/validate helpers added to `services/consultation/models.py`
- Cross-object memo-to-request lineage validation function
- Pydantic model field constraints tightened (non-empty identities, confidence bounds)
- Advisory-only boundary enforced at schema level

## Review Findings

### Schema Correctness
- Both schemas correctly declare `"$schema": "http://json-schema.org/draft-07/schema#"` and are valid Draft-07 documents (verified by `Draft7Validator.check_schema`).
- `additionalProperties: false` on both root objects correctly rejects unknown fields including `deployment_command` and `broker_order`. Tests confirm these rejections.
- The `consult_memo.schema.json` uses an `allOf` conditional to enforce `published_at` is a non-null datetime string when `status == "published"`. This invariant is tested and passes.
- `nullableDateTime` and `nullableString` definitions are cleanly factored and reused.

### Model Constraints
- `ConsultRequest` and `ConsultMemo` Pydantic models correctly reflect the schema constraints with `Field(min_length=1)` on identity fields.
- `ConsultMemo.confidence: float = Field(ge=0.0, le=1.0)` correctly bounds confidence.
- All enums (`ConsultRequestType`, `MemoStatus`, `Recommendation`, `FindingSeverity`, etc.) are properly defined as `str, Enum` subclasses.

### Cross-Object Lineage Validation
- `validate_consult_memo_against_request` checks `request_id`, `target_type`, `target_id` linkage and `published` memo `published_at` requirement.
- Test verifies both the happy path and the `target_id` mismatch case.

### Advisory-Only Boundary
- Schema rejects `deployment_command` and `broker_order` via `additionalProperties: false`.
- Two explicit tests confirm this rejection for both ConsultRequest and ConsultMemo payloads.
- No `ConsultRequest` or `ConsultMemo` model fields carry deployment authority, rollback commands, or broker order fields.

### Test Coverage
- `test_models.py`: 8 tests — schema validity, round-trip, advisory rejection, identity constraint, memo published_at, cross-validation linkage.
- `smoke_test.py` + `test_postgres_store.py`: 8 tests — store integration.
- `test_compose_activation.py`: 1 test — compose activation.
- `test_cw01_consult_request_contract.py` + `test_read_store_service_clients.py`: 10 tests — BFF contract.
- Total: 27 tests, all passing.

### Evidence
- `support/evidence/ASK-002/README.md` present and accurate.

## No Required Changes

The implementation is complete and correct. Returning to owner Codex for closeout.
