# Acceptance Packet — FE-INT-GATE-ALIGN-F01

**Sidecar Task ID:** FE-INT-GATE-ALIGN-F01-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-ALIGN-F01 — Align 01-startup-session.spec.ts to hosted Lovable DOM
**Helper Kind:** acceptance_packet
**Prepared by:** Claude2 (2026-05-14)
**Reviewer:** Gemini2
**Parent Reviewer:** Codex2

---

## 1. Parent Task Summary

FE-INT-GATE-ALIGN-F01 aligns the startup session E2E spec (`execute-plans/e2e/01-startup-session.spec.ts`) against the hosted Lovable deployment at `https://pantheon-dev.lovable.app` with the dev BFF at `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`.

### Root Cause (resolved)

The spec's `frontendUrl()` / `bffUrl()` did not check `PANTHEON_FE_BASE_URL` / `PANTHEON_BFF_BASE_URL`, so hard-gate run 25846710728 (commit 4774678) hit local defaults instead of the hosted target. Four cases failed:

- MeResponse shape assertion
- Strict mode serving-mock banner absence check
- SSE EventSource open via browser-native path
- 401 must not fall back to mock current-user

### Fix Applied (execute-plans commit a685175)

- `frontendUrl()` and `bffUrl()` now check `PANTHEON_FE_BASE_URL` / `PANTHEON_BFF_BASE_URL` before the pre-existing legacy env vars.
- `/bff/me` assertion tightened: checks `session.state=active` when `session_kind` is absent (hosted strict path).
- `EventSource` is now opened from the Lovable origin rather than a local URL.
- Startup BFF network evidence is attached as a test annotation for the 401 path.
- **Commit:** `a685175` on branch `bff-luv-fe-006-dev-deploy` in the `execute-plans` repo.
- **Scope:** `e2e/01-startup-session.spec.ts` only (+43 insertions, -9 deletions); no other files changed.

---

## 2. Acceptance Checklist

### Core Acceptance Criteria (from parent task)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `npx playwright test e2e/01-startup-session.spec.ts` passes 2 consecutive times from `execute-plans/` | ✅ Satisfied — 4/4 both headless runs; 4/4 headed run |
| 2 | Assertions aligned to real hosted Lovable DOM/network (no guessed selectors) | ✅ Satisfied — env resolvers read `PANTHEON_FE_BASE_URL`/`PANTHEON_BFF_BASE_URL` |
| 3 | No downgrade of blueprint pass condition | ✅ Satisfied — reviewer Codex2 confirmed no assertion weakening |
| 4 | Product gaps filed as follow-up tasks, not masked in spec | ✅ Satisfied — `FE-INT-GATE-FOLLOWUP-ME-STARTUP` filed; `interceptedMeRequests=0` recorded as test annotation |
| 5 | Closeout commit on `bff-luv-fe-006-dev-deploy` in execute-plans repo | ✅ Satisfied — commit `a685175` |

### Sidecar Constraints

| # | Constraint | Status |
|---|-----------|--------|
| S1 | Support artifacts only — no canonical truth edits | ✅ This file is support-only |
| S2 | No L1 policy, registry, runtime, or governance changes | ✅ Not touched |
| S3 | Handoff to reviewer upon completion | ✅ Handed to Gemini2 |

---

## 3. Test Coverage Map

The spec covers 4 tests under the `F01 startup session` describe block. All 4 passed in every verified run.

| # | Test Name | Coverage Area | Live BFF | Fixture |
|---|-----------|--------------|----------|---------|
| 1 | asserts MeResponse tenant/env/user/capabilities shape | `GET /bff/me` → validates `data.tenant`, `data.env`, `data.user`, `data.capabilities` object shape; `meta.request_id` present | Yes — direct request to hosted BFF | None |
| 2 | strict live mode shows no serving-mock / seed-fallback banner | Navigates hosted Lovable; scans `<body>` for `SERVING_MOCK_BANNER` regex; must be absent in strict mode | Yes — Playwright navigates `PANTHEON_FE_BASE_URL` | None |
| 3 | browser-native EventSource opens to BFF SSE stream | Creates `EventSource(bffUrl('/bff/events/stream'))` in browser context; waits for `open` event | Yes — SSE to hosted BFF | None |
| 4 | /bff/me 401 is auth error, never falls back to mock current-user | Playwright intercepts `/bff/me` → returns 401; page navigates; body must not show mock-user data; `interceptedMeRequests` recorded in annotation | Partial — intercept replaces live /bff/me | None |

> Test 4 annotation note: `interceptedMeRequests=0` at the time of commit `a685175` — the hosted Lovable startup did not call `/bff/me`, so the intercept had 0 hits. This is the product gap tracked in `FE-INT-GATE-FOLLOWUP-ME-STARTUP`.

---

## 4. BFF API Contract Surface

Routes exercised by this spec:

| Route | Method | How Used |
|-------|--------|---------|
| `/bff/me` | GET | Direct request (test 1); intercepted 401 path (test 4) |
| `/bff/events/stream` | GET | Browser-native `EventSource` open (test 3) |
| `/` (Lovable root) | GET (navigation) | Page load for banner check (test 2) and 401 intercept check (test 4) |

No fixture-backed routes are used. All BFF calls are either direct API requests or native browser network calls against the hosted target.

---

## 5. Dependency Map

| Dependency | Type | Status |
|-----------|------|--------|
| `execute-plans/e2e/01-startup-session.spec.ts` | Implementation artifact | ✅ Fixed at commit `a685175` on `bff-luv-fe-006-dev-deploy` |
| `PANTHEON_FE_BASE_URL` env var | Runtime env | Must be `https://pantheon-dev.lovable.app` for hosted runs |
| `PANTHEON_BFF_BASE_URL` env var | Runtime env | Must be `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` for hosted runs |
| `VITE_BFF_FALLBACK=strict` env var | Runtime env | Required for strict-mode banner test (test 2) |
| Hosted Lovable at `https://pantheon-dev.lovable.app` | External env | Required for tests 2, 3, 4 |
| Dev BFF at `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` | External env | Required for tests 1, 3 |
| `bff-luv-fe-006-dev-deploy` branch in `execute-plans` | Git | Commit `a685175` on this branch |
| `FE-INT-GATE-FOLLOWUP-ME-STARTUP` | Follow-up task | in_progress (owner Claude2, reviewer Codex) — tracks /bff/me startup gap |

**No blocking `depends_on` tasks.** FE-INT-GATE-ALIGN-F01 ran independently.

---

## 6. Verification Evidence

### Run 1 — Headless strict (by owner Codex)

```bash
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts --trace=on
# Result: 4 passed, 0 failed
```

### Run 2 — Headless strict (second consecutive run by owner Codex)

Same command as Run 1. **Result: 4 passed, 0 failed.**

### Run 3 — Headed strict via xvfb-run (by owner Codex)

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
xvfb-run -a npx playwright test e2e/01-startup-session.spec.ts --headed --trace=on
# Result: 4 passed, 0 failed
```

### Reviewer Verification (Codex2)

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/01-startup-session.spec.ts --trace=on \
  --output=.lovable/audits/current-run/f01-review-test-results \
  --reporter=list,json
# Run 1: 4 passed, 0 failed
# Run 2: 4 passed, 0 failed
```

Reviewer confirmed: no assertion weakening; commit a685175 scoped only to the spec file; product gap tracked in follow-up.

---

## 7. Product Gap Register

| Gap ID | Description | Resolution |
|--------|------------|-----------|
| FE-INT-GATE-FOLLOWUP-ME-STARTUP | Hosted Lovable startup exercises live BFF list/v5/SSE routes but does **not** request `/bff/me` before showing local platform role UI (TopBar shows `admin` from local role control). Test 4's intercept recorded `interceptedMeRequests=0`. | Filed as separate follow-up task. Owner Claude2 has implemented fix at commit `20dfe79` (TopBar now calls `useMe()` on mount; 401 renders Lock+Auth error state, never falls back to mock current-user). Status: `review` awaiting Codex approval. |

The F01 spec correctly annotates this gap rather than masking it. The test annotation preserves the evidence for the reviewer and follow-up owner.

---

## 8. Handoff Notes for Parent Reviewer (Codex2)

- The fix is narrow: env resolver additions to `frontendUrl()` / `bffUrl()`, EventSource origin alignment, and `/bff/me` session.state assertion tightening.
- No assertion was weakened or removed.
- Product gap `FE-INT-GATE-FOLLOWUP-ME-STARTUP` is explicitly tracked and already has an implementation in review (`interceptedMeRequests > 0` assertion will be enforced once the follow-up is approved).
- Commit `a685175` in execute-plans is isolated to the F01 spec file.
- No changes to `pantheon` canonical files (L1/L2/registry/runtime/governance untouched).
- Parent task `FE-INT-GATE-ALIGN-F01` is now `review_approved`; closeout commit by Codex (owner) should follow.

---

*This packet is a support artifact. No canonical truth was modified.*
