# Task Brief: AG-COMPAT-002-GATE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Finalize Agora cross-repository compatibility gate
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Claude2 independently approved the exact pair, fail-closed gate,
  rollback harness, and hosted read-back. Owner closeout reverified the
  16-test manifest suite and exact manifest against both real repositories.

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

## Hosted Delivery Record (2026-07-24)

- Pantheon PR #4016 merged as
  `e2f7e7356b517844a946b780b373492d98af8c30`, pinning FE
  `e4399e3ec68f882ace35d0349e6597cdd101525f` and BFF
  `00b38f41ec51296762d502c4bd5732f95ccf2953`.
- execute-plans integration-gate run `30003411349` attempt 3 passed.
- execute-plans deploy run `30056451511` attempt 1 rejected an intervening
  live BFF drift before switch and preserved the previous hosted release.
- Pantheon BFF-only restore run `30056916386` passed exact-version and
  restart-persistence probes; deploy run `30056451511` attempt 2 then passed.
- Hosted `deployment.json` and live `/bff/version` expose the exact FE/BFF
  pair. The deployment is accepted, live/strict, read-only, and has both real
  and stub writes disabled.
- Sealed attempt-2 evidence records 26/26 production controller scenarios
  passing, including mismatch no-switch and exact previous-release rollback.

## Owner Finalization Record (2026-07-24)

- Claude2 independently approved the exact manifest, negative gates,
  gate-before-switch behavior, rollback/no-switch harness, workflow runs, and
  hosted read-back.
- Owner closeout reran `scripts/test_agora_compat_manifest.py` with the
  repository virtual environment: 16 passed.
- Owner closeout reran `scripts/agora_compat_manifest.py verify` against the
  Pantheon worktree and `/home/lupin/code/execute-plans`: `ok`.
- Manifest SHA-256 remained
  `494980f204f0af21effc018ebbba657c1027b3052e984577833dfa46ab360bb3`.
