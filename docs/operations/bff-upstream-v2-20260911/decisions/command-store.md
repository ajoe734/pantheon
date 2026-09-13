# CommandStore transaction and replay contract

Task: `BFF-COMMAND-STORE-CONTRACT-DECISION-001` (D-COMMAND). Owner: Codex.
Independent reviewer: Antigravity. Date: 2026-09-13.
Status: design selected; independent review pending. This document implements
V2 §02.9's design prerequisite, not U3 source delivery or hosted acceptance.

## Decision and evidence boundary

Keep **one local JSONL backend inside the existing `CommandStore`**, using a
stable sidecar file lock, fresh transaction reads, and atomic durable replacement.
All command and confirmation changes use that transaction. Do not implement a
PostgreSQL alternative, fallback selector, second admission owner, router lock,
or manager lock in U3. Keep `CommandAdapterService` as admission owner and the
existing executor/domain owners as execution authority.

This is a bounded repair of existing storage semantics on a single control
host. It does not grant cross-host HA, data migration, new database privileges,
production deployment, or capital operations. No additional operator decision
is needed for this selected scope. If a future requirement needs cross-host
writers or a storage migration, stop that expansion and obtain the explicit
topology/migration/permission decision before implementing it; replace the
backend in that separately authorized delivery, never run both as fallbacks.

Source baseline: `e401682d1fa8cc90e7820c1b39d396ccb4e5b477`. Paths below are
relative to the repository; `BFF/` means `services/control-plane/bff/`.
The evidence manifest records exact source blobs and repeatable probes.

| Evidence | Finding and implication |
| --- | --- |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §0–3 | Current baseline permits one operator-bff on the control host; multi-replica/LB is explicitly deferred. A second process sharing storage is a concurrency requirement, not authorization for HA. |
| `BFF/Dockerfile:27`; `docker-compose.yml` operator-bff; `docker-compose.control.yml:375` | Uvicorn command has no worker-count option. Both compose contracts mount `bff-data:/data/bff`; no CommandStore DB selector exists. |
| `BFF/bootstrap/dependencies.py:111–117` | Construction uses `BFF_DATA_DIR/commands.jsonl`. Unconfigured local development defaults to `/tmp/pantheon/bff`; mounted deployment durability requires the configured data directory. |
| `BFF/personas/service.py:270–284` | A separate module store can point at the same file. Even one HTTP process needs consistent multi-instance access. U3 removes the competing command path while storage itself remains safe across instances. |
| `BFF/command_queue.py:17–73` | Per-instance RLock and persistent `_cache`; append fsync and replacement exist, but no cross-process transaction or directory fsync. Constructor check-then-truncate also races. |
| `BFF/command_queue.py:176–263` | Guarded command and redemption already share one replacement; preserve this multi-record atomicity. |
| `BFF/command_queue.py:289–311` | Replay lookup has optional actor restriction but no required tenant/namespace. Tightening this primitive belongs to U3. |
| `BFF/command_adapters/service.py:235–303`; `BFF/main.py:7660` | Optional identity/read guards and memory replay paths coexist with the main transaction. Storage locking alone cannot repair authorization or duplicated admission. |
| `services/foundation/reliable_delivery.py:61–147` | `AtomicJsonRecordStore` offers lock/replace/fsync examples but stores a JSON object with one-record CAS. It is not a drop-in multi-record JSONL transaction, and its optional-fcntl behavior must not become this store's fallback. |
| `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` §2–4 | Shared PostgreSQL is available with strict domain write ownership; availability does not authorize a new CommandStore table or migration. Existing domain Postgres owners stay unchanged. |

Read-only local runtime observation (2026-09-13): Docker reported one visible
operator-bff container, `cd11db368e38`, under project `l12closure20260904`, one
uvicorn process, `/data/bff` on a local writable Docker volume backed by ext4,
and `BFF_DATA_DIR=/data/bff`. This corroborates the local-file process/storage
shape only. It is **not** evidence that this project serves the configured dev
origins or an accepted FE/BFF pair. Current environment identity remains
`docs/deployment/vm-dev-staging-prod-management-plan.md` §3.1. No business
records, credentials, remote VM state, or hosted switches were read or changed.

## Transaction contract inside CommandStore

1. Normalize the configured file path to one absolute real path. Reject
   unsupported file aliases/hard links or symlink replacement rather than
   letting aliases establish different lock identities. Supported storage is a
   local POSIX filesystem with working advisory locks, atomic same-filesystem
   rename, and fsync. NFS, separate container-private copies, and cross-host
   volumes are outside this decision. Missing lock support fails closed.
2. Use one stable sibling `commands.jsonl.lock` for the canonical data path.
   Never lock the replaceable data inode, unlink the lock on release, or create
   locks per command/tenant/router. The store's internal per-path transaction
   context owns the thread RLock, file descriptor, nesting depth, and working
   snapshot; it contains no admission policy or replay ledger. Distinct store
   objects for the same path in one process join this context. After fork,
   inherited descriptors/context must be discarded before use.
3. The outermost transaction takes the thread lock then `flock(LOCK_EX)` using
   bounded nonblocking retries against a monotonic deadline (5 seconds total
   initially, including thread wait). Failure returns the existing unavailable
   error envelope, never 202. Inner synchronous calls join the same snapshot
   without reopening/relocking or committing independently. Any inner mutation
   error makes the outer transaction rollback-only, even if a caller catches
   it. No `await`, provider call, executor dispatch, or HTTP callback while the
   store lock is held; async callers run the complete synchronous transaction
   without yielding, through the existing application's sync boundary.
4. Read the complete current file **after** locking on every outer transaction,
   including read-only operations. No process-lifetime `_cache` or mtime-only
   freshness test. Return deep copies so caller mutations cannot change the
   authoritative snapshot. Readers, active-target checks, replay, token
   lifecycle, audit snapshots, and status updates all use this boundary.
5. Create a previously absent file under this same lock with an exclusive
   creation/atomic initialization path, never `exists()` followed by `open(w)`.
   Once opened successfully, loss of the backing file is an unavailable store,
   not permission to silently initialize an empty ledger. Malformed/truncated
   JSONL, invalid record shape, contradictory identifiers, or permission errors
   fail closed without dropping records, skipping lines, or falling back to
   memory. Blank lines remain compatible with the existing JSONL format.
6. Revalidate replay/hash, tenant-scoped active target, confirmation lifecycle,
   expiry, bound actor/tenant/action/target, and required approval evidence
   against the locked snapshot. A new admission produces command + foundation
   + needed redemption records in that one working snapshot. It must not
   append one record before validating another. Mutators exposed without an
   explicit transaction establish the same boundary themselves.
7. Write the full updated JSONL snapshot to an exclusive temporary sibling,
   flush and fsync it, atomically replace the original, then fsync the parent
   directory before reporting durable success. All mutations, including simple
   submission and status updates, use this path; remove unlocked/append-only
   write alternatives. Preserve historical records, IDs, nested fields, and
   intended file owner/access mode without broadening permissions. The service
   account must already have the directory rights needed for replacement.
8. Before replacement, an exception leaves the old snapshot intact and causes
   no dispatch or token consumption. Clean up only this transaction's temporary
   file. After replacement but before confirmed directory fsync, the outcome is
   uncertain: **never promise rollback or restore the old snapshot**. Readers
   may observe the complete new snapshot, never half a command/redemption pair.
   Return unavailable/no 202 and no immediate dispatch. Subsequent access must
   refresh and establish durability under the same lock before replay success;
   continuing storage failure stays unavailable. Do not blindly rerun a write.

The file primitives follow Python's documented
[`flock`](https://docs.python.org/3/library/fcntl.html#fcntl.flock),
[`os.replace`](https://docs.python.org/3/library/os.html#os.replace), and
[`os.fsync`](https://docs.python.org/3/library/os.html#os.fsync) APIs. The complete
transaction protocol above is this task's design, not a guarantee that those
primitives alone provide application-level exactly-once execution.

## Identity, hashing, and authorized reads

Persist a versioned replay identity in each new foundation idempotency record:

```text
scope_version = 1
identity = (trusted_tenant_id, authenticated_actor_id, namespace, key)
namespace = "operator-command/v1"
request_hash = SHA256(canonical JSON({operation, target: {type, id}, params}))
```

The tuple is structurally encoded, never ambiguous colon concatenation. Resolve
tenant through the already-bound auth policy (`auth/policy.py`'s
`bff_me_tenant_payload`), checking a requested tenant against trusted membership;
do not trust an arbitrary header/body tenant. Use the authenticated actor from
the existing auth/session owner. Missing guard/identity fails closed; no dummy
actor or colon-token parser. This task does not alter role grants or auth policy.

Canonicalize operation aliases, target aliases/types, and schema-validated
params before hashing. Stable UTF-8 JSON uses sorted object keys, compact
separators, finite JSON values, and preserves array order and validated value
types. Preserve semantic distinctions such as absent versus null unless the
existing schema explicitly normalizes them. Include all user-controlled
behavior-affecting inputs; exclude transport URL, header aliases, trace IDs,
server-generated command IDs/timestamps, and credentials. Confirmation token
binding is separately checked even on replay, preserving the current duplicate
token check; normalized confirmation operations include their token and original
command target. Never allow a changed token to evade that check.

All equivalent generic and domain command transports use the same namespace;
operation and target are in the hash, not the replay key. Token create/redeem/
revoke operations also normalize to named operations in this namespace; generated
token IDs and expiry are generated once, after replay lookup, and replayed from
the durable result. Internal auto-redemption evidence does not create a second
client replay owner or consume a user key in a conflicting namespace.

| Situation | Required behavior |
| --- | --- |
| Same tenant/actor/namespace/key and hash | Return the original command/receipt identity and stored acceptance facts; current status is a projection of that record. No new persistence, confirmation consumption, or dispatch. |
| Same identity/key, changed operation/target/params | 409; preserve the original record. |
| Same key, different tenant or actor | Independent authorized admission; never borrow the other identity's replay. Other policies such as active-target conflict can still reject it. |
| Read command status, confirmation status, audit, query, export | Apply current role + trusted tenant + record visibility before exposing any record, count, pagination, or receipt. Reviewer/admin may read another actor's record when existing policy grants it; actor equality is not the read ACL. |
| Unknown or invisible record | Existing non-disclosing not-found/forbidden contract; no existence or receipt leak. A missing backend is unavailable, not fabricated not-found. |
| Authorized reader attempting replay as another actor | Deny reuse of that actor's replay identity, even if reading their record is allowed. |

Active-target uniqueness is `(trusted tenant, canonical target type, target id)`
for submitted/processing commands, not actor-specific. Truly global targets keep
their existing explicit global policy; this document does not invent a global
tenant. Legacy unscoped active records conservatively conflict with matching
targets until provenance is established; do not silently allow duplicate work.

Legacy JSONL remains readable without rewriting history. A record lacking
trusted tenant/namespace/version is **not** an automatic scoped replay match.
When a legacy same-actor/key candidate or active target makes replay ambiguous,
return an explicit conflict/unavailable result requiring reconciliation; do not
submit again, infer tenant from the current caller, or adopt the record into a
new scope. Legacy read access requires proven tenant/visibility from existing
record provenance and policy; unknown scope fails closed, including for admins.
Any backfill or expanded historical access is a separate authorized data/policy
decision, not an implicit migration in this repair.

## Confirmation, execution, and audit

Create/read/redeem/revoke all use the one store snapshot and retained policy.
Create persists the server expiry, action/target/actor/tenant binding and approval
requirements. Redeem rechecks these immediately before committing. Revocation
and redemption races are serialized; an expired, revoked, wrong-bound, or already
consumed token cannot admit another command. Preserve two-person approval where
the existing operation requires it. Replay of a completed admission returns its
record without consuming the token again; it is not a new expired-token action.
Standalone confirmation endpoints retain their distinct semantics through thin
adapters, with no second token ledger or changed expiry calculation.

Only the transaction result `newly_admitted` may schedule the existing executor,
after durable commit and lock release. Twenty identical concurrent requests
must create one command and one initial dispatch. `202` means durable admission;
`executed` requires actual owner outcome. A crash between commit and dispatch or
between a domain effect and receipt is **not** solved by a file lock: preserve
pending/uncertain state and use existing command/owner receipt reconciliation.
Client replay never redispatches an unknown outcome. This decision adds no new
queue, worker, retries, or claim of exactly-once external effects.

Completed Agora feedback/handoff/domain mutations project their actual owner
outcome into audit; they must not enqueue another execution. NL conversation
exchange, Research aggregate state, Assistant command reservations, Capital CRUD,
and domain governance records retain their existing owners. Audit projector is
read-only composition of durable command/foundation/domain receipt; remove fake
accepted receipts, memory-only audit truth, and swallowed storage errors.

## U3 exact-file composition contract

Execution owner is existing task
`BFF-AUDIT-ADMISSION-PROJECTION-SEAM-CORRECTIVE-001` (U3), after U2 completion and
this decision's reviewed delivery. Current canonical artifact grants and source
writer ordering always win over a proposed scope. The manifest embeds the exact
42-path U3 list from `dispatch-map.json`; no file is added to its grant here.

| Exact paths (all under BFF unless stated) | Owned layer / required composition |
| --- | --- |
| `command_queue.py` | Entire single-file transaction, replay lookup and record access boundary; remove stale cache, append bypass, incomplete replacement durability. |
| `main.py`, `command_adapters/service.py`, `command_adapters/router.py` | Move complete real normalization/policy/foundation/admission/confirmation into the existing service; main wires dependencies, routers delegate. Remove callback-switch, minimal-foundation fallback and command memory replay. |
| `command_adapters/contracts.py`, `preconditions.py`, `receipts.py`, `registry.py`; `models.py`, `action_catalog.py` | Typed, stateless normalization/precondition/receipt pieces only as needed; preserve actual operation rules and executor mapping. |
| `personas/service.py`, `personas/router.py`, `personas/routes/common.py`, `personas/routes/lifecycle.py`, `personas/routes/ranking.py` | Consume the one admission owner; remove semantic/action copies and missing-global dependency. Compose with U2's app-scoped projection service. |
| `agora/service.py`, `agora/router.py`, `agora/identity/router.py`, `agora/personalization/router.py` | Classify pending command versus already-completed mutation versus conversation exchange; route only pending commands to admission. |
| `governance/service.py`, `governance/router.py`, `governance/command_audit.py` | Retain governance domain owner; command audit is projection; no fake accepted fallback. |
| `incidents/service.py`, `incidents/router.py`, `tools_integrations/service.py`, `tools_integrations/router.py` | Fix Alert persistence failure and typed callbacks; no TypeError retry guessing, memory ack, or Tools fake acceptance. |
| `runtime/router.py`, `deployment/router.py`, `control_loops/router.py`, `jobs/router.py`, `evolution/router.py`, `research/router.py` | Update actual command callsites only; preserve each domain's own mutation authority. Capital command callers in already-granted paths compose here; Capital CRUD stays out. |
| `test_ask005_sse_event_publishing_contract.py`, `test_bff_write_gap_2026_05_28.py`, `test_v5_interventions.py`; `tests/test_bff_b1_007_security_hardening.py`, `tests/test_command_adapters_router.py`, `tests/test_command_admission_single_owner.py`, `tests/test_command_audit_scope.py`, `tests/test_command_store_multiprocess.py`, `tests/test_read_surface_caller_migration.py` | Preserve and migrate affected assertions; add real transaction/security failure coverage in the granted test paths. |
| `docs/deployment/evidence/BFF-AUDIT-ADMISSION-PROJECTION-SEAM-CORRECTIVE-001/evidence.json` | U3 case mapping, commands/results, source owner/deletion inventory and exact delivery binding. |

Read dependencies outside the U3 grant include `bootstrap/dependencies.py`,
`auth/policy.py`, `command_executor.py`, `services/foundation/reliable_delivery.py`,
compose and Dockerfile. Existing dependency injection can be wired from granted
`main.py`; if actual implementation requires editing an ungranted file, obtain a
governed artifact-contract amendment before editing. No foundation store rewrite,
executor replacement, auth-policy rewrite or deployment change is implied.

At U3's fresh base, classify every actual caller/persist candidate from the V2
inventory (65 callers / 20 persist candidates are historical static counts).
Record exact path + symbol + original business case + retained owner + migrated
consumer + deleted mechanism. Reconcile newly found callers instead of treating
the historical count as a ceiling. Remove only command-specific dictionary users
after noncommand users have moved to their own existing owners. U9 owns generic
legacy POST route retirement and its BE/FE consumer-zero check; U3 must already
remove all duplicate admission bodies in its accepted flows. Do not claim U9
route deletion or whole-system single ownership from this design artifact.

## Required implementation acceptance (not results of this design task)

| Risk / preserved business case | Required U3 proof |
| --- | --- |
| Replay identity and normal transports | Same-key 20 concurrent requests: one command/initial dispatch. Canonical/legacy/domain transports replay the same command. Different operation, params or target with same key: 409. Different actor/tenant: no borrowed replay. |
| Read authorization | Owner and authorized same-tenant reviewer/admin reads succeed; wrong tenant, missing visibility and revoked role fail on status/audit/query/export, including counts. Read privilege does not grant another actor's replay. |
| Confirmation | Create/read/redeem/revoke, server expiry, action/target/actor/tenant binding and required two-person approval. Redeem/redeem and redeem/revoke races with distinct request keys; one winner, no partial pair. |
| Storage concurrency | Two prewarmed instances, two independent processes, constructor race, concurrent update/submit, lock timeout, nested same-instance and cross-instance access, deep-copy isolation, fork lifecycle, process exit and restart. No stale reads/lost updates/deadlocks. |
| Storage failures | Inject lock/open/read/JSON parse/write/flush/file-fsync/replace/directory-fsync/permission failures. Before-replace old data survives; after-replace uncertainty retains complete pair. No 202/dispatch on failure. Disk-full and read-only volume stay fail-closed. |
| Crash windows | Process termination before replacement, after replacement, after durable commit before dispatch, and after owner effect before receipt. No half token consumption, fabricated completion or client-replay redispatch. |
| Owner receipt and original negative cases | Receipt/status/audit/export trace to actual command and owner result; Persona missing-global, Alert lost-persist, Tools/Governance missing-admission probes rerun through real migrated entrypoints. Completed Agora mutation dispatch count remains zero. |
| Old mechanisms and regressions | Exact owner/consumer/deletion inventory and zero old implementation consumers for each delivered flow. Focused tests plus whole affected files from B03/B04/B05/B06/B14/B16, retaining every original assertion's semantics. U2/U9 source-writer and API retirement boundaries remain explicit. |

Implementation evidence must record collected/pass/fail/skip, full commands,
terminal exit status, elapsed time and timeouts. AST test function counts are
not passed tests. A failed or collecting batch is not passed; no skips or fake
fixtures may replace missing behavior. Additional batch test files outside the
current grant require formal scope reconciliation, not silent omission.

## Rollout and delivery boundaries

U3 may land source/isolated tests under existing authority. The deployment lane
must quiesce all old command writers before replacing code; old code ignores the
sidecar lock, so mixed old/new writing is unsupported. Keep the same persisted
JSONL history and volume. No online backfill, destructive cleanup, automatic
schema rewrite, or second backend. A rollback must stop writers and review
new scoped records against the old code's weaker replay semantics; do not blindly
switch back to a writer that could reuse unscoped keys. This is a release
constraint, not an instruction to perform a deployment in D-COMMAND or U3.

D-COMMAND changes exactly this document and its task evidence manifest. Local
baseline probes establish existing defects, not the success of an unimplemented
transaction. No runtime source or historical data is deleted. The PR head/base,
manifest blob and independent review are bound by governed handoff; merge
identity is supplied by the supervisor/GitHub after review, never invented in
this pre-review document. Owner `done` follows that merge without changing the
approved head merely to add bookkeeping.
