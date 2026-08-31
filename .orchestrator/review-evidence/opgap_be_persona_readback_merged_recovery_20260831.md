# Task Brief: OPGAP-BE-PERSONA-READBACK-FIXTURE-MIGRATION-20260831

Stable recovery evidence for the already-merged Persona readback delivery.
This record binds the original task lifecycle metadata required by
`reconcile_merged_done`; it is not a replacement product delivery or a new
review decision.

- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Target repository: ajoe734/pantheon
- Approved PR: #5486
- Approved PR head: aef80f11384b265dad14747564fc796810de3785
- Review evidence manifest: docs/deployment/evidence/full-operation-gap/OPGAP-BE-PERSONA-READBACK-FIXTURE-MIGRATION-20260831/evidence.json
- Review evidence manifest blob: c13bbd0a51e578883cba3bc3873e75b57a025ff1
- Independent review: Codex verified the fixture-only diff, frozen manifest, and 55 composed tests at the exact approved head.
- Delivered commit: ec7cc92f60130d41366261e3fdf20236e48845a0
- Merge receipt: canonical auto-integrator merged PR #5486 into dev at ec7cc92f60130d41366261e3fdf20236e48845a0.
- Verification: `services/control-plane/bff/test_persona_provisioning_readback.py` plus `services/control-plane/bff/tests/test_persona_provisioning_read_surface_mutation.py` — 55 passed, 10 pre-existing deprecation warnings.

## Recovery delivery provenance

- Recovery task: OPGAP-BE-PERSONA-READBACK-MERGED-EVIDENCE-RECOVERY-20260831
- Recovery scope: preserve the original task's immutable approval and merge
  receipt only; do not modify its product code, fixture, or reviewed manifest.
- Evidence repository and target: `ajoe734/pantheon`, `origin/dev`.
- Required delivery ancestor: `ec7cc92f60130d41366261e3fdf20236e48845a0`.

The source delivery commit is the merge receipt for approved PR #5486. It must
remain an ancestor of `origin/dev`; the recovery evidence must likewise first
be merged to `origin/dev` before it is supplied to the governed recovery
command. These are reachability checks, not inferred equivalence claims.

## Recovery note

This immutable recovery record preserves the original exact-head approval and
merge receipt after a transient pre-merge blocker was incorrectly reopened.
The recovery changes no product code, does not replace the reviewed manifest,
and exists only so `reconcile_merged_done` can validate the already-merged
delivery without hand-editing canonical task state.

## Human/Ops reconciliation preconditions

After this exact evidence file is merged, `Human/Ops` (or the original task's
current reviewer when permitted by the governed command) must provide its
full dev-ancestor evidence commit, this repository-relative path, and the
delivery commit above to `reconcile_merged_done`. The command revalidates the
original task id, `review_approved` status, Codex2/Codex identity binding,
repository identity, and both merged ancestries. No recovery action may alter
the original approval head or manufacture canonical state by hand.
