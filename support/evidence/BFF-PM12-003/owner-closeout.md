# BFF-PM12-003 Owner Closeout

Task: BFF-PM12-003 - GET /bff/management/portfolio-book/pools pool summaries
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-4 / EPIC-BFF-GAP-PM12
Date: 2026-05-23

## Scope Check

Confirmed the approved PM-12 portfolio-book pools surface is present in the
current worktree after fast-forwarding the task branch to `origin/dev` at
`b803f4923114f2397ed60b64141ea9a111a3b3c8`.

- `GET /bff/management/portfolio-book/pools` is registered in
  `services/control-plane/bff/main.py` and requires BFF read-role auth.
- The route returns strict-fallback-compatible `data`, `items`, and `pools`
  list aliases, plus `summary`, `page_info`, and `meta`.
- Pool rows include risk budget, current exposure, risk-budget utilization, PnL
  summary, binding/deployment/runtime counts, stage breakdowns, and source ids.
- Query support covers `status`, `risk_policy_ref`, `page_token`, and
  `page_size`.
- `meta.surfaces` reports the composed `portfolio_book_pools` surface and its
  capital pool, persona binding, deployment plan, runtime binding, and telemetry
  source surfaces.
- `execute-plans/src/lib/bff-v1/management.ts` and
  `execute-plans/src/lib/bff-v1/paths.ts` expose the FE path, query, response,
  item, and fetch helper contracts.

No runtime behavior or API contract code was changed during owner closeout.

## Reviewer Approval

Claude2 approved the task in
`support/reviews/BFF-PM12-003-review-claude2.md`, verifying route shape,
composition logic, degraded-surface handling, auth, pagination/filtering, FE
contracts, and OpenAPI registration.

Implementation PR #454 merged to `dev` at
`4d6ff8cb279528a1de37e6ef4034b0562ce22a9f`.

## Verification

Commands run from `task/BFF-PM12-003` on 2026-05-23 after the `origin/dev`
fast-forward:

```bash
python3 -m py_compile services/control-plane/bff/main.py
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -v
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py -v
```

Results:

- `services/control-plane/bff/main.py` compiled cleanly.
- Portfolio-book contract tests: 11 passed in 4.83s.
- PM-12 persona-league regression tests: 7 passed in 4.80s.
