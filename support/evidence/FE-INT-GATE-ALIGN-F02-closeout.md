# FE-INT-GATE-ALIGN-F02: Closeout Evidence

**Task:** Align 02-control-room.spec.ts to hosted Lovable DOM
**Owner:** Claude
**Reviewer:** Codex2
**Phase:** Pantheon FE Integration Gate 2026-05-13
**Closeout date:** 2026-05-14

---

## Approved Commit

- **Repo:** `/home/lupin/code/execute-plans` (`bff-luv-fe-006-dev-deploy` branch)
- **Commit:** `30c7dc3` — _FE-INT-GATE-ALIGN-F02: align 02-control-room.spec.ts to hosted Lovable DOM_
- **Scope:** `e2e/02-control-room.spec.ts` only — 2 insertions (+), 0 deletions (-)

## Root Cause (resolved)

`frontendUrl()` and `bffUrl()` resolver functions in the spec read `FRONTEND_BASE_URL` / `PLAYWRIGHT_BASE_URL` but not `PANTHEON_FE_BASE_URL`. The CI gate runner and `playwright.config.ts` both set `PANTHEON_FE_BASE_URL`. All 5 fixture-backed tests hit `localhost:5173` with `ERR_CONNECTION_REFUSED` on every hard-gate run.

## Fix Applied

Both resolver functions now check `PANTHEON_FE_BASE_URL` / `PANTHEON_BFF_BASE_URL` first, before legacy env aliases, before `DEFAULT_FRONTEND_BASE_URL`.

## Reviewer Approval

Codex2 approved commit 30c7dc3:
- Diff scoped to env resolver only; no assertion downgrade
- Verification passed twice against hosted Lovable with `--trace=on`: **5 passed, 1 skipped** (live BFF probe, requires `FE_INT_GATE_LIVE_BFF=1`)
- Live BFF opt-in probe also passed separately

## Final Owner Verification

```
cd /home/lupin/code/execute-plans
PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app \
  PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io \
  npx playwright test e2e/02-control-room.spec.ts --reporter=line
```

Result: **5 passed, 1 skipped** (2.1 min) — exit code 0

## Product Gaps

None. All 5 fixture tests passed after the env resolver fix. No selector changes required.

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| npx playwright test e2e/02-control-room.spec.ts passes 2 consecutive times | ✅ (reviewer: 2×; owner final: 1×) |
| Assertions use real hosted Lovable DOM — no guessed selectors | ✅ URL resolver aligned to PANTHEON_FE_BASE_URL |
| No downgrade of blueprint pass condition | ✅ All 5 fixture tests retained |
| Product gaps filed as follow-up, not masked | ✅ No product gaps found |
| Closeout commit on bff-luv-fe-006-dev-deploy in execute-plans repo | ✅ Commit 30c7dc3 |
