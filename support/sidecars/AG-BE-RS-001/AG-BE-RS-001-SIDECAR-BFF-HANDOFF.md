# AG-BE-RS-001 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-RS-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-RS-001` - ResearchPlan facade/router |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, research services, registry/governance
implementation, or execute-plans frontend code. It summarizes the BFF query
gaps, operator journey, and frontend handoff boundaries for `AG-BE-RS-001`; the
parent owner decides whether and how to absorb it into the main implementation.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_be_rs_001_sidecar_bff_handoff.md` | This sidecar is support-only: BFF query gap, operator journey, frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes must pass task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, helper parent `AG-BE-RS-001`, helper kind `bff_handoff_packet`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001` | Parent is `in_progress`; owner is implementing a ResearchPlan facade/router after `AG-XR-OPENAPI-004` unblocked the v1.3 bundle. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-DES-RS-001` | Done; v4 `research_plan_execution` and `research_run_projection` schemas were accepted. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Done; additive v1.3 OpenAPI, capability manifest, and bundle index are merged. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Frontend research cards are still `todo`; they depend on `AG-BE-RS-002` and the v1.3 bundle. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` | Plan-first rule, ten research facade routes, approval gate, typed stage routing, fail-closed fallback, DAG/concurrency, run projection, no-order-route constraints. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Frontend card contracts: `research_plan_proposal`, `research_progress`, and `research_result`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Defines plan-first research routes for plans and runs with typed envelopes / `ResearchRunProjection`. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Defines required plan fields, lifecycle, approval state, 12 stage types, routing backend enum, explicit fallback policies, and plan no-order proof. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Defines run lineage, backend mode, status/outcome, progress, metrics, findings, refs, failure, data cutoff, and run no-order proof. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | Marks `agora.research.v1` as `execution_authority: research_only` and lists the v1.3 research path prefixes. |
| `services/control-plane/bff/agora/router.py` | Agora sub-routers are mounted through the BFF router factory; research sub-router is included. |
| `services/control-plane/bff/agora/research/router.py` | In this sidecar worktree the research sub-router is still a placeholder with no plan/run handlers. |
| `services/control-plane/bff/main.py` | Current live compatibility aliases only expose `GET /bff/agora/research-tasks` and `GET /bff/research/tasks` backed by research tickets. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Legacy `POST /bff/agora/workshops/{workshop_id}/research-runs` is a `501` stub; safe today, but it must not become a plan-approval bypass. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current BFF State Observed In This Worktree

| Surface | Observed state | Handoff meaning |
|---|---|---|
| `GET /bff/agora/research-tasks` | Implemented in `main.py`; list wrapper over `research_tickets`. | Legacy/task-list compatibility only. Not a `ResearchPlanExecution` facade. |
| `GET /bff/research/tasks` | Implemented alias in `main.py`. | Same as above; useful for task inbox, not enough for plan-first research. |
| `services/control-plane/bff/agora/research/router.py` | Placeholder router returns an empty `APIRouter`. | Parent implementation must add plan-first handlers here or in its chosen BFF module. |
| `POST /bff/agora/workshops/{workshop_id}/research-runs` | Mounted legacy workshop route, currently `501`. | Keep safe; if revived, it must materialize or reference an approved `ResearchPlan`. |
| `agora_v1_3.openapi.yaml` research routes | Present and merged by `AG-XR-OPENAPI-004`. | Contract is available; implementation still belongs to `AG-BE-RS-001` / `AG-BE-RS-002`. |
| v4 research schemas | Present under `services/control-plane/specs/agora/v4/`. | Use these exact shapes; do not invent fields, route names, lifecycle values, or backend enums. |

## Parent Scope Boundary

`AG-BE-RS-001` should focus on the plan-first facade and route-policy resolved
stage plan shape:

- Create/list/detail `ResearchPlanExecution` records for a workshop and strategy
  version.
- Approve or cancel a plan with the frozen lifecycle
  `draft -> approved -> running -> completed/cancelled`.
- Resolve stage intent into typed stage routing, where the LLM proposes
  `stage_type` and policy resolves backend.
- Preserve no-order-route proof on the plan.
- Never create `RuntimeBinding`, capital binding, broker orders, or candidate
  promotion directly from research completion.

`AG-BE-RS-002` should own unified run/progress/result projection depth:

- `ResearchRunProjection` progress, metrics, findings, evidence refs, artifact
  refs, failures, data cutoff, and SSE-backed progress cards.
- Research result cards that require completed run metrics and artifact links.

If `AG-BE-RS-001` chooses to expose a thin run creation/listing route, it should
stay plan-first and transparent: dispatch only approved plans, label
`backend.mode`, and return typed blocked/unavailable states instead of synthetic
success.

## BFF Query Gap Matrix

| Gap | Needed BFF surface | Parent disposition |
|---|---|---|
| Workshop plan list is missing | `GET /bff/agora/workshops/{workshop_id}/research-plans` returning list envelope of `ResearchPlanExecution`. | `AG-BE-RS-001` candidate. |
| Draft plan creation is missing | `POST /bff/agora/workshops/{workshop_id}/research-plans` from a StrategySpec version and explicit stage plan. | `AG-BE-RS-001` candidate. |
| Plan detail is missing | `GET /bff/agora/research-plans/{plan_id}` with schema-conformant plan and `no_order_route_proof`. | `AG-BE-RS-001` candidate. |
| Trader acceptance / approval gate is missing | `POST /bff/agora/research-plans/{plan_id}/approve` with governance precondition checks. | `AG-BE-RS-001` candidate. |
| Cancel flow is missing | `POST /bff/agora/research-plans/{plan_id}/cancel`, idempotent across approved/running plans. | `AG-BE-RS-001` candidate. |
| Stage routing is missing | Stage `stage_type` -> policy-resolved backend with `fail_closed` or `explicit_fixture_only`. | `AG-BE-RS-001` candidate. |
| Legacy run route could bypass approval if implemented casually | `POST /bff/agora/workshops/{workshop_id}/research-runs` must create/reference an approved plan first. | Guardrail for `AG-BE-RS-001`. |
| Run projection is not implemented | `GET /bff/agora/research-runs/{run_id}` and `GET /bff/agora/research-plans/{plan_id}/runs`. | Prefer `AG-BE-RS-002` unless parent adds a thin, clearly limited projection. |
| Artifact/evidence refs by run are not implemented | `GET /bff/agora/research-runs/{run_id}/artifacts`. | Prefer `AG-BE-RS-002`; depends on real run projection and artifact linkage. |
| Frontend client/types are not present | `execute-plans/src/lib/bff-v1/agora/research.ts` plus card components. | `AG-FE-RS-001`, after backend route/projection readiness. |

## Operator Journey

### Journey A: Draft A Research Plan

1. Operator opens a Strategy Workshop and selects the active StrategySpec
   version.
2. Servant proposes a `research_plan_proposal` card with objectives,
   data requirements, stages, budget, assumptions, warnings, and approval
   requirement.
3. Frontend calls
   `POST /bff/agora/workshops/{workshop_id}/research-plans` through the BFF
   client only.
4. BFF validates the plan against `ResearchPlanExecution`, sets
   `status=draft`, attaches workshop/strategy/version refs, and records
   `no_order_route_proof=research_plan_no_order_route`.
5. UI renders the returned plan as draft. It must not show a run, result, or
   candidate promotion as already available.

### Journey B: Review And Approve The Plan

1. Operator reviews stage order, data requirements, backend route choices,
   `backend_mode`, budget, and governance flags.
2. UI visibly labels any fixture/stub request and any activation-gated backend.
3. Operator approves with
   `POST /bff/agora/research-plans/{plan_id}/approve` using an idempotency key
   and optimistic concurrency header when the route supports it.
4. BFF rejects missing governance approval for private/restricted data, paid
   data access, heavy compute, policy training/RL, budget/runtime overage, or
   tenant-boundary crossing.
5. Accepted plan becomes `approved`; rejected approval is represented as
   `status=cancelled` plus `terminal_reason=approval_rejected` rather than a new
   base lifecycle enum.

### Journey C: Dispatch An Approved Plan

1. Operator chooses dispatch only from an `approved` plan.
2. BFF verifies all hard dependencies and stage routing preconditions.
3. Any fixture/stub backend mode must be explicit. Missing real capability
   returns a typed blocked stage, not a successful fake run.
4. BFF creates or queues a research-only run. It must not write
   `RuntimeBinding`, capital binding, broker order, or candidate promotion.
5. UI shows queued/running status only after a returned run/projection is
   available.

### Journey D: Monitor Progress And Results

1. Until `AG-BE-RS-002` lands, the frontend should show plan state and a gated
   progress/result state, not an empty success state.
2. After run projection lands, frontend reads `ResearchRunProjection` and/or
   workshop SSE `research.run.*` events.
3. UI must show backend `real`, `fixture`, or `stub`, data cutoff, warnings,
   blocking reasons, and failure details.
4. Result cards may recommend a follow-up or patch proposal, but they do not
   promote artifacts or route orders.

### Journey E: Capability Blocked

1. Operator requests a plan stage whose backend is not available or not allowed.
2. Route policy returns `status=blocked` for that stage with
   `blocking_reasons`.
3. UI displays the blocked stage and next gate. It must not silently reroute to
   a stub or hide the blocked backend.

## Frontend Handoff

| UI / client need | Binding guidance |
|---|---|
| BFF client | Add a typed client under `execute-plans/src/lib/bff-v1/agora/research.ts`; page/components must not call research services directly. |
| Fallback posture | Use live strict behavior. Do not add local fixture fallback, synthetic plan data, or direct service fanout. |
| Plan card | Bind `research_plan_proposal` to `ResearchPlanExecution` fields: plan identity, objectives, stage list, route choices, budget, assumptions, warnings, approval requirement. |
| Progress card | Bind only after `ResearchRunProjection` is available; show run/stage status, backend, progress, warnings, blocking reasons, and timestamps. |
| Result card | Bind only after `AG-BE-RS-002`; group metrics by schema category and display findings/evidence refs. |
| Backend label | Always display backend mode: `real`, `fixture`, or `stub`. Fixture/stub cannot satisfy full validation readiness. |
| Write actions | `approve`, `cancel`, and run dispatch must use BFF action endpoints with idempotency keys. |
| Concurrency | Use `If-Match` / version headers when returned by BFF; map conflict or precondition errors to a refresh-required state. |
| Degraded state | Treat `501` as feature not implemented, `403` as missing scope, `404` as missing resource, `422` as governance/precondition failure, and `503`/blocked stage as capability unavailable. |
| No-order guard | UI may show "research only"; it must not render canary/live/order/capital controls from any research response. |

Suggested first frontend methods once backend routes land:

```ts
listWorkshopResearchPlans(workshopId)
createWorkshopResearchPlan(workshopId, body, options)
getResearchPlan(planId)
approveResearchPlan(planId, options)
cancelResearchPlan(planId, options)
listResearchPlanRuns(planId)
dispatchResearchPlan(planId, options)
getResearchRun(runId)
cancelResearchRun(runId, options)
listResearchRunArtifacts(runId)
```

## Suggested Backend Acceptance Checks

| Check | Expected result |
|---|---|
| Plan schema validation | Created/detail plans validate against `services/control-plane/specs/agora/v4/research_plan_execution.schema.json`. |
| Lifecycle guard | Only allowed lifecycle transitions are accepted; rejected approval maps to `cancelled` plus terminal reason, not a new status enum. |
| Stage route allowlist | Only the 12 schema stage types and the schema backend enum are accepted. |
| Fallback policy | `fail_closed` is default; `explicit_fixture_only` requires explicit fixture/stub request. |
| Legacy route guard | Legacy workshop research-run creation cannot dispatch without an approved plan. |
| No-order proof | Plan and run responses include the required no-order proof enum values. |
| Runtime boundary | No code path writes `RuntimeBinding`, capital binding, broker order, or governance promotion from research routes. |
| Frontend readiness | `AG-FE-RS-001` can consume plan fields without direct fetch or local fallback. |

## Reviewer Handoff

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact and task-owned status/brief metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, research service, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | Parent is `in_progress`; `AG-DES-RS-001` and `AG-XR-OPENAPI-004` are done; `AG-FE-RS-001` is still todo. |
| Current-state accuracy | Existing worktree still has only legacy research task aliases and a placeholder research sub-router; legacy workshop run route remains `501`. |
| Boundary clarity | Packet does not ask `AG-BE-RS-001` to own full run/result projection depth that belongs to `AG-BE-RS-002`. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: it records the plan-first ResearchPlan facade gaps, operator journey, frontend client/card boundaries, no-order-route guardrails, and AG-BE-RS-001 versus AG-BE-RS-002 ownership boundary without modifying canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-BE-RS-001-SIDECAR-BFF-HANDOFF \
  "Support-only AG-BE-RS-001 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-BE-RS-001-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, ownership-boundary issue, or missing handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-DES-RS-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json >/tmp/ag-be-rs-001-plan-schema.json
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json >/tmp/ag-be-rs-001-run-schema.json
```
