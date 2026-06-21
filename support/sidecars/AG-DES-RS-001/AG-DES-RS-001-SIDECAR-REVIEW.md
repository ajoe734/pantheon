# AG-DES-RS-001 — Review Packet & Evidence Summary

**Sidecar kind:** review_packet  
**Parent task:** AG-DES-RS-001 (Research Facade / Stage Routing / Run Projection)  
**Prepared by:** Claude (AG-DES-RS-001-SIDECAR-REVIEW)  
**Reviewer target:** Claude2  
**Date:** 2026-06-21  
**Status:** ready for reviewer

---

## 1. Task Summary

AG-DES-RS-001 delivers the v1.3 Agora design contract for the Research subsystem:

- **Plan-first enforcement** — every research run must reference an approved `ResearchPlan`; no run can bypass approval.
- **Typed stage routing** — 12 typed stage kinds; the LLM proposes intent, a route policy resolves the effective framework backend.
- **Run projection** — a rich read model exposing identity, lineage, backend mode, metrics, findings, and no-order-route proof.
- **No-order-route boundary** — a research completion event cannot create a `RuntimeBinding`, capital binding, or broker order.

This is a **design-only** slice. It produces prose, schemas, and OpenAPI routes. No runtime implementation is included.

---

## 2. Artifact Evidence

### 2.1 Canonical design document

| Artifact | Path | Merged? |
|---|---|---|
| Research facade prose (B1–B10) | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` | Yes (via PR #2053 / commit `7b4f553b`) |

### 2.2 JSON Schemas

| Schema | `$id` | Path |
|---|---|---|
| `ResearchPlanExecution` | `https://pantheon/agora/research_plan_execution/v1` | `design-closure-round2/schemas/research_plan_execution.schema.json` |
| `ResearchRunProjection` | `https://pantheon/agora/research_run_projection/v1` | `design-closure-round2/schemas/research_run_projection.schema.json` |

Both schemas are present in the merged closure round2 bundle (commit `7b4f553b`).

Target canonical path (post-implementation): `services/control-plane/specs/agora/v4/`

### 2.3 OpenAPI routes (delta in `08_openapi_v1_3_delta.yaml`)

| Operation | Method | Path |
|---|---|---|
| `listWorkshopResearchPlans` | GET | `/bff/agora/workshops/{workshop_id}/research-plans` |
| `createWorkshopResearchPlan` | POST | `/bff/agora/workshops/{workshop_id}/research-plans` |
| `getAgoraResearchPlan` | GET | `/bff/agora/research-plans/{plan_id}` |
| `approveAgoraResearchPlan` | POST | `/bff/agora/research-plans/{plan_id}/approve` |
| `cancelAgoraResearchPlan` | POST | `/bff/agora/research-plans/{plan_id}/cancel` |
| `listAgoraResearchPlanRuns` | GET | `/bff/agora/research-plans/{plan_id}/runs` |
| `dispatchAgoraResearchPlan` | POST | `/bff/agora/research-plans/{plan_id}/runs` |
| `getAgoraResearchRun` | GET | `/bff/agora/research-runs/{run_id}` |
| `cancelAgoraResearchRun` | POST | `/bff/agora/research-runs/{run_id}/cancel` |
| `listAgoraResearchRunArtifacts` | GET | `/bff/agora/research-runs/{run_id}/artifacts` |

### 2.4 Capability manifest entry

`capability_manifest_v1_3.json` registers:

```json
{
  "name": "agora.research.v1",
  "version": "1.1",
  "description": "Adds plan-first research facade, governed stage routing and run/result projections.",
  "bff_path_prefixes": [
    "/bff/agora/research-plans",
    "/bff/agora/research-runs",
    "/bff/agora/workshops/{workshop_id}/research-plans"
  ],
  "schemas": [
    "v4/research_plan_execution.schema.json",
    "v4/research_run_projection.schema.json"
  ],
  "auth_level": "agora_user",
  "execution_authority": "research_only"
}
```

---

## 3. Design Decision Verification

| Section | Decision | Status |
|---|---|---|
| B1 — Boundary | Agora is a BFF facade; no truth ownership | Documented |
| B2 — Plan-first | Every run must reference an approved ResearchPlan | Enforced in schema (`plan_id` required on all run payloads) |
| B3 — API routes | 10 typed endpoints across plans and runs | Present in OpenAPI delta |
| B4 — Plan approval | Draft → Trader acceptance → optional governance approval | Documented; `approval.state` field in plan schema |
| B5 — Typed stages | 12 stage types; LLM proposes, route policy resolves | Enum in `stage_type` and `preferred_backend` |
| B6 — No silent fallback | `backend_mode=fixture\|stub` explicit only | `fallback_policy: fail_closed\|explicit_fixture_only` in routing sub-object |
| B7 — DAG / concurrency | DAG via `dependencies[]`; max parallel stages 4 | `budget.max_parallel_stages` capped at 4 in schema |
| B8 — Run projection | Full identity, lineage, metrics, findings, no-order-route | All fields present in `ResearchRunProjection` |
| B9 — Progress semantics | Monotonic percent; retry creates new attempt ID | `progress.percent` constrained 0–100; schema does not allow decrement per design |
| B10 — No-order-route | Research completion cannot create RuntimeBinding or order | `no_order_route_proof` required enum field; `execution_authority: research_only` in manifest |

---

## 4. Schema Quality Notes

**`research_plan_execution.schema.json`**
- All required fields present: `plan_id`, `workshop_id`, `strategy_id`, `strategy_spec_registry_id`, `status`, `stages`, `no_order_route_proof`, `created_at`.
- `approval` sub-object is optional (not required on draft plans). States: `pending | approved | rejected | not_required`.
- Stage `routing` sub-object enforces `preferred_backend` and `fallback_policy` as required.
- `additionalProperties: false` throughout (tight schema, no silent expansion).
- `budget.max_parallel_stages` maximum is 4 — matches B7.

**`research_run_projection.schema.json`**
- All required fields present including `no_order_route_proof` (enum `research_only_not_direct_action`).
- Metrics use typed `category` enum (7 categories); each carries `gate_result`.
- `evidence_refs` uses `EvidenceRef` object with typed `ref_type` enum.
- `additionalProperties: false` throughout.
- `progress.percent` is `minimum: 0, maximum: 100` — monotonicity is a runtime contract, not enforceable in JSON Schema alone (acceptable).

---

## 5. Downstream Unblock Conditions

The following tasks remain blocked **until** AG-DES-RS-001 schemas and routes are merged into `services/control-plane/specs/agora/v4/` and the OpenAPI merged into `agora_v1_3.openapi.yaml`:

| Blocked task | Unblocked by |
|---|---|
| `AG-BE-RS-001` | RS facade/OpenAPI merged |
| `AG-BE-RS-002` | RS routing/projection schema merged |
| `AG-BE-RS-004` | VERS + RS merged |
| `AG-FE-RS-001` | VERS + RS + CARD generated types mirrored |

These tasks must cite merged artifact paths, not design brief section numbers (per dispatch rule in `07_dispatch_unblock_matrix.md`).

---

## 6. Open Questions / Reviewer Flags

1. **Approval rejection representation** — B2 states a rejected approval is represented as `status=cancelled` + `terminal_reason=approval_rejected`. The current `ResearchPlanExecution` schema does not include a `terminal_reason` field. The reviewer should confirm whether this field should be added to the schema or remains a BFF-layer concern only.

2. **Compat route** — B3 mentions the older `POST /bff/agora/workshops/{workshop_id}/research-runs` route "may remain for compatibility." This route is **absent** from the OpenAPI delta. The reviewer should confirm whether it needs an explicit deprecation notice or can simply be omitted as not yet active.

3. **`strategy_spec_registry_id` vs `strategy_id`** — Both are required on ResearchPlanExecution and ResearchRunProjection. The distinction is clear in intent (registry entry vs strategy identity), but no cross-validation rule is encoded in the schema. Reviewer should confirm this is acceptable at the design layer, with enforcement deferred to BFF.

4. **No retry attempt ID in schema** — B9 states "a retry creates a new attempt ID." The `ResearchRunProjection` schema has no `attempt_id` or `attempt_count` field. Reviewer should confirm whether this is intentional (implementation detail, not projected to the BFF layer) or an omission.

---

## 7. Acceptance Summary

All design deliverables for AG-DES-RS-001 are present in the merged round2 package:

- [x] Prose contract (B1–B10) merged in `02_research_facade_run_projection.md`
- [x] `ResearchPlanExecution` schema present with all required fields and enums
- [x] `ResearchRunProjection` schema present with full metric, finding, and evidence model
- [x] 10 typed OpenAPI routes present in the v1.3 delta
- [x] `agora.research.v1` capability entry registered in `capability_manifest_v1_3.json`
- [x] `execution_authority: research_only` and `no_order_route_proof` enforce the no-order-route boundary

Items **not** in scope for this design slice (deferred to implementation tasks):
- Runtime BFF handlers for these routes
- Contract tests against the schemas
- Frontend generated types (unblocked after AG-DES-RS-001 + AG-DES-VERS-001 + AG-DES-CARD-001 merge)
- `terminal_reason` field (see flag #1 above)

---

## 8. Handoff Note for Reviewer (Claude2)

This packet covers the completeness and consistency of the AG-DES-RS-001 design slice only. No canonical truth has been modified by this sidecar.

**Reviewer action items:**
1. Validate section 3 (Design Decision Verification) — confirm all B1–B10 decisions are correctly reflected.
2. Review section 6 (Open Questions) and return a disposition for each flag (accept as-is, or require a schema amendment before the parent task closes).
3. Confirm the downstream unblock table in section 5 is accurate.
4. Approve via `AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-DES-RS-001/AG-DES-RS-001-SIDECAR-REVIEW.md ./scripts/ai-status.sh approve AG-DES-RS-001-SIDECAR-REVIEW "…"` when satisfied.

The parent task AG-DES-RS-001 may remain open while this sidecar is under review; this packet is a parallel support artifact.
