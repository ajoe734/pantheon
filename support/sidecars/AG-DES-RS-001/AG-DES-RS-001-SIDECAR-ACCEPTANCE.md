# AG-DES-RS-001 Acceptance Packet

**Sidecar ID:** AG-DES-RS-001-SIDECAR-ACCEPTANCE  
**Parent Task:** AG-DES-RS-001 — Research facade / stage routing / run projection  
**Sidecar Kind:** acceptance_packet  
**Date:** 2026-06-21  
**Author:** Claude (auto-worker)  
**Reviewer:** Claude2  
**Status:** ready_for_review  

> **Scope notice.** This packet is a support artifact only.
> It does not modify canonical truth (L1 policy, OpenAPI contracts, DB schemas, or service implementations).
> All design decisions cited here originate from:
> - `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` (Round 2 SD response, section B)
> - `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/MASTER_SD_RESPONSE.md` (Master SD response)
> - `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/07_dispatch_unblock_matrix.md` (Dispatch unblock matrix)
> - `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/08_openapi_v1_3_delta.yaml` (OpenAPI v1.3 delta)
> - `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/research_plan_execution.schema.json`
> - `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/research_run_projection.schema.json`

---

## 1. What AG-DES-RS-001 delivers

AG-DES-RS-001 is one of seven required v1.3 design/contract tasks from the Agora Round 2 SD response. It defines the **research facade, stage routing contract, and run projection schema** for the Agora BFF layer.

### 1.1 Design deliverables

| Artifact | Target canonical path |
|---|---|
| Research facade + stage routing prose | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` |
| `ResearchPlanExecution` JSON schema | `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` |
| `ResearchRunProjection` JSON schema | `services/control-plane/specs/agora/v4/research_run_projection.schema.json` |
| OpenAPI v1.3 research routes | `services/control-plane/openapi/agora_v1_3.openapi.yaml` (10 research routes) |
| Bundle index v1.3 (includes RS schemas) | `services/control-plane/specs/agora/bundle_index.v1_3.json` |

### 1.2 Core semantic decisions

1. **Plan-first**: every research run must reference a persisted, approved `ResearchPlan`. The older `POST /bff/agora/workshops/{workshop_id}/research-runs` route may remain for compatibility but must materialize or reference a `ResearchPlan` and must not bypass approval.

2. **Schema-frozen lifecycle**: the base `ResearchPlan` lifecycle (`draft → approved → running → completed → cancelled`) is frozen for v1 compatibility. A rejected approval is represented as `status=cancelled` with `terminal_reason=approval_rejected`. The new projection may expose `approval.state=rejected` without introducing an incompatible base status value.

3. **Typed stage routing**: research stages are typed (12 stage types). The LLM proposes stage intent; route policy resolves the effective backend. No arbitrary tool names are allowed. No silent stub fallback in production/dev integration.

4. **Fail-closed backend mode**: `backend_mode=fixture|stub` must be explicitly requested for smoke/CI. Capability unavailable returns a typed blocked stage, not a synthetic successful result. Fixture/stub runs are visibly labelled and cannot satisfy full-validation readiness gates.

5. **No-order-route proof**: research plans carry `no_order_route_proof = "research_plan_no_order_route"`. Research runs carry `no_order_route_proof = "research_only_not_direct_action"`. A research completion event cannot create `RuntimeBinding`, capital binding, or broker order.

6. **Monotonic progress**: progress percentage must be monotonic within a run attempt. A retry creates a new attempt ID and must not rewind the old attempt's progress.

7. **DAG concurrency limits**: maximum parallel stages default is 2; hard platform maximum is 4.

---

## 2. Canonical BFF routes (v1.3 contract)

Ten routes are introduced by AG-DES-RS-001. All must appear in `agora_v1_3.openapi.yaml`.

| # | Method | Path | operationId | Response schema |
|---|---|---|---|---|
| 1 | GET | `/bff/agora/workshops/{workshop_id}/research-plans` | `listWorkshopResearchPlans` | array of `ResearchPlanExecution` |
| 2 | POST | `/bff/agora/workshops/{workshop_id}/research-plans` | `createAgoraResearchPlan` | `ResearchPlanExecution` |
| 3 | GET | `/bff/agora/research-plans/{plan_id}` | `getAgoraResearchPlan` | `ResearchPlanExecution` |
| 4 | POST | `/bff/agora/research-plans/{plan_id}/approve` | `approveAgoraResearchPlan` | `ResearchPlanExecution` |
| 5 | POST | `/bff/agora/research-plans/{plan_id}/cancel` | `cancelAgoraResearchPlan` | `ResearchPlanExecution` |
| 6 | GET | `/bff/agora/research-plans/{plan_id}/runs` | `listAgoraResearchPlanRuns` | array of `ResearchRunProjection` |
| 7 | POST | `/bff/agora/research-plans/{plan_id}/runs` | `createAgoraResearchRun` | `ResearchRunProjection` |
| 8 | GET | `/bff/agora/research-runs/{run_id}` | `getAgoraResearchRun` | `ResearchRunProjection` |
| 9 | POST | `/bff/agora/research-runs/{run_id}/cancel` | `cancelAgoraResearchRun` | `ResearchRunProjection` |
| 10 | GET | `/bff/agora/research-runs/{run_id}/artifacts` | `listAgoraResearchRunArtifacts` | array of artifact refs |

**Compatibility note**: `POST /bff/agora/workshops/{workshop_id}/research-runs` (legacy) may remain but must materialize a `ResearchPlan` internally. Legacy routes cannot bypass plan-approval gate.

---

## 3. Stage routing table

The acceptance verifier must confirm that all 12 stage types are present in `research_plan_execution.schema.json` under `definitions.stage.properties.stage_type.enum` and that the routing table below matches `02_research_facade_run_projection.md` §B5.

| Stage type | Default backend | Notes |
|---|---|---|
| `source_discovery` | governed source ingestion / allowlisted search | No unrestricted agent crawling |
| `data_validation` | data source registry / validator | Mandatory before dependent stages |
| `prototype_backtest` | vectorbt | Quick rules and candidate scans |
| `alpha_training` | Qlib | Cross-sectional alpha / ranking |
| `rolling_oos` | Qlib | Rolling / walk-forward |
| `econometric_validation` | statsmodels | Cointegration / regime / VAR-VECM |
| `derivatives_pricing_risk` | QuantLib | Options / rates / Greeks |
| `policy_training` | FinRL or RLlib | Activation-gated |
| `parameter_search` | Ray Tune | Research-only optimizer output |
| `portfolio_synthesis` | existing optimizer-svc | Weights / constraints; not a new service |
| `robustness_stress` | orchestrated framework set | Selected by strategy family |
| `evidence_synthesis` | OpenClaw result-synthesis skill | Last stage; no truth ownership |

Backend enum in `research_plan_execution.schema.json`:
`source_ingestion`, `data_validation`, `vectorbt`, `qlib`, `statsmodels`, `quantlib`, `finrl`, `rllib`, `ray_tune`, `optimizer_svc`, `openclaw_result_synthesis`

---

## 4. Schema acceptance checklist

### 4.1 `ResearchPlanExecution` (`research_plan_execution.schema.json`)

| Criterion | Pass condition |
|---|---|
| Required identity fields | `spec_version`, `plan_id`, `workshop_id`, `strategy_id`, `strategy_spec_registry_id` present and required |
| Plan lifecycle enum | `status` enum = `draft, approved, running, completed, cancelled` (no `rejected` in base lifecycle) |
| Approval sub-object | `approval.state` includes `rejected` as a projection-level value; `approval` is not required at the plan level |
| Stage array | `stages[]` has `minItems: 1`; each stage has `stage_id`, `stage_type`, `status`, `dependencies`, `required_capability`, `routing` |
| Stage routing object | `routing.preferred_backend` enum covers all 11 listed backends; `routing.fallback_policy` = `fail_closed` or `explicit_fixture_only` (no silent fallback) |
| Concurrency budget | `budget.max_parallel_stages` max = 4 |
| No-order-route proof | `no_order_route_proof` enum = `research_plan_no_order_route` and is required |
| `additionalProperties: false` | Enforced at plan, stage, routing, and budget levels |

### 4.2 `ResearchRunProjection` (`research_run_projection.schema.json`)

| Criterion | Pass condition |
|---|---|
| Required lineage fields | `run_id`, `plan_id`, `workshop_id`, `strategy_id`, `strategy_spec_registry_id`, `stage_id`, `stage_type` all required |
| Execution status enum | `execution_status` = `queued, dispatching, running, succeeded, failed, cancelled, timed_out` |
| Outcome enum | `outcome` = `pending, pass, fail, inconclusive` |
| Backend object | `backend.requested`, `backend.effective`, `backend.mode` required; `mode` = `real, fixture, stub` |
| Metrics array | Each metric has `category`, `name`, `value`, `gate_result`; `category` enum covers `performance, risk, cost, capacity, robustness, calibration, data_quality` |
| Findings severity | `severity` enum = `info, watch, warning, high, critical` |
| No-order-route proof | `no_order_route_proof` = `research_only_not_direct_action` and is required |
| Evidence refs | `ref_type` covers research-specific types including `research_run`, `experiment_artifact`, `evidence_bundle` |
| Progress monotonicity | `progress.percent` has `minimum: 0, maximum: 100` |
| `additionalProperties: false` | Enforced at run, backend, metric, finding, evidence ref, and progress levels |

---

## 5. No-order-route acceptance matrix

All of the following must be verifiable from the design doc and schema:

| Guard | Evidence |
|---|---|
| Research plans cannot request `canary` or `live` deployment | Plan schema has no deployment-stage field; plan lifecycle terminates at `completed` / `cancelled` |
| Framework adapters produce research artifacts only | `evidence_synthesis` stage has no order-route capability; `no_order_route_proof` is a required field |
| A candidate artifact must go through existing Registry/Governance | AG-DES-RS-001 does not define any Registry promotion path; that remains in AG-DES-VERS-001 scope |
| Research completion event cannot create RuntimeBinding | No `RuntimeBinding`, `capital_binding`, or broker-order field exists in `ResearchRunProjection` |
| Backend mode is always visible in projection | `backend.mode` (real / fixture / stub) is a required field in `ResearchRunProjection` |

---

## 6. Plan approval governance gate

Plan approval is required before dispatch for workshop-driven research. Additional governance approval is required when the plan:

- uses private or restricted data;
- requests paid/external data access;
- uses heavy compute (`compute_tier=heavy`);
- invokes policy training / RL (stage type `policy_training`);
- exceeds configured runtime or budget limits;
- crosses tenant policy boundaries.

These governance conditions must be reflected in the BFF approve endpoint's error codes and in operator-facing error messaging. The acceptance verifier should confirm that the `approve` endpoint in `agora_v1_3.openapi.yaml` lists relevant 422/403 error responses.

---

## 7. Dependency map

### 7.1 What AG-DES-RS-001 depends on

| Depends on | Why |
|---|---|
| `AG-DES-VERS-001` merged | RS design cites strategy version / registry ref in `ResearchPlanExecution` (`strategy_spec_registry_id`); the VERS schema must be stable before RS references it |
| `bundle_index.v1_2.json` immutable and present | AG-DES-RS-001 schemas must extend v1.2 baseline; they cannot overwrite v1.2 content |
| `services/control-plane/specs/agora/v4/` directory created | RS schemas land in this new directory; other v1.3 tasks (VERS, SSE, TR, CARD, E2E) share this target |

### 7.2 What is unblocked once AG-DES-RS-001 merges

Per `07_dispatch_unblock_matrix.md`:

| Downstream task | Unblocked condition |
|---|---|
| `AG-BE-RS-004` | VERS + RS merged |
| `AG-FE-RS-001` | VERS + RS + CARD generated types mirrored |
| `AG-BE-RS-001` | RS facade/OpenAPI merged |
| `AG-BE-RS-002` | RS routing/projection schema merged |

Additionally, `AG-XR-OPENAPI-004` cannot be completed until all v1.3 design tasks (including AG-DES-RS-001) are merged, because the bundle index v1.3 must include every v4 schema's exact-byte SHA-256 hash.

### 7.3 What AG-DES-RS-001 does NOT own

| Concern | Actual owner |
|---|---|
| Strategy version patch / readiness gates | AG-DES-VERS-001 |
| Workshop SSE typed event catalog | AG-DES-SSE-001 |
| Trading Room aggregate / intent handoff | AG-DES-TR-001 |
| Workshop card projections | AG-DES-CARD-001 |
| Winner-branch E2E + isolation matrix | AG-DES-E2E-001 |
| v1.3 OpenAPI + capability manifest + bundle hash | AG-XR-OPENAPI-004 |
| Registry promotion and Governance paths | existing Registry / Governance services |
| RuntimeBinding, capital binding, broker orders | existing execution plane |

---

## 8. Implementation verification checklist

For the implementer of AG-DES-RS-001 (and for AG-XR-OPENAPI-004's bundle validator):

- [ ] `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` present and validates against draft-07
- [ ] `services/control-plane/specs/agora/v4/research_run_projection.schema.json` present and validates against draft-07
- [ ] Both schemas are listed in `services/control-plane/openapi/agora_v1_3.openapi.yaml` under `components.schemas`
- [ ] All 10 research routes are defined in `agora_v1_3.openapi.yaml`
- [ ] `bundle_index.v1_3.json` references both schemas with their exact SHA-256 hashes (generated after merge, not pre-computed from design package)
- [ ] Legacy `POST /bff/agora/workshops/{workshop_id}/research-runs` route either removed or internally creates a `ResearchPlan` (cannot bypass approval)
- [ ] `backend_mode=real` is the default; `fixture` and `stub` require explicit parameter
- [ ] `no_order_route_proof` field is present and non-optional on both plan and run projections
- [ ] `policy_training` stage type is present in enum; activation-gate state is surfaced in `backend.activation_state`
- [ ] Progress percent monotonicity is enforced: a retry must create a new `attempt_id`, not reset the existing attempt
- [ ] Schema `additionalProperties: false` is enforced at every nested level (plan, stage, routing, budget, run, backend, metric, finding, evidence ref)

---

## 9. Cross-repo notes

AG-DES-RS-001 lives in `pantheon`. The Agora frontend repo (`execute-plans`) will consume the generated TypeScript types from `agora_v1_3.openapi.yaml` after `AG-XR-OPENAPI-004` merges the final bundle. Frontend tasks `AG-FE-RS-001` are gated on:

1. `AG-DES-VERS-001` merged (provides `strategy_spec_registry_id` type)
2. `AG-DES-RS-001` merged (provides research plan/run route and schema)
3. `AG-DES-CARD-001` merged (provides card projection types used by research result cards)
4. `AG-XR-OPENAPI-004` merged (provides generated types and exact bundle hashes)

No frontend work should cite design doc section numbers. Frontend task briefs must cite the merged schema path and the v1.3 bundle hash.

---

## 10. Acceptance summary

AG-DES-RS-001 is accepted when all of the following are true:

1. `02_research_facade_run_projection.md` is merged to `docs/04/.../design-closure-round2/` with plan-first rule, 12 typed stage definitions, and B1–B10 sections intact.
2. `research_plan_execution.schema.json` and `research_run_projection.schema.json` are merged to `services/control-plane/specs/agora/v4/` with all required fields, correct enums, and `additionalProperties: false` at every level.
3. All 10 research routes are included in `agora_v1_3.openapi.yaml` with typed request/response schemas.
4. Both schemas appear in `bundle_index.v1_3.json` with their exact SHA-256 hashes.
5. No v1.0, v1.1, or v1.2 bundle files are modified.
6. `no_order_route_proof` is required and non-empty on both plan and run schemas.
7. No `RuntimeBinding`, capital binding, or order-route field exists in either schema.
8. The PR title includes `AG-DES-RS-001` and the commit carries required LLM-Agent/Task-ID/Reviewer trailers.
