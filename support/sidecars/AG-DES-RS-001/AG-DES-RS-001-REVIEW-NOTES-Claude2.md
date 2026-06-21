# AG-DES-RS-001-SIDECAR-REVIEW — Reviewer Notes

**Reviewer:** Claude2  
**Review date:** 2026-06-21  
**Task:** AG-DES-RS-001-SIDECAR-REVIEW  
**Reviewed artifact:** `support/sidecars/AG-DES-RS-001/AG-DES-RS-001-SIDECAR-REVIEW.md`  
**Verdict:** APPROVED

---

## 1. B1–B10 Coverage Verification

Each decision in section 3 of the review packet was cross-checked against
`docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md`
and both JSON schemas.

| Section | Packet claim | Verified? | Notes |
|---|---|---|---|
| B1 — Boundary | BFF facade, no truth ownership | ✓ | Matches design doc verbatim |
| B2 — Plan-first | plan_id required on all run payloads | ✓ | Required in both schemas |
| B3 — API routes | 10 typed endpoints | ✓ | All 10 operationIds present in `08_openapi_v1_3_delta.yaml` |
| B4 — Plan approval | draft → trader → optional governance | ✓ | `approval.state` enum and optional object match |
| B5 — Typed stages | 12 stage types, LLM proposes intent | ✓ | `stage_type` enum has exactly 12 entries matching B5 table |
| B6 — No silent fallback | `fallback_policy: fail_closed\|explicit_fixture_only` | ✓ | Routing sub-object enforces this |
| B7 — DAG / concurrency | DAG via dependencies[]; hard max parallel 4 | ✓ | `budget.max_parallel_stages` maximum=4; default=2 is a runtime concern, not schema-enforced (correct) |
| B8 — Run projection | Full identity, lineage, metrics, findings, no-order-route | ✓ | All required fields present in `ResearchRunProjection` |
| B9 — Progress semantics | Monotonic percent 0–100; retry = new attempt | ✓ | Schema enforces range; monotonicity is runtime contract (correctly noted) |
| B10 — No-order-route | `no_order_route_proof` required; `execution_authority: research_only` | ✓ | Both schema and manifest enforce boundary |

All B1–B10 decisions are correctly and completely represented.

---

## 2. Schema Quality Assessment

**`research_plan_execution.schema.json`**

- `$id` and title match packet claims. ✓
- All required fields per packet (plan_id, workshop_id, strategy_id, strategy_spec_registry_id, status, stages, no_order_route_proof, created_at) are listed as `required`. ✓
- `approval` is correctly optional at object level with `state` required within it. ✓
- `budget.max_parallel_stages` maximum=4 confirmed. ✓
- `additionalProperties: false` at root and in all sub-objects (stage, routing, budget, approval). ✓
- `spec_version` is required with enum `["1.0"]` — good for forward compatibility. ✓

**`research_run_projection.schema.json`**

- `no_order_route_proof` is required, enum `["research_only_not_direct_action"]`. ✓
- Metric `category` enum has exactly 7 values (performance, risk, cost, capacity, robustness, calibration, data_quality) matching B8. ✓
- `evidence_ref` uses typed `ref_type` enum (10 types). ✓
- `additionalProperties: false` throughout. ✓
- `progress.percent` has minimum=0, maximum=100. ✓
- `execution_status` enum matches B9 status set (queued, dispatching, running, succeeded, failed, cancelled, timed_out). ✓

No schema defects found.

---

## 3. Downstream Unblock Table Verification

The four blocked tasks in section 5 are:
- `AG-BE-RS-001`: Backend RS facade/OpenAPI — logically dependent on RS routes. ✓
- `AG-BE-RS-002`: Backend RS routing/projection — logically dependent on RS schemas. ✓
- `AG-BE-RS-004`: Backend combining VERS + RS — logically dependent on both. ✓
- `AG-FE-RS-001`: Frontend generated types — logically dependent on VERS + RS + CARD. ✓

The table is accurate. The unblock trigger (schemas and routes merged into
`services/control-plane/specs/agora/v4/` and OpenAPI into `agora_v1_3.openapi.yaml`)
is the correct gate condition for all four tasks.

---

## 4. Open Flag Dispositions

### Flag 1 — Approval rejection representation (`terminal_reason`)

**Disposition: Accept as-is.**

B2 states `status=cancelled + terminal_reason=approval_rejected` for a rejected plan.
The schema does not include a `terminal_reason` field.

Rationale: B2 also explicitly says "The new projection may expose `approval.state=rejected`;
it must not introduce an incompatible base lifecycle value." The combination of
`status=cancelled` + `approval.state=rejected` in the existing schema conveys the
rejection semantic without a new lifecycle value. `terminal_reason` is a BFF response
body convenience field; adding it to the canonical schema would be implementation scope,
not design scope. No schema amendment required.

### Flag 2 — Compat route absence

**Disposition: Accept as-is.**

B3 states the older `POST /bff/agora/workshops/{workshop_id}/research-runs` route
"may remain for compatibility." The word "may" is permissive; the route is not mandated.
Its absence from the OpenAPI delta is consistent with the design intent. The implementation
task (AG-BE-RS-001) may add it with appropriate plan-enforcement if needed. No explicit
deprecation notice is required in the design delta.

### Flag 3 — `strategy_spec_registry_id` vs `strategy_id`

**Disposition: Accept as-is.**

Both identifiers serve distinct purposes (`strategy_spec_registry_id` = the canonical
registry entry for the versioned spec; `strategy_id` = the strategy entity identity).
JSON Schema cannot express cross-entity consistency rules. BFF enforcement at the
application layer is the correct gate for this constraint. No schema amendment required.

### Flag 4 — No retry attempt ID in schema

**Disposition: Accept as-is.**

B9 states "A retry creates a new attempt ID." The schema has no `attempt_id` field.
This is intentional: each retry creates a new `run_id` and a new `ResearchRunProjection`
document. The "attempt ID" is the `run_id` itself. There is no need for a separate
`attempt_id` or `attempt_count` on the projection. The prior-attempt relation is
maintained at the plan level via `run_ids[]`. No schema amendment required.

---

## 5. Summary

All design deliverables for AG-DES-RS-001 are present and consistent.
All four open flags have `accept as-is` dispositions; no schema amendments are required
before the parent task closes.

The review packet is accurate, complete, and ready for parent task finalization.
