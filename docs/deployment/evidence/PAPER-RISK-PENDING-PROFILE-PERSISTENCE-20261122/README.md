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
regressions. Test fixtures now explicitly supply risk instead of relying on a
production fallback.

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
