# Dev Persona provisioning: Capital identity and Deployment wire repair

Task scope: DEV-OWNER-CONTRACT-002. Product repair authorized by the operator
after the failed deployment of PR 5687. This is a direct-delivery task branch,
not a fabricated canonical auto-worker task or completed hosted acceptance.

## Analysis and observed failure

Deployment run [34234694118](https://github.com/ajoe734/pantheon/actions/runs/34234694118)
deployed BFF commit `39a3cbddd9fe479b23a7a63c28e0953889e5c417` but failed its
governed paper baseline. At 2026-09-08T14:20:16Z the BFF returned HTTP 502,
`UPSTREAM_ERROR`, precondition `capital_pool`. A new read-only PostgreSQL
connection after automatic rollback recovered the failed ledger receipt:

- tenant `tenant-dev`, Persona `persona-d57f6d76254c20418a9e`;
- fixed idempotency key `dev-paper-bootstrap-20260720-operator-a-v3`;
- state `failed`, step `capital_pool_failed`, attempt count 16;
- upstream mutation error `HTTP Error 403: Forbidden`;
- no persisted owner reference or compensation receipt keys.

The actual transport lost the structured HTTP error body. No hosted traceback
or captured `ACTOR_ID_MISMATCH` body is claimed. Independently invoking Capital's
real mutation binder reproduces that error for the production mismatch:
the coordinator sends `pantheon-persona-provisioner`, while the verified BFF
service JWT subject is `control-plane-bff` and contains no delegated actor.

The separate Deployment contract defect is deterministic: plan POST includes
`registry_entry` and `approval_decision`, while dispatch POST includes
`registry_entry`; the authoritative request models forbid all three fields.

## Design and scope

Use one explicit BFF service actor identity for transport and both production
coordinator constructors, including compensation. Preserve human `requested_by`
as audit metadata. Do not weaken Capital's actor, role, tenant or service checks,
invent delegation claims, or alter unrelated worker identities.

Remove the forbidden Deployment snapshot fields. Keep exact Registry and
Governance IDs, versions, digests, paper-only stage and rollback identity.
Deployment still reads Registry/Governance owner state using its own verified
reader principals; client snapshots never become authority. Keep `extra=forbid`.

The implementation worker is a real authenticated Claude CLI session in the
isolated task worktree; Codex owns integration/review. No concurrent source
worker was dispatched for these same files. The existing broad DEV502-FIX task
was still waiting on dependencies, with no matching active PR found. Other
supervisor-tooling and Loop-truth PRs are not part of this change.

## Required verification

- Real strict Capital HTTP owner create, readback through fresh persistence,
  same-key replay, no-reference failure retry and semantic conflict behavior.
- Wrong actor, ungranted role, wrong service, foreign tenant and missing/expired
  token rejection before successful mutation.
- Both create and compensation production constructors use the same service
  principal as the transport, without changing human audit metadata.
- Real Deployment HTTP request/handler validation, exact owner-reader fixtures,
  durable readback, rejected embedded snapshots and invalid approval negatives.
- Existing Persona draft-create contract tests and relevant provisioning suites.
- Protected-branch CI and genuine review/acceptance of the new exact head.

Local fixture tokens and simulated owner/runtime events are test-only evidence.
They are not hosted receipts, real research, executable paper RuntimeBindings,
or a successful twelve-loop fresh-stimulus chain.

### Executed local verification (2026-09-08)

- The exact proposed CI selection passed: **95 passed**, one existing Starlette
  deprecation warning. An independent Codex reviewer repeated it: **95 passed**.
- The expanded selection including provisioning store/readback passed:
  **167 passed**, three warnings. The 38 new Capital/Deployment cases are included
  in both selections, not additional independent hosted acceptance results.
- Negative dispatch tests assert both an empty durable saga list and an empty
  outbox after an approval expires, is revoked, changes digest or changes tenant.
  They preserve the owner's existing HTTP 400 dispatch / HTTP 422 create split.
- Compile, workflow YAML parsing and `git diff --check` passed.
- The older route-security suite is **not green**. On a separately provisioned,
  untouched worktree at `39a3cbddd9fe479b23a7a63c28e0953889e5c417`, the comparable
  selection reproduced **17 failed, 130 passed**: the same 15 old route-harness
  failures plus two failures repaired here (forbidden Deployment wire fields and
  an obsolete Deployment service constructor in the persistence test). The old
  route failures concern missing Capital fixture configuration and a missing
  Persona readback snapshot; no full-suite or deployed success is inferred.

Claude completed the initial source repair and test implementation, then its
authenticated CLI returned a quota error. Codex completed the bounded review
corrections, additional forward/retry and authorization-negative assertions,
CI and documentation after that worker stopped. The independent source review
approves only this scope, not canonical task attestation or hosted acceptance.

## Deployment prerequisites discovered before retry

Read-only dev inspection found both `DEPLOYMENT_REGISTRY_SERVICE_TOKEN` and
`DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN` unconfigured. Deployment also had no runtime
or owner JWT signing keys configured. Only presence booleans were printed.
No existing reader credential was found among dev/repository GitHub secret names.
An authorized scoped product-reader issuance and delivery path is required;
the development-task execution-grant issuer is not that product authority.
The Registry container likewise has `REGISTRY_GOVERNANCE_BASE_URL` configured
but no `REGISTRY_GOVERNANCE_SERVICE_TOKEN`. Its owner-approval transition also
depends on a genuine read principal. The same bounded check found Registry's
JWT key present but its service-specific issuer/audience absent; no inference
about fallback values or verified read success is made from those booleans alone.

Independent source review also identified separate Governance caller gaps:

- proposal `owner_user_id` is populated from human `requested_by`, but strict
  Governance requires it to equal the verified service subject;
- review/decision requests declare `automated_gate`, absent from the transport's
  current granted role claims;
- Governance requires `Idempotency-Key`, while these coordinator calls omit it.
  Its strict body models do not permit fixing this by adding an arbitrary field.
- Approval requests omit `expires_at` and `candidate_digest`, while Registry's
  approval verifier requires a current expiry and its exact canonical checksum.
- The forward StrategySpec revision lacks `parent_registry_ids`; Registry
  requires explicit parent identity for a noninitial revision of the same strategy.

These are explicit follow-up prerequisites, not silently fixed by loosening
authentication, self-granting an automation role, or forging owner decisions.
Do not dispatch another unchanged dev deployment and expect these gates to pass.

Safe follow-up requires either an explicitly authorized tenant-scoped product
automation principal with low-risk approval authority, or a proposer-only flow
that waits for an already-authorized reviewer to issue a real decision. Neither
the development-task execution-grant issuer nor a read-only consumer token may
be substituted for that product write authority. Keep the Registry expiry,
checksum and parent/version validation intact.

### Ordered follow-up execution plan (not canonical task rows)

| Work item | Prerequisite | Required output / acceptance |
| --- | --- | --- |
| Product principal decision | Operator chooses genuine paper-only automation or an existing human reviewer flow | Explicit tenant, principal, allowed operations and expiry; no live-capital authority |
| Scoped reader delivery | Genuine product issuance authority and secret delivery | Deployment Registry/Governance and Registry Governance reads succeed; wrong tenant, audience and expired credentials fail; secrets never enter evidence |
| Governance calling contract | Principal decision | Required idempotency headers, verified proposal subject, genuine role-bound decision, exact digest and expiry; replay is durable and no approval is fabricated |
| Registry lineage | Authoritative previous revision read | Forward StrategySpec names its exact approved parent; stale parent/version and invalid approval fail closed |
| Candidate integration | All preceding local and owner contracts pass, PR checks and exact-head acceptance complete | Fresh protected-dev FE/BFF pair and immutable artifact identities; no reused acceptance proof |
| Gate-before-switch and rollback | Candidate admitted and previous artifact pair retained | Candidate gate passes before serving switch; exact prior artifacts, not rebuilt source, are recoverable |
| Hosted product acceptance | Healthy accepted served identity | Fresh stimulus, Loops 1–12 trigger/terminal/consumer/owner/reload evidence, provenance and authenticated journeys described below |

These are delivery dependencies, not a claim that supervisor dispatch has
materialized or completed them. There is no canonical queue/task JSON edit in
this repair. Credential/role issuance is intentionally not performed by this
source-only scope.

An agy CLI read-only review was attempted but timed out without a response;
it is not counted as a completed review. The additional source-contract findings
were independently checked by the Codex persona_review agent without mutations.

## Release and acceptance boundaries

Current dev is project `pantheon-dev-20260902`, VM `pantheon-dev-deploy`,
zone `asia-east1-b`, IP `34.81.52.222`. Owned HTTPS endpoints are
`https://app.dev.mvl-cap.tw` and `https://api.dev.mvl-cap.tw`.
Retired hosts in historical source documentation must not be probed/deployed.

The prior deployment automatically restored the old source pair BFF
`4d7f440c29d8f9057641b680f31e4ecd012f7558` / FE
`a3bf4060f803d1f8b44f6611e89347d59cd6ae0f`, healthy, with unchanged FE artifact.
BFF was rebuilt into a different image, so this is not an exact prior-artifact
rollback proof. Public BFF digest remains unknown.

This repair alone does not establish a new accepted release_id, immutable BFF
artifact, fresh Loops 1–12 receipts, Loop 5 real/simulation provenance, Loop 8
executable binding, Loop 9 lifecycle, authenticated desktop journeys or the
Management AI answer/action/terminal/reload chain. Keep these acceptance gaps
explicit until actual hosted evidence exists.
