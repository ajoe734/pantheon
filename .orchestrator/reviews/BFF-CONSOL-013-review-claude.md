# BFF-CONSOL-013 Review — Claude

Disposition: **Approved**

Reviewer: Claude
Reviewed at: 2026-05-13

## Summary

All 6 acceptance criteria are satisfied in the real execute-plans repository. The previous review finding (bearer-storage-only gate) has been fully resolved.

## Acceptance Criteria Verification

1. **`/bff/me` response 含 session_kind 欄位** ✅
   - `_bff_me_session_payload()` (main.py:3123) includes `"session_kind": _resolve_session_kind(identity)`.
   - `_resolve_session_kind()` (main.py:310) returns "cookie", "bearer", or "stub" based on `identity.token_kind`.
   - Backend test `test_bff_consol_013_cookie_session_write_gate.py` line 69, 82, 97 verify all three variants.

2. **`liveWriteGated()` 不再只看 sessionStorage** ✅
   - `writeGate.ts` (execute-plans) `liveWriteGated()` now calls `bffFetch({ method: "GET", path: paths.me(), mode: "live" })`.
   - `bffFetch` in `client.ts` line 115 sets `credentials: "include"` for all live-mode requests, so cookies are sent.
   - No sessionStorage read in the gate path.

3. **cookie-only session 寫入 gate 通過** ✅
   - `sessionKindAllowsWrite("cookie", ...)` returns `true` regardless of production/strict context.
   - Frontend test `writes.test.ts` line 210–219 and `runAction.test.ts` line 54–68 confirm the cookie path, including `credentials: "include"` assertion.

4. **Bearer session 寫入 gate 通過** ✅
   - `sessionKindAllowsWrite("bearer", ...)` returns `true`.
   - Tests in both test files confirm bearer path.

5. **stub session 在 production 模式被擋** ✅
   - `sessionKindAllowsWrite("stub", { production: true })` returns `false`.
   - `sessionKindAllowsWrite("stub", { strict: true })` returns `false`.
   - Tests confirm both blocking conditions.

6. **unit test cover 三種 session 模式** ✅
   - Backend: `TestSessionKindStub`, `TestSessionKindBearer`, `TestSessionKindCookie`.
   - Frontend (`runAction.test.ts`): cookie (line 54), bearer (line 70), stub blocked in strict mode (line 76).
   - Frontend (`writes.test.ts`): `VI-2 session-kind write gate` describe block covers all three.

## Verified Files

- `execute-plans/src/lib/bff-v1/writeGate.ts` — `liveWriteGated()` and `sessionKindAllowsWrite()` implemented correctly.
- `execute-plans/src/lib/bff-v1/writes.ts` — imports and re-exports from `writeGate`.
- `execute-plans/src/lib/bff/runAction.ts` — imports `liveWriteGated` and `sessionKindAllowsWrite` from `bff-v1/writeGate`.
- `execute-plans/src/lib/bff-v1/client.ts` line 115 — `credentials: "include"` confirmed for live requests.
- `services/control-plane/bff/main.py` — `pantheon_session` cookie param on `/bff/me`, `_extract_identity` cookie fallback, `_resolve_session_kind` helper, `session_kind` in payload.
- `services/control-plane/bff/test_bff_consol_013_cookie_session_write_gate.py` — 13 focused tests.

## Notes

- `bffFetch` always passes `credentials: "include"` in live mode (not only for `/bff/me`), which is correct since all BFF endpoints need cookies.
- The gate falls back to `false` on any fetch error, which is the right safe default.
- `readSessionSummary()` in `writeGate.ts` handles both `session_kind` (snake_case from backend) and `sessionKind` (camelCase) for forward-compat.
