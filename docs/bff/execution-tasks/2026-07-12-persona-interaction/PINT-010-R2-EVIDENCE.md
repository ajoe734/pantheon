# PINT-010-R2 hosted integration evidence

Date: 2026-07-13 UTC
Owner: Codex2
Reviewer: Codex
Status: blocked; hosted completion is not yet proven

## Proven in this remediation pass

- Pantheon PR #3480 is merged into `dev` at
  `ca36f1209e401c7ed1953003c60295dd56b54c9f`. Its branch checks passed and
  it records the PINT-006 frontend handoff.
- execute-plans PR #275 is merged into `main` at
  `ff195d8166a5be5bb928b86dfb103afc706bdf9c`.
- The Pantheon-owned BFF health endpoint returned HTTP 200 with
  `live=true`, `ready=true`, and all reported dependencies healthy.
- The Pantheon-owned frontend deployment record returned strict-live settings:
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and
  `VITE_BFF_REAL_WRITES=false`.
- Focused BFF interaction, identity-scope, and router tests passed (28 tests):

  ```text
  pytest -q \
    services/control-plane/bff/tests/test_agora_persona_interactions.py \
    services/control-plane/bff/tests/test_agora_identity_scope.py \
    services/control-plane/bff/tests/test_agora_router.py
  ```

These tests cover idempotent versioned context resolution, participant
eligibility exclusions, typed submission with `execution_authority=none`,
cross-tenant failure, Agora-only capability scope, and authenticated route
behavior. They are local contract/security evidence, not hosted browser proof.

## Blocking evidence

The deployed frontend does not currently prove the PINT-006 merge:

- Hosted `deployment.json` reports execute-plans commit
  `d335a0e70811b7d49fa630ddfe323e35929613b9` from source branch `dev`.
- PINT-006 PR #275 merged to execute-plans `main`, not `dev`.
- GitHub compare between the PINT-006 merge and the deployed SHA reports the
  histories as `diverged` (`ahead_by=439`, `behind_by=18`). Therefore ancestry
  cannot establish that the deployed bundle contains #275.
- The FE-BFF integration gate attached to execute-plans PR #275 failed (run
  `29198329220`). The PINT-005, PINT-007, and PINT-008 PR gates also failed.
  A later execute-plans `dev` gate passed at deployed SHA `d335a0e...` (run
  `29208034260`), but that does not substitute for proving the PINT feature
  commits are present in the deployed branch.

Because of this branch/deployment split, authenticated desktop/mobile hosted
E2E for one-Persona ask, red-team consultation, visible disagreement, proposal
revision, paper validation, Trading Room linkage, Journal reflection, audit
readback, and degraded/rollback behavior is not yet valid completion evidence.

## Required next action

The execute-plans delivery owner must reconcile the PINT-005 through PINT-009
feature commits onto the actual hosted delivery branch using a reviewed,
scoped PR. Do not merge the divergent branches wholesale as a closeout shortcut.
After deploying the reconciled GitHub-visible commit, rerun the authenticated
desktop/mobile persona-interaction E2E and authority-negative suite. Record the
frontend and BFF deployed SHAs, deployment run, integration-gate run, hosted
browser evidence, audit readback, and rollback/degraded proof before moving
PINT-010-R2 to review.

## Explicitly unproven claims

PINT-010-R2 does not claim hosted feature completion, full cross-repository
compatibility, or program closeout. PR #3480 is evidence that the PINT-006
handoff was recorded; it is not evidence that execute-plans PR #275 is present
on the hosted dev frontend.

## Re-verification 2026-07-13T01:05Z (owner: Claude)

All six PINT-010-R2 dependency tasks (`PINT-003`, `PINT-004`, `PINT-005`,
`PINT-007`, `PINT-008`, `PINT-009`) now show `status: done` in the task
archive, and `PINT-006` shows `terminal_outcome: superseded`. That closes the
task-tracking dependency graph, but the underlying branch-split blocker
documented above is unchanged and independently reconfirmed today:

- `execute-plans` `default_branch` is `dev`; that is what
  `pantheon-lupin-dev-fe` deploys from.
- `dev` HEAD is still `d335a0e70811b7d49fa630ddfe323e35929613b9` (last moved by
  merged PR #281, `TJ-E2E-009`, at 2026-07-12T20:37Z). The live
  `deployment.json` on `pantheon-lupin-dev-fe` confirms this SHA and
  `sourceBranch: dev`.
- `main` HEAD is `3a9a75500e38912d23e1bb35e980c31fef563854` (PR #283,
  `PINT-008`). PRs #275 (`PINT-006`), #276 (`PINT-005`), #278 (`PINT-007`),
  and #283 (`PINT-008`) all merged into `main`, not `dev`. Only PR #277
  (`PINT-009`) merged into `dev`.
- `gh api repos/ajoe734/execute-plans/compare/dev...main` reports
  `status: diverged`, `ahead_by: 28` (commits on `main` not on `dev`,
  including the PINT-005/006/007/008 chain), `behind_by: 439` (unrelated
  commits on `dev` not on `main`).
- A GitHub code search on this checkout for `ProposalCard` (a PINT-006 UI
  artifact) returns no hits, and `dev`'s own commit log tail shows only
  `TJ-E2E-009`-series work, not any `PINT-005`..`PINT-008` merge commit.
- No open `execute-plans` PR reconciles `main` into `dev`; open PRs (#284,
  #285, #286, #264, #222, #131, #122, #78) are unrelated maintenance/feature
  work, and #258 targets `main`, not `dev`.
- Pantheon BFF (`/healthz`: `live=true`, `ready=true`) and the focused BFF
  suite (28 tests: `test_agora_persona_interactions.py`,
  `test_agora_identity_scope.py`, `test_agora_router.py`) both still pass,
  reconfirming the Pantheon-owned half of the stack is unaffected.

Conclusion: the blocker from the prior remediation pass is still open and
unresolved by any other lane. PINT-010-R2 cannot record hosted E2E, dev
deploy, or program closeout evidence for PINT-005 through PINT-008 UX because
those commits are not present on the branch `pantheon-lupin-dev-fe` deploys
from. This task remains blocked on the same required next action: the
`execute-plans` delivery owner (human or a dedicated cross-repo lane) must
open and merge a reviewed, scoped `main` -> `dev` reconciliation PR in
`ajoe734/execute-plans`, after which PINT-010-R2 can rerun hosted E2E against
the reconciled deployed commit. A background Pantheon worker should not
attempt that reconciliation unilaterally: it spans 28+439 diverged commits on
a branch that auto-deploys to shared hosted infrastructure, and a wrong merge
would corrupt the currently-healthy hosted dev environment.
