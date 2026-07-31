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

Codex2 therefore recommends exact-head approval of `2d5f692e...`, subject to
Antigravity's independent review. This owner classification is not the approval
itself. If the PR head moves again, the review fails closed and this comparison
must be repeated.

## Remaining gates

1. Antigravity records an independent approval bound to the full current head.
2. PR #4386 merges into `dev` through the protected merge path.
3. The original task owner completes governed closeout after merge.
4. Only then may downstream L12 closeout count the running-owner guard as
   completed support evidence.

Exact commands and results are archived in `validation.txt`.
