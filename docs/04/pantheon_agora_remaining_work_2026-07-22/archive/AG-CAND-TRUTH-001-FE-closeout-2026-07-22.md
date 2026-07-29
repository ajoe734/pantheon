# AG-CAND-TRUTH-001-FE — Owner Closeout Record

- Task: `AG-CAND-TRUTH-001-FE` — Stop mixing live candidates with sample fields
- Owner: Codex
- Reviewer: Claude
- Closeout date: 2026-07-22
- Delivery repository: `ajoe734/execute-plans`
- Delivery PR: [#506](https://github.com/ajoe734/execute-plans/pull/506)
- Reviewed implementation HEAD:
  `f9fb01d6adaba41045178571d3d006e2ed1e6b05`
- Execute-plans `dev` merge:
  `9597d0c3146451a004c30f2e638010c4eec86488`
- Consumed Pantheon backend contract merge:
  `5004450c5493aa8aef284cf42439c9b27ef54235` (bundle v1.12)
- Independent approval:
  `AG-CAND-TRUTH-001-FE-review-2026-07-22.md`

## Delivered truth boundary

The merged frontend maps each live Trading Room candidate exclusively from the
same v1.12 candidate identity and rejects cross-candidate provenance. Missing,
unavailable, stale, empty, and failed responses remain explicit. Demo content
is available only as a whole-dataset sample mode with visible dataset, row, and
drawer labels; it is never blended into strict live rows.

The change preserves per-lens fetching/filtering and displays field provenance
and freshness. No Pantheon backend behavior, workshop write behavior, or live
capital authority changed in this frontend task.

## Owner verification

The owner created an isolated execute-plans worktree at the exact reviewed
HEAD and ran:

```sh
npm ci
npx vitest run \
  src/agora/pages/trading-room/TradingRoomPage.test.tsx \
  src/lib/bff-v1/agora/candidatePool.test.ts
npx tsc --noEmit
npx eslint \
  e2e/agora-candidate-truth.spec.ts \
  src/agora/pages/trading-room/TradingRoomPage.test.tsx \
  src/agora/pages/trading-room/TradingRoomPage.tsx \
  src/lib/bff-v1/agora/candidatePool.test.ts \
  src/lib/bff-v1/agora/candidatePool.ts
VITE_BFF_MODE=live \
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io \
VITE_BFF_FALLBACK=strict \
VITE_BFF_REAL_WRITES=false \
VITE_BFF_ALLOW_DEV_STUB_WRITES=false \
VITE_SUPABASE_URL=https://example.supabase.co \
VITE_SUPABASE_PUBLISHABLE_KEY=sb_publishable_closeoutverification \
npm run build
PANTHEON_FE_BASE_URL=http://127.0.0.1:5174 \
npx playwright test e2e/agora-candidate-truth.spec.ts
```

Results:

- focused Vitest: 91 passed (77 Trading Room + 14 candidate client)
- TypeScript: clean
- targeted ESLint: 0 errors, 1 existing fast-refresh warning
- live/strict read-only production build: passed
- Playwright: 2 passed (Chromium desktop and mobile Chromium at 393 px)

The first Playwright attempt started against a cold Vite server: desktop timed
out while the route was still a blank compiling page, then mobile passed. The
full desktop/mobile suite passed on the immediate warm-server rerun. No product
assertion failed on that accepted rerun.

GitHub checks at the reviewed HEAD passed Commit trailers, Generated files
guard, Smoke acceptance, and the Pantheon FE-BFF integration gate. PR #506 was
still `CLEAN` and `MERGEABLE` immediately before the owner merged it with an
exact-head guard.

## Residual boundary

This closeout proves the frontend delivery and PR-scoped desktop/mobile
behavior. It does not claim that the replacement Pantheon dev host is already
serving merge `9597d0c3`; accepted hosted FE/BFF pairing and replacement-VM
proof remain owned by downstream `AG-HOSTED-CLOSE-001` as defined by the
execution packet DAG.

## Finalization decision

The reviewer-approved bytes are merged into execute-plans `dev`, owner
verification is green, and the delivery metadata is recorded. After this
Pantheon closeout record merges into Pantheon `dev`, the owner may transition
the task from `review_approved` to `done` with the governed status command.
