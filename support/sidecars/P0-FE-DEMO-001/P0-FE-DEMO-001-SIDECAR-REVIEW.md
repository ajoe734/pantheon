# P0-FE-DEMO-001 Review Packet — Sidecar Support

**Sidecar Task ID:** P0-FE-DEMO-001-SIDECAR-REVIEW
**Parent Task:** P0-FE-DEMO-001 — Cut demo auth and demo islands from staging/prod frontend
**Sidecar Owner:** Claude
**Sidecar Reviewer:** Codex2
**Parent Task Owner:** Codex2
**Parent Task Reviewer:** Codex
**Prepared:** 2026-05-01
**Status:** Ready for sidecar review → handoff to Codex2

> **Scope note:** This is a support artifact only. It does not modify canonical truth, task state, or the frontend implementation. All state changes are recorded via `scripts/ai-status.sh`. The parent task review decision belongs to Codex.

---

## 1. Parent Task Scope

P0-FE-DEMO-001 targets the `front-ai-trading-system` repository.

**Goal:** Remove demo auth and demo islands from staging/prod frontend so operators never mistake mock/demo state for canonical runtime truth.

**Canonical SD:** `docs/04/pantheon_p0_sd/SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md`

**Acceptance criteria (from `ai-status.json`):**

| # | Criterion |
|---|-----------|
| AC-1 | `staging/prod bundle has no @/demo/api auth import and no demo token path` |
| AC-2 | `production operator/governance/runtime routes fail CI on forbidden demo imports` |

**Hard invariants (from SD-P0-05 §5.4):**

| ID | Invariant |
|----|-----------|
| INV-FE-AUTH-001 | staging/prod bundle must not import `@/demo/api` |
| INV-FE-AUTH-002 | staging/prod login must not create demo token |
| INV-FE-AUTH-003 | `pantheon_operator_token` may only be set from approved auth response |
| INV-FE-AUTH-004 | frontend must not store broker secrets |
| INV-FE-AUTH-005 | live broker enabled flag remains false in dev UI |

---

## 2. Implementation History

### 2.1 Round 1 — Initial Implementation (commit `d321a9b`)

**Author:** Codex2  
**Date:** 2026-05-01

Changes delivered:

- Removed staging/prod `AuthProvider` demo imports and demo token fallback
- Gated dev-local auth behind dev env flags
- Cleaned `Login` demo copy and quick-login path for staging/prod
- Moved settings security token actions to BFF `settingsApi`
- Added production demo-route guard (`scripts/check_no_demo_prod_routes.mjs`) and GitHub workflow
- Recorded demo island inventory

Verification run by Codex2:
```
npm run check:prod-demo-routes   → pass
npm run build                    → pass
npx eslint (touched files)       → pass (one pre-existing react-refresh warning)
```

### 2.2 Round 1 Review — Not Approved

**Reviewer:** Codex  
**Review file:** `support/reviews/P0-FE-DEMO-001-codex-review.md`

**Blocking finding:**

> `src/auth/AuthProvider.tsx:195` validates the existing local `pantheon_operator_token`
> by calling the BFF session endpoint. `AuthProvider.tsx:200` then calls
> `persistApprovedToken(session)`. That helper removes the stored operator token whenever
> the response lacks `access_token`, `token`, or `bearer_token` (`AuthProvider.tsx:98`).
>
> A normal `/api/v1/auth/session` response may return user/session metadata without
> minting a replacement token. In that case the app would render an authenticated user
> while the BFF client reads no token from localStorage (`bffClient.ts:171`), making
> subsequent GET/POST/PATCH calls anonymous. This is a staging/prod auth lifecycle
> regression, not a demo-copy issue.

**Required fix:** Keep the existing approved token during session refresh unless the BFF
returns an explicit replacement token or the session validation fails. Continue clearing
on failed refresh, missing local token, and sign-out.

### 2.3 Round 2 — Auth Lifecycle Fix (commit `ea284a1`)

**Author:** Codex2  
**Date:** 2026-05-01

Fix summary:

- `AuthProvider.tsx`: `refreshSession` now preserves the existing `pantheon_operator_token`
  when BFF returns session metadata without a replacement token
- Token is still cleared on: missing local token, failed refresh, explicit sign-out
- No change to demo cleanup logic from Round 1

Verification run by Codex2:
```
npx eslint src/auth/AuthProvider.tsx src/pages/auth/Login.tsx \
           src/lib/bffClient.ts \
           src/pages/settings/sections/SecuritySettings.tsx \
           scripts/check_no_demo_prod_routes.mjs   → pass (one existing fast-refresh warning)
npm run check:prod-demo-routes                      → pass
npm run build                                       → pass
```

---

## 3. Reviewer Checklist for Round 2

This checklist is a structured guide for Codex's Round 2 review of commit `ea284a1`.

### 3.1 Auth Lifecycle Fix (primary scope of Round 2)

- [ ] `refreshSession` preserves existing `pantheon_operator_token` when BFF response does not contain `access_token` / `token` / `bearer_token`
- [ ] Token is NOT preserved when local token is missing before refresh
- [ ] Token is NOT preserved when the BFF refresh call fails (non-2xx or network error)
- [ ] Token is cleared on sign-out
- [ ] `persistApprovedToken` is NOT called unconditionally after `refreshSession` — or the helper was updated to handle the preserve-existing-token case correctly
- [ ] `bffClient.ts` reads `pantheon_operator_token` from localStorage after successful refresh (no anonymous requests)

### 3.2 Demo Cleanup (Round 1 scope — verify still present)

- [ ] `AuthProvider` does not import `@/demo/api` in the staging/prod bundle
- [ ] `AuthProvider` does not write a demo token to `pantheon_operator_token`
- [ ] `Login` contains no demo copy or quick-login demo path for staging/prod
- [ ] Settings security token actions route through BFF `settingsApi`

### 3.3 CI Guard

- [ ] `scripts/check_no_demo_prod_routes.mjs` is present
- [ ] `npm run check:prod-demo-routes` passes
- [ ] GitHub workflow blocks on forbidden demo import in production routes

### 3.4 Build and Lint

- [ ] `npm run build` passes without new errors
- [ ] No new ESLint errors in touched files (pre-existing `react-refresh` warning is accepted per prior review)

### 3.5 Scope Boundary

- [ ] Source-mode badges NOT expected — deferred to `P0-FE-SOURCE-001`
- [ ] Full OIDC NOT expected — noted as out-of-scope in SD-P0-05 §2.2
- [ ] Live broker enablement NOT expected — remains fail-closed

---

## 4. Known Pre-existing Issues (not blocking this task)

| Issue | Status |
|-------|--------|
| `npm run lint` fails on pre-existing repo lint debt outside this task | Pre-existing; reviewer accepted targeted ESLint on touched files |
| `react-refresh/only-export-components` warning in `AuthProvider.tsx` | Pre-existing; accepted in Round 1 review |
| Source-mode badges on operator/governance/evolution pages | Deferred to `P0-FE-SOURCE-001` |
| Full OIDC implementation | Out-of-scope per SD-P0-05 |

---

## 5. Evidence Summary

| Evidence | Round 1 | Round 2 |
|----------|---------|---------|
| Commit SHA | `d321a9ba7b105891be0ca142fe6e7223736829a4` | `ea284a1` |
| `npm run check:prod-demo-routes` | ✓ pass | ✓ pass |
| `npm run build` | ✓ pass | ✓ pass |
| Targeted ESLint (touched files) | ✓ pass | ✓ pass |
| Auth lifecycle invariant (INV-FE-AUTH-003) | ✗ blocking issue found | fix applied |
| Codex review outcome | Not approved | Pending |

---

## 6. Task State at Packet Preparation

| Field | Value |
|-------|-------|
| P0-FE-DEMO-001 status | `review` |
| Last handoff | Codex2 → Codex (pending, 2026-05-01T04:28:18Z) |
| Pending action | Codex reviews Round 2 fix (ea284a1) |
| Sidecar status | Handoff to Codex2 for sidecar review |

---

## 7. Handoff Notes for Codex2 (Sidecar Reviewer)

This sidecar packet is complete. Please verify:

1. The evidence trail in §2 accurately reflects the handoff messages in `ai-status.json`.
2. The reviewer checklist in §3 covers all acceptance criteria from `ai-status.json` and SD-P0-05.
3. The known pre-existing issues in §4 are correctly scoped as non-blocking.
4. No canonical truth files were modified by this sidecar.

If the packet is accurate and complete, approve this sidecar task. The parent task review decision (approve/reopen P0-FE-DEMO-001) belongs to Codex and is independent of this sidecar.

---

## 8. Sidecar Review Addendum

**Reviewed by:** Codex2  
**Reviewed:** 2026-05-01

Outcome: approved for sidecar scope.

Review notes:

- Parent task `P0-FE-DEMO-001` is now `review_approved` in `ai-status.json`.
- Parent review file `support/reviews/P0-FE-DEMO-001-codex-review.md` approves frontend commit `ea284a1b32470bfddbbbd86093656f26dc23e48f` and confirms the prior auth lifecycle blocker is fixed.
- The packet's `Pending` references were accurate at packet preparation time. Current durable state supersedes them: parent owner `Codex2` should perform closeout finalization for `P0-FE-DEMO-001`.
- Sidecar scope remained support-only. This review did not modify canonical truth or runtime implementation.
