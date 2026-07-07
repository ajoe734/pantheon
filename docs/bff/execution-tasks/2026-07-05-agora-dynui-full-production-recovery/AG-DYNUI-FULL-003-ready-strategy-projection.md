# AG-DYNUI-FULL-003 Ready Strategy Projection

Owner: Codex
Reviewer: Codex2
Status: backend partial merged; task-board closeout still requires evidence
review.

## Delivered Scope

- Pantheon PR #3020 merged readiness projection into Trading Room backend reads.
- Pantheon PR #3021 deployed the Postgres workshop store needed for durable
  readiness state.
- Direct BFF proof showed a SQL-seeded workshop could reach
  `highest_ready_gate = trading_room` and expose strategy
  `strat-full003-live-20260705T131055Z`.

## Not Yet Production Complete

- The proof seeded the completeness snapshot through SQL instead of a public
  workshop API or hosted UI path.
- A browser-created workshop still has no completeness snapshot, no Strategy
  Registry ref, and no Trading Room-ready strategy.
- `GET /bff/agora/trading-room` still returns `strategies: []` for
  `tenant:pantheon-dev:user:pantheon-dev-browser`.

## Acceptance To Close

- Record the #3020 and #3021 PR URLs, merge SHAs, deploy evidence, and direct
  BFF curl proof in task closeout.
- Confirm reviewer acceptance that SQL-seeded proof is backend partial only.
- Keep downstream `AG-DYNUI-FULL-005` open for public workflow materialization.
- Do not cite this task as hosted production E2E proof.
