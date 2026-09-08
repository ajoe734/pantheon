# Dev Persona create contract hotfix — SA / SD and validation

Task scope: `DEV-PERSONA-CREATE-CONTRACT-001`.
The operator authorized direct implementation of this Persona hotfix, independent
review, the PR workflow and dev redeployment. This is not an authorization to
invent canonical reviewer credentials, operator exact-head acceptance, or hosted
release evidence. No production or real-capital action is included.

## SA: observed failure and scope

[Nonprod deployment run 34219494323](https://github.com/ajoe734/pantheon/actions/runs/34219494323)
failed at the governed paper baseline. Its BFF returned 502 / `UPSTREAM_ERROR`
with `precondition_failed=provisioning_coordination`; bounded hosted diagnostics
identified the underlying Persona owner HTTP 422:

> Persona creation must start in 'draft'; use the governed lifecycle endpoint.

The BFF supplied its coordinator progress (`provisioning`) as the canonical
owner's governed lifecycle. The real owner correctly requires creation in
`draft`. Reproduction through the HTTP write port and actual owner application
failed before the fix with that same 422, not a fabricated error fixture.

A port-only fix also leaves a second deterministic mismatch: owner readback is
`draft`, but the background BFF reconciler only selects `provisioning` or
`provisioning_failed`. The pending ledger must be projected separately so the
controller can complete provisioning without changing owner policy.

## SD: bounded correction

1. At Persona creation, translate only the BFF progress labels `provisioning`,
   `provisioning_failed`, and `paper_running` to owner `draft`. Preserve `draft`;
   leave governed/unknown creation states for the real owner's rejection.
2. Preserve HTTP authentication, actor binding, duplicate conflict checks and
   lifecycle transition endpoints. A retry reads the existing owner, rather
   than resetting its state.
3. Join canonical owner and provisioning ledger only on matching Persona ID,
   explicit tenant and name. A mismatched read cannot relabel the owner; a
   mismatched mutation fails closed.
4. Project ledger progress onto the BFF view for `draft` / `research_only`
   owners and legacy BFF progress labels. Retain `owner_lifecycle_state` when
   the owner supplied a canonical state. Preserve later governed states,
   including frozen and retired; do not reactivate or downgrade them.
5. A terminal `paper_running` view requires the existing materializer's minimum
   proof shape: succeeded ledger, runtime binding ID, runtime ID, authoritative
   readback mapping, and a result explicitly confirming paper running. A state
   string or request metadata alone is insufficient.
6. Directory and controller reads consume current, scoped ledger metadata rather
   than retaining stale owner progress. No owner lifecycle allowlist is widened.

No schema migration, credential rotation, source-writing product API or
supervisor change is required. The failed auto-worker packet was not
materialized; direct implementation does not duplicate an active owner worker.

## Validation and evidence limits

The focused `test_persona_create_owner_contract.py` suite exercises actual
Persona HTTP routing, authentication, policy, JSON owner persistence and a
fresh-process JSON reload. Only socket transport is replaced by TestClient.
Provisioning uses an isolated in-memory ledger through its normal lease/store
protocol. Simulated terminal receipts are explicitly labelled simulation.
These tests are not hosted acceptance or Postgres restart proof.

The suite covers draft-only creation, negative governed-state requests, replay
without downgrade, auth/actor rejection, pending controller eligibility, actual
pending evaluation, stale failed/compensated readback, incomplete success
rejection, tenant/name/ID conflicts, ordinary draft exclusion, and preservation
of later owner lifecycle states. A dedicated `Persona Owner Contract` PR
workflow runs the new contract tests on an isolated GitHub runner.

Adjacent local regression run: 106 passed, 2 failed. The failing Deployment
constructor test and coordinator wire-model test also fail unchanged protected
dev `4decb18ac`. Two packaged-transport tests additionally fail due to missing
strict JWT / owner URL configuration; unchanged dev reproduces both. The
legacy orphan reconciler test has the same package import collection error on
unchanged dev. None of these failures is counted as a pass or repaired by this
bounded change.

In particular, the coordinator wire-model test supplies `registry_entry` and
`approval_decision` fields rejected by the current Deployment request model.
This is a separate pre-existing contract gap requiring owner-path investigation
before claiming a fresh end-to-end provisioning journey. Likewise, the Persona
adapter's tenant metadata is not a fix for the separate immutable top-level
owner `tenant_id` gap.

## Deployment and acceptance still required

After an honestly admitted, merged PR, resolve both protected `dev` tips again
and use the existing `nonprod-deploy.yml` root-component lane, with strict auth,
safe write defaults, shared lease, gate-before-switch and compensation intact.
The only current dev target is `pantheon-dev-20260902` / `pantheon-dev-deploy`
in `asia-east1-b`; FE is `https://app.dev.mvl-cap.tw`, BFF is
`https://api.dev.mvl-cap.tw`. Retired environment text is not deployment authority.

Record the new run, exact pair, immutable artifacts and served identities.
Do not reuse previous acceptance IDs or bypass the governed baseline. Failure
must retain/restore the prior pair and capture bounded diagnostics.

The prior failed run restored BFF `4d7f440c29d8f9057641b680f31e4ecd012f7558`
and left FE `a3bf4060f803d1f8b44f6611e89347d59cd6ae0f` unchanged. That proves
source-pair restoration, not the requested no-rebuild exact-artifact rollback
drill: the public BFF digest was unknown and compensation may rebuild it.

This source hotfix does not establish a completed release, fresh Loops 1–12,
Loop 5 real research provenance, Loop 8 executable RuntimeBinding, Loop 9 paper
lifecycle, authenticated Management/Agora desktop journeys, or Management AI
answer/action/terminal/reload acceptance. Those require new hosted evidence.
