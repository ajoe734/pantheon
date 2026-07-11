# Task Brief: PPL-ALLOC-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promotion and allocation workbench
- Status: review
- Owner: Claude
- Reviewer: Codex
- Next: Review execute-plans PR #251 — Real ranking target-weight panel, read-only Emergency actions tab, rebalance workflow-state column/focus link, persona-league/quarterly-ranking folded into workbench tabs (no more duplicate standalone nav pages).

## Summary
把 Promotion & Allocation 擴成唯一操作工作台：paper candidates、real ranking、quarterly capital、emergency actions。

## Implementation Notes (Claude, 2026-07-11)

Scope note: `execute-plans/` was removed from this repo the day before
(`REPO-BOUNDARY-EXECUTE-PLANS`, 2026-07-10) — the frontend now lives only in
the standalone `ajoe734/execute-plans` repo. All implementation work for this
task happened there; nothing was added back into the pantheon mirror.

- Frontend PR: `ajoe734/execute-plans#251` (branch `task/PPL-ALLOC-006-workbench`, base `dev`)
- Depends on (both done): `PPL-ALLOC-003` (capital binding read model, PR #3105→dev `ffe83a8f`), `PPL-ALLOC-004` (allocation policy + rebalance contract, PR #3112→dev `cec3660e`)
- Delivered:
  - `RealRankingPanel` — joins `mgmt.personaFleet.get()` (capital binding) with `mgmt.personaLeague.rankingsLiveOnly()` (scores) and calls the new `mgmt.allocationPolicy.evaluate()` client (POST `/bff/management/allocation-policy/evaluate`) to render current/target weight, delta, and cap reasons per canary/live persona. A capital increase only ever renders "requires human approval", never "applied" — this panel evaluates a fresh proposal, not a persisted rebalance.
  - `EmergencyActionsPanel` — read-only view of containment-relevant Human Inbox kinds (capital_breach/policy_violation/rollback_request/broker_disconnect/sentinel), links out to Human Inbox detail only. PPL-ALLOC-008 (emergency containment BFF/UI) has not shipped, so no mutation control is invented here.
  - `ManagementPersonaFleetRow` + its adapter extended to read the PPL-ALLOC-003 capital-binding projection (`capital_scope`, `capital_sleeve_id`, `current_weight`, `target_weight`, `binding_state`) the BFF already returns but the frontend wasn't consuming.
  - `RebalancesList` gains a workflow-state column (recommendation/review/approved/applied, from the existing `Rebalance.state`) and a `rebalance_id` focus link.
  - `/management/persona-league` and `/management/quarterly-ranking` are now `LegacyPromotionAllocationRedirect`s onto workbench tabs instead of live standalone pages; the duplicate `ManagementLayout` sidebar entries were removed; `ManagementOperationsNav` links straight to the tab instead of bouncing through the legacy path.
- Verification (execute-plans repo): `npx tsc --noEmit` 0 errors; `npm run lint` 0 errors (pre-existing warnings only, none in touched files); `npx vitest run` 130 files / 1214 tests passed (full suite, no regressions); `npm run build` succeeds. Added 5 new component tests (`PromotionAllocation.test.tsx`) and 4 new BFF-adapter tests (`management.test.ts`).
- Residual risk: no hosted dev-environment smoke check was run (this sandbox has no live BFF to point the frontend at); deferred to the PPL-ALLOC-009 packet-level closeout, consistent with how PPL-ALLOC-003/004/005 handled it.
- Per the repo's cross-repo governance rule, this AI cannot self-merge an `execute-plans` PR — a human must merge #251 after CI/review pass.
