# SA / SD — Registry Strategy owner capability prerequisite

Task: REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001
Work class: functional source development; no hosted, production, provider, capital, or credential operations.
Baseline: pantheon dev `471dc5391a0f9cbde54d51730891583043708e42`.
This document becomes immutable when signed into the local development packet.

## SA — verified problem and non-duplication

The original structural SA/SD requires a single authoritative writer, durable receipts,
restart/multi-replica readback, and deletion of process-local product overlays. Overlay retirement
cannot satisfy these by writing a query facade's private dictionary or substituting a fake adapter.

Two independent source inspections and a guarded offline actual-source probe establish:

1. RegistryService uses get_store(), which always selects lock-and-dictionary RegistryStore.
   Fresh instances do not share a synthetic created record. A storage_ref naming DB/GCS is only
   an artifact reference, not persistence of the Registry entry itself.
2. StrategyCommandAdapter._execute_strategy_action builds status/authoritative_readback without
   owner I/O; a nonexistent synthetic strategy receives a paused receipt in the exact method body.
3. Registry has real StrategySpec create/get, immutable registration/version listing/advance APIs. WorkshopCanonicalOperations actually
   POSTs and GETs, but the owner is currently memory-only. This does not prove restart durability.
4. The BFF research query port uses a different _strategy_specs dictionary. Wiring it to the owner
   remains Overlay work; fixing Registry does not automatically fix the BFF read path.
5. BFF name-only draft/metadata operations are not equivalent to complete StrategySpec registration.
   StrategySpec requires semver, valid schema, lineage and content/storage checksums. Artifact-state
   is not deployment/runtime stage. Do not fabricate these fields or declare an unsupported action successful.

Authoritative V2 read-only duplicate check at sequence2519 examined14 unfinished tasks: no Registry
or Strategy-adapter owner candidate. Historical BP5-SVC-002, STRAT-002, REG-001..004A and
SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2 are terminal; do not reopen or rewrite their evidence.
DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 excludes these artifacts and currently follows Overlay.
This prerequisite is a distinct missing owner capability, not a duplicate of its other-domain repairs.

## SD1 — one existing owner, one selected production backend

Keep RegistryService/storage/service/main as the Registry owner. Replace production memory selection
with an explicitly composed PostgreSQL owner backend using existing
services.foundation.postgres_json_store.PostgresJsonOwnerStore and its CAS/insert-if-absent primitives.
Memory may only be an explicitly injected test double, not an automatic fallback on configuration,
connection, schema or transaction failure. Read methods must consult the selected durable store,
not bootstrap a second mutable dictionary/cache authority. Built-in checked-in artifacts remain
idempotently registered with immutable identity/content checks and separately defined system scope.

Use the existing persistence_posture function with explicit Registry backend mapping and the existing
health-route integration. Select and validate the configured store at startup/readiness; liveness is
not acceptance of an unavailable store. Add the actual required driver in Registry requirements.
Both existing Registry Compose blocks already provide DATABASE_URL and infrastructure dependencies;
change only their Registry backend/readiness configuration if needed. Do not provision another DB,
change unrelated services, copy credentials, deploy, change hosted identity or create a new scheduler.

## SD2 — command, identity, version and readback contract

Document a machine-checkable command-to-owner matrix in the Registry contract before changing behavior:

Minimum required successful capabilities (all against isolated authenticated fixtures, not real product data):
create a name-only operator draft with a stable strategy identity and owner-held metadata; update its allowed
metadata with expected version; register a schema/lineage-valid StrategySpec; create its next immutable
revision with parent/base digest and semver; read/list each committed identity and exact version after restart.
Draft metadata is explicitly not a complete validated StrategySpec. It stays under this SAME Registry owner,
with an explicit record kind/schema, and must not become a second BFF store. Derived PnL/performance values
are read projections, not caller-authored authority. Returning unavailable for every command does not satisfy
the prerequisite or remove the Overlay blocker. Unsupported runtime/capital transitions may fail explicitly.

- Keep valid StrategySpec create/get/list and immutable version/lineage semantics. A spec revision must
  validate the actual schema and parent/base digest. Never repurpose mutable StrategyArtifact child
  revisions as arbitrary StrategySpec or operator-metadata PATCH.
- Distinguish operator draft/metadata fields from full spec content and derived metrics. Trace each
  existing BFF Strategy mutation to a supported existing owner command and read projection. If a
  name-only draft or metadata command is genuinely missing, extend the SAME Registry owner contract
  explicitly and preserve public DTO requirements; do not synthesize a valid spec, store derived metrics
  as truth, silently ignore requested fields, or invent a second BFF aggregate store.
- Keep artifact-state distinct from deployment/runtime transitions. Strategy adapter actions must
  invoke the genuine selected owner capability and verify its readback. An unsupported or unavailable
  action returns an explicit error, never an inferred successful lifecycle status. Do not activate,
  pause, promote, trade, or otherwise mutate any real product/capital environment during tests.
- Bind tenant, actor, command, aggregate and normalized request hash to durable idempotency. Identity
  must come from an existing verified trust boundary, not arbitrary body metadata or unverified headers.
  Same caller key with divergent semantics conflicts; cross-tenant/private supplied-ID collisions must
  not reveal or mutate another principal's record. Version/base/CAS checks occur in the owner transaction.
- Receipts contain real command/aggregate identity and version, owner, status, event/correlation IDs and
  committed timestamp. Readback comes from the selected owner after commit, with matching tenant and
  aggregate identity/version. Never construct authoritative_readback/downstream_verified from input.

Reuse services/runtime_auth_inbound.py:validate_request_auth with EXPLICIT strict JWT mode and verified
issuer/audience/claims; its default permissive structured-token behavior is not a trusted boundary. Do not
copy JWT verification. Tenant/actor scope comes from the validated claim contract, with missing or ambiguous
scope rejected. Do not introduce an anonymous, unverified-header or body-identity compatibility bypass.

Before enabling the new owner contract, inventory real callers and their token/actor/tenant propagation.
The known WorkshopCanonicalOperations transport currently sends only Accept/Content-Type; body metadata
does not authenticate it. This known consumer MUST receive a concrete scoped handoff, not an assumption that
its old calls remain compatible. BFF Strategy composition belongs to blocked Overlay; Agora workshop caller
integration belongs to existing AGORA-CHAIN-001 after its exact artifacts are revalidated. Record both required
consumer changes and the verified token transport contract in the prerequisite evidence. Do not claim those
consumers are operational or roll out the changed owner before their positive integration gates pass.
If safe source delivery/required CI needs a caller edited in this prerequisite, checkpoint and request the
exact additional artifact contract first; do not add an anonymous bypass, weaken its assertions, or silently
edit Agora/Overlay. This is an explicit staged owner-capability delivery, not a compatibility-complete product release.

Do not fork the Agora workshop workflow, reuse its tables as Registry authority, or introduce a
universal service locator/import fallback. Existing HTTP transport may be reused through the present
Strategy command adapter. Modify only its Strategy branch; unrelated ranking/formula ownership is
not implicitly assigned. Any required helper change is scoped and regression-tested.

## SD3 — atomic durability, migration and failure semantics

PostgresJsonOwnerStore operations currently open independent connections; successive puts are NOT one
transaction. For a command accepted as committed, persist its owner state and durable command/replay
receipt atomically. When an event/outbox is emitted, its durable admission must share that commit or
use the existing explicitly specified recoverable prepared-event protocol with exhaustive crash-window
proof; do not call that protocol a single transaction. Existing foundation reliable_delivery provides
prepare/activate/reconcile semantics, not an automatic cross-table transaction guarantee.

Prefer the smallest transaction-capability extension of the existing foundation store when required,
with existing consumers unchanged and focused regression coverage. Do not copy a new generic SQL/CAS,
outbox/retry framework into Registry or create a competing event dispatcher. Registry-specific schema,
constraints and mutation policy remain in Registry. Rollback/connection failure must not emit a success
receipt or leave an unrecoverable half-commit. Follow existing shared envelope and reliable-delivery types.

Distinguish not-committed from committed-but-response/readback-unconfirmed. If the response is lost or a
post-commit GET is temporarily unavailable, the same scoped key must recover the original durable command
receipt without a second mutation. Replay remains tied to its committed version even if a later command has
advanced the aggregate; comparing only latest GET must not reinterpret a committed operation as uncommitted.
Include crashes before/after commit and after receipt commit but before response in the acceptance matrix.

Define Registry-owned schema/migration, tenant/actor/version constraints, immutable source IDs/checksums,
and a resumable dry-run/conflict report for any existing durable/legacy inputs. Do not claim recovery of
volatile entries that are not available. Built-in rows and missing-scope legacy rows require explicit
classification, never implicit grant to every tenant. Do not rewrite already archived evidence.

## SD4 — bounded acceptance and handoff

1. Freeze actual Registry API/DTO/spec fixtures and command-owner mapping. Retain valid existing
   Registry StrategySpec/artifact/allocation tests and negative approval/state-transition checks.
2. Use a dedicated isolated PostgreSQL test instance/schema, not live DB data. Exercise the actual
   mounted Registry app and selected backend, not only a dict fake or direct helper body. Prove actual
   fresh-process restart and two-process shared-backend read/write/CAS/replay parity.
3. Cover duplicate and conflicting retries after restart, same actor in different tenants, cross-actor
   private IDs, unauthorized reads/writes, missing owner, missing config/driver/schema, commit failure,
   rollback/outbox crash windows and stale versions. Source hashes and terminal commands/exits/counts
   belong in task evidence. Missing infrastructure is a genuine blocker, not skip/xfail/green evidence.
4. Verify real Strategy-adapter owner transport/readback against isolated owner fixtures, including
   nonexistent/unsupported actions, owner failure and readback mismatch. No fake success or external
   providers. Do not classify new-instance tests as process restart or collection as test execution.
5. Produce Registry capability/consumer handoff, schema and exact merged identity. Overlay still owns
   BFF strategies/routes/ports/composition wiring and retirement; it must use these contracts and prove
   end-to-end readback, not treat this prerequisite's completion as its own acceptance.

## Dispatch, scope and delivery

Owner Claude; independent reviewer Antigravity. Depends only on terminal BP5-SVC-002 and DOMAIN-WRITERS-001.
It does NOT depend on Overlay or any corrective downstream of Overlay, avoiding a dependency cycle.
No active worker edits are overwritten. Overlay's live lease/artifacts/dependencies remain unchanged;
its owner must checkpoint/report the authentic prerequisite blocker before a formal scope/order update.
At 01:19:26 UTC the real Overlay owner did so: canonical blocked, checkpoint PR5618 head
98a29570061dbf7f0b2102d6a8154fe7882745a5. This is checkpoint evidence, not reviewed or merged acceptance.
The supervisor alone dispatches implementation. This packet authorizes neither blanket BFF changes nor
use of the current dirty shared checkout as a delivery branch.

Exact artifacts are the task packet's explicit list: Registry owner/API/model/schema/contract/requirements,
new Registry pg_store and migrations/tests, the Strategy adapter and one dedicated adapter test, optional
minimal shared transaction helper plus its focused new test, only two Registry Compose blocks, and
task-scoped evidence. Additional artifacts require authenticated scoped blocker and formal contract revision.
Do not edit BFF main/personas/strategies/ports, existing worker tests, canonical task JSON or cron in this task.

Clean current-dev task worktree; actual author/task/reviewer trailers; bounded foreground verification;
commit/push/PR; independent exact-head canonical review; required CI; existing integrator merge/archive.
Rollback uses the exact prior compatible release or governed source revert, never re-enable memory/dual
writer production fallback. Source delivery remains distinct from hosted12-loop/Management/Agora acceptance.
