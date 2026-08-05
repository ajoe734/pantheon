# PR #4386 exact-head reconciliation

Task: `SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731`

Owner: Codex2

Reviewer: Antigravity

Review manifest: `evidence.json`

## Outcome

PR #4386 remains open at
`2d5f692e960a22eef7c4b6d63002996a68468079`. The canonical approval for
`SUP-L12-RUNNING-OWNER-RECONCILE-20260729` names the stale pre-rebase head
`0528e5cab1df5386adfdb3113b8653411635fe86`, so that row alone is not
countable as completed L12 support evidence.

The exact tree comparison classifies every difference between the two heads:

- `.orchestrator/task-briefs/sup_l12_fleet_dispatch_readback_20260729.md`
  and the matching fleet-dispatch `evidence.json` were inherited from the new
  `dev` base `6f87a207eabf5c6121a59cae1bb8bc5bbc5cbf8e`. Their blobs are identical
  to the base and they add no PR-owned runtime behavior.
- The running-owner evidence README only removes three Markdown trailing
  double spaces. `git range-diff` maps the first two task commits exactly and
  shows only this formatting delta in the third commit.
- `.orchestrator/supervisor.py`, `.orchestrator/test_supervisor.py`, the
  running-owner task brief, `evidence.json`, and `validation.txt` have identical
  blob IDs at both heads.

Codex2 recommended exact-head approval of `2d5f692e...`. Antigravity then
independently approved that exact head and this evidence manifest at
`2026-07-31T12:22:57Z`. If the PR head moves again, the review fails closed and
this comparison must be repeated.

The task-scoped delivery is ReviewBus PR #4396. Its reviewed anchor is
`c4346b8d53941d665acd931d32a98b3802b1e7b2`; the owner closeout commit records
the independent decision without changing the comparison or runtime scope.

## Remaining gates

1. PR #4386 merges into `dev` through the protected merge path.
2. The original task owner completes governed closeout after merge.
3. Only then may downstream L12 closeout count the running-owner guard as
   completed support evidence.

Exact commands and results are archived in `validation.txt`.
