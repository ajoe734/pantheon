# PR #4385 stale-reaper evidence-anchor repair

Task: `SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731`

Owner: `Claude`. Reviewer: `Antigravity`. Delivery PR: #4452.

This packet repairs the concrete referential-integrity defect identified by
PR #4395. Before editing, PR #4385 still pointed at exact head
`f5e70e86e01bde005dae5fed94b151c9bc07f389`. Its subject README and both
machine-readable anchor fields named nonexistent commit
`9d53a94a265c55af4c8d15c50ab3751f1440ac0f`; the actual implementation anchor is
`9d53a94a295d71ee49aea6f4b96e47fbcfd29093`.

## Repair topology

Commit `87dd23dc84552dc58f72bd58cb58b968d358b684` composed the then-current
`origin/dev@93e5b3d4ad0ad94f79bfe512ba3f67402da8d468` with the rejected PR #4385
head. Keeping #4385 as the second parent preserves the actual implementation
anchor in the delivered ancestry. The task branch and PR #4452 supersede #4385 as
the governed delivery path, so the original task's stale `review_approved` row is
never treated as authority for an unreviewed head.

## Delivered change on the current base

Against `origin/dev@eca6b7de6313027d4c943679a1fa8fb7d93028ba` the remaining
delivery is documentation only:

- `docs/.../SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729/README.md` — 1 line
- `docs/.../SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729/evidence.json` — 2 lines
  (`/implementation/anchor_commit` and `/delivery/anchor_commit`)
- this packet's `README.md` and `evidence.json`
- `.orchestrator/task-briefs/sup_l12_stale_reaper_evidence_anchor_repair_20260731.md`

No `.orchestrator/*.py` file differs from `origin/dev`. The reaper implementation
itself reached `dev` independently, through the squash merge
`23ae23c21` of PR #4590. Earlier revisions of this manifest listed
`.orchestrator/supervisor.py` and `.orchestrator/test_supervisor.py` as changed
files; that is no longer true of this head and has been corrected.

## Anchor verification

- `9d53a94a265c55af4c8d15c50ab3751f1440ac0f` does not resolve as a commit.
- `9d53a94a295d71ee49aea6f4b96e47fbcfd29093` resolves, is an ancestor of this
  head, and is the commit that introduces
  `reap_stale_l12_missing_process_failure_streaks`.
- `f5e70e86e01bde005dae5fed94b151c9bc07f389` (rejected #4385 head) is an ancestor
  of this head.
- The invalid SHA no longer appears in the subject packet. It survives only in
  this task's own prose and manifest, where it is named as the defective value.
- `git diff --check origin/dev..HEAD` is clean, the task commit trailer range
  check passes, and `.orchestrator/config.json` has no diff from `origin/dev`.

## Test status is constrained by a dev-level regression

The focused stale-reaper regressions still pass, but they cannot be run against
`origin/dev` as-is. The same squash merge `23ae23c21` (PR #4590) that carried the
reaper into `dev` also reverted `.orchestrator/provider_permissions.py` from 2427
lines to 2006, deleting `provider_auth_probe_due`, which
`.orchestrator/supervisor.py:90` still imports. On `origin/dev` and therefore on
this branch, `import supervisor` raises `ImportError` and every supervisor test
errors at collection.

To obtain real evidence about this task's subject matter, the five focused tests
were run in a working tree whose `provider_permissions.py` was temporarily
restored to its pre-#4590 revision `0f52e40e9`. That restoration was a local
diagnostic only: it was reverted, and the delivered branch contains no code
change. On that tree the five focused stale-reaper regressions pass.

The full 611-test suite is **not** green on that hybrid tree (8 failures, 18
errors, all in provider probe, hysteresis, and codex-cache-quarantine paths).
That is expected, because `dev`'s `supervisor.py` and `test_supervisor.py` expect
the newer `provider_permissions.py` that #4590 removed. No green full-suite
baseline exists on `dev` today, so this manifest does not claim one. Earlier
revisions of this manifest claimed passing 473- and 584-test runs; those runs
predate the regression and are retained below only as historical record.

## Fleet condition, not owned by this task

`23ae23c21` is a stale-base mass-deletion squash. Besides the
`provider_permissions.py` revert it deleted
`.github/workflows/canonical-review-gate.yml` from `dev`; that file is still
present on `master`. Both effects are outside this task's owned layer and are not
repaired here. They are recorded so the reviewer is not misled by the absent
test baseline, and they need a separate Human/Ops remediation.

## Admission boundary

This is owner evidence only. It does not approve PR #4452's current head, merge
the task PR, close the subject task, prove live promotion, or resume Wave 0.
Antigravity must review the exact PR #4452 head produced by this evidence commit
and bind this manifest, after which protected merge and governed owner closeout
remain mandatory.

## Superseded owner records

The `2026-08-01` closeout audit recorded that the then-immutable Antigravity
approval event named reviewed SHA `14487789314c4495e865a7d7ef1aae9c43d70650`,
which does not resolve, while PR #4452 and both task refs resolved to
`1448778931c2058fceb715ad13423e639f5c0865`. That audit stands: the approval was
never valid for a real head. Ownership has since moved from `Codex` to `Claude`
and the reviewer named in the `2026-08-04` revalidation (`Codex2`) is superseded
by the canonical row's reviewer, `Antigravity`.
