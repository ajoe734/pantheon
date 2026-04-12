# TEL-001A — Telemetry Field Map, Binding/Stage Evidence Contract, and Migration Checklist

**Task**: `TEL-001A` — Prepare TEL-001 field map and migration packet
**Owner**: Qwen
**Reviewer**: Codex
**Date**: 2026-04-10
**Status**: Draft Support Slice

## Purpose

This document parallel-prepares TEL-001 so that once RUN-001 (RuntimeBinding + runtime-manager authority) finalizes, the telemetry schema can immediately adopt deployment-stage and runtime-binding references without re-scoping.

It covers three deliverables:

1. **Telemetry Field Map** — how RuntimeBinding fields and deployment stage map into telemetry event envelopes.
2. **Binding/Stage Evidence Contract** — how every telemetry event proves which runtime binding and deployment stage it originated from.
3. **Migration Checklist** — what TEL-001 must change once RUN-001 is `done`.

---

## 1. Telemetry Field Map

### 1.1 Source Artifacts

This field map is derived from:

| Source | Artifact | Role |
|---|---|---|
| RUN-001A | `services/execution/runtime-manager/runtime_binding.schema.json` | Canonical RuntimeBinding field inventory |
| RUN-001A | `services/execution/runtime-manager/authority_matrix.md` | Write authority boundaries |
| RUN-001A | `services/execution/runtime-manager/rollback_action_matrix.md` | Rollback action vocabulary |
| RUN-001 | `services/execution/runtime-manager/review_run001_codex_approved_zh.md` | Reviewer-locked RuntimeBinding semantics |
| RUN-001 | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §19 | Runtime Manager write authority |
| TEL-001 | `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` | Canonical ingest + storage architecture |

### 1.2 RuntimeBinding → Telemetry Event Field Mapping

Every telemetry event emitted by the execution plane MUST carry the following fields, mapped directly from the active RuntimeBinding context:

| Telemetry Field | RuntimeBinding Source | Type | Required | Notes |
|---|---|---|---|---|
| `binding_id` | `RuntimeBinding.binding_id` | UUID | Yes | Identifies the exact binding that produced this event. Primary join key to RuntimeBindingStore. |
| `runtime_id` | `RuntimeBinding.runtime_id` | string | Yes | The LEAN instance / container / worker that generated the event. |
| `capital_pool_id` | `RuntimeBinding.capital_pool_id` | string | Yes | The capital pool this event belongs to. Used for pool-scoped queries and lineage joins. |
| `artifact_id` | `RuntimeBinding.artifact_id` | string | Yes | The strategy/model artifact that was loaded. Required for performance attribution. |
| `artifact_version` | `RuntimeBinding.artifact_version` | string (semver) | Yes | Version of the artifact. Pattern: `^\d+\.\d+\.\d+$`. |
| `deployment_stage` | `RuntimeBinding.deployment_mode` | enum | Yes | Mapped 1:1 from `deployment_mode` enum: `paper`, `canary`, `live`, `frozen`. TEL-001 uses the term `deployment_stage` in telemetry envelopes to avoid conflating the runtime concept with the binding concept. |
| `plan_id` | `RuntimeBinding.plan_id` | string | Yes | The DeploymentPlan that triggered this binding. Required — no telemetry event may exist without a backing plan reference. |
| `persona_capital_binding_id` | `RuntimeBinding.persona_capital_binding_id` | string | Yes | Governance admissibility proof. Links the event back through the binding to the persona registry. |
| `event_produced_at` | `RuntimeBinding.effective_at` (reference) + event-local timestamp | RFC3339 | Yes | When this event was generated. Must be ≥ `effective_at` of the binding and < `retired_at` (if set). |
| `rollback_parent` | `RuntimeBinding.rollback_parent` | UUID (nullable) | No | If this event was produced during a rollback transition, references the binding_id that was replaced. |
| `rollback_action_type` | `RuntimeBinding.rollback_action_type` | enum (nullable) | No | One of `replace`, `pause_then_replace`, `liquidate_then_replace`. Null if not a rollback event. |

### 1.3 Additional Telemetry-Specific Fields

These fields are NOT from RuntimeBinding but MUST be present on every telemetry event envelope:

| Field | Type | Required | Notes |
|---|---|---|---|
| `event_type` | enum | Yes | One of the canonical event types defined in TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §4.1 (e.g., `order_filled`, `deploy_started`, `rollback_completed`). |
| `event_id` | UUID | Yes | Unique per-event identifier for idempotency deduplication. |
| `trace_id` | string (UUID) | No | Correlation ID for distributed tracing across ingest → buffer → Postgres → ClickHouse. |
| `environment` | string | Yes | e.g., `paper`, `canary`, `live`. Mirrors `deployment_stage` for backward compatibility with legacy telemetry consumers. During migration, both fields will be populated; post-migration, `environment` is a derived alias of `deployment_stage`. |

### 1.4 Field Map Validation Rules

| Rule | Description | Enforcement |
|---|---|---|
| **FM-1** | `binding_id` MUST resolve to a valid RuntimeBinding in the RuntimeBindingStore. | telemetry-ingest-svc validates at ingest time. |
| **FM-2** | `deployment_stage` MUST match the source RuntimeBinding's `deployment_mode` exactly. | Reject if mismatch. |
| **FM-3** | `artifact_version` MUST match semver pattern `^\d+\.\d+\.\d+$`. | Schema validation at ingest. |
| **FM-4** | `event_produced_at` MUST be ≥ binding `effective_at` and (if binding is retired/failed) ≤ binding `retired_at`. | Reject out-of-window events. |
| **FM-5** | `rollback_action_type` MUST be one of the canonical values: `replace`, `pause_then_replace`, `liquidate_then_replace`. | Reject unknown values. |
| **FM-6** | If `rollback_parent` is set, `rollback_action_type` MUST also be set (and vice versa). | Schema validation. |

---

## 2. Binding/Stage Evidence Contract

### 2.1 Purpose

This contract defines how every telemetry event **proves** which RuntimeBinding and deployment stage it originated from. It ensures that downstream consumers (lineage aggregation, incident investigation, drift detection, BFF dashboards) can trust the binding and stage information without needing to join to external systems at query time.

### 2.2 Evidence Requirements

#### E-1: Binding Identity Proof

Every telemetry event MUST carry the full `(binding_id, artifact_id, artifact_version, runtime_id)` tuple.

- This tuple is the **minimal binding identity** required to answer: "Which runtime, running which artifact version, produced this event?"
- The tuple MUST match exactly one record in the RuntimeBindingStore.
- If the binding has been retired, the event MUST still carry the original binding identity (not the replacement binding).

#### E-2: Deployment Stage Proof

Every telemetry event MUST carry `deployment_stage` as a first-class field, mapped directly from `RuntimeBinding.deployment_mode`.

- `deployment_stage` is **not** inferred from the event context, environment variable, or config file.
- `deployment_stage` MUST match the `deployment_mode` of the binding referenced by `binding_id`.
- The `environment` field (legacy) is maintained for backward compatibility but MUST equal `deployment_stage` for all new events.

#### E-3: Governance Admissibility Proof

Every telemetry event MUST carry `persona_capital_binding_id` and `plan_id`.

- `persona_capital_binding_id` proves that the binding was authorized by a valid PersonaCapitalBinding.
- `plan_id` proves that the binding was triggered by an approved DeploymentPlan.
- Together, these form the **governance admissibility proof** — without them, the event is orphaned from the governance chain.

#### E-4: Temporal Window Proof

Every telemetry event's `event_produced_at` timestamp MUST fall within the binding's temporal window.

- Window: `[effective_at, retired_at)` for retired bindings, or `[effective_at, ∞)` for active bindings.
- Events outside this window are **evidence violations** and MUST be routed to a dead-letter queue with an incident tag.

#### E-5: Rollback Lineage Proof

If a telemetry event was produced during or as a result of a rollback:

- `rollback_parent` MUST reference the `binding_id` of the binding being replaced.
- `rollback_action_type` MUST indicate which rollback strategy was used.
- This allows lineage queries to reconstruct the **rollback chain**: which binding replaced which, and why.

#### E-6: No-Orphan Rule

No telemetry event MAY exist in the canonical Postgres store without a resolvable `(binding_id, plan_id)` pair.

- Events that arrive before their binding is created are buffered in the durable buffer until the binding exists.
- Events whose binding has been permanently purged (e.g., after retention policy expiry) are tagged as `orphan_suspect` and routed to audit.

### 2.3 Ingest-Time Validation

The `telemetry-ingest-svc` is responsible for enforcing the evidence contract at ingest time:

| Step | Action |
|---|---|
| 1 | Parse event envelope and extract `binding_id`. |
| 2 | Resolve `binding_id` against RuntimeBindingStore (or cache). |
| 3 | Validate `deployment_stage` matches binding `deployment_mode`. |
| 4 | Validate `artifact_id` and `artifact_version` match binding. |
| 5 | Validate `event_produced_at` falls within binding temporal window. |
| 6 | If rollback fields present, validate `rollback_action_type` enum and `rollback_parent` resolvability. |
| 7 | If all checks pass, inject `trace_id` (if missing) and push to durable buffer. |
| 8 | If any check fails, reject event to dead-letter queue with diagnostic tag. |

### 2.4 Storage Implications

#### Canonical Postgres

- `telemetry.event_raw` stores the full event envelope including all binding/stage evidence fields.
- `telemetry.event_normalized` joins `binding_id` to RuntimeBindingStore snapshot for denormalized querying.
- No in-place mutation of binding/stage fields after write (append-only policy, TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md §5.3).

#### ClickHouse Mirror

- ClickHouse mirrors receive the same binding/stage evidence fields.
- Dashboards MUST display `deployment_stage` (not just `environment`) as the authoritative stage label.
- `mirror_last_synced_at` latency must be visible on all dashboards that filter by stage.

---

## 3. Migration Checklist for TEL-001

### 3.1 Pre-Conditions

- [ ] **RUN-001** status is `done` (or at minimum `review_approved` with owner consent to proceed).
- [ ] **RUN-001A** artifacts are stable and accessible at:
  - `services/execution/runtime-manager/authority_matrix.md`
  - `services/execution/runtime-manager/runtime_binding.schema.json`
  - `services/execution/runtime-manager/rollback_action_matrix.md`
- [ ] **TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md** is stable (currently L1 canonical).

### 3.2 Schema Changes

- [ ] **SCH-1**: Add `binding_id` (UUID, required) to the telemetry event schema.
- [ ] **SCH-2**: Add `deployment_stage` (enum: `paper`, `canary`, `live`, `frozen`, required) to the telemetry event schema.
- [ ] **SCH-3**: Add `plan_id` (string, required) to the telemetry event schema.
- [ ] **SCH-4**: Add `persona_capital_binding_id` (string, required) to the telemetry event schema.
- [ ] **SCH-5**: Add `rollback_parent` (UUID, nullable) to the telemetry event schema.
- [ ] **SCH-6**: Add `rollback_action_type` (enum: `replace`, `pause_then_replace`, `liquidate_then_replace`, nullable) to the telemetry event schema.
- [ ] **SCH-7**: Ensure `artifact_id`, `artifact_version`, and `runtime_id` are present in the existing telemetry schema (they should already exist; if not, add them).
- [ ] **SCH-8**: Define `environment` as a derived alias of `deployment_stage` for backward compatibility. Document deprecation timeline.

### 3.3 Ingest Changes

- [ ] **ING-1**: Update `telemetry-ingest-svc` to resolve `binding_id` against RuntimeBindingStore at ingest time.
- [ ] **ING-2**: Implement evidence contract validation steps E-1 through E-6 (§2.3 above).
- [ ] **ING-3**: Route events with unresolved `binding_id` to durable buffer with retry (not dead-letter) for up to a configurable grace period.
- [ ] **ING-4**: Route events that fail evidence contract validation to dead-letter queue with diagnostic tag.
- [ ] **ING-5**: Ensure `deployment_stage` is normalized from RuntimeBinding `deployment_mode` (no independent inference).

### 3.4 Storage Changes

- [ ] **STO-1**: Update `telemetry.event_raw` DDL to include new binding/stage fields.
- [ ] **STO-2**: Update `telemetry.event_normalized` DDL to include denormalized binding snapshot.
- [ ] **STO-3**: Verify partition strategy (by `event_date`, `environment`, optionally `capital_pool_id` hash) still performs with new fields.
- [ ] **STO-4**: Update CDC/ETL pipeline to mirror new fields to ClickHouse.
- [ ] **STO-5**: Verify ClickHouse analytical queries that filter by `deployment_stage` have appropriate indexes/projections.

### 3.5 Runtime Producer Changes

- [ ] **PRD-1**: Update LEAN runtime event producers to inject `binding_id`, `deployment_stage`, `plan_id`, and `persona_capital_binding_id` from the active RuntimeBinding context.
- [ ] **PRD-2**: Update runtime-manager event producers to inject `rollback_parent` and `rollback_action_type` during rollback transitions.
- [ ] **PRD-3**: Ensure `event_produced_at` is set to the actual event generation time (not wall clock of ingest).
- [ ] **PRD-4**: Verify that events produced during binding transitions (e.g., `active → pending_pause → paused → retired`) carry the correct binding identity throughout.

### 3.6 Cross-Document Alignment

- [ ] **DOC-1**: Update `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md` to reference the new binding/stage evidence fields.
- [ ] **DOC-2**: Update `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` §11 (follow-on specs) to mark binding/stage field injection as complete.
- [ ] **DOC-3**: Ensure `ROLLBACK_AND_POSITION_SEMANTICS.md` telemetry cutover semantics (§2, §3 of rollback_action_matrix.md) are reflected in the telemetry schema.
- [ ] **DOC-4**: Verify `BINDING_AND_DEPLOYMENT_SEMANTICS.md` references telemetry as a consumer of RuntimeBinding state.

### 3.7 Verification

- [ ] **VER-1**: Smoke test: produce telemetry events from a mock RuntimeBinding with all evidence fields present; verify ingest accepts them.
- [ ] **VER-2**: Negative test: produce events with missing/invalid `binding_id`; verify dead-letter routing.
- [ ] **VER-3**: Negative test: produce events with mismatched `deployment_stage`; verify rejection.
- [ ] **VER-4**: Negative test: produce events outside binding temporal window; verify rejection.
- [ ] **VER-5**: Rollback test: produce events during a `pause_then_replace` transition; verify `rollback_parent` and `rollback_action_type` are correctly populated.
- [ ] **VER-6**: Query test: verify Postgres `telemetry.event_raw` can be joined to RuntimeBindingStore on `binding_id`.
- [ ] **VER-7**: Mirror test: verify ClickHouse receives new fields and dashboards display `deployment_stage` correctly.

### 3.8 Rollback / Safety

- [ ] **SAF-1**: Define feature flag or config toggle to enable/disable binding/stage field injection (for safe rollout).
- [ ] **SAF-2**: During transition period, accept events both WITH and WITHOUT binding/stage fields (graceful degradation).
- [ ] **SAF-3**: Define cut-over date after which events WITHOUT binding/stage fields are rejected.
- [ ] **SAF-4**: Ensure dead-letter queue monitoring alerts fire if rejection rate spikes after deployment.

---

## 4. Gap Analysis

### 4.1 Open Items for TEL-001 Contract Lock

| Item | Description | Impact | Owner |
|---|---|---|---|
| **G-1** | `environment` vs `deployment_stage` naming. This packet recommends `deployment_stage` as canonical, with `environment` as a legacy alias. TEL-001 owner should confirm this naming decision with the telemetry ingest team. | Low — affects field naming only. | TEL-001 owner |
| **G-2** | RuntimeBindingStore read access pattern for telemetry-ingest-svc. Does ingest-svc query the store directly, or through a read API? This packet assumes direct store access is acceptable for v1. | Medium — affects ingest implementation. | TEL-001 owner + RUN-001 owner |
| **G-3** | Buffer retention policy for events waiting on unresolved bindings. How long should the durable buffer hold an event whose `binding_id` does not yet exist? | Medium — affects buffer configuration. | TEL-001 owner |
| **G-4** | ClickHouse projection strategy for new binding/stage fields. Should there be a dedicated projection keyed on `(deployment_stage, capital_pool_id, event_date)` for dashboard queries? | Low — performance optimization. | TEL-002 owner |

### 4.2 Dependency Status

| Dependency | Status | Impact on TEL-001A |
|---|---|---|
| RUN-001A | `done` | Source artifacts stable and consumed. |
| RUN-001 | `review_approved` | RuntimeBinding schema and authority locked; awaiting owner finalization. Field map is based on reviewed-and-approved schema. |
| TEL-001 | `todo` | This packet is the preparatory work; TEL-001 owner can begin implementation immediately using this document as the spec. |

---

## 5. Reviewer Packet (for Codex)

### 5.1 What This Document Provides

1. **Telemetry Field Map** (§1): Complete mapping of every RuntimeBinding field into the telemetry event envelope, with validation rules.
2. **Binding/Stage Evidence Contract** (§2): Six evidence requirements (E-1 through E-6) that ensure every telemetry event can prove its origin.
3. **Migration Checklist** (§3): 30+ actionable items across schema, ingest, storage, producer, documentation, verification, and safety categories.
4. **Gap Analysis** (§4): Four open items flagged for TEL-001 owner resolution.

### 5.2 Expected Outcomes After Review Approval

- TEL-001 owner has a complete field-level specification to implement against.
- RuntimeBinding fields are fully traced into the telemetry schema.
- Migration path is clear with pre-conditions, verification steps, and safety guards.
- No re-scoping needed when RUN-001 transitions from `review_approved` to `done`.

### 5.3 Cross-Check Summary

| Source | Verified Against | Status |
|---|---|---|
| `runtime_binding.schema.json` | Field map §1.2 | ✅ All 13 RuntimeBinding fields mapped |
| `authority_matrix.md` | Write authority boundaries | ✅ Runtime Manager confirmed as sole binding writer |
| `rollback_action_matrix.md` | Rollback telemetry semantics | ✅ 3 action types mapped, position lineage preserved |
| `review_run001_codex_approved_zh.md` | Canonical rollback vocabulary | ✅ `replace / pause_then_replace / liquidate_then_replace` confirmed |
| `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md` | Ingest architecture | ✅ Postgres canonical store, durable buffer, ClickHouse mirror all referenced |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §19 | Runtime Manager authority | ✅ Confirmed execution plane exclusive write owner |
