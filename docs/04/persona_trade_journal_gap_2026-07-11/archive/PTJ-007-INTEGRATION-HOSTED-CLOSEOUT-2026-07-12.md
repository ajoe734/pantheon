# PTJ-007 Integration and Hosted Closeout Evidence

Date: 2026-07-12  
Task: `PTJ-007`  
Owner: Codex  
Reviewer: Antigravity  
Environment: Pantheon dev, paper-only validation

## Delivery inventory

| Slice | Repository | PR / merge evidence |
|---|---|---|
| PTJ-001 contracts and schema | `pantheon` | PR #3294, merge `aea82011977f526db0f76dbf9de5c4f7bc84657b` |
| PTJ-002 episode projection and replay | `pantheon` | PR #3297, merge `d45dd141fb304fa4ce5c8973ac9f78465e8583eb`; follow-up PR #3302, merge `d44790962f204764c3ce7a2a3d65c5e1c4bb2ab4` |
| PTJ-003 reflection pipeline | `pantheon` | PR #3310, merge `2da671cc941ae833c1df8b4fc4094d690894606a` (after review/fix PRs #3303 and #3306) |
| PTJ-004 BFF APIs | `pantheon` | PR #3322, merge `36f44ab487710c073453e1539f778b703268c818` |
| PTJ-005 memory governance | `pantheon` | PRs #3313, #3315, #3319, and #3325; final merge `71089bb9cd832da2a68775f5c6f9196fb8b35f25` |
| PTJ-006 Trade Journal UX | `execute-plans` | PR #266, merge `916abb9bfc84084a18d7b81a7ec8781c04ae0476` |

All implementation slices have merged. The collaboration state may lag those GitHub merge facts; PTJ-007 does not rewrite another task owner's lifecycle state.

## Deterministic integration verification

The focused suite exercises schema validation, append-only projection replay (including duplicate, late, out-of-order, partial-fill, scale, reversal, force-close, and unresolved joins), immutable reflection facts, retry/version behavior, lesson governance transitions, BFF pagination, environment/persona isolation, RBAC, idempotency, masking, downstream failure, and audit receipts.

```text
python3 -m pytest -q \
  services/telemetry/test_trade_episode_projection.py \
  services/telemetry/test_trade_journal_contracts.py \
  services/persona/test_trade_reflection_contracts.py \
  services/persona/test_trade_reflection_pipeline.py \
  services/memory/test_lesson_governance_api.py \
  services/control-plane/bff/test_ptj_004_trade_journal.py

50 passed, 4 warnings in 18.40s
```

The warnings are existing FastAPI `on_event` deprecations and do not alter the result.

This is deterministic paper fixture evidence. It does not invoke a broker adapter, enable real frontend writes, or submit a live order. Reflection remains candidate-only; policy, capital, risk, and live behavior still require their governed evaluation/deployment paths.

## Hosted dev evidence

- Frontend: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- Frontend deployment run: execute-plans GitHub Actions run `29199429734`, explicitly dispatched for latest `main` SHA `c6eeff25be926a9748576cb624bff65e64af9eab` with `skip_probe=false`.
- Required build safety: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`.
- Unauthenticated BFF probes for journal, reflections, and patterns return governed `401 AUTH_REQUIRED`, proving the deployed routes exist and fail closed rather than exposing cross-persona data.
- `deployment.json` reports deployed commit `c6eeff25be926a9748576cb624bff65e64af9eab` at `20260712T160610Z`; this commit contains PTJ-006 merge `916abb9bfc84084a18d7b81a7ec8781c04ae0476`.
- Hosted bundle `assets/index-9puPnkYu.js` contains `Trade Journal`, `trade-journal`, and `tradeJournal`, contains the intended dev BFF URL, and contains neither the legacy BFF IP nor the Lovable dev URL.
- A clean detached worktree at the deployed SHA ran the PTJ-006 Playwright scenario against the hosted FE. Navigation, deterministic paper episodes, timeline/detail, partial/degraded states, reflection retry receipt, lesson endorsement receipt, and pattern view passed: `1 passed (8.1s)`.

```text
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
  npx playwright test e2e/22-persona-trade-journal.spec.ts \
  --project=chromium --reporter=line

1 passed (8.1s)
```

The deployment workflow concluded `failure` after installation because its generic persona-fleet probe waited for `/bff/management/persona-fleet`, while the loaded page requested `/bff/management/fleet` and received 404. The run still installed the requested SHA, and the task-specific manifest, bundle, BFF fail-closed probes, and hosted PTJ Playwright scenario passed. The generic probe route mismatch remains owned by the execute-plans dev-deploy workflow lane; it is not hidden as a green workflow claim.

## SLO and alert ownership

The product targets remain those in the gap specification: paper projection freshness p95 at or below 60 seconds; canary/live readback p95 at or below 15 seconds; reflection request within five minutes of the attribution watermark. Operational metrics/alerts cover projection freshness, journal coverage, unresolved joins, reflection queue age/failure, and lesson review age. This closeout verifies the contracts and surfaced states; it does not claim production SLO history from deterministic fixtures.

## Residual boundaries

- No live broker proof was attempted or authorized.
- Hosted commands remain safe-write disabled; command behavior is covered by deterministic BFF/frontend tests and governed auth probes.
- Canonical order/fill/P&L authority remains runtime telemetry and attribution. The journal is a rebuildable projection, the BFF is an aggregation boundary, and the frontend performs no canonical P&L inference.
- Generic deploy probe route mismatch: execute-plans deployment workflow owner; reconcile `/bff/management/persona-fleet` versus `/bff/management/fleet` before treating the broad persona-fleet deploy gate as green.
