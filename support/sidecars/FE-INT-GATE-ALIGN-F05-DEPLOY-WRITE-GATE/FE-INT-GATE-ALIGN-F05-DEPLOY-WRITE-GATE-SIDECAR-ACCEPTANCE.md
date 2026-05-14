# Acceptance Packet — FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE

**Sidecar Task ID:** FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE — Restore hosted Lovable dev real-write gate for F05
**Helper Kind:** acceptance_packet
**Prepared by:** Claude (2026-05-14); revised by Codex (2026-05-14T13:32Z)
**Reviewer:** Claude
**Parent Owner:** Codex
**Parent Reviewer:** Gemini

---

## 1. Parent Task Summary

FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE restores the real-write integration gate on the hosted Lovable dev deployment (`https://pantheon-dev.lovable.app`) so that `e2e/04-sentinel-remediation.spec.ts` (F05) can complete the hosted two-pass acceptance run.

### Root Cause

Hard-gate run 25846710728 (commit 4774678) failed F05 with two timeouts on `POST /bff/v5/interventions/{id}/remediate`. Investigation (commit `55ca952`) confirmed the failure is **not** a selector mismatch:

- The hosted DOM correctly renders the B04 Sentinel finding drawer with both actions (`Open incident`, `Pause persona routing`, `執行` button).
- The hosted bundle (`/assets/index-BYfBkno5.js`) was built with `VITE_BFF_MODE=live` and `VITE_BFF_BASE_URL` set, but **without** `VITE_BFF_REAL_WRITES` or `VITE_BFF_FALLBACK`.
- As a result, `realWritesEnabled()` returns `false` in the hosted browser, and every remediation action is routed through the v5 overlay path instead of issuing the live `POST /bff/v5/interventions/{id}/remediate`.
- The spec expects a real POST; the overlay provides none.

Current hosted evidence has moved past that exact old asset but has **not** satisfied the gate. As of `2026-05-14T13:32Z`, `https://pantheon-dev.lovable.app/` references `/assets/index-CrXlErW2.js`, not `/assets/index-BYfBkno5.js`. The new hosted bundle exposes `VITE_BFF_REAL_WRITES` / `VITE_BFF_FALLBACK` strings, but it still does **not** expose the expected sessionStorage override literals `pantheon.integration.realWrites` / `pantheon.integration.fallback` from commit `104f06b`. Codex2's hosted F05 review check at `2026-05-14T13:24:10Z` still failed both tests with `waitForResponse` timeouts.

### Fix Applied (execute-plans commits 104f06b + 49899d0)

A dev-host-scoped browser runtime gate was introduced:

- **`sessionStorage["pantheon.integration.realWrites"]="true"`** — enables the integration-test real-write path even when the hosted bundle was compiled without `VITE_BFF_REAL_WRITES`.
- **`sessionStorage["pantheon.integration.fallback"]="strict"`** — selects strict fallback mode for the dev-host integration run.
- These overrides are honored **only** on `localhost` and `pantheon-dev.lovable.app`; other hosts (production, non-dev) keep their build-time write-gate behavior.

`e2e/04-sentinel-remediation.spec.ts` now injects both session keys via `page.addInitScript()` before navigation. The spec still:
- Waits for the emergency remediation `POST` (with route interception returning 428 + `CONFIRM_TOKEN_REQUIRED`)
- Asserts the emergency 428 response shape and that no success toast appears
- Waits for the advisory remediation `POST` (route interception returns 202 queued)
- Asserts advisory `202` response and renders success

**Commits in execute-plans repo, branch `bff-luv-fe-006-dev-deploy`:**

| Commit | Summary |
|--------|---------|
| `55ca952` | FE-INT-GATE-ALIGN-F05 — record hosted write gate gap (evidence audit note, +85 lines) |
| `104f06b` | FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE — restore dev write gate (+247 lines across 8 files) |
| `49899d0` | FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE — record deploy wait (update gap note with push+asset check) |

**Files changed in commit `104f06b`:**

| File | Change |
|------|--------|
| `.lovable/audits/current-run/fe-int-gate-align-f05-hosted-write-gate-gap.md` | Remediation + verification section added |
| `e2e/04-sentinel-remediation.spec.ts` | Inject session keys in `installB04Routes()`; route handler already present |
| `src/lib/bff-v1/runtimeEnv.ts` | New module: `getRuntimeEnv()` reads build-time env + sessionStorage overrides |
| `src/lib/bff-v1/liveTransport.ts` | Switched to `getRuntimeEnv()` for `realWritesEnabled()` lookup |
| `src/lib/bff-v1/writeGate.ts` | Switched to `getRuntimeEnv()` for gate check |
| `src/lib/bff/client.ts` | Switched to `getRuntimeEnv()` for client-side gate check |
| `src/lib/bff-v1/__tests__/writes.test.ts` | Unit tests for sessionStorage override paths |
| `src/lib/bff/__tests__/liveTransportSnapshot.test.ts` | Snapshot tests for runtime env resolution |

---

## 2. Acceptance Checklist

### Core Acceptance Criteria (from parent task)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Hosted bundle exposes dev-scoped real-write integration gate | ❌ Not yet — source contains the runtime gate at `104f06b`, but current hosted `/assets/index-CrXlErW2.js` lacks `pantheon.integration.realWrites` / `pantheon.integration.fallback` literals |
| 2 | F05 hosted headed run observes remediation POST | ❌ Not yet — Codex2 hosted check at `2026-05-14T13:24:10Z` still failed both tests with `waitForResponse` timeouts |
| 3 | F05 hosted `npx playwright test` passes twice | ⏳ Pending — requires a correct Lovable branch/artifact deploy, then two hosted passes; production-preview F05 has passed twice as interim source-level evidence |

### Pre-Hosted Verification Evidence (interim)

Production-preview F05 (`npm run build` + `npx vite preview`) passed twice as structural proof that the runtime gate works under a `VITE_BFF_REAL_WRITES=false` production build when sessionStorage keys are injected.

### Sidecar Constraints

| # | Constraint | Status |
|---|-----------|--------|
| S1 | Support artifacts only — no canonical truth edits | ✅ This file is support-only |
| S2 | No L1 policy, registry, runtime, or governance changes | ✅ Not touched |
| S3 | Handoff to reviewer upon completion | ✅ Will hand off to Claude |

---

## 3. Test Coverage Map

The spec covers 2 tests under `F05 Sentinel remediation`. Both passed in all interim verification runs.

| # | Test Name | Coverage Area | Route Interception | Live POST Expected |
|---|-----------|--------------|-------------------|-------------------|
| 1 | treats CONFIRM_TOKEN_REQUIRED as a non-success emergency precondition | `POST /bff/v5/interventions/ra_pause_persona_routing_*/remediate` → 428; body must not contain success text; no crash | Yes — emergency POST intercepted and returned 428 | Yes — `emergencyPosts.length > 0` |
| 2 | allows an advisory Sentinel remediation action to be queued | `POST /bff/v5/interventions/ra_open_incident_*/remediate` → 202; body must match `{status:"queued",data:{action_id:"open_incident"}}` | Yes — advisory POST intercepted and returned 202 | Yes — `advisoryPosts.length > 0` |

**Key behavioral assertion:** Both tests assert that the POST **was actually issued** (`calls.emergencyPosts.length > 0`, `calls.advisoryPosts.length > 0`). This is what was failing before the write-gate fix: the overlay path silently suppressed the POST, so `waitForResponse()` timed out.

---

## 4. BFF API Contract Surface

Routes exercised by this spec:

| Route | Method | How Used |
|-------|--------|---------|
| `/bff/me` | GET | Route-intercepted stub — returns `{data:{session:{authenticated:true,session_kind:"stub"}}}` |
| `/bff/v5/sentinel/findings` | GET | Route-intercepted; returns the B04 fixture with `finding_id: "finding-b04-confirm-token"` |
| `/bff/v5/interventions/{id}/remediate` | POST (emergency) | Route-intercepted; returns 428 `CONFIRM_TOKEN_REQUIRED` |
| `/bff/v5/interventions/{id}/remediate` | POST (advisory) | Route-intercepted; returns 202 `{status:"queued"}` |
| `/health`, `/healthz`, `/bff/health` | GET | Route-intercepted stub |
| All other `/bff/*` GET | GET | Neutral stub `{items:[],data:[],count:0}` |

All routes are intercepted via `page.route("**/*", ...)`. No live BFF calls escape to the network during these tests.

---

## 5. Dependency Map

| Dependency | Type | Status |
|-----------|------|--------|
| `execute-plans/e2e/04-sentinel-remediation.spec.ts` | Implementation artifact | ✅ Fixed at commit `104f06b` on `bff-luv-fe-006-dev-deploy` |
| `execute-plans/src/lib/bff-v1/runtimeEnv.ts` | New module | ✅ Committed at `104f06b` — provides `getRuntimeEnv()` with sessionStorage override |
| `execute-plans/src/lib/bff-v1/writeGate.ts` | Modified | ✅ Committed at `104f06b` — uses `getRuntimeEnv()` |
| `execute-plans/src/lib/bff-v1/liveTransport.ts` | Modified | ✅ Committed at `104f06b` |
| `execute-plans/src/lib/bff/client.ts` | Modified | ✅ Committed at `104f06b` |
| `bff-luv-fe-006-dev-deploy` branch in `execute-plans` | Git | ✅ Commits `55ca952`, `104f06b`, `49899d0` pushed to `origin` |
| Lovable dev branch/artifact deployment | External env | ❌ Still open — current hosted `/assets/index-CrXlErW2.js` is newer than `/assets/index-BYfBkno5.js` but still lacks the `104f06b` sessionStorage override literals; this is not satisfied by just any redeploy refresh |
| `PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app` | Runtime env | Required for hosted F05 run |
| `PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io` | Runtime env | Required for hosted F05 run |
| `FE-INT-GATE-ALIGN-F05` | Parent blocker context | Blocked — F05 parent is waiting on the same correct Lovable artifact deploy |
| `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE` | Parent task | Blocked waiting for Gemini/Lovable to deploy the branch/artifact containing `104f06b` |

**No code-level `depends_on` tasks.** The only open dependency is the Lovable dev deployment selecting the correct branch/artifact that contains `104f06b`; a refresh to a different artifact does not clear the gate.

---

## 6. Verification Evidence

### Evidence A — Gap Discovery (commit 55ca952, by Codex)

```bash
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_REAL_WRITES=true \
VITE_BFF_FALLBACK=strict \
xvfb-run -a npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --headed
# Result: 2 failed — waitForResponse timed out on both tests
```

Confirmed: hosted bundle lacks `VITE_BFF_REAL_WRITES`/`VITE_BFF_FALLBACK`; overlay suppresses POST.

### Evidence B — Local Control (by Codex, pre-fix)

```bash
VITE_BFF_MODE=live \
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=strict \
VITE_BFF_REAL_WRITES=true \
npm run dev -- --host 127.0.0.1 --port 5175
# (in separate terminal)
PANTHEON_FE_BASE_URL=http://127.0.0.1:5175 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_REAL_WRITES=true \
VITE_BFF_FALLBACK=strict \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on
# Run 1: 2 passed
# Run 2: 2 passed
```

Confirmed: the issue is deploy-side only; spec selectors and assertion logic are correct.

### Evidence C — Unit / Type Checks (commit 104f06b, by Codex)

```bash
npx tsc --noEmit
npx vitest run src/lib/bff-v1/__tests__/writes.test.ts \
  src/lib/bff/__tests__/liveTransportSnapshot.test.ts
# All passed
```

### Evidence D — Production-Preview F05 Run 1 (commit 104f06b, by Codex)

```bash
VITE_BFF_MODE=live \
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_FALLBACK=auto \
VITE_BFF_REAL_WRITES=false \
npm run build
# (serves at http://127.0.0.1:4175)
PANTHEON_FE_BASE_URL=http://127.0.0.1:4175 \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
VITE_BFF_MODE=live \
VITE_BFF_REAL_WRITES=false \
VITE_BFF_FALLBACK=auto \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --reporter=list \
  --output=/tmp/fe-int-gate-align-f05-deploy-write-gate-preview-run1
# Result: 2 passed
```

### Evidence E — Production-Preview F05 Run 2 (commit 104f06b, by Codex)

Same command as Evidence D, output to `/tmp/fe-int-gate-align-f05-deploy-write-gate-preview-run2`.

**Result: 2 passed.**

### Evidence F — Hosted Asset Check (commit 49899d0, by Codex)

Hosted asset check performed at `2026-05-14T12:32:26Z`:

```text
https://pantheon-dev.lovable.app/ still references /assets/index-BYfBkno5.js
```

This is the pre-fix bundle. The remediation commits have been pushed to `origin/bff-luv-fe-006-dev-deploy`, but Lovable dev has not yet redeployed.

This older observation has been superseded by Evidence G. It remains useful only as the first deploy-wait checkpoint.

### Evidence G — Current Hosted Asset / Bundle String Check (by Codex sidecar)

Hosted checks performed at `2026-05-14T13:32Z`:

```bash
curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o "/assets/index-[A-Za-z0-9_-]+\\.js" \
  | head -n 5
# /assets/index-CrXlErW2.js

curl -fsSL https://pantheon-dev.lovable.app/assets/index-CrXlErW2.js \
  | rg -o "pantheon\\.integration\\.realWrites|pantheon\\.integration\\.fallback|VITE_BFF_REAL_WRITES|VITE_BFF_FALLBACK|getRuntimeEnv|realWritesEnabled" \
  | sort \
  | uniq -c
#       3 VITE_BFF_FALLBACK
#       1 VITE_BFF_REAL_WRITES
```

There were no matches for `pantheon.integration.realWrites`, `pantheon.integration.fallback`, `getRuntimeEnv`, or `realWritesEnabled` in the current hosted bundle. Source verification against `/home/lupin/code/execute-plans` on `bff-luv-fe-006-dev-deploy` still shows commit `104f06b` and the expected sessionStorage literals in `e2e/04-sentinel-remediation.spec.ts` plus `src/lib/bff-v1/runtimeEnv.ts`.

Codex2 review evidence at `2026-05-14T13:24:10Z` also found current hosted F05 still failed 2/2 with `waitForResponse` timeouts. Therefore the remaining gate is deployment of the correct branch/artifact, not merely observing a new asset hash.

### Closeout Verification — Codex (2026-05-14T13:54Z)

Codex re-ran the hosted asset string gate during owner closeout:

```bash
asset=$(curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o "/assets/index-[A-Za-z0-9_-]+\\.js" \
  | head -n 1)
printf 'asset=%s\n' "$asset"
curl -fsSL "https://pantheon-dev.lovable.app${asset}" \
  | rg -o "pantheon\\.integration\\.realWrites|pantheon\\.integration\\.fallback|VITE_BFF_REAL_WRITES|VITE_BFF_FALLBACK|getRuntimeEnv|realWritesEnabled" \
  | sort \
  | uniq -c
# asset=/assets/index-CrXlErW2.js
#       3 VITE_BFF_FALLBACK
#       1 VITE_BFF_REAL_WRITES
```

No `pantheon.integration.realWrites`, `pantheon.integration.fallback`, `getRuntimeEnv`, or `realWritesEnabled` match was present in the hosted bundle at closeout time, so the packet's open external deployment gate remains current.

---

## 7. Open Gate — Hosted Rerun Required

The parent task `FE-INT-GATE-ALIGN-F05-DEPLOY-WRITE-GATE` is **blocked** on an external ops action:

| Gate | Owned by | Required action |
|------|----------|----------------|
| Lovable dev correct artifact deploy | Gemini | Ensure `pantheon-dev.lovable.app` is built from the branch/artifact containing `104f06b`, with the sessionStorage override literals present in the hosted bundle |

Before rerunning F05, use a quick asset gate:

```bash
asset=$(curl -fsSL https://pantheon-dev.lovable.app/ \
  | rg -o "/assets/index-[A-Za-z0-9_-]+\\.js" \
  | head -n 1)
curl -fsSL "https://pantheon-dev.lovable.app${asset}" \
  | rg "pantheon\\.integration\\.realWrites|pantheon\\.integration\\.fallback"
# Expected: both sessionStorage key literals are present, or an equivalent hosted proof explains why minification removed them.
```

Once the hosted bundle is the correct artifact:

```bash
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
npx playwright test e2e/04-sentinel-remediation.spec.ts --trace=on --reporter=list \
  --headed
# Expected: 2 passed (run 1)
# Expected: 2 passed (run 2)
```

The parent task's acceptance criteria 1-3 will be satisfied after the hosted bundle exposes the dev-scoped gate and these two runs pass.

---

## 8. Handoff Notes for Sidecar Reviewer (Claude) and Parent Reviewer (Gemini)

- The write-gate gap is a **deploy-side issue** only; all spec selectors and assertion logic were correct from the start.
- The sessionStorage runtime override is scoped to `localhost` and `pantheon-dev.lovable.app` only — no production risk.
- Unit tests, type checks, and production-preview F05 (×2) confirm the fix is structurally sound.
- The current hosted asset hash has changed to `/assets/index-CrXlErW2.js`, but the bundle still lacks the `pantheon.integration.*` sessionStorage keys, so the remaining action is a correct Lovable branch/artifact deployment, then a hosted two-pass F05 rerun.
- Once the hosted runs pass, the parent task can proceed to review and closeout.
- No changes to `pantheon` canonical files (L1/L2/registry/runtime/governance untouched).

---

*This packet is a support artifact. No canonical truth was modified.*
