# PAPER-PERSONA-RISK-PROFILE-CHANGE-20261123

Owner: Codex. Assigned reviewer: Codex2 after Antigravity2 quota recovery
(distinct configured identities, same model family exception). Root cause: RC2 WP2.1,
continuing #7755; frontend counterpart: FE-PERSONA-RISK-PROFILE-CHANGE-20261123.
Source implementation is awaiting independent review. Hosted acceptance is
pending an integrated exact backend/frontend pair; no deployed or accepted
claim is made here.

## Reproduction and scope

The existing operator `PATCH /bff/personas/{id}` wrote only the Persona risk
scalar. The original governed repair rejected a later medium request because
the immutable create request and pool provenance still said low. The operator's
2026-11-23 receipt is
`/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121-closure-20261122.yjscFJxG/policy-version-change-live-dryrun-20261123.json`:
low dry-run 200/unresolved; medium dry-run 409/requested_profile_source_conflict.
No real trading pool risk request, policy terms, grant or vote is changed by
the software validation in this packet.

## Joint GET/PATCH contract

For supported, tenant-scoped trading paper Personas with a dedicated pool,
`data.riskPolicyChange` has exactly these public fields:

```json
{
  "requestedRisk": "medium",
  "effectiveRisk": "low",
  "status": "pending_approval",
  "policyVersion": "1.0",
  "approvalId": "the-verified-current-grant"
}
```

`requestedRisk` is low/medium/high when the request is available.
`effectiveRisk` is a verified installed tier or null. Version and approval are
null when no effective tier is verified. Status is `effective`,
`pending_approval`, or explicit `unavailable`; a failed mutation is an HTTP
error. An unavailable read returns null request/effective/version/approval
rather than inventing evidence. Unsupported/no-pool/non-trading Personas omit
the field. Old BFF versions also omit it; frontend must keep the editor closed
when capability or authority cannot be verified. Do not consume snake-case or
other undeclared aliases or replace an explicit null with legacy hints.

Normal mutation remains operator-authenticated `PATCH {"risk":"medium"}` with
a stable opaque `Idempotency-Key`, including native UUIDs and existing
execute-plans `idk_<base36 issued milliseconds>_<lowercase alphanumeric nonce>`
keys. Keep the exact key across retries. UUID/opaque keys are not parsed as a
time or assigned a new arrival timestamp. No body or public response field
changes are required in the frontend counterpart.
GET/refetch is authoritative. Pending means the
requested tier cannot authorize new/increased exposure; existing governed
position reductions/closures remain evaluated. A previous effective tier is
evidence of the installed binding, not permission to increase exposure while
the latest request is pending. Suspended pools retain suspension and report
unavailable without claiming a new effective policy.

## Ownership, ordering and recovery

Capital owns the latest durable intent, its monotonic revision and durable
idempotency receipts on the existing pool row. The BFF service principal uses
the existing Capital write authority and tenant transport. The authenticated
operator identity is recorded as requested_by. Original provisioning payload,
wire hash and create/idempotency checks are untouched. The legacy Persona
scalar remains the original request hint for supported paper risk edits;
detail GET/PATCH project the latest Capital intent. This avoids a second,
unordered risk authority in the Persona writer. Unrelated Persona fields and
unsupported edit paths keep their existing behavior.

New intent and its exact approved policy/version/digest are committed in one
Capital owner mutation. PostgreSQL `SELECT ... FOR UPDATE` serializes separate
service facades. Stale expected revisions conflict. The last 128 keys are
retained with immutable payload hashes. A known superseded key observes the
latest intent without replaying its old tier. Timestamped keys also retain
their original issued time and a durable eviction watermark; an evicted or
older unseen timestamped key returns a stale conflict even after a fresh
revision read. Keys more than five minutes ahead of server time cannot advance
that window. A key at/below the watermark requires a fresh operator edit after
refetch, never an automatic re-key of the old command. Opaque/UUID first-seen
ordering uses the owner revision CAS; such keys carry no issuance chronology.
Retired opaque duplicates use exact durable audit receipts and observe the
latest intent; changing their payload returns a conflict. The same current
key can reconcile after independent approval. Missing/ambiguous/unapproved
mapping keeps the installed policy and prior approvals, persists the new
request and holds increases. Financial limits come only from existing policy
definitions and verified Governance approvals, never the BFF or a tier label.
Proposal deduplication uses existing owner routing with a digest-specific key;
old tier/digest votes cannot approve a replacement.

The mutation response is followed by scoped owner GETs. A lost response is
recovered only if the durable request hash/profile receipt exists. Capital
failure cannot return success from a Persona-only update. If a later Persona
field update fails, refetch still exposes the committed Capital intent and a
retry does not duplicate it. Older repair paths delegate reconciliation of the
latest intent and cannot replace it from the immutable create request. Older
in-flight risk metadata patches are rejected at the owner row boundary.
Unrelated partial metadata annotations merge under the same row lock without
erasing receipts, changing policy, or requiring new approval for an already
active pool. Tenant pinning and status activation authority remain intact. Existing
PostgreSQL pool create/status writes now touch only their addressed row, so a
stale full-store snapshot cannot overwrite another pool's new intent.

## Verification and hosted continuation

See `evidence.json` and committed logs for commands, exit codes and residuals.
The focused normal-route tests exercise real Capital HTTP handlers and owner
stores with synthetic local approval snapshots. One unchanged running paper
algorithm reads the new approved policy at both submission sizing and order
evaluation. These are software tests, not business approvals or hosted proof.

The post-approval trigger is the existing operator-authenticated
`POST /bff/personas/{id}/provisioning/paper-capital/risk-policy` with `{}`.
It reads the persisted latest intent and current receipt and reconciles through
Capital's ordered path, without another risk PATCH, new revision, policy
parameters, or caller-supplied approval. GET/refetch alone is read-only and
does not install a discovered mapping. A focused test removes the synthetic
medium approval, PATCHes pending, restores the independently issued fixture
approval, calls this actual governed route, and verifies new sizing and order
gate behavior in the same running algorithm. Viewer calls are refused. No
automatic approval watcher or recurring reconciliation job is added.

The coordinator's existing isolated hosted acceptance Persona is
`persona-d64835a203ce276b8fbb` (dataScope=test, paper, dedicated single pool).
After source review/integration and an exact-pair dev release:

1. Record hosted manifest, backend/frontend SHAs and expected steady service
   versions/health. A build or `/bff/version` alone is insufficient.
2. Use the genuine operator through its existing detail editor to request
   medium/high and then low, with separate stable idempotency keys. Refetch and
   reload after each save. Use this isolated Persona only. Do not edit the
   three real trading pools or supply/vote financial terms for a positive case.
3. Existing independently approved mappings may become effective. If absent,
   record pending with verified previous effective tier or null, and verify the
   Capital guard holds increases while evaluating genuine reductions.
4. Run the bounded read-only probe with the existing viewer/owner credential
   transport and explicit isolated id:

   ```bash
   ACCEPTANCE_EXPECTED_PERSONA_IDS=persona-d64835a203ce276b8fbb \
     timeout 190 acceptance/probes/persona-risk-profile-change
   ```

   Existing transport variables are documented by `paper-risk-policy-binding`;
   credentials must not be logged. The probe uses GETs plus viewer login,
   at most 24 requests, 180 seconds, bounded response bodies and at most three
   explicitly named Personas. It checks persisted intent, pool/tenant/persona
   scope, verified effective approval and reload. Missing credentials/explicit
   IDs exit 77, which is not acceptance. Pending can pass only as truthful
   pending evidence with Capital's risk-increase hold.

## Runtime budget

No recurring job, new table, file or queue. Each actual operator request or
approved reconciliation updates one existing pool row and adds one existing
Capital audit event; converged same-key retries write neither. Intent plus
receipts add <=64 KiB per pool: at most 128 receipts of <=384 B plus <=2 KiB
fixed latest intent. At three trading pools plus one acceptance pool the
increment is <=256 KiB total, 0 B/day steady, <=384 B per new receipt until
the 128-entry window fills, then 0 net receipt growth per new edit. The scalar
eviction watermark retires timestamped keys without admitting their replay.
Opaque receipts retire to the existing Capital audit owner store before their
pool-row receipt is dropped, under the same pool lock. An archive failure
prevents eviction. If the archive commits but the pool write fails, the
still-present row receipt wins on retry; deterministic archive IDs prevent
duplicate tombstones. A scoped exact-key audit read recovers retired opaque
retries and hash conflicts, including lost responses and process/store reload.
PostgreSQL uses the existing event primary key and formal tenant predicate;
the JSON fallback scans its existing journal with constant working memory.
No recurring cleanup job is added. The pool row remains bounded beyond 128
edits; exact opaque tombstones have pool-lifetime retention in the existing
audit store and must be preserved by future RC3 cleanup (or migrated before
removal). This does not claim bounded lifetime audit history.

Each opaque retirement adds <=2 KiB and one event to that existing audit
writer, at most one per actual new edit after the 128-entry row window fills.
At four hosted pools and E edits per pool per day, retirement adds <=8E KiB/day
(0 B/day without edits), in addition to <=8E KiB/day for ordinary mutation
events. No new audit table/file is created; tombstones are scoped to a pool
and follow its lifecycle. Existing audit cleanup/cap remains unimplemented
under RC3 WP3.1; no automatic tombstone deletion is authorized here. Tests
perform 258 opaque edits, verify bounded row metadata, reload both stores,
and reject payload changes for an evicted UUID without a new revision. They
also cover archive failure, pool failure after archival, independent PostgreSQL
writers, same-key approval reconciliation and actual UUID/FE PATCH/refetch.
Each actual mutation also appends <=2 KiB to the existing audit writer. Its
whole-journal cap/cleanup remains the existing RC3 WP3.1 residual, not a new
boundedness claim. Writers and budget are in storage-manifest.json.

## Residual findings

- Two existing Persona list security tests fail with 503
  `strategy_store_unavailable` / "Canonical strategy registry readback requires
  caller authorization". Both reproduce on a clean origin/dev archive at
  bd47d73bc; they are not reported passed. RC2 owns the existing BFF canonical
  read/fixture chain; the coordinator must attach this instance before closing
  the chain. This change does not weaken registry authorization or rewrite
  those tests to hide the failure.
- The latest request contract is on detail GET/PATCH. Other consumers that
  still interpret the legacy Persona scalar as the latest risk request need
  to adopt the Capital-owned contract; a tier hint cannot authorize execution.
  The frontend task is limited to this detail touchpoint. RC6/RC2 coordinator
  should retain this read-model boundary in follow-through.
- Existing historical Capital audit retention is unbounded/unreviewed under
  RC3 WP3.1. This packet quantifies the incremental ordinary and retirement
  event rate and size. Exact opaque tombstones must survive for the pool
  lifetime; future cleanup must preserve or migrate them before deletion.
- Hosted operator edit/reload and exact-pair release acceptance remain pending;
  no local pass closes those obligations.
