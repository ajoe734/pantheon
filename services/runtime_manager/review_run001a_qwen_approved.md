# RUN-001A Review — Qwen

**Task**: Prepare runtime authority matrix and rollback prework  
**Owner**: Gemini  
**Reviewer**: Qwen  
**Date**: 2026-04-10  
**Status**: review → review_approved

---

## 1. Review Summary

**APPROVED for v1 lock.** All three acceptance criteria are met. The artifacts provide a solid foundation for RUN-001 (RuntimeBinding definition) and EX-002 (rollback execution alignment) follow-on work.

---

## 2. Acceptance Criteria Verification

### AC1: Authority matrix maps deployment plan binding and runtime binding write owners ✅

**Artifact**: `services/execution/runtime-manager/authority_matrix.md`

The authority matrix correctly identifies:
- **Runtime Manager** as the sole write owner for `RuntimeBinding` core fields, status, timestamps, and rollback references.
- **DeploymentPlan** (status: approved/executing) as the authority source/trigger for binding creation — implementing the deny-first policy from §3.1.
- **Position Lineage** fields (`opened_by_artifact_id`, `current_managed_by_binding_id`) are correctly attributed to Runtime Manager, aligned with `ROLLBACK_AND_POSITION_SEMANTICS.md` §9.
- **Runtime Health** (heartbeat) fields are included as a distinct object class, preventing conflation with binding state.

**Governance constraints (§3)** are well-formed:
1. Deny-first for deployment — Runtime Manager must resolve an active DeploymentPlan before writing bindings.
2. Immutable history — retired/failed bindings cannot have core fields modified.
3. Lineage preservation — replace rollbacks update position management pointers.
4. Stage enforcement — DeploymentPlan.target_stage must match RuntimeBinding.deployment_mode.

**Conflict resolution (§4)** correctly implements fail-safe semantics: plan transitions to `failed` rather than attempting unsafe recovery.

### AC2: Runtime binding field inventory is documented against L1 semantics ✅

**Artifact**: `services/execution/runtime-manager/runtime_binding.schema.json`

The JSON schema aligns with L1 canonical documents:

| Field | L1 Source | Status |
|---|---|---|
| `binding_id` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present |
| `runtime_id` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present |
| `capital_pool_id` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present |
| `artifact_id` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present |
| `artifact_version` | deployment_plan.schema.json (pattern `^\\d+\\.\\d+\\.\\d+$`) | ✅ Aligned |
| `deployment_mode` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present (enum: paper/canary/live/frozen) |
| `effective_at` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present |
| `retired_at` | ROLLBACK_AND_POSITION_SEMANTICS.md §8 | ✅ Present (optional) |
| `status` | BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | ✅ Present (enum extended: active/retired/failed/pending_pause/paused) |
| `plan_id` | deployment_plan.schema.json §rollback | ✅ Present |
| `rollback_parent` | ROLLBACK_AND_POSITION_SEMANTICS.md §8 | ✅ Present |
| `rollback_action_type` | ROLLBACK_AND_POSITION_SEMANTICS.md §10 | ✅ Present (enum: replace/pause_then_replace/liquidate_then_replace) |
| `metadata` | General extensibility | ✅ Present (additionalProperties: false) |

**Enum alignment check**:
- `deployment_mode` enum (`paper`, `canary`, `live`, `frozen`) matches `DeploymentPlan.current_stage` and `target_stage` enums in `deployment_plan.schema.json`. ✅
- `status` enum (`active`, `retired`, `failed`, `pending_pause`, `paused`) extends the base L1 status set with operational states needed for rollback sequencing. This is acceptable — L1 docs list statuses conceptually, not exhaustively.
- `rollback_action_type` enum matches `DeploymentPlan.rollback.action_type` enum (`replace_binding`, `pause_then_replace`, `liquidate_then_replace`). Minor naming drift: schema uses `replace` vs `replace_binding`. See §3 below.

### AC3: Rollback action matrix is ready for RUN-001 and EX-002 handoff ✅

**Artifact**: `services/execution/runtime-manager/rollback_action_matrix.md`

The rollback action matrix correctly maps:
- **`replace`**: hot-swap with position preservation — minimal disruption path.
- **`pause_then_replace`**: drain-then-swap with position stabilization — medium-risk path.
- **`liquidate_then_replace`**: flatten-then-swap with zero-position verification — high-risk path.

**Position lineage rules (§3)** are correct:
- `opened_by_artifact_id` remains immutable (trade provenance preserved).
- `current_managed_by_binding_id` updates to new binding after cutover.

**Operational guards (§4)** are well-scoped:
- Timeout policy for escalation to Severity-1 incident.
- Atomic swap requirement prevents telemetry gaps.

---

## 3. Non-Blocking Corrections

### 3.1 `rollback_action_type` enum naming drift (LOW)

The RuntimeBinding schema uses `rollback_action_type` enum values: `["replace", "pause_then_replace", "liquidate_then_replace"]`.

The DeploymentPlan schema's `rollback.action_type` uses: `["replace_binding", "pause_then_replace", "liquidate_then_replace"]`.

There is a naming inconsistency: `replace` vs `replace_binding`. This is a minor schema alignment issue. For v1, both are functionally clear, but downstream consumers (EX-002, telemetry ingest, lineage queries) should standardize on one vocabulary. **Recommendation**: align to `replace` (shorter, consistent with ROLLBACK_AND_POSITION_SEMANTICS.md §10 table which uses `replace`).

### 3.2 Authority matrix missing `metadata` field reference (LOW)

The authority matrix (§2) lists core RuntimeBinding fields but does not include `metadata`. Since `metadata` is part of the schema and is writeable by Runtime Manager, consider adding a row for it. Not blocking — the schema itself is the authoritative field list.

### 3.3 `frozen` in `deployment_mode` (MEDIUM, informational)

The schema includes `frozen` as a `deployment_mode` enum value. BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 does not explicitly list `frozen` in the RuntimeBinding structure text, but `ROLLBACK_AND_POSITION_SEMANTICS.md` §11 distinguishes rollback (operational) from freeze (governance quarantine). Including `frozen` as a deployment mode is the right call — it allows the runtime to record a governance-imposed freeze state without conflating it with `failed`. This is aligned with the L1 decision that freeze and rollback are distinct but may co-occur.

---

## 4. Cross-Reference Validation

| L1 Document | Alignment Check | Result |
|---|---|---|
| BINDING_AND_DEPLOYMENT_SEMANTICS.md §7.3 | RuntimeBinding field structure | ✅ Aligned |
| ROLLBACK_AND_POSITION_SEMANTICS.md §8 | Rollback lineage, rollback_parent, rollback_action_type | ✅ Aligned |
| ROLLBACK_AND_POSITION_SEMANTICS.md §9 | Position lineage fields | ✅ Aligned |
| ROLLBACK_AND_POSITION_SEMANTICS.md §10 | Rollback action types and owners | ✅ Aligned |
| deployment_plan.schema.json | Stage enums, rollback.action_type, runtime_action | ✅ Aligned (see §3.1 for minor drift) |
| CANONICAL_CONTRACT_MIGRATION_DECISION.md §4.3 | RuntimeManager as RuntimeBinding owner | ✅ Aligned |
| CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md | Saga path: DeploymentPlan → RuntimeBinding | ✅ Aligned |

---

## 5. Readiness for Follow-On Tasks

### RUN-001 (Define RuntimeBinding and runtime-manager authority)
The prep package provides all necessary scaffolding:
- Authority matrix defines write boundaries.
- Schema provides the canonical field inventory.
- Governance constraints are explicit and enforceable.

**Ready for consumption.**

### EX-002 (Align rollback execution actions with runtime-manager semantics)
The rollback action matrix provides:
- Three action types with clear runtime manager behavior.
- Position treatment per action type.
- Telemetry cutover semantics.
- Operational guards (timeout, atomicity).

**Ready for consumption.**

---

## 6. Verdict

**APPROVED.** All acceptance criteria met. Two low-severity and one informational non-blocking corrections documented. The artifacts are sufficient for RUN-001 and EX-002 owners to begin implementation without redefining scope.
