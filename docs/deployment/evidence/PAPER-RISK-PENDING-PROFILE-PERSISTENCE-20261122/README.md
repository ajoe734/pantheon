# Pending requested-profile provenance

Task: PAPER-RISK-PENDING-PROFILE-PERSISTENCE-20261122. Owner: Codex.
Independent reviewer: Antigravity. Root cause: RC2 WP2.1, existing paper-risk
binding row in `docs/operations/problem-register.md`. Source awaits independent
review and merge; no deployed or accepted claim is made by these local tests.

## Reproduction and change

The existing hosted-proof receipts at
`PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121-closure-20261122.yjscFJxG/`
show the same three personas on build `f78d53689e2443338353f14a9cb40d595bbe8535`:
`f78-profile-provenance-records.jsonl` has original Persona risk `low` and no
pool profile fields; `f78-capital-guard-after-proposals.jsonl` (19:57Z) has
Capital `requested_profile=null`, nested owner request `low`, policy unapproved,
new risk disabled and reducing actions enabled. These are prior hosted
observations, not a hosted execution of this change.

Repair returned from its unresolved/ambiguous branches before reaching the
successful-policy metadata PATCH. Provisioning similarly resolved policy before
building its creation payload, and its suspension branch only handled active,
not already suspended/admitted pools. Consequently creating a Governance case
did not persist the request on CapitalPool.

The repair now saves verified original request provenance before routing and
returning an unresolved/ambiguous owner decision. Sources (request aliases,
provisioning record and pool metadata) must agree; absent/invalid/conflicting
sources fail with a named reason rather than falling back to `low`. Pool,
tenant, persona and paper identity are checked before a write. Medium and high
requests retain their own values. This source-only repair keeps `repaired=false`
for policy repair until an actual approved policy is applied.

The existing Capital status PATCH permits omitted `status` only for the exact
metadata pair `requested_risk_profile` and `require_approved_profile_mapping=true`,
with `expected_persona_id`. The owner keeps its current status, policy, limits,
allocation and unrelated metadata. This operation cannot activate a pool or
write approval evidence. The JSON owner uses its store lock; Postgres uses a
row lock, current payload merge and one keyed UPDATE, avoiding the legacy
whole-store `_save`. Same-data retries return without a state or audit write.
Response loss is recovered only by authoritative GET readback.

Governance GET/list reuse requires tenant, pool, profile, candidate policy ID,
version, policy digest and pool digest to match. A conflicting deterministic ID
returns `existing_proposal_request_mismatch`, with the conflicting case ID,
without treating that case as the current request or posting over it. The three
existing hosted cases keep their IDs for unchanged requests:

| Pool suffix | Existing case |
|---|---|
| 06d4775a9b6a8f63b311 | apv-risk-profile-550ae83ccfeaafa69ec1 |
| 039ad01f8bcd2b4aa466 | apv-risk-profile-a1cdc8408fe251534581 |
| a81b4fd15944698341aa | apv-risk-profile-6bf0cafe806a1e89fb34 |

## Independent review regression: unknown request must hold

Antigravity reopened PR #7755 at `f1959a0aa62652cfdfcbc7bd0997f9279cfe70e1`.
The released typed CapitalPool could select an approved catalog low policy
without a requested-profile source: with no inline policy it reported low,
and with a legacy generic inline policy it reported null while enabling risk.
The same six-case synthetic non-trading reviewer control was rerun before and
after this repair. `typed-control-before.jsonl` preserves the original reviewer
receipt; `typed-control-after.jsonl` records the local corrected run. Low passes,
high holds, and both unknown-source cases now hold and report null.

Capital discovery no longer supplies a default low. The resolver compares all
request/pool/binding aliases, including typed CapitalPool metadata, and returns
`requested_profile_missing`, `requested_profile_conflict` or
`requested_profile_invalid` in `owner_decision_request.absence_source` before
any required profile mapping can authorize increased risk. Candidate policy
profile metadata cannot establish what the Persona requested. Inline policies
are validated before this hold so existing policy validation errors remain
intact, and a valid bound policy remains available for governed reductions and
exits. Explicit policies with no required profile mapping remain compatible.
No persistent writer, cadence or storage budget changes in this correction.

`services/capital/test_requested_profile_resolution.py` contains 32 controls for
typed/wire pools, low/medium/high/unknown, source conflicts, invalid sources,
discovery, implicit legacy mapping, explicit contracts and runtime reduction,
close, increase and reversal behavior. The full owner/provisioning regression
was rerun against disposable PostgreSQL 16.16 (593 passed, no skips), and the
independent-connection row-update test passed again separately. The container
was removed after the bounded batch.

The operator reopened `a54100a3f`: the original normal name-only request passed
on f78 but failed with `requested_profile_source_missing` on that head.
Adding explicit low to four unrelated caller suites concealed a supported
ingress regression. All four suites are restored byte-for-byte to their
original inputs; the old `lifecycle-fixtures.log` only describes the rejected
fixture modification, not acceptance of the original contract.

At `POST /bff/personas`, the original canonical input is hashed unchanged before
the existing new-Persona default is recorded as `risk=low` with
`metadata.requested_risk_profile_provenance=persona_create_default`. The existing
reservation writer stores it. Same-key retries still compare the original hash;
an explicit-low request differs from an omitted-risk request as before. Explicit
policies keep their separate contract. Actual financial-profile aliases remain
validated for conflicts. A historical request with only `traits.risk_appetite`
uses that supplied value when no financial profile or policy is explicit; it
cannot override an explicit financial profile. These are actual request values,
not a catalog policy default.

An older reservation's payload and hash remain unchanged. When its profile is
absent, the coordinator may recover a profile from the real Persona write owner,
checking Persona/tenant IDs, declared pool IDs and the request hash when present.
The owner value must actually exist and agree across its fields. The checkpoint
labels this source `verified_persona_owner`. Missing owners/profiles still hold;
a bare older pool never receives low merely because it lacks provenance. Owner
readback also feeds policy repair and publication. A recovered medium/high value
survives Persona materialization instead of being overwritten by a display default.
The real owner's nested `metadata.risk_profile` object is read as a profile
object, with agreement checked against its scalar risk level. A previous
`requested_profile_source_missing` failure's display default is excluded from
recovery, so repeating an unknown legacy request cannot manufacture provenance.
No policy or approval is inferred by any of these paths.

## Runtime budget and cleanup

This is event driven through existing provisioning/repair calls, with no poller
or job. Each verified account missing provenance gets at most one existing pool
row update, adding <=96 bytes for a profile of <=6 characters and a boolean.
The three hosted pools add <=288 bytes once, no pool rows, and <=3 existing
capital audit events (<=512 bytes each at current hosted identity lengths,
<=1536 bytes once). Steady growth is zero; daily incremental growth is <=96*N
pool bytes plus <=512*N audit bytes for N newly repaired accounts. Retries,
concurrent same-data submissions and dry_run add zero. Existing identical
Governance cases are reused. Pre-admission provisioning retains its existing
single suspension write, now carrying the profile as a narrow metadata merge.

Create ingress adds <=96 bytes to an existing provisioning request for the
default and label. Verified legacy Persona recovery adds <=512 bytes per
existing provisioning row at current hosted identity lengths (<=1536 bytes
once for the three accounts). Both are bounded replacing fields. Daily growth
is <=96*N + 512*M bytes for N new omitted-profile requests and M newly recovered
records; steady-state growth is zero. No additional rows, recurring writes or
cleanup process are introduced; the existing provisioning row retention applies.
This increment is registered on the provisioning writer in the storage manifest.

Provenance is retained with the existing pool row and its archival policy;
there is no new cleanup process. Whole-pool and audit-history retention remain
under RC3 WP3.1. These increments are registered on the existing JSON, Postgres
and audit writers in `docs/operations/storage-manifest.json`.

## Validation and hosted handoff

See `evidence.json`, `validation.log`, `final-focused.log` and `storage-check.json` for terminal local
results. Tests exercise normal create/resume, the three existing paper pool
fixtures, low/medium/high, real Capital dataclass/store/mapper, HTTP `/risk-policy`,
dry_run, response loss, no-op retries, concurrent status/metadata preservation,
Postgres independent connections updating exactly one row, mismatched
pending/rejected proposals, and existing explicit policy/version/revocation/exit
regressions. `original-lifecycle-contracts.log` verifies the 107 original callers
unchanged. `ingress-owner-validation.log` and `ingress-pg.log` supersede the
previous-head regression runs; the typed missing/conflict/invalid guard remains
in force. Focused tests cover default provenance, old-hash replay, owner identity,
aliases, missing old sources, conflicting sources and no-approved-policy holds.

After review/merge, `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121` owns deployment
and normal repair of the three accounts, then read-only collection of original
provisioning payload, Capital pool GET, Capital `/risk-policy` GET and the
existing Governance case GET. Do not vote or activate a policy to validate this
field repair. Verify the same case IDs above and unchanged policy/allocations.

The executable read-only probe accepts those four owner receipts as an array
of `{tenant_id, persona_id, request_payload, capital_pool,
risk_policy_resolution, governance_proposal}`:

```sh
python3 docs/deployment/evidence/PAPER-RISK-PENDING-PROFILE-PERSISTENCE-20261122/check_readback.py owner-receipts.json
```

It requires exactly three unique accounts by default, consistent requested
profiles and identity/digests, and policy-unapproved risk-reducing-only state.
It performs no HTTP calls or writes; the existing hosted-proof task collects
receipts through its authenticated owner read transport. The local real-owner
test runs the same probe against low/medium/high and rejects a null readback.

## Residual findings and obligations

- RC2 policy owner disposition remains in
  `PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122`; source persistence does not
  approve a limit mapping. Hosted acceptance stays with the existing proof task.
- RC3 WP3.1: the existing general Postgres pool `_save` still snapshots all pools
  on unrelated create/status/policy mutations. This task's dedicated provenance
  method avoids it; broad writer amplification/retention is not repaired here.

- RC2 residual observed in a local legacy-record fixture: `capital.main.pool_digest`
  hashes a raw integer budget while `capital_pool_approval_digest` normalizes
  it to float, producing different digests for an integer-valued dataclass.
  Normal CreateCapitalPoolRequest wire parsing supplies floats (as do the
  three hosted budgets). This task preserves existing approval hashes and
  tests that actual service shape; legacy integer-record compatibility needs
  assessment by the existing RC2 capital/risk chain before changing hashes.
