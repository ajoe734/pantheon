# Task Brief: AG-COMPAT-002-GATE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Finalize Agora cross-repository compatibility gate
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Next: Antigravity verification was insufficient: it only verified stale accepted e4399e3/19d8352 and made no delivery. Required owner delivery is exact: update Pantheon dev compatibility manifest to FE e4399e3ec68f882ace35d0349e6597cdd101525f and BFF 00b38f41ec51296762d502c4bd5732f95ccf2953 in a clean task PR to dev; merge with checks; rerun execute-plans integration gate run 30003411349 to a new attempt; trigger/verify the corresponding FE deploy; prove hosted deployment.json and live /bff/version expose that same exact pair with strict live read-only defaults; update closeout artifacts; then hand off to Claude2. Do not run any bulk dispatch script or create tasks.

## Summary
把 pending/zero placeholder manifest 換成 exact FE/BFF pair，部署前驗證 commits/hashes/dev reachability，失配 gate-before-switch 並測 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Implementation Record (2026-07-23, review changes addressed)

- Pantheon anchor `948534e1030f594d94a02b8feaafab465b77356f`
  makes the protected deployment controller check out the candidate payload
  separately, passes its exact `TARGET_SHA` as `--backend-runtime-commit`, and
  binds both the candidate commit and Git tree in accepted gate evidence.
- The accepted evidence also binds the manifest digest, protected controller
  commit/tree, exact frontend commit/tree, backend commit/tree, source
  handoffs, and hash policy. Evidence is emitted only after the accepted,
  blocker-free gate passes.
- execute-plans anchor
  `f8ff56a6` consumes that evidence on the real hosted release path before
  `deploy-dev-vm.sh` can create a release directory or change the live
  symlink. The write-activation and watchdog-restore paths regenerate and
  revalidate evidence through a protected Pantheon `dev` controller.
- `deploy-dev-vm.sh` rejects missing, malformed, pending, rejected, or
  FE/BFF-mismatched evidence before any switch, copies the evidence into the
  audit trail without following symlinks, and embeds its identities in the
  active release manifest.
- Production release-harness coverage drives the actual deployment controller:
  pending, rejected, FE mismatch, and BFF mismatch leave both the hosted
  symlink and active manifest unchanged; the rollback drill restores the exact
  previous symlink and byte-identical manifest.

Verification:

- Pantheon focused pytest suite: 47 passed.
- Exact clean `origin/dev` deployment gate with evidence output: passed.
- execute-plans `npm run test:deploy-release`: 7 atomic symlink CAS, 5 release
  manifest, and 26 production deploy-controller scenarios passed.
- Both edited GitHub workflows parse with PyYAML; edited shell scripts pass
  `bash -n`; `release-evidence.mjs` passes `node --check`; both repositories
  pass `git diff --check`.
