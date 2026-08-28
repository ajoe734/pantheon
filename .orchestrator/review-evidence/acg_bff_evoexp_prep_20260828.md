# Task Brief: ACG-BFF-EVOEXP-PREP-20260828

Stable review-evidence manifest for the existing
`reconcile_merged_done` recovery path
(`scripts/ai_status.py::validate_merged_done_evidence`). This file reconstructs
the immutable approval and delivery facts already recorded for the original
task. It does not introduce a second router, closeout mechanism, or product-code
change.

The task's current projection is `blocked` only because normal closeout rejected
the squash-merged ancestry. The required `- Status: review_approved` line below
records the canonical lifecycle state reached for the exact original PR head
before that delivery error; it is not a claim that the current projection was
already reconciled.

## Canonical Metadata

- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity
- Title: Prepare canonical Evolution and Experiment routers
- Task class: product
- Target repository: pantheon
- Merge target: dev
- Delivery repository: ajoe734/pantheon
- Original PR: https://github.com/ajoe734/pantheon/pull/5298
- Original approved head: 1aa1978c86703ee6d42d8d186d34ba6b1302a75d
- Actual delivery commit: e31fa6be1db64b2a0404fe1022e65d9b1dc94c38
- PR merged at: 2026-08-28T02:11:30Z

## Original Reviewer Approval

The canonical GitHub review bridge recorded Antigravity's approval for PR 5298
and exact head `1aa1978c86703ee6d42d8d186d34ba6b1302a75d` at
2026-08-28T01:50:56Z. The immutable approval binding is:

- Decision: approve
- Actor: Antigravity
- Mode: required_commit_status
- Repository: ajoe734/pantheon
- Base: dev
- Head branch: task/ACG-BFF-EVOEXP-PREP-20260828
- Head SHA: 1aa1978c86703ee6d42d8d186d34ba6b1302a75d
- Proof ref: refs/tags/pantheon-review/approve/1aa1978c86703ee6d42d8d186d34ba6b1302a75d
- Commit-status context: Pantheon canonical review gate
- Commit-status ID: 53069332473
- Commit-status state: success

This approval is the canonical exact-head commit-status proof recorded by the
review bridge; GitHub's PR review-object list is empty and is not the authority
used for this binding.

The independent reviewer verdict passed the task acceptance: factory-injected
Evolution and Experiment routers and their characterization documents were
present, `main.py` and `read_store.py` were untouched, and all 49 focused and
pre-existing contract tests reported by the review passed.

## Acceptance Bound To The Approval

1. A prepared router contract exists for Evolution programs and actions.
2. A prepared durable Experiment router replaces process overlays.
3. Existing envelopes, filters, pagination, and validation remain
   characterized.
4. The task does not modify `main.py` or `read_store.py`.

## Delivered Artifact And Tree-Equivalence Evidence

PR 5298 was squash-merged into `dev` as the full delivery commit
`e31fa6be1db64b2a0404fe1022e65d9b1dc94c38`. The delivery commit is an ancestor
of the audited `origin/dev` tip
`f983c0d0dc40f3ab7138e2c9a1a76a3e51b5fc41`; the approved head is not an
ancestor because a squash merge does not preserve the head commit as a parent.

The complete task artifact set is:

- `services/control-plane/bff/evolution/CHARACTERIZATION.md`
- `services/control-plane/bff/evolution/__init__.py`
- `services/control-plane/bff/evolution/router.py`
- `services/control-plane/bff/evolution/test_router.py`
- `services/control-plane/bff/research/CHARACTERIZATION.md`
- `services/control-plane/bff/research/__init__.py`
- `services/control-plane/bff/research/router.py`
- `services/control-plane/bff/research/test_router.py`

The following audit commands were run from a clean worktree at that `origin/dev`
tip:

    git merge-base --is-ancestor e31fa6be1db64b2a0404fe1022e65d9b1dc94c38 origin/dev
    # exit 0: the actual delivery is on dev

    git merge-base --is-ancestor 1aa1978c86703ee6d42d8d186d34ba6b1302a75d origin/dev
    # exit 1: the squash merge did not preserve approved-head ancestry

    git diff --exit-code \
      1aa1978c86703ee6d42d8d186d34ba6b1302a75d \
      e31fa6be1db64b2a0404fe1022e65d9b1dc94c38 -- \
      services/control-plane/bff/evolution \
      services/control-plane/bff/research
    # exit 0 with no output: every delivered task artifact is tree-equivalent

This is artifact-complete equivalence for PR 5298, whose GitHub file list is
exactly the eight paths above. It does not substitute a partial file sample for
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
   `e31fa6be1db64b2a0404fe1022e65d9b1dc94c38`.
4. Keep the canonical owner/reviewer identities `Claude` and `Antigravity`.
   A later reassignment requires the separately verified reassignment evidence
   enforced by `validate_merged_done_evidence`.

No impossible precondition remains once this manifest is merged and the command
runtime is refreshed. Neither a new product implementation nor a replacement
review of unchanged content is required.

## Human/Ops Reconcile Recipe

After the preconditions above are true, run exactly one canonical transition:

    RECONCILE_EVIDENCE_FILE=.orchestrator/review-evidence/acg_bff_evoexp_prep_20260828.md \
    RECONCILE_EVIDENCE_COMMIT=<full-dev-ancestor-commit-containing-this-file> \
    RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon \
    RECONCILE_DELIVERY_ROOT=<absolute-current-pantheon-checkout> \
    RECONCILE_DELIVERY_COMMIT=e31fa6be1db64b2a0404fe1022e65d9b1dc94c38 \
    "$PANTHEON_COMMAND_ROOT/scripts/human-ops-status.sh" reconcile_merged_done \
      ACG-BFF-EVOEXP-PREP-20260828 \
      "Reconciled approved PR 5298 after artifact-complete verification of squash delivery e31fa6be1db64b2a0404fe1022e65d9b1dc94c38."

`RECONCILE_EVIDENCE_TARGET_REF` and `RECONCILE_DELIVERY_TARGET_REF` both default
to `origin/dev`; leave them unset unless the canonical target policy changes.
