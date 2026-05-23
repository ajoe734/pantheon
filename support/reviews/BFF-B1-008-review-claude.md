# Review: BFF-B1-008 — POST /bff/actions/{entityType}/{entityId}/{actionId} action facade

Reviewer: Claude
Task: BFF-B1-008
Owner: Codex
Review date: 2026-05-23
Task commit: aa6289c88c22660d922365daad215e1c7fba17d2
PR: #432 (merged into dev, merge commit b4b3903c)

## Verdict: APPROVED

## Scope

§13 acceptance criteria 8, 9, and 10 — the deprecated action compatibility facade for
`POST /bff/actions/{entityType}/{entityId}/{actionId}`.

## Evidence

### Route registration (criterion 8)

`main.py:24444` — route decorated with `@app.post("/bff/actions/{entityType}/{entityId}/{actionId}", deprecated=True, operation_id="submit_bff_action_named")`. Both named (`entityType`/`entityId`/`actionId`) and generic (`type`/`id`/`action`) facades are discoverable in the OpenAPI schema. Tested by `test_bff_actions_openapi_exposes_frontend_and_generic_action_templates` (existing pre-BFF-B1-008 test).

### Idempotency header alias and body rejection (criterion 9)

`main.py:1564` — `_resolve_final_idempotency_key` prefers `Idempotency-Key`; falls back to `X-Idempotency-Key` when the canonical header is absent.

`main.py:1584` — `_reject_body_idempotency_key` checks for `idempotencyKey` and `idempotency_key` keys in the request body and raises HTTP 400 with `precondition_failed="body_idempotency_key"` before any command-store write.

New test `test_bff_actions_named_facade_accepts_x_idempotency_alias` sends only `X-Idempotency-Key` on a named facade request, confirms HTTP 202, checks `meta.idempotency.idempotencyKey`, and verifies the stored foundation record's `idempotency_record.idempotency_key`.

New test `test_bff_actions_named_facade_rejects_body_idempotency_key` sends `idempotencyKey` in the body, confirms HTTP 400 with the correct error code and `precondition_failed`, and asserts the command store is empty.

### Source route in foundation / audit (criterion 10)

`main.py:24388` — `_submit_canonical_action_command` passes `source_route=_ACTIONS_TO_COMMANDS_SOURCE_ROUTE` (`"POST /bff/actions/{entityType}/{entityId}/{actionId}"`) and `route=_FINAL_COMMAND_ROUTE` (`"POST /bff/v1/commands"`) to `_submit_final_command_admission`.

`main.py:14691-14700` — `_build_foundation_command_context` serializes both `admission_route` and `source_route` into the persisted foundation context and audit chain.

Covered by the existing `test_bff_actions_adapter_records_final_command_foundation_context` which asserts `foundation["admission_route"] == "POST /bff/v1/commands"`, `foundation["source_route"] == "POST /bff/actions/{entityType}/{entityId}/{actionId}"`, and matching values in the audit block.

### Deprecation headers/body metadata

`main.py:8123-8128` — `_apply_legacy_action_deprecation_headers` sets `Deprecation`, `Sunset`, `Link`, `Warning`, and `X-Pantheon-Deprecated-Route` headers. Response body includes `deprecated=True` and deprecation notice in `data`, `data.receipt`, and `meta`.

### Commit hygiene

Required trailers present: `LLM-Agent: Codex`, `Task-ID: BFF-B1-008`, `Reviewer: Claude`, `Verified:` with exact commands. Owned-layer, not-changing, and composes-with are stated. Commit diff is narrow: spec doc update + two new test functions. py_compile clean on main.py and test file. Working tree clean (only untracked task brief).

## Spec doc update

The §13 Fix and Acceptance Criteria sections now accurately describe BFF-B1-008 scope (criteria 8–10), the affected files list is updated, and both BFF-B1-007 and BFF-B1-008 are attributed under Task.

## Notes

No issues. The implementation correctly composes with BFF-B1-007 (canonical command admission) and leaves the live broker fail-closed gate and legacy `/api/v1/operator/commands` behavior unchanged.
