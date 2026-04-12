# Review: APP-002-W1-COMMAND-DEPLOYMENT

**Reviewer:** Codex
**Date:** 2026-04-11
**Status:** ❌ CHANGES REQUESTED

## Summary

The new `command_executor` path replaces the stub worker and adds execution/error handling, but several issues prevent the Wave 1 command path from being authoritative. The biggest blockers are missing internal API authentication and audit persistence gaps. See Findings below.

## Findings (Blockers)

1. **Internal API calls always 401 (no auth headers propagated)**
   - `services/control-plane/bff/command_executor.py` `_post_json()` does not send `Authorization` or MFA headers.
   - `services/control_plane/internal_api.py` requires Bearer auth on all endpoints.
   - Result: every real execution fails with HTTP 401 → BFF returns `FAILED`, so Promotion Review commands are not authoritative.
   - **Fix:** add a service-to-service token (env) or propagate operator JWT/MFA into `command_executor` headers. If using a service token, add `Authorization: Bearer <token>` and optionally `X-MFA-Token` when required.

2. **Audit timeline updates are not persisted**
   - `_process_command()` mutates `audit` (`execution_completed_at`, `executor`, `failure_reason`, etc.) but `CommandStore.update_status()` only writes `status`, `result`, and `error`.
   - Result: command polling never shows the execution timeline or failure context → violates `audit_and_failure_paths_truthful`.
   - **Fix:** extend `update_status()` to accept and persist `audit`, or add a dedicated `update_audit()` call and use it after enrichment.

## Findings (Non-blocking / Follow-up)

3. **PauseRuntime param mismatch**
   - Validator requires `runtime_binding_id`, but executor reads `binding_id` → requests `/runtimes/None/pause` when only the validated param is supplied.
   - W1 scope is deployment commands, but this is a correctness issue for future waves. Align param names now to avoid regression.

4. **Authoritative status alignment (risk)**
   - Executor marks `EXECUTED` immediately on 202 responses and does not reconcile with internal API state. Internal API stores its own `command_id` that is not tied to the BFF command id.
   - If Wave 1’s acceptance expects authoritative command status, consider returning downstream receipt + mark `PROCESSING` until confirmed, or pass BFF `command_id` to internal API for storage.

## Tests

- `python3 -m unittest services/control-plane/bff/test_command_executor.py` **failed** locally due to missing `pydantic` module.

## Recommendation

Address blockers 1–2, then re-request review. The changes are scoped and should not affect L1 canonical truth.
