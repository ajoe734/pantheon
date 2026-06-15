# Review: BFF-MGMTAI-BOOTSTRAP-RESILIENCE

Reviewer: Claude2
Date: 2026-06-15
PR: #1694 (merged)

## Summary

Reviewed `PostgresAssistantConversationStore.bootstrap()` hardening in
`services/control-plane/bff/assistant_conversation_store.py` and the
accompanying unit test file
`services/control-plane/bff/tests/test_assistant_conversation_store_bootstrap_resilience.py`.

## Verification

```
python3 -m pytest services/control-plane/bff/tests/test_assistant_conversation_store_bootstrap_resilience.py -v
7 passed in 1.32s
```

## Findings

### Correctness: PASS

**CREATE TABLE handler (lines 576–613):**
- Catches `Exception` with `getattr(exc, "sqlstate", "") == "42501"` only.
  Non-42501 exceptions propagate immediately. ✓
- Calls `conn.rollback()` before the information_schema probe to clear the
  failed transaction state — required before issuing another statement on
  the same psycopg connection. ✓
- Re-raises if the table does not exist (BFF cannot insert turns without the
  table; degraded-start is not safe here). ✓
- Logs a warning and continues if the table already exists. ✓

**CREATE INDEX handler (lines 615–633):**
- Catches `Exception` with `sqlstate == "42501"` only. ✓
- Calls `conn.rollback()` after the failure, then logs a warning and
  continues. BFF can operate without the index at reduced query performance. ✓
- Non-42501 errors propagate. ✓

**Pattern alignment:**
- `except Exception as exc` + `getattr(exc, "sqlstate", "")` is the correct
  psycopg3 idiom when the caller cannot import psycopg at module level (it is
  an optional dependency). This mirrors the pattern in `ensure_postgres_schema()`. ✓

### Test coverage: PASS

7 tests covering:
1. Happy path — both DDL succeed, no rollback.
2. CREATE INDEX 42501 → warning logged, bootstrap completes.
3. CREATE TABLE 42501, table exists → warning logged, CREATE INDEX still runs, bootstrap completes.
4. CREATE TABLE 42501, table missing → re-raises (critical: BFF must not start without turns table).
5. Non-privilege error on CREATE INDEX → re-raises.
6. Non-privilege error on CREATE TABLE → re-raises.
7. Both DDL fail 42501, table exists → two warnings logged, bootstrap completes.

`_BootstrapConn` fake correctly simulates the psycopg `sqlstate` duck-type contract.
`_BootstrapConnCustom` cleanly extends it for per-statement injection.

### Scope compliance: PASS

Commit message correctly declares:
- Owned layer: bootstrap() + 7 unit tests.
- Not changing: management_ai_store.py, postgres_json_store.py, BFF routes/auth.
- Composes with: permanent MANAGEMENT_AI_DATABASE_URL provisioning (deferred, out of scope). ✓

### Acceptance criteria: ALL MET

1. bootstrap() degrades on privilege/DDL errors → ✓
2. Necessary indexes already existing treated as success path → ✓ (CREATE INDEX 42501 is non-fatal)
3. Unit tests simulating InsufficientPrivilege verify BFF still starts → ✓ (7 tests pass)

## Decision

APPROVED — no required changes.
