# Task Brief: ACG-ENTRYPOINT-WORKER-20260828

Stable review-evidence manifest for the existing
`reconcile_merged_done` recovery path
(`scripts/ai_status.py::validate_merged_done_evidence`). This file reconstructs
the immutable approval and delivery facts already recorded for the original
task. It does not add another launcher, fallback, closeout mechanism, or
product-code change.

The task's current projection is `blocked` only because normal closeout rejected
the squash-merged ancestry. The required `- Status: review_approved` line below
records the canonical lifecycle state reached for the exact original PR head
before that delivery error; it is not a claim that the current projection was
already reconciled.

## Canonical Metadata

- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity2
- Title: Repair the Agora interaction worker package entrypoint truthfully
- Task class: product
- Target repository: pantheon
- Merge target: dev
- Delivery repository: ajoe734/pantheon
- Original PR: https://github.com/ajoe734/pantheon/pull/5292
- Original approved head: ebe0c94505ff5ccb6ca8c829070e10b17d22d48b
- Actual delivery commit: 7a9674ea259bbac883e42f3ee217b3e8f68170fe
- PR merged at: 2026-08-28T02:14:30Z

## Original Reviewer Approval

The canonical GitHub review bridge recorded Antigravity2's approval for PR 5292
and exact head `ebe0c94505ff5ccb6ca8c829070e10b17d22d48b` at
2026-08-28T01:15:57Z. The immutable approval binding is:

- Decision: approve
- Actor: Antigravity2
- Mode: required_commit_status
- Repository: ajoe734/pantheon
- Base: dev
- Head branch: task/ACG-ENTRYPOINT-WORKER-20260828
- Head SHA: ebe0c94505ff5ccb6ca8c829070e10b17d22d48b
- Proof ref: refs/tags/pantheon-review/approve/ebe0c94505ff5ccb6ca8c829070e10b17d22d48b
- Commit-status context: Pantheon canonical review gate
- Commit-status ID: 53068153605
- Commit-status state: success

This approval is the canonical exact-head commit-status proof recorded by the
review bridge; GitHub's PR review-object list is empty and is not the authority
used for this binding.

The independent reviewer verdict passed the task acceptance: launcher imports
worked from the repository root and a foreign working directory, Persona
discovery used the typed `ReadSurfaceStore` adapter, missing dependencies failed
truthfully without the nonexistent `FastBffReadStore` or catch-all empty
fallback, Compose and workflow files were untouched, and all 18 focused tests
reported by the review passed.

## Acceptance Bound To The Approval

1. Re-audit the exact launcher landed by `PFG-HOSTED-ACCEPT-20260820`.
2. The launcher works from the repository-root container workdir and an
   arbitrary working directory.
3. Persona discovery uses a typed canonical client, and a missing dependency
   fails truthfully.
4. The nonexistent `FastBffReadStore` and catch-all empty fallback are absent.
5. The task does not modify `docker-compose.yml` or deployment workflows.

## Delivered Artifact And Tree-Equivalence Evidence

PR 5292 was squash-merged into `dev` as the full delivery commit
`7a9674ea259bbac883e42f3ee217b3e8f68170fe`. The delivery commit is an ancestor
of the audited `origin/dev` tip
`f983c0d0dc40f3ab7138e2c9a1a76a3e51b5fc41`; the approved head is not an
ancestor because a squash merge does not preserve the head commit as a parent.

The complete task artifact set is:

- `scripts/run_agora_interaction_worker.py`
- `scripts/test_run_agora_interaction_worker.py`
- `services/control-plane/bff/agora/interaction/persona_client.py`

The following audit commands were run from a clean worktree at that `origin/dev`
tip:

    git merge-base --is-ancestor 7a9674ea259bbac883e42f3ee217b3e8f68170fe origin/dev
    # exit 0: the actual delivery is on dev

    git merge-base --is-ancestor ebe0c94505ff5ccb6ca8c829070e10b17d22d48b origin/dev
    # exit 1: the squash merge did not preserve approved-head ancestry

    git diff --exit-code \
      ebe0c94505ff5ccb6ca8c829070e10b17d22d48b \
      7a9674ea259bbac883e42f3ee217b3e8f68170fe -- \
      scripts/run_agora_interaction_worker.py \
      scripts/test_run_agora_interaction_worker.py \
      services/control-plane/bff/agora/interaction/persona_client.py
    # exit 0 with no output: every delivered task artifact is tree-equivalent

This is artifact-complete equivalence for PR 5292, whose GitHub file list is
exactly the three paths above. It does not substitute a partial file sample for
the delivery comparison.

## Why Normal Closeout Cannot Be Replayed

Normal `done` validates that the approved head itself is an ancestor of
`origin/dev`. That invariant is deliberately exact-head-bound and cannot be
satisfied by the squash commit even though its task artifacts are identical.
Changing the delivery binding to the squash commit would discard the original
review identity, while adding another product commit would add no functional
value. The existing evidence-based reconciliation path preserves both truths:
the exact reviewed head and the actual `dev` delivery commit.

## Reconcile Preconditions

1. Merge this exact file to `dev` and retain the full commit that first contains
   these bytes as the evidence commit.
2. Refresh `PANTHEON_COMMAND_ROOT` to a tracked `dev`-ancestor runtime containing
   the same file bytes. A command runtime predating this manifest cannot pass
   evidence validation.
3. Use an absolute, non-symlink Pantheon delivery checkout whose `origin` is
   `ajoe734/pantheon`, and fetch `origin/dev` so it contains
   `7a9674ea259bbac883e42f3ee217b3e8f68170fe`.
4. Keep the canonical owner/reviewer identities `Claude` and `Antigravity2`.
   A later reassignment requires the separately verified reassignment evidence
   enforced by `validate_merged_done_evidence`.

No impossible precondition remains once this manifest is merged and the command
runtime is refreshed. Neither a new product implementation nor a replacement
review of unchanged content is required.

## Human/Ops Reconcile Recipe

After the preconditions above are true, run exactly one canonical transition:

    RECONCILE_EVIDENCE_FILE=.orchestrator/review-evidence/acg_entrypoint_worker_20260828.md \
    RECONCILE_EVIDENCE_COMMIT=<full-dev-ancestor-commit-containing-this-file> \
    RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon \
    RECONCILE_DELIVERY_ROOT=<absolute-current-pantheon-checkout> \
    RECONCILE_DELIVERY_COMMIT=7a9674ea259bbac883e42f3ee217b3e8f68170fe \
    "$PANTHEON_COMMAND_ROOT/scripts/human-ops-status.sh" reconcile_merged_done \
      ACG-ENTRYPOINT-WORKER-20260828 \
      "Reconciled approved PR 5292 after artifact-complete verification of squash delivery 7a9674ea259bbac883e42f3ee217b3e8f68170fe."

`RECONCILE_EVIDENCE_TARGET_REF` and `RECONCILE_DELIVERY_TARGET_REF` both default
to `origin/dev`; leave them unset unless the canonical target policy changes.
