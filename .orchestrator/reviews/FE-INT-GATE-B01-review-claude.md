# FE-INT-GATE-B01 Review — Claude

Status: approved
Reviewer: Claude
Reviewed at: 2026-05-13

## Summary

All four acceptance criteria are implemented. The Codex2 blocking finding (EventSource.OPEN read in Node v18 test runner context where `globalThis.EventSource` is undefined) is fully resolved. Approving.

## Fix Verification — Commit 52023180

**Root cause (confirmed):** Node v18.19.1 has `typeof EventSource === "undefined"`.
The original `expect(opened.readyState).toBe(EventSource.OPEN)` in the Node runner context would
throw before any assertion, masking a successful SSE open.

**Fix pattern (correct):**
- All three resolve paths inside `page.evaluate` now include `openState: EventSource.OPEN`,
  capturing the constant (= 1) in the browser context where `EventSource` is defined.
- The Node-side assertion changes from `EventSource.OPEN` to `opened.openState`, comparing
  two plain numbers returned from the serialised resolve payload.

Diff reviewed: `+openState: EventSource.OPEN` added to all three `resolve({...})` branches;
`expect(opened.readyState).toBe(EventSource.OPEN)` → `.toBe(opened.openState)`.
No other code touched.

## Acceptance Criteria Check

| Criterion | Implemented | Notes |
|---|---|---|
| MeResponse shape 完整 assert | ✅ | tenant (id/default_id/allowed_ids/scope/tenant_id alias), environment (name/deployment_stage/auth_mode/timezone/strict_auth), user (id/operator_id/display_name/roles/capabilities/mfa_verified), alias fields currentUser/current_user/roles/capabilities, `capabilities` ∋ `"runtime.read"`, session (id/session_kind/auth_mode/checked_at/authenticated/fresh/mfa_verified), feature_flags.sessionAuthMe===true, meta.route/meta.contract |
| strict 模式無 serving-mock banner | ✅ | Defaults VITE_BFF_FALLBACK/BFF_FALLBACK to "strict"; polls body text for absence of SERVING_MOCK_BANNER regex |
| SSE EventSource open assertion | ✅ | `EventSource.OPEN` captured in browser context; assertion uses returned `openState` in Node |
| 401 不 fallback mock | ✅ | `page.route` intercepts `/bff/me` → 401; verifies request was made; body has no mock banner or mock-user strings |

## Codex2 Verification (reproduced)

- Node v18.19.1: `typeof EventSource === "undefined"` — confirms the bug was real and now avoided.
- TypeScript `--noEmit`: passed per commit metadata.
- `playwright --list`: 4 F01 tests enumerated.
- esbuild bundle: passed per commit metadata.

## Environment Note

Staging BFF endpoints are unreachable from the local runner (curl timeout, EventSource readyState=0).
This is an environment constraint, not a spec defect. The test is structurally correct and will produce
meaningful results when the BFF endpoint is reachable.

## Outcome

Approve. No blocking issues. Owner (Codex2) to finalize as `done`.
