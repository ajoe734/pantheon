# Task Brief: FE-SECRET-BOUNDARY-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reland EP PR #311 auth boundary (resolve 5-file conflict, unfreeze dev FE deploy)
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Closeout in progress. `execute-plans` PR #329 is merged and deployed;
  wait for integration run `29396886716` to finish, confirm the reviewed
  exceptions have not changed shape, then finalize the Pantheon task PR and
  run the canonical `done` command.

## Summary
解 execute-plans PR #311 衝突並落地 public/hosted auth 邊界，恢復 dev FE 部署

## Closeout evidence (Codex, 2026-07-15)

### Integration and merge

- The stale reviewed head `d89e9f680104650cf96f2715feab346dbc4ee5ea`
  was composed with current `execute-plans/dev` twice as the base advanced.
- Eight conflicts were resolved (five hosted E2E specs, deploy/probe scripts,
  and deploy-safe-default tests) while preserving the reviewed public secret
  boundary and current dev provenance/rollback behavior.
- Final task head: `e8c26b3141cac088ae4d851bdbb2ac28e304c423`.
- `ajoe734/execute-plans#329` merged into `dev` at
  `b352faa087e6e1bd6087c619d6e9d99a35fbca41` on
  `2026-07-15T07:23:34Z`.
- Required branch checks passed: Commit trailers, Generated files guard, and
  Smoke acceptance. Informational FE-BFF integration run `29396886716` is
  still in progress; this anchor does not close the task.

### Local verification

- `npm test -- --reporter=dot --pool=threads --maxWorkers=8 --minWorkers=1`:
  156 files and 1503 tests passed.
- Focused Vitest: 5 files and 116 tests passed; the final Strategy Workshop
  sync rerun passed 30 tests.
- `npm run lint`, `npx tsc --noEmit`, safe production build, deploy rollback
  harness, release-identity checks, focused ESLint, Playwright discovery, and
  bundle secret scans passed.
- The default Vitest fork pool passed all 1503 assertions twice but exited on
  a worker RPC `onTaskUpdate` timeout; the bounded thread-pool run above
  completed cleanly and no test assertion failed.

### Dev deployment acceptance

- Push deployment run `29397197580` completed successfully for merge SHA
  `b352faa087e6e1bd6087c619d6e9d99a35fbca41`.
- Hosted `deployment.json` reports that exact FE SHA, BFF SHA
  `a10f752b3ea4420f271535e255f2d4e7d3d498b2`, the intended Pantheon dev BFF,
  `live` / `strict`, both real and dev-stub writes disabled, and no embedded
  bearer token.
- `node scripts/probe-hosted-browser-bff.mjs` passed against the hosted FE with
  no credential: `/bff/me` and Persona Fleet returned 401/AUTH_REQUIRED,
  browser requests carried no Authorization header, the bundle contained no
  dev bearer literal, the old BFF had zero hits, and health/readiness passed.

### Scope boundary

- Owned: `execute-plans` public-build/hosted auth boundary and its composition
  with current dev deployment/provenance behavior.
- Not changed: Pantheon BFF grants, credential issuance, live secrets, or
  write authorization policy.
- Reviewer exceptions in
  `.orchestrator/reviews/FE-SECRET-BOUNDARY-002-review-claude.md` remain the
  acceptance basis until the current informational integration run finishes.
