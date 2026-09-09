# Tenant-dev paper principal and owner contracts

Status: implementation and verification in progress; not hosted acceptance.
Authorization: operator explicitly authorized a tenant-dev-only low-risk paper
approval service identity, read-only owner credentials, contract repair and dev
deployment. Production, live trading, real capital, new generic administrative
grants and verifier-key rotation are outside scope.

## System analysis

The previous CP1/DP1 repair (PR 5694) aligned Capital transport/body actors and
removed prohibited Deployment snapshots. It did not supply Governance's required
approval identity, exact Registry digest/expiry, strict Registry CAS wire, or
the owner-to-owner read credentials. A successful BFF build therefore did not
prove that Persona paper provisioning could traverse the actual owners.

A JWT role label alone is insufficient: downstream Deployment and Runtime must
not interpret an automated paper approval as permission for canary/live. Static
short-lived JWTs without renewal would also reproduce an outage after expiry.

## System design

1. BFF uses the separately issued `pantheon-dev-paper-provisioner` identity only
   for Governance. Its tenant is exactly `tenant-dev`, role exactly
   `automated_gate`, and scope exactly `pantheon:dev-paper-approval`. The human
   requester remains attribution metadata, never an impersonated service actor.
   Governance Idempotency-Key is derived from operation/path/tenant/canonical
   CAS body; it is not inserted into strict request JSON.
2. Governance additionally requires explicit dev enablement and verifies the
   actual Registry candidate, checksum, paper intent and source-spec lineage.
   It stamps durable `authorization_scope={environment: dev,
   allowed_target_stages: [paper], max_capital_scale_pct: 0}`. Request bodies
   cannot supply scope. Dedicated-principal claim deviations fail closed.
3. Registry, Deployment and Runtime consumers enforce that scope against the
   actual candidate or persisted plan. Missing usage context, canary, live,
   non-zero capital and non-dev environments are denied. CAS, command receipts
   and approval readbacks retain the scope; issuer renewal never renews product
   ApprovalDecision validity.
   Legacy generic approvals retain their previous authority digest: only the
   new optional `authorization_scope: null` is omitted from hashing. Non-null
   scope remains hash-covered and dedicated-principal missing scope is denied
   before hashing. Existing RuntimeBinding digest comparison stays strict.
4. A trusted dev-only issuer has fixed profiles, no listening socket, no network,
   no Docker socket, no privilege and a read-only root filesystem. It atomically
   writes 0600 tokens into separate consumer volumes every hour. Credentials are
   valid for 24 hours and contain finite expiry, issuer/audience, tenant and a
   unique jti. Consumers mount only their own directory read-only and load it per
   request. A configured unavailable credential file never falls back to an old
   environment token. Signing material stays in the existing authorized dev
   verifier trust domain; this is **not** asymmetric or isolated signing-key
   authority.
5. Delivery opt-in is the `dev` GitHub environment variable
   `DEV_PAPER_PRINCIPALS_AUTHORIZED=true`. First adoption requires a root deploy
   because all owners need the new policy and consumer mounts. BFF-only deploys
   check prior owner adoption. Exact older FE/BFF pairs without the new issuer
   remain restorable through `component=bff`, retaining upgraded owner scope
   enforcement. A full root downgrade to pre-scope consumers is refused once
   the fixed credential volume records adoption: old readers could otherwise
   treat durable paper approvals as unrestricted. The controller does not
   invent authority for old source or rewrite committed approvals.
   The existing exact-pair lease, gate-before-switch and failure compensation
   remain required.

Withdrawal is explicit: setting the dev authorization variable to `false` and
running the dev lane stops the previous issuer, writes a revoked grant marker
and removes only its fixed credential files. Governance checks the marker at
each approval admission, including for an otherwise unexpired captured token.
Changing a GitHub variable alone is not immediate runtime revocation. A withdrawn
paper grant cannot satisfy the paper-provisioning release gate; this operation
must not be reported as a successful paper-ready deployment. Existing business
ApprovalDecisions are not silently rewritten or extended by access revocation.

| Consumer | Credential authority |
| --- | --- |
| BFF → Governance | Dedicated tenant-dev low-risk paper proposer/reviewer/decider |
| Governance → Registry | `registry-reader` |
| Registry → Governance | `approval_reader` |
| Deployment → Registry / Governance | Separate `registry-reader` / `approval_reader` |
| Runtime → Registry / Governance | Separate `registry-reader` / `approval_reader` |
| Deployment outbox | Same read-only owner grants as Deployment |

## Execution and acceptance tasks

- Caller: authoritative digest/finite expiry, canonical StrategySpec, correct
  lineage, strict Registry advance CAS and response-loss retry.
- Owner: verified grant admission, immutable candidate verification, durable
  scope, actual-stage/scale enforcement before plan/saga/runtime mutation.
- Delivery: rotating credentials, consumer isolation, initialization health,
  exact issuer source identity and backward-compatible prior-pair restoration.
- Verification: synthetic wrong-claim/expired/read-only-write negatives; genuine
  PostgreSQL and mounted Registry/Governance lifecycle, command replay and fresh
  process readback; local and CI contract regression; independent source review.
- Release: rebase on protected current dev, commit scoped changes, push, PR,
  required checks, merge, then exact FE/BFF dev pair deployment. Record real run
  IDs, artifacts and served identities, not build-only or local-test claims.

## Remaining acceptance boundary

This repair's unit and isolated database fixtures are not hosted research,
RuntimeBinding, paper trading or browser-journey receipts. Original Loops 1–12
still require a new stimulus and each trigger/terminal/next-consumer/owner/reload
chain. Loop 5 simulation provenance, Loop 8 executable binding, Loop 9 terminal
paper lifecycle, authenticated Management/Agora/AI journeys and exact prior
artifact rollback must be reported from actual deployed evidence separately.
