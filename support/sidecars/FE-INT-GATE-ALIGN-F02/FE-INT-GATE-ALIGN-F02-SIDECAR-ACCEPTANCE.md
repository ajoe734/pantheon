# Acceptance Packet — FE-INT-GATE-ALIGN-F02

**Sidecar Task ID:** FE-INT-GATE-ALIGN-F02-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-ALIGN-F02 — Align 02-control-room.spec.ts to hosted Lovable DOM
**Helper Kind:** acceptance_packet
**Prepared by:** Claude (2026-05-14)
**Reviewer:** Codex
**Parent Reviewer:** Codex2

---

## 1. Parent Task Summary

FE-INT-GATE-ALIGN-F02 aligns the Control Room E2E spec (`execute-plans/e2e/02-control-room.spec.ts`) against the hosted Lovable deployment at `https://pantheon-dev.lovable.app`.

### Root Cause (resolved)

`frontendUrl()` and `bffUrl()` in the spec did not check `PANTHEON_FE_BASE_URL` / `PANTHEON_BFF_BASE_URL` first. They fell back to local defaults (`http://127.0.0.1:5173`) instead of the hosted URL. All 5 fixture-driven tests were hitting the local dev URL rather than the hosted Lovable environment.

### Fix Applied

Both resolver functions now check `PANTHEON_FE_BASE_URL` and `PANTHEON_BFF_BASE_URL` before the pre-existing legacy env vars:

```ts
function frontendUrl(path = "/"): string {
  const base =
    process.env.PANTHEON_FE_BASE_URL ||   // ← added
    process.env.FRONTEND_BASE_URL ||
    process.env.PLAYWRIGHT_BASE_URL ||
    DEFAULT_FRONTEND_BASE_URL;
  ...
}

function bffUrl(path: string): string {
  const base =
    process.env.PANTHEON_BFF_BASE_URL ||  // ← added
    process.env.BFF_BASE_URL ||
    process.env.VITE_BFF_BASE_URL ||
    DEFAULT_BFF_BASE_URL;
  ...
}
```

**Commit:** `30c7dc3` on branch `bff-luv-fe-006-dev-deploy` in the `execute-plans` repo.

---

## 2. Acceptance Checklist

### Core Acceptance Criteria (from parent task)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `npx playwright test e2e/02-control-room.spec.ts` passes 2 consecutive times from `execute-plans/` dir | ✅ Verified (5/5 pass, 2 runs) |
| 2 | Assertions aligned to real hosted Lovable DOM/network | ✅ URL resolver reads `PANTHEON_FE_BASE_URL` |
| 3 | No downgrade of blueprint pass condition | ✅ All 5 fixture tests retained at full coverage |
| 4 | Product gaps filed as follow-up (not masked) | ✅ No product gaps found; env var fix was sufficient |
| 5 | Closeout commit on `bff-luv-fe-006-dev-deploy` in execute-plans repo | ✅ Commit `30c7dc3` |

### Sidecar Constraints

| # | Constraint | Status |
|---|-----------|--------|
| S1 | Support artifacts only — no canonical truth edits | ✅ This file is support-only |
| S2 | No L1 policy, registry, or runtime implementation changes | ✅ Not touched |
| S3 | Handoff to reviewer upon completion | ✅ Handed to Codex |

---

## 3. Test Coverage Map

The spec covers 6 tests under the `F02 Control Room` describe block:

| Test | Coverage Area | Fixture Dep | Live BFF |
|------|--------------|-------------|----------|
| renders KPI cards, loops, sentinel findings, and interventions | Control Room full render; KPI label presence; loop/sentinel/intervention row items visible | `NON_EMPTY_CONTROL_ROOM` | No |
| drill-down link reaches the loop surface | Loop item click navigates to `/management/loops` or calls `/bff/v5/loop-runs/loop-fixture-1` | `NON_EMPTY_CONTROL_ROOM` | No |
| drill-down link reaches the sentinel surface | Sentinel item click navigates to `/management/sentinel` or calls sentinel detail endpoint | `NON_EMPTY_CONTROL_ROOM` | No |
| drill-down link reaches the intervention surface | Intervention item click navigates to `/management/interventions` or calls intervention detail endpoint | `NON_EMPTY_CONTROL_ROOM` | No |
| renders empty control-room data without crashing | Empty KPI payload renders non-blank body; no crash text | `EMPTY_CONTROL_ROOM` | No |
| live control-room API preserves composed read-model shape | Live BFF shape contract probe | (none) | Yes — requires `FE_INT_GATE_LIVE_BFF=1` |

---

## 4. BFF API Contract Surface

Routes stubbed/probed by this spec:

| Route | Method | Purpose |
|-------|--------|---------|
| `/bff/me` | GET | Session / MeResponse (tenant, user, session, feature_flags) |
| `/health` | GET | Health check |
| `/bff/v5/control-room` | GET | Control Room read model (kpis, kpi_cards, loops, sentinel, interventions, meta) |
| `/bff/v5/loop-runs` | GET | Loop runs list |
| `/bff/v5/sentinel/findings` | GET | Sentinel findings list |
| `/bff/v5/interventions` | GET | Interventions list |
| `/bff/v5/loop-runs/loop-fixture-1` | GET | Loop run detail (drill-down) |
| `/bff/v5/sentinel/findings/sentinel-fixture-1` | GET | Sentinel finding detail (drill-down) |
| `/bff/v5/interventions/intervention-fixture-1` | GET | Intervention detail (drill-down) |
| `/bff/v5/execution/persona-health` | GET | Persona health matrix |
| `/bff/v5/execution/strategy-health` | GET | Strategy health |
| `/bff/alerts`, `/bff/approvals`, `/bff/jobs` | GET | Shell-level empty lists |
| `/bff/search` | GET | Search empty result |
| `/bff/events/stream` | GET | SSE fixture stream (keep-alive, empty) |

All routes include `CORS` headers (allow-origin, credentials, methods, exposed headers). The spec uses `installBffFixtureRoutes()` to stub these via Playwright `page.route()` before navigation.

---

## 5. Dependency Map

| Dependency | Type | Status |
|-----------|------|--------|
| `execute-plans/e2e/02-control-room.spec.ts` | Implementation artifact | ✅ Fixed at commit `30c7dc3` |
| `PANTHEON_FE_BASE_URL` env var | Runtime env | Must be set to `https://pantheon-dev.lovable.app` for hosted runs |
| `PANTHEON_BFF_BASE_URL` env var | Runtime env | Optional; needed only for `FE_INT_GATE_LIVE_BFF=1` live BFF probe |
| Hosted Lovable at `https://pantheon-dev.lovable.app` | External env | Required for acceptance runs |
| `bff-luv-fe-006-dev-deploy` branch in `execute-plans` | Git | Commit `30c7dc3` on this branch |

**No parent depends_on tasks.** FE-INT-GATE-ALIGN-F02 has no blocking dependencies.

---

## 6. Verification Evidence

Run commands used to verify:

```bash
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
  npx playwright test e2e/02-control-room.spec.ts --reporter=line
```

Result (2 consecutive runs):
- **5 passed**, 1 skipped (live BFF probe correctly skipped without `FE_INT_GATE_LIVE_BFF=1`)
- 0 failures
- No console errors captured

---

## 7. Product Gap Register

No product gaps discovered during F02 alignment. The failures in the hard-gate run were entirely due to the env var resolver not checking `PANTHEON_FE_BASE_URL`. Once the resolver was fixed, all fixture tests passed against the hosted Lovable DOM without selector or assertion changes.

---

## 8. Handoff Notes for Reviewer (Codex2, parent task)

- The fix is narrow: two 1-line additions to `frontendUrl()` and `bffUrl()`.
- No assertion logic was changed or weakened.
- The spec fixture shape (`NON_EMPTY_CONTROL_ROOM`, `EMPTY_CONTROL_ROOM`) is unmodified.
- Live BFF probe test remains correctly skipped under normal CI (no live credential required for fixture-backed tests).
- Commit `30c7dc3` in execute-plans is isolated to F02.
- No changes to `pantheon` canonical files.

---

*This packet is a support artifact. No canonical truth was modified.*

---

## 9. Finalization Record

**Finalized by:** Claude
**Date:** 2026-05-14
**Reviewer approval:** Codex (2026-05-14T12:06:05Z) — spot-check `5 passed, 1 skipped`
**Closeout commit:** `20654966` (initial packet), this finalization commit
**Push:** branch `feat/bff-consol-022-staging-strict-cutover` pushed to upstream
**Status:** `done`
