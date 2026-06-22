# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 7

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This support artifact does not edit L1 canonical truth, OpenAPI, JSON schemas,
BFF runtime, registry/governance code, or execute-plans frontend code. It
extends the prior AG-FE-RS-001 packet chain with a BFF handoff queue: which
missing runtime/card-projection surfaces should become follow-up work, which
surfaces AG-FE-RS-001 can still use immediately, and which frontend stop lines
must remain blockers until a backend owner lands them.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support artifacts do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_7.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, review/merge, then owner closeout. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001` | Parent remains `todo`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`; frontend must stop on spec/code mismatch instead of guessing. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002` | Conversation/result cards and completeness rail remain `todo`; full stream-card integration is still a coordination gate. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Archived `done`; run/progress/result projection and closeout are complete. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 2-6 already document route inventory, envelope corrections, SSE runtime shape, parent intake gates, and active stop lines. |
| `services/control-plane/bff/agora/research/router.py` | Plan/run route family is implemented; plan detail/actions use envelopes, run detail returns raw projection, dispatch returns queued ids, active run cancel returns accepted envelope. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop versions, workshop-level research-runs, consultations, and conclude routes remain deferred `501` stubs; `/stream` is implemented. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run detail, list/artifact envelopes, no-order proof, and `research.run.queued` event publication. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert runtime SSE events use `id`, `type`, `timestamp`, and `data`; deferred workshop stubs still return `501`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists `/cards`, version-comparisons, and research plan/run routes; not all listed workshop/card/version surfaces are implemented in runtime. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Source map: research plan from plan detail, research progress/result from run projection, consult result from consultation projection, patch/compare from patch/comparison routes. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` has `execution_authority: research_only`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

Follow-ups 2-6 already give the parent frontend owner enough guidance for a
route-backed `research.ts` and route-backed research cards. This packet adds the
BFF handoff queue for the parts AG-FE-RS-001 must not fill locally:

| Need | Current disposition | Handoff result |
|---|---|---|
| Typed WorkshopCard projection source | OpenAPI lists `/bff/agora/workshops/{workshop_id}/cards`; no inspected BFF runtime route is present. | Open a backend/card-projection follow-up before WorkshopCard-backed rendering. |
| Consultation projection for `ConsultResultCard` | Workshop consultation create route is a `501` stub; no Agora BFF consultation detail/projection GET route was found. | Open a consultation projection BFF follow-up; frontend must not call internal consult routes. |
| Version compare/card source | OpenAPI lists version-comparisons; inspected workshop version routes are `501` stubs. | Keep `VersionCompareCard` outside AG-FE-RS-001 unless a versioning/card-projection owner lands runtime support. |
| Workshop-level research-run dispatch | `POST /bff/agora/workshops/{workshop_id}/research-runs` is `501`; plan-scoped dispatch is implemented. | Parent must dispatch through `POST /bff/agora/research-plans/{plan_id}/runs` only. |
| SSE schema/runtime alignment | Runtime emits `id/type/timestamp/data`; v4 schema names richer fields. | Frontend may consume runtime shape; if exact schema event shape is required, open backend alignment work. |

This packet should be used to file backend/BFF or card-projection follow-ups,
not to broaden the frontend slice.

---

## Route-backed Surface AG-FE-RS-001 Can Use Now

The route-backed plan/run slice remains implementable without waiting for the
card-projection queue above.

| Frontend method | Runtime route | Handling rule |
|---|---|---|
| `listWorkshopResearchPlans` | `GET /bff/agora/workshops/{workshop_id}/research-plans` | Read list envelope `items[]`. |
| `createWorkshopResearchPlan` | `POST /bff/agora/workshops/{workshop_id}/research-plans` | Send `Idempotency-Key`; runtime currently does not require `If-Match`; read detail envelope `data` and `meta.etag`. |
| `getResearchPlan` | `GET /bff/agora/research-plans/{plan_id}` | Read detail envelope `data`, `allowedActions`, and `meta.etag`. |
| `approveResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/approve` | Send `If-Match` and `Idempotency-Key`; refetch plan after mutation for a fresh ETag. |
| `cancelResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/cancel` | Send `If-Match` and `Idempotency-Key`; disable outside cancellable plan statuses. |
| `listResearchPlanRuns` | `GET /bff/agora/research-plans/{plan_id}/runs` | Read list envelope `items[]`; items are run projections. |
| `dispatchResearchRun` | `POST /bff/agora/research-plans/{plan_id}/runs` | Send `If-Match` and `Idempotency-Key`; treat response as queued ids only, then fetch run detail. |
| `getResearchRun` | `GET /bff/agora/research-runs/{run_id}` | Raw `ResearchRunProjection`; do not unwrap `data`. |
| `cancelResearchRun` | `POST /bff/agora/research-runs/{run_id}/cancel` | Send `Idempotency-Key`; only `queued`, `dispatching`, and `running` are cancellable. |
| `listResearchRunArtifacts` | `GET /bff/agora/research-runs/{run_id}/artifacts` | Read list envelope `items[]`; items may mix artifact refs and evidence refs. |

Route-backed card rendering remains limited to:

| Component | Safe source | Boundary |
|---|---|---|
| `ResearchPlanCard` | `ResearchPlanExecution` detail envelope plus `allowedActions` and `meta.etag`. | Stop if acceptance requires `WorkshopCard.payload` before `/cards` runtime exists. |
| `ResearchRunCard` | Raw `ResearchRunProjection`, polling, and runtime `research.run.*` SSE events. | Preserve `backend.mode`, `warnings[]`, and `blocking_reasons[]`; do not synthesize green state. |
| `BacktestResultCard` | Succeeded backtest-like `ResearchRunProjection`. | Do not invent a distinct BacktestResult route or schema. |

---

## BFF Handoff Queue

### 1. Workshop Card Projection Route

| Field | Recommendation |
|---|---|
| Candidate owner | Backend workshop/card-projection owner, coordinated with `AG-FE-SW-002` and `AG-FE-RS-001`. |
| Candidate surface | `GET /bff/agora/workshops/{workshop_id}/cards` or the runtime-equivalent route named by the owner. |
| Inputs | Workshop event/session state, research plan detail, research run projection, consultation projection once available, version comparison/patch projections once available. |
| Output | Schema-conformant `WorkshopCard` list using typed `card_type` payloads. |
| Frontend dependency | Required only if parent acceptance demands WorkshopCard-backed conversation stream rendering instead of explicit route-backed research cards. |
| Acceptance notes | Do not parse arbitrary LLM markdown; do not stuff raw `ResearchRunProjection` into `WorkshopCard.payload`; preserve source refs and no-order guardrails. |

Suggested blocker text:

```text
AG-FE-RS-001 blocked on WorkshopCard projection source: v1.3 OpenAPI lists
GET /bff/agora/workshops/{workshop_id}/cards, but no matching inspected BFF
runtime route is available. Frontend must not fabricate typed WorkshopCard
payloads; need backend/card-projection owner to land or hand off the route.
```

### 2. Consultation Projection For `ConsultResultCard`

| Field | Recommendation |
|---|---|
| Candidate owner | Backend consultation/BFF owner, coordinated with the Strategy Workshop owner. |
| Candidate surface | A BFF consultation projection route, for example list/detail under `/bff/agora/workshops/{workshop_id}/consultations`. |
| Current evidence | `POST /bff/agora/workshops/{workshop_id}/consultations` is a `501` stub; no Agora BFF consultation detail/projection GET route was found. |
| Frontend dependency | Required before `ConsultResultCard` can be implemented from live strict BFF data. |
| Acceptance notes | Projection should carry the fields needed by `payload_consult_result`; frontend must not fan out to internal `/api/v1/consult/*` routes or mock payloads. |

Suggested blocker text:

```text
AG-FE-RS-001 blocked on ConsultResultCard: Agora workshop consultation route is
a 501 stub and no consultation detail/projection GET route was found. Frontend
must not call internal consult routes or mock consultation payloads.
```

### 3. Version Compare / Patch Projection

| Field | Recommendation |
|---|---|
| Candidate owner | Backend versioning/card-projection owner, likely outside AG-FE-RS-001. |
| Candidate surface | Runtime support for the v1.3 version-comparison surface and/or a card projection that feeds `VersionCompareCard`. |
| Current evidence | v1.3 OpenAPI lists version-comparisons; inspected workshop version routes remain `501` stubs. |
| Frontend dependency | Required before `VersionCompareCard` can be claimed complete. |
| Acceptance notes | Do not let AG-FE-RS-001 infer version/card semantics from freeform text or from route names not implemented in runtime. |

Suggested blocker text:

```text
AG-FE-RS-001 blocked on VersionCompareCard: version-comparison/card semantics
are not backed by an implemented inspected runtime route. Need the versioning
or card-projection owner to land the BFF route before UI implementation.
```

### 4. Workshop-level Research-run Dispatch

| Field | Recommendation |
|---|---|
| Candidate owner | Backend research/workshop owner if the route is still desired. |
| Candidate surface | `POST /bff/agora/workshops/{workshop_id}/research-runs`, or an explicit decision to deprecate/leave it deferred in favor of plan-scoped dispatch. |
| Current evidence | Workshop-level route is `501`; plan-scoped `POST /bff/agora/research-plans/{plan_id}/runs` is implemented and tested. |
| Frontend dependency | Not required for the route-backed AG-FE-RS-001 slice. |
| Acceptance notes | Parent frontend must not call the workshop-level route unless a backend owner lands it and defines how it selects a plan/stage. |

### 5. SSE Schema/runtime Alignment

| Field | Recommendation |
|---|---|
| Candidate owner | Backend stream/schema alignment owner if exact v4 `WorkshopStreamEvent` runtime shape is required. |
| Candidate surface | `/bff/agora/workshops/{workshop_id}/stream` event envelope. |
| Current evidence | Runtime/test shape is `id`, `type`, `timestamp`, `data`; v4 schema names richer fields such as `event_id`, `event_type`, and `payload`. |
| Frontend dependency | Route-backed UI can consume current runtime shape; exact schema-shape acceptance needs backend alignment. |
| Acceptance notes | Frontend must not invent missing stream fields such as sequence numbers or payload schema metadata. |

---

## Parent Absorption Decision Table

| If the parent is doing... | Then use... | Stop if... |
|---|---|---|
| First `research.ts` client | Implemented plan/run BFF routes. | Generated types or client assumptions cannot represent mixed envelopes/raw run detail. |
| Route-backed `ResearchPlanCard` | Plan detail envelope. | Reviewer requires WorkshopCard payload source before `/cards` runtime exists. |
| Route-backed `ResearchRunCard` | Raw run projection and current SSE runtime shape. | UI tries to infer missing backend state from partial SSE data instead of refetching. |
| Backtest result rendering | Succeeded backtest-like run projection. | A distinct BacktestResult schema/route is required but not present. |
| Full conversation stream cards | `AG-FE-SW-002` and card projection work. | `/cards` runtime or stream card payload source is still missing. |
| Consult or version cards | Consultation/version BFF follow-ups. | Current runtime is still `501` or absent. |

---

## No-order Guardrail

This packet preserves the same authority boundary as the prior chain:

- `agora.research.v1` remains `research_only`.
- Research cards may display plans, runs, metrics, findings, blockers,
  artifacts, evidence, and backend mode.
- Research cards must not place orders, bind capital, write `RuntimeBinding`,
  promote canary/live, mutate governance/registry state, or call non-BFF
  research/consult/OpenClaw routes directly.
- Missing BFF surfaces are blockers or backend handoffs, not frontend mock
  opportunities.

---

## Reviewer Intake Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally changed; no runtime/frontend/schema/canonical docs are modified. |
| Added value | Follow-up 7 adds a BFF handoff queue and does not replace the factual corrections in Follow-ups 2-6. |
| Runtime accuracy | Implemented plan/run routes, mixed envelopes, raw run detail, 501 workshop stubs, and current SSE shape match inspected code/tests. |
| Parent safety | AG-FE-RS-001 can still proceed with route-backed `research.ts` and explicit plan/run cards while keeping `/cards`, consult, version, and stream-card integration as stop lines. |
| No-order guardrail | No order, capital, canary/live, governance mutation, `RuntimeBinding`, direct service fanout, or local fixture fallback is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: BFF/card-projection handoff queue, route-backed parent absorption boundaries, active stop lines, and no-order guardrails are documented for AG-FE-RS-001; no canonical truth, runtime, schema, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Describe the factual correction, missing BFF handoff item, or parent blocker detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_7.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
# -> source: active; status: in_progress; owner: Codex; reviewer: Claude

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001
# -> source: active; status: todo; depends_on includes AG-FE-SW-002, AG-BE-RS-002, AG-XR-OPENAPI-004

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002
# -> source: active; status: todo

AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002
# -> source: archive; terminal_status: done

AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004
# -> source: archive; terminal_status: done

python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/workshop_card.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/capability_manifest_v1_3.json > /dev/null
# -> all passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py -q -p no:cacheprovider
# -> 27 passed in 22.21s

git diff --check
# -> passed
```
