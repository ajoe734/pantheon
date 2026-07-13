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
