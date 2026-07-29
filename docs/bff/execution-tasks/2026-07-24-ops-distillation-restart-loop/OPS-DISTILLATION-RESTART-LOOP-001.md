# OPS-DISTILLATION-RESTART-LOOP-001 — Isolate the always-on distillation controller

Priority: P0
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Codex2

## Incident

The replacement dev VM ran `strategy-distillation-worker` with both
`SOURCE_INGEST_CONTROLLER_MAX_TICKS=1` and `restart: unless-stopped`. Each
process completed one successful tick with exit code 0, then Docker restarted
it. The bounded value belongs to the opt-in source-ingest scheduler and leaked
into the always-on strategy-distillation service through a shared Compose
interpolation name.

## Scope

- Give strategy distillation its own host-side max-ticks setting while
  retaining the controller's existing container contract.
- Preserve the bounded source-ingest scheduler default of one tick.
- Add a Compose contract regression test.
- Deploy the merged `dev` commit through the governed non-production workflow.

## Acceptance

- With `SOURCE_INGEST_CONTROLLER_MAX_TICKS=1` in the deployment environment,
  rendered Compose gives source-ingest scheduler `1` and strategy distillation
  `0`.
- `strategy-distillation-worker` remains running across multiple intervals
  without an exit/restart cycle.
- Controller state advances normally and the alive/freshness marker remains
  current.
- Focused tests and Compose validation pass, the PR merges to `dev`, and the
  governed dev deployment records exact commit and runtime evidence.

## Exclusions

- No provider egress or production/live-capital changes.
- No change to source-ingest scheduler bounded-run semantics.
