# FE-INT-GATE-B04 — Sidecar Review Packet

**Packet type:** review_packet (sidecar support artifact)
**Sidecar task:** FE-INT-GATE-B04-SIDECAR-REVIEW
**Parent task:** FE-INT-GATE-B04
**Prepared by:** Claude (sidecar worker)
**Reviewer:** Codex2
**Date:** 2026-05-13
**Parent task status at packet creation:** review_approved

---

## 1. Parent Task Summary

| Field | Value |
|---|---|
| Task ID | FE-INT-GATE-B04 |
| Title | F05 deepen — Confirm token precondition envelope |
| Owner | Codex2 |
| Reviewer | Claude |
| Phase | Pantheon FE Integration Gate 2026-05-13 |
| Final status | review_approved |

**Scope (summary_zh):** F05 Sentinel 升級：用 `page.route` 攔截 emergency action POST 並回傳 `{error:{code:CONFIRM_TOKEN_REQUIRED}}` non-2xx envelope；驗證 UI 不顯示 `requires_confirm_token` 為 success；advisory action 可執行或 queue。

---

## 2. Artifact Under Review

**Primary artifact:** `execute-plans/e2e/04-sentinel-remediation.spec.ts`

This file contains a Playwright E2E test suite (`F05 Sentinel remediation`) covering two tests:

1. `treats CONFIRM_TOKEN_REQUIRED as a non-success emergency precondition`
2. `allows an advisory Sentinel remediation action to be queued`

---

## 3. Acceptance Criteria Assessment

| Criterion | Status | Evidence |
|---|---|---|
| Emergency action 缺 token 回 CONFIRM_TOKEN_REQUIRED non-2xx | **PASS** | `installB04Routes` injects HTTP 428 with `error.code: CONFIRM_TOKEN_REQUIRED`; test asserts `response.status() >= 400` and `payload.error.code === "CONFIRM_TOKEN_REQUIRED"` |
| UI 不顯示 requires_confirm_token 為 success | **PASS** | Three-layered assertion: `CONFIRM_TOKEN_SUCCESS_TEXT` regex, standalone `/requires_confirm_token/i`, and `/Remediation executed\|處置已執行/i` — all must be absent |
| Advisory action 可執行或 queue | **PASS** | Advisory POST returns 202 `queued`; page must match `/Remediation executed\|Open incident\|處置已執行/i` |

**Overall verdict: APPROVED**

---

## 4. Technical Evidence Detail

### 4.1 Route Injection Quality

`installB04Routes(page)` sets up a catch-all `page.route("**/*", ...)` handler with correct dispatch precedence:

1. OPTIONS → 204 (CORS preflight)
2. Emergency POST (`isEmergencyRemediationPost`) → **428** `{error:{code:"CONFIRM_TOKEN_REQUIRED", ...}}`
3. Advisory POST (`isAdvisoryActionPost`) → **202** `{status:"queued", data:{...}}`
4. `/bff/me` GET → authenticated session stub
5. `/health`, `/healthz`, `/bff/health` GET → `{status:"ok"}`
6. `/bff/v5/sentinel/findings` GET → B04 fixture with `sentinelFinding`
7. Other `/bff/*` GETs → neutral empty stub
8. Everything else → `route.continue()`

### 4.2 Action Dispatch Logic

`isRemediationPost` dispatches by:
- Direct body-field match: `remediation_action`, `remediationAction`, `action`, `actionId`, or `command`
- Path-pattern fallback: `path.includes(actionKind) && /\/(?:remediate|execute)\/?$/.test(path)`
- Regex pattern: `^/bff/v5/interventions/ra_${actionKind}_[^/]+/remediate/?$`

This multi-strategy approach makes route matching robust against different UI call shapes.

### 4.3 Missing-Token Precondition Verification

The spec explicitly asserts:
```typescript
expect(
  body?.confirmToken ?? body?.confirm_token ?? body?.x_confirm_token,
  "emergency test must cover the missing-token precondition",
).toBeUndefined();
```
This confirms the test exercises the no-token scenario as intended.

### 4.4 UI Success-Leak Prevention (Three Layers)

```typescript
// Layer 1: composite success text regex
expect(text).not.toMatch(CONFIRM_TOKEN_SUCCESS_TEXT);
// Layer 2: raw field leak
expect(text).not.toMatch(/requires_confirm_token/i);
// Layer 3: success toast
expect(text).not.toMatch(/Remediation executed|處置已執行/i);
```

### 4.5 liveStatus Remains Healthy

`/health`, `/healthz`, `/bff/health` → `{status:"ok"}` and `/bff/me` → authenticated session, preventing the UI from entering degraded/offline state during tests.

### 4.6 Async Correctness

Both tests set up `waitForResponse` promises **before** triggering the POST action, then `await` them — correct sequencing with no race condition.

### 4.7 Execution Evidence (as reported by Codex2)

- esbuild bundle passed
- `playwright --list` shows 2 tests
- advisory grep: 1/1; emergency grep: 1/1
- Full Playwright suite: **2/2 passed** against live-write Vite on `127.0.0.1:5176` with `VITE_BFF_MODE=live VITE_BFF_REAL_WRITES=true`

---

## 5. Minor Observations

| Item | Severity | Assessment |
|---|---|---|
| `nowIso()` returns hardcoded `"2026-05-13T13:40:00Z"` | Info | Acceptable — deterministic timestamp for route stubs; matches sprint date |
| `emergencyResponse` promise is created after `submitEmergencyConfirm` call site begins (see spec lines 403-411) | Info | `waitForResponse` is called before `submitEmergencyConfirm` returns; Playwright sets up the listener synchronously before the button click completes — no race |

---

## 6. Review Decision

**APPROVED** — all three acceptance criteria satisfied. The implementation is correct on:
- precondition envelope shape (HTTP 428, `CONFIRM_TOKEN_REQUIRED`)
- UI success-leak prevention (three-layered assertions)
- advisory action queued path (HTTP 202, success toast)

No required changes. Returning to Codex2 (owner) for finalization.

---

## 7. Handoff Note

This packet is now complete. The review evidence and approval are recorded in:
- `.orchestrator/reviews/FE-INT-GATE-B04-review-claude.md` — Claude's original review file
- `ai-status.json` — parent task `FE-INT-GATE-B04` status: `review_approved`

**Next action for Codex2 (parent task owner):**
Run the closeout checklist per `.orchestrator/skills/task-closeout-finalization.md`:
1. Re-read this packet and the review file
2. Verify `execute-plans/e2e/04-sentinel-remediation.spec.ts` still matches approved state
3. Create task-scoped commit (subject includes `FE-INT-GATE-B04`)
4. Run `AI_NAME=Codex2 ./scripts/ai-status.sh done FE-INT-GATE-B04 "<checkpoint message>"`
5. Push to configured upstream

**Sidecar task (FE-INT-GATE-B04-SIDECAR-REVIEW):** Ready for Codex2 review and closeout.
