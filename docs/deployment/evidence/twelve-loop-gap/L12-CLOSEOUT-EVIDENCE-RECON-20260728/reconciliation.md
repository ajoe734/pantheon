# L12 Closeout Evidence Reconciliation

Task ID: `L12-CLOSEOUT-EVIDENCE-RECON-20260728`
Owner: `Codex2`
Reviewer: `Codex`
Evidence state: `owner_evidence_draft`
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
merges. The common inputs are:

- `RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon`
- `RECONCILE_DELIVERY_ROOT=<absolute clean Pantheon git root>`
- `RECONCILE_EVIDENCE_COMMIT=<this evidence PR head or merge commit on origin/dev>`
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

Pending after the anchor commit:

- parse the governed parent rows and validate each repaired evidence file
  through `validate_merged_done_evidence` with the task commit as the evidence
  target and `origin/dev` as the delivery target;
- confirm both delivery commits and both merge commits are ancestors of
  `origin/dev`;
- confirm PR #4286 and PR #4301 exact head/merge identities from GitHub;
- run focused reconciliation tests and `git diff --check`;
- after merge, invoke the governed recovery command as `Codex2` and record its
  exact fail-closed actor guard.

## Non-goals

- No edit to `services/source_ingestion`.
- No edit to `.orchestrator/supervisor.py`, worker runtime, or supervisor tests.
- No ProductEvidence maturity, hosted deployment, or runtime readiness claim.
- No manual edit to canonical status, activity, archive, or dashboard files.
