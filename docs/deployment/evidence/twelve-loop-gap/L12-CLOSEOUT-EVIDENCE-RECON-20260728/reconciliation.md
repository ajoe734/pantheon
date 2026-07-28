# L12 Closeout Evidence Reconciliation

Task ID: `L12-CLOSEOUT-EVIDENCE-RECON-20260728`
Owner: `Codex2`
Reviewer: `Codex`
Evidence state: `owner_evidence_ready_for_review`
Repository: `ajoe734/pantheon`
Base: `dev`

## Decision

This task repairs only the immutable recovery evidence for two implementations
that are already merged. It does not restart, amend, or re-run the distillation
or fleet-worker implementation.

At the reconciliation cut, the governed canonical rows are both
`review_approved`:

| Parent task | Owner | Reviewer | Recovery evidence | Delivery commit | Merge commit |
|---|---|---|---|---|---|
| `L12-DIST-001` | `Codex2` | `Codex` | `.orchestrator/task-briefs/l12_dist_001.md` | `e91382b508b42456d75747fdf3cef92c7850d2ad` | `cf94be38a548a31df020456904ea10ff95ffb4dd` |
| `L12-FLEET-WORKER-OUTCOME-001` | `Codex` | `Codex2` | `.orchestrator/task-briefs/l12_fleet_worker_outcome_001.md` | `25f238f94282f2cd8541ff488b003b5e983fd864` | `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0` |

Each repaired task brief now carries the exact heading and metadata required by
`validate_merged_done_evidence`, cites `ajoe734/pantheon`, and cites the full
delivery commit that the recovery command must verify against `origin/dev`.
The evidence commit is supplied only after this PR merges, because the guard
requires the task-brief bytes to be byte-identical to an ancestor of
`origin/dev`.

## Recovery inputs

Human/Ops may reconcile each parent independently after this evidence PR
merges. Commit `64a25069d6cc7548eb8f2bf86331a867d582e199` contains the exact
repaired parent-brief bytes. Use it as `RECONCILE_EVIDENCE_COMMIT` only after
it is an ancestor of `origin/dev`.

The common inputs are:

- `RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon`
- `RECONCILE_DELIVERY_ROOT=<absolute clean Pantheon git root>`
- `RECONCILE_EVIDENCE_COMMIT=64a25069d6cc7548eb8f2bf86331a867d582e199`
- `RECONCILE_EVIDENCE_TARGET_REF=origin/dev`
- `RECONCILE_DELIVERY_TARGET_REF=origin/dev`

Parent-specific inputs:

| Parent task | `RECONCILE_EVIDENCE_FILE` | `RECONCILE_DELIVERY_COMMIT` |
|---|---|---|
| `L12-DIST-001` | `.orchestrator/task-briefs/l12_dist_001.md` | `e91382b508b42456d75747fdf3cef92c7850d2ad` |
| `L12-FLEET-WORKER-OUTCOME-001` | `.orchestrator/task-briefs/l12_fleet_worker_outcome_001.md` | `25f238f94282f2cd8541ff488b003b5e983fd864` |

Only `Human/Ops` may execute `reconcile_merged_done`. This worker must retain
`AI_NAME=Codex2`, so after merge it will record the exact actor-guard output
instead of impersonating Human/Ops.

## Verification

Completed on 2026-07-28:

- Governed `show` confirms both parent rows are `review_approved` with the
  owner/reviewer pairs recorded above.
- `validate_merged_done_evidence` accepted each repaired brief with evidence
  commit `64a25069d6cc7548eb8f2bf86331a867d582e199` targeted at that task commit
  for the pre-merge bytes check and with delivery target `origin/dev`. It
  resolved repository id `pantheon`, slug `ajoe734/pantheon`, and the exact
  parent delivery commit in each case.
- `git merge-base --is-ancestor <commit> origin/dev` passed for
  `e91382b508b42456d75747fdf3cef92c7850d2ad`,
  `cf94be38a548a31df020456904ea10ff95ffb4dd`,
  `25f238f94282f2cd8541ff488b003b5e983fd864`, and
  `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0`.
- GitHub reports PR #4286 merged to `dev` with exact head
  `e91382b508b42456d75747fdf3cef92c7850d2ad` and merge commit
  `cf94be38a548a31df020456904ea10ff95ffb4dd`; PR #4301 merged to `dev`
  with exact head `25f238f94282f2cd8541ff488b003b5e983fd864`
  and merge commit `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0`.
- `<provisioned-python> -m pytest -q scripts/test_ai_status.py -k
  'reconcile_merged_done or merged_done_evidence'` passed: 3 tests,
  149 deselected.
- `<provisioned-python> -m py_compile scripts/ai_status.py
  scripts/test_ai_status.py`, `git diff --check`, and
  `git diff --check origin/dev...HEAD` passed.

Post-merge checkpoint still required: invoke the governed
`reconcile_merged_done` command as `Codex2` and record its exact fail-closed
actor guard. This proves the worker did not impersonate `Human/Ops`; a real
reconciliation remains an operator-only action.

## Non-goals

- No edit to `services/source_ingestion`.
- No edit to `.orchestrator/supervisor.py`, worker runtime, or supervisor tests.
- No ProductEvidence maturity, hosted deployment, or runtime readiness claim.
- No manual edit to canonical status, activity, archive, or dashboard files.
