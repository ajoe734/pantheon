# PR #4385 exact-head reconciliation

Task: `SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731`

This packet compares the canonical stale-reaper reviewed head
`86dd9006b15bba67a83886e4a2d672e511aa8709` with PR #4385's exact current
head `f5e70e86e01bde005dae5fed94b151c9bc07f389`. It does not treat the
underlying task's existing `review_approved` row as proof for the newer head.

## Decision

**Reopen required; exact-head approval at `f5e70e86...` is not eligible.**

The runtime change remains patch-equivalent to the previously reviewed
implementation, the five focused regressions pass, and the full 462-test
supervisor suite passes. However, the current head's README and machine-readable
manifest both bind the implementation anchor to
`9d53a94a265c55af4c8d15c50ab3751f1440ac0f`. That object does not exist. The
actual rebased anchor is
`9d53a94a295d71ee49aea6f4b96e47fbcfd29093`.

Antigravity independently confirmed this packet against task PR #4395 head
`607a474688566b1a62c4ec24998c4d6864d62a88` at
`2026-07-31T12:25:16Z`. The review approved this reconciliation decision while
rejecting PR #4385 head `f5e70e86...` for exact-head approval. The subject task
`SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729` must be reopened with the
concrete correction. After the correction changes PR #4385's head, the
resulting new exact head must be reviewed again. If the PR is abandoned
instead, it may be superseded with an explicit replacement. The current head
must not be counted as a Wave 0 dependency, merged, root-frozen, or
governed-closeout finalized on the strength of the old approval.

## Head topology and path classification

`git range-diff` proves that the two original stale-reaper commits were rebased
without patch changes:

- `b41a69c1...` = `9d53a94a...` (implementation anchor)
- `86dd9006...` = `833c3658...` (original evidence commit)
- `f5e70e86...` is an additional commit after the rebased pair

The direct old-head/current-head tree diff contains 18 paths because the branch
was rebased from `575040212...` onto `dev@6f87a207...`.

| Class | Count | Meaning |
|---|---:|---|
| Base-only rebase delta | 14 | Already present in `dev@6f87a207...`; includes `branch-ci.yml`, `publish-promote.yml`, publish helper/tests, and other L12 evidence. These paths are not in `origin/dev...f5e70e86`. |
| New `f5e70e86...` delta | 4 | Stale-reaper task brief, one reviewer-redispatch test, README refresh, and evidence manifest refresh. |
| Whole PR delta vs current `dev` | 5 | Supervisor implementation plus its test, brief, README, and evidence manifest. `.orchestrator/config.json` is absent. |

Every path and its classification is recorded in
[`evidence.json`](evidence.json).

## Required correction

1. Replace both invalid anchor references with the actual full SHA
   `9d53a94a295d71ee49aea6f4b96e47fbcfd29093`.
2. Push the narrow correction to PR #4385; this necessarily creates a new head.
3. Re-run the focused and full supervisor validations on that new head.
4. Obtain independent review bound to that exact PR number/head/base and record
   the decision in the reviewed evidence manifest.
5. Only after protected merge may the underlying owner run governed closeout;
   root-freeze remains a separate required gate.

No `.orchestrator/config.json` change is authorized or present in this task.
