# Task Brief: AG-COMPAT-002-GATE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Finalize Agora cross-repository compatibility gate
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Reviewer changes required: (1) cryptographically bind the actual Pantheon deployment payload TARGET_SHA/tree to the accepted manifest pair; the workflow currently omits --backend-runtime-commit, so a later arbitrary dev payload passes even though manifest backend.runtime_commit is 6e08b040eebd2c317a9b44741d8badbf878e26ad. (2) Put the compatibility decision on the real execute-plans hosted switch path before deploy-dev-vm.sh, or supply equivalent immutable gate evidence consumed there; pantheon nonprod-deploy only guards the BFF VM path. Replace the synthetic no-switch/rollback tests that manually mutate symlinks inside the test with production release-harness coverage proving pending/rejected cannot change the hosted symlink/active manifest and rollback restores the prior accepted pair. Preserve the passing exact clean origin/dev gate and 47 focused tests.

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
