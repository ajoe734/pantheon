# Review: FE-INT-GATE-C01 — F04 Optimization Loop ranking to approval timeline

Reviewer: Claude
Date: 2026-05-13
Decision: **APPROVED**

## Acceptance Criteria Check

| # | Criterion | Status |
|---|---|---|
| 1 | `/management/loops/optimization` renders all 5 stages (ranking → rebalance → awaiting approval → apply → evolution/promotion) | ✅ Pass |
| 2 | Awaiting-approval stage exposes navigation to Approvals or HIQ | ✅ Pass |
| 3 | Stage timeline renders from canonical stage/entity fields, not display-only mock labels | ✅ Pass |
| 4 | SSE stream is properly stubbed (no real EventSource connection) | ✅ Pass |

## Test Coverage

**Test 1 – `renders ranking, rebalance, approval, apply, and promotion stages`**
- Installs self-contained route stubs via `installC01Routes` covering all BFF paths
- Checks for no crash text before asserting stage visibility
- Uses `expectAnyVisibleText` (poll-based) for robustness on text matching
- Verifies `REBALANCE_ID` literal visible in DOM
- Asserts at least one optimization/loop BFF path was requested

**Test 2 – `awaiting approval links to Approvals or HIQ`**
- `clickAwaitingApprovalPath` uses `clickFirstVisible` with broad role-based and href-based selectors
- Post-click assertion checks URL path OR BFF call — correctly handles both SPA navigation and prefetch patterns

**Test 3 – `timeline fixture uses canonical stage fields only`**
- Static unit test; no browser required
- `collectForbiddenTimelineFields` recursively checks for any key in `MOCK_ONLY_TIMELINE_FIELDS` set
- Stage order validated to match expected sequence exactly
- Each stage checked for `entity_type`, `entity_id`, `started_at` presence

## Code Quality Notes

- `installC01Routes` pattern is consistent with other specs in this gate series
- Dual-key defensive payload (`next_action`/`nextAction`, `timeline`/`stages`, `evidence`/`evidence_refs`) correctly handles both snake_case and camelCase API response conventions
- Flexible path-matching helpers (`isOptimizationPath`, `isRankingPath`, etc.) are broad enough to handle route variations without being overly permissive
- CORS handling and OPTIONS preflight stub are correct
- SSE stub body `": fe-int-gate-c01 stream stub\n\n"` is a valid SSE comment event

## Verification (from owner handoff)

- `npx tsc --noEmit --pretty false` passed in sibling execute-plans
- `esbuild` bundle passed for pantheon spec
- `playwright --list` found 3 tests
- `FRONTEND_BASE_URL=http://127.0.0.1:5173 NODE_PATH=/home/lupin/code/execute-plans/node_modules playwright test execute-plans/e2e/04-optimization-loop.spec.ts --reporter=line` passed 3/3 after Vite warmup

## Conclusion

Spec is self-contained, covers all 4 acceptance criteria, and follows established patterns in the FE-INT-GATE series. No changes required. Returning to Codex2 for closeout finalization.
