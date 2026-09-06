# SA/SD — first-release architecture resumption

Date: 2026-09-06. Source baseline: Pantheon protected dev 471dc5391a0f9cbde54d51730891583043708e42; execute-plans protected dev 5d4f385284b44a30e10764426a47fd808a7ae3cb.
Operator decision: operator-architecture-confirmation.md in this directory, conversation 01a06776-5119-7ad3-a360-a74741c3466d, confirmation observed 02:18:55 UTC.
This document becomes immutable when referenced by the signed successor packet. It is source-development scope, not a hosted/security/live authorization packet or an acceptance proof.

## 1. Change authority and continuity

The operator approved one strategy/version write authority, correct business action semantics, separate Persona lifecycle/provisioning/runtime state, synchronous affected callers, and retirement of replaced APIs/stores without backwards compatibility. The architecture-decision hold is released. Existing genuine agy/Claude implementation and independent review remain; the current root integrates/verifies/deploys and does not implement a simultaneously dispatched source scope.

The original REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001 V2 packet is immutable and already dispatched. Replace that task formally with REGISTRY-STRATEGY-UNIFIED-CONTRACT-001 through the existing signed bridge and Human/Ops supersede command. Superseded is not completed implementation. Preserve the original signed plan, worktree archive, negative evidence and all capabilities/acceptance below. No task should depend on the superseded ID as if it proved source completion.

The successor is initially materialized with Human/Ops as a non-executable preparation fence and reviewer Antigravity. Only after authoritative readback, predecessor supersession, overlap/dependency checks and consumer handoffs is it reassigned to Claude using the supported expected-owner/reviewer checks. This is not permission to start a parallel Registry worker or blindly restore the archived WIP.

## 2. SA: verified failures

Baseline Registry selects process-memory storage. Its BFF Strategy adapter returns inferred successful action status/readback without owner I/O. The paused WIP added a second full spec/revision authority separate from RegistryEntry, mapped submit_review to draft creation, paper promotion to spec registration and activation to revision creation, and assembled authoritative readback from POST/input without a verified owner GET. These are not accepted capabilities.

Separately, the current-source packaged Persona route with real strict Persona HTTP owner and isolated PostgreSQL sends lifecycle_state=provisioning to create. The owner only permits draft and returns 422; BFF returns 502 after the provisioning ledger reached schedule_registered. See baseline-real-persona-pg-replay.json. Other owners in that diagnostic were explicit test doubles, so neither its references nor its ledger persistence prove a full real product lifecycle. Canonical Persona states and saga/runtime states are different models.

Known Registry callers include BFF Strategy/Persona, Agora Workshop, source-ingestion distillation, alpha replication/revalidation, and artifact readers in Deployment/Runtime. Existing source/research requests lack explicit Authorization at the observed call sites. Body tenant/actor fields do not authenticate a caller. Changed owner auth/DTOs cannot be declared usable before each required caller passes integration.

## 3. Registry successor implementation and acceptance

### 3.1 One business authority, durable production backend

RegistryService with its selected repository owns StrategySpec content, immutable versions, RegistryEntry identities and artifact-state. Operator draft metadata belongs to the same owner but is a distinct record kind; its existence must not fabricate a validated spec. Separate tables/classes for metadata, immutable entries, command receipts and outbox are allowed only with one set of invariants and no independently writable second full spec/revision copy.

Use explicitly configured PostgreSQL through the existing foundation PostgresJsonOwnerStore/CAS/transaction capabilities. Memory is an explicitly injected test double only, not a production selector default or fallback for missing configuration, driver, connection, schema or transaction failure. Actual mounted reads, startup, readiness and idempotent built-in registration use this same selected store. Preserve system/builtin scope explicitly; missing tenant legacy rows are not globally authorized. No new generic storage/retry/cron framework.

### 3.2 Freeze the first-release contract before implementation

Within the scoped Registry contract files, produce a machine-checkable capability/API/DTO matrix before changing behavior. Cover each capability, canonical route, verified principal/tenant/actor/scope, request identity, expected/base version, sole writer, legal transition, original durable receipt, exact-version GET/list and retired route/selector/store. Endpoint spelling is an engineering decision within this admitted scope, not another operator approval gate. It must be reconciled with actual callers; retaining a correct canonical existing URL is allowed, retaining a replaced duplicate path as compatibility is not.

Required successful capabilities: name-only draft creation with stable strategy identity; allowed metadata update with CAS; schema/lineage/checksum-valid full StrategySpec registration; next immutable revision with parent/base digest and semver; scoped read/list of each exact committed version after restart. An aggregate CAS counter is not semver. Performance/PnL remains a projection of its real execution/research owner, not arbitrary caller-authored metrics authority.

Review submission, approval, paper promotion, activation, pause and archive must be mapped to their actual responsible business owner and lawful state transition. Never relabel create-draft/register-spec/create-revision as those actions. Registry owns artifact-state, not paper/live runtime state. The Strategy adapter must perform a genuine supported owner command and verify scoped durable readback; if a required capability is elsewhere or absent, report the exact owner/caller handoff and fail explicitly until delivered. All-commands-unavailable is not Registry completion: the required Registry positive capabilities above must work. Full product acceptance additionally requires real review/paper/runtime positives in their existing owner tasks.

Retire the WIP parallel strategy-command full-spec/revisions authority and any new or old route that the selected canonical contract replaces. For generic Registry entry APIs, explicitly exclude mutation kinds assigned exclusively to a typed canonical path where required; retain unrelated artifact/allocation/execution-bundle capabilities and their proper validation. No compatibility endpoint, deprecated alias, dual write, permissive fallback or second singleton authority. Retired mounted routes must reject writes and have zero required callers; an rg-only inventory is insufficient proof.

### 3.3 Trust, atomicity and real readback

Reuse services/runtime_auth_inbound.py validate_request_auth rather than copying JWT verification. Require explicit strict mode plus configured expected issuer/audience and explicit verified nonempty subject/actor, tenant, permitted role/scope and expiry. Its synthesized default operator/actor values are not proof; missing claims are denied. Do not trust body/header identities, accept anonymous calls for compatibility, use universal service credentials as a substitute for tenant scope, or persist bearer credentials in receipts.

Use scoped idempotency keyed by verified tenant/actor/command/aggregate/caller key; compare normalized request hash as semantics, not as a key suffix permitting divergent same-key replay. Commit owner state and original durable command/replay receipt in one real transaction. Reuse the foundation transaction/CAS primitives and focused extensions; separate puts/connections are not atomic. CAS must bind the caller/base snapshot, not fetch latest immediately before writing to hide stale requests. A duplicate receipt cannot overwrite the original version/event/committed timestamp.

An outbox must commit with state/receipt, or use the explicitly documented existing recoverable prepare/activate/reconcile protocol with crash-window proof; do not falsely call separate commits one transaction. Distinguish not committed, committed but response lost, readback temporarily unconfirmed, and verified terminal state. A same-key retry recovers the original exact committed version even after newer versions exist. POST accepted/body data does not constitute owner GET/reload proof. Verify tenant, aggregate identity, version, command/event/correlation and commit time; fail closed on mismatch/unavailability without manufacturing success or redoing a committed mutation.

### 3.4 Verification and delivery

Use dedicated isolated PostgreSQL, the actual mounted app and Strategy HTTP adapter, strict synthetic scoped credentials and bounded foreground tests. Prove fresh-process restart and two concurrent processes sharing the backend; valid draft/spec/revision/readback positives; duplicate/divergent retry; cross-tenant and cross-actor supplied-ID collision; stale CAS; missing config/driver/schema/owner; auth negatives; commit/rollback failures; response-loss and outbox crash windows; exact original-version replay and readback mismatch. Record executed counts, terminal exits/outputs and exact source hashes. No real provider/broker/capital/hosted mutations, collection-only, dict-only substitute, new-instance-as-restart, skip/xfail-as-pass or weakened assertions.

Retain genuinely distinct Registry artifact/allocation/regression behavior; update tests for the correct first-release contract rather than retaining duplicate APIs to satisfy old tests. Clean current-dev worktree, scoped changes, genuine author/task/reviewer trailers, push/PR, independent exact-head canonical review, required CI and existing integrator merge/archive. Source merge is not whole product acceptance or permission to publish an incomplete FE/BFF pair.

Original V2 capability, atomicity, authorization, restart, rollback, scope safety and independent review requirements are retained by this section. Prior requirements to maintain backwards compatibility or treat current callers as permanent API constraints are superseded by the explicit first-release decision; no requirement to preserve distinct business capability is removed. The original V2 files are not edited or relabelled as delivery.

## 4. Existing consumer owners: one coordinated release, no duplicate implementation

Use the existing task chain; do not introduce another consumer implementation task. Their existing acceptance remains, with formal pre-dispatch artifact additions where required and explicit handoff notes. These notes are not forged immutable packet amendments. If an existing acceptance truly conflicts with the approved direction, formally supersede it rather than silently replacing signed metadata.

| Existing task | Responsibility in the approved direction |
| --- | --- |
| OVERLAY-RETIRE-001 — Antigravity / Claude | BFF Strategy and Persona query/command composition use the single owner, not local overlays. Include Persona provisioning coordinator/ledger and exact integration tests through formal artifact additions. Fix draft-first canonical Persona creation versus independent saga/runtime projections, with lawful subsequent commands, order/compensation and replay. Do not weaken the Persona owner's draft guard, alias all states or publish paper_running from schedule/heartbeat alone. Registry owner absence remains a real prerequisite until successor delivery. |
| AGORA-CHAIN-001 — Antigravity / Antigravity2 | Workshop Registry transport/DTO/verified tenant and actor credential propagation, exact version/receipt handoff through Trading Room to performance; preserve full existing Agora acceptance. |
| DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 — Antigravity / Claude | Existing research/deployment/runtime owner callers adopt the canonical Registry auth/read/version contract. Formally add unowned source-distillation caller/test paths and relevant compose caller configuration; preserve the full existing domain-command durability scorecard. Include missing canonical Persona owner changes only if actually necessary, not to relax lifecycle rules. |
| LOOP-TRUTH-001 — Claude / Antigravity | Read actual durable controller/consumer evidence, preserve simulation provenance and five-field causal loop proofs. Do not infer real from a flag or reuse old IDs. |
| MGMT-READ-001 and FE-STRICTLIVE-001 | All affected Management/Agora FE/BFF DTO/action mappings adopt actual capabilities; no FE-only success, fixture overlays or fallback. Preserve full Management/OpenClaw and authenticated journey acceptance. |
| DEV-DELIVERY-001 — Antigravity2 / Antigravity | Gate the whole exact FE/BFF pair before publication, bind both artifact digests and release identity, retain exact previous bytes, restore original image+FE artifact in rollback, require OpenClaw/journey acceptance. No source-only rebuild called exact artifact rollback. |

Registry source capability may merge before all consumers only if its actual required checks pass; the coordinated dev release cannot publish until all required source/consumer positive and negative integration gates pass. No staging of an incompatible public BFF before the FE gate. No fake success, compatibility auth bypass or retirement waiver to make intermediate checks green.

## 5. Overlap, safety and completion

At preparation the original Registry task is Human/Ops generation 2 with no worker/lease; its successor starts fenced. No other active task explicitly owns services/registry or the Strategy adapter; downstream broad test/migration ownership is already sequential behind blocked Overlay. Preserve that order and explicitly hand off the successor ID. No task currently declares the old Registry ID as a depends_on member; verify again before supersession. Other Astra source/review scopes remain theirs; canonical notes carry this root's preparation and operator confirmation, not a claim that another conversation acknowledged a message.

Archive retirement does not prove implementation done, and terminal superseded facts do not prove dependency completion. Do not auto-rewrite dependent tasks or their signed declarations. No canonical JSON edits, fake run IDs, reviewer proof, forged operator MFA or materializer bypass. No product-route task maintenance.

Do not delete dev data, rotate/expose credentials, touch production/live capital, retired VMs or unrelated source. Retirement of replaced code must follow normal scoped branch/PR review and retain unrelated capabilities. The original full goal remains unfulfilled until a fresh current exact pair, artifact-bound gate/served identities, one new Loops 1–12 causal chain with all five evidence fields, simulation integrity, executable RuntimeBinding, paper lifecycle, authenticated Management/Agora/OpenClaw journeys and exact-artifact rollback are proven on dev. Report missing evidence honestly.
