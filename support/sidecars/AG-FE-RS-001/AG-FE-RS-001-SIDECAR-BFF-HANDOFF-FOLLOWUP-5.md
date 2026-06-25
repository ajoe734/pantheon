# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 5

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It packages the prior AG-FE-RS-001 handoff packets into a parent-owner
dispatch manifest: what to read first, what can be absorbed into the frontend
task, which facts override older guidance, and where the frontend owner must
stop instead of guessing.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 status coordinates lifecycle; support artifacts do not override canonical product truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_5.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, review, merge, then final owner closeout. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001` | Parent is `todo`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`; frontend must stop on unclear spec/design gaps. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002` | Conversation/result cards and completeness rail remain `todo`; full stream-backed card integration is not yet delivered. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Archived `done`; run/progress/result projection was implemented, reviewed, and closed. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is merged. |
| Prior AG-FE-RS-001 packets | Base packet plus Follow-ups 2-4 already document route state, envelope corrections, SSE runtime shape, absorption sequence, and stop lines. |
| `services/control-plane/bff/agora/research/router.py` | Plan/run route family is implemented; create requires `Idempotency-Key`; approve/cancel/dispatch require `If-Match` and `Idempotency-Key`; run detail returns raw projection. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop versions, workshop-level research-runs, consultations, and conclude routes are explicit `501` stubs; `/stream` emits runtime SSE events. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run projection, empty artifact list envelope, no-order proof, and `research.run.queued` event publication. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert runtime stream events use `type` and `data`, including `research.run.progress`; deferred stubs still return `501`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists `/cards`, research routes, and version-comparisons; not every OpenAPI-listed route is implemented in runtime. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` has `execution_authority: research_only`. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Plan no-order proof is fixed to `research_plan_no_order_route`. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Run no-order proof is fixed to `research_only_not_direct_action`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Source map: research plan from plan detail; research progress/result from run projection; consult/version cards require their own projections/routes. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Strategy Workshop page includes ResearchPlanCard, ResearchRunCard, BacktestResultCard, and VersionCompareCard. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Packet Chain Precedence

Use the AG-FE-RS-001 sidecar packet chain in this order:

| Packet | Parent-owner use |
|---|---|
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md` | Baseline route inventory, operator journeys, and original card binding map. |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Corrected factual layer for mixed envelopes, create-plan header mismatch, terminal cancel conflict, SSE runtime shape, and WorkshopCard/raw projection mismatch. |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Absorption order and implementation stop lines for the frontend owner. |
| `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` | Parent-owner intake checklist, BFF client contract table, card source matrix, and blocker text. |
| This packet | Final dispatch manifest for handing the support work to the parent owner and reviewer without broadening scope. |

When these packets conflict, prefer the newer factual correction. In practice,
Follow-up 2 overrides the baseline packet on run cancel behavior, response
envelopes, and SSE runtime shape. Follow-ups 3-5 do not change route facts; they
only package absorption guidance.

---

## Parent-Owner Dispatch Manifest

`AG-FE-RS-001` should be treated as a staged frontend task, not a license to
fill missing backend or card-projection gaps.

### Step 1: Preflight before frontend coding

| Preflight item | Required result |
|---|---|
| Checkout | Use a clean `execute-plans` task branch from the active delivery base. Do not use a detached checkout. |
| Status | Re-check `AG-FE-RS-001`, `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004` with the status CLI. |
| Type source | Regenerate or explicitly mirror v1.3 Agora research types before adding `research.ts`. |
| Packet read order | Read Follow-up 2 before writing route clients; read Follow-up 4 before card work. |
| Scope | Keep the first frontend slice to route-backed research client/adapters and only explicit card data sources. |

### Step 2: Implement only the route-backed research client first

Create or update `execute-plans/src/lib/bff-v1/agora/research.ts` with BFF-only
methods. Pages and components must not call research services, internal
`/api/v1/consult/*`, or OpenClaw directly.

| Method | Route | Response handling |
|---|---|---|
| `listWorkshopResearchPlans` | `GET /bff/agora/workshops/{workshop_id}/research-plans` | Read list envelope `items[]`. |
| `createWorkshopResearchPlan` | `POST /bff/agora/workshops/{workshop_id}/research-plans` | Send `Idempotency-Key`; runtime does not currently require `If-Match`; read detail envelope `data` and `meta.etag`. |
| `getResearchPlan` | `GET /bff/agora/research-plans/{plan_id}` | Read detail envelope `data`, `allowedActions`, and `meta.etag`. |
| `approveResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/approve` | Send `If-Match` and `Idempotency-Key`; refetch for new ETag. |
| `cancelResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/cancel` | Send `If-Match` and `Idempotency-Key`; disable for non-cancellable plan statuses. |
| `listResearchPlanRuns` | `GET /bff/agora/research-plans/{plan_id}/runs` | Read list envelope `items[]`; items are run projections. |
| `dispatchResearchRun` | `POST /bff/agora/research-plans/{plan_id}/runs` | Send `If-Match` and `Idempotency-Key`; queued command response only; follow with run detail fetch. |
| `getResearchRun` | `GET /bff/agora/research-runs/{run_id}` | Raw `ResearchRunProjection`; do not unwrap `data`. |
| `cancelResearchRun` | `POST /bff/agora/research-runs/{run_id}/cancel` | Send `Idempotency-Key`; only active statuses are cancellable; terminal statuses return conflict. |
| `listResearchRunArtifacts` | `GET /bff/agora/research-runs/{run_id}/artifacts` | Read list envelope `items[]`; items may combine artifact refs and evidence refs. |

Error handling must preserve backend state exactly. `409` means conflict or
refresh-required; `412`/`428` mean stale or missing precondition where surfaced;
`501` means the route is not implemented. Do not convert these states into local
fixture success.

### Step 3: Prove adapters before UI

Add client/adapter tests before card UI. Minimum proof:

| Behavior | Expected proof |
|---|---|
| Mixed envelopes | Plan lists read `items[]`; plan detail reads `data`; run detail reads raw projection. |
| Header discipline | Commands send `Idempotency-Key`; approve/cancel/dispatch send fresh `If-Match`. |
| Dispatch | Queued command response is not treated as a full run projection. |
| Run cancel | UI/client disables terminal cancel and maps terminal `409` to refetch. |
| SSE adapter | Reads runtime event shape `{ id, type, timestamp, data }`, not the richer schema-only event shape. |
| Backend mode | `backend.mode` remains visible for real/fixture/stub and is never hidden. |
| Blockers | `warnings[]` and `blocking_reasons[]` remain visible; no synthetic green state. |
| No-order | No client method routes to broker order, capital binding, canary/live promotion, or `RuntimeBinding` writes. |

### Step 4: Card work only where the source is explicit

| Component | Parent action |
|---|---|
| `ResearchPlanCard` | Implementable as route-backed rendering from `ResearchPlanExecution` plus detail envelope metadata. Stop if the intended source must be `WorkshopCard` projection before cards runtime exists. |
| `ResearchRunCard` | Implementable from raw `ResearchRunProjection`, polling, and current runtime `research.run.*` SSE events. |
| `BacktestResultCard` | Implementable only as succeeded backtest-like `ResearchRunProjection` rendering. No separate backtest result route exists. |
| `ConsultResultCard` | Blocked until an Agora BFF consultation projection route exists. Do not fan out to internal consult routes. |
| `VersionCompareCard` | Blocked for AG-FE-RS-001 unless the versioning/card-projection runtime lands first. |
| Conversation stream / completeness rail | Coordination point with `AG-FE-SW-002`; do not synthesize `WorkshopCard.payload`. |

---

## Stop-Line Blockers To Carry Forward

Use these as parent task blockers if the frontend implementation reaches the
edge before the owning backend or workshop task lands.

```text
AG-FE-RS-001 blocked on WorkshopCard projection source: v1.3 OpenAPI lists
GET /bff/agora/workshops/{workshop_id}/cards, but the inspected BFF routers do
not expose a matching runtime route. Frontend must not fabricate typed
WorkshopCard payloads.
```

```text
AG-FE-RS-001 blocked on ConsultResultCard: Agora workshop consultation route is
a 501 stub and no consultation detail/projection GET route was found. Frontend
must not call internal /api/v1/consult/* routes or mock consult payloads.
```

```text
AG-FE-RS-001 blocked on VersionCompareCard: version-comparison/card semantics
are not backed by an implemented inspected runtime route. Need the versioning or
card-projection owner to land the BFF route first.
```

```text
AG-FE-RS-001 blocked on stream-card integration while AG-FE-SW-002 remains todo:
route-backed research client/card slices can proceed, but full conversation
card and completeness rail integration require AG-FE-SW-002 handoff.
```

---

## Do Not Absorb Into AG-FE-RS-001

The parent task should not absorb any of the following as "frontend cleanup":

- adding or changing L1 canonical architecture docs;
- changing OpenAPI, JSON schemas, or capability manifests;
- implementing BFF runtime routes;
- broadening capability allowlists;
- adding local fixture fallback for live strict BFF calls;
- direct frontend calls to research services, consult services, OpenClaw, or VM tools;
- order placement, capital binding, broker write, canary/live promotion, or
  `RuntimeBinding` writes from research cards;
- inventing card fields, enum values, widget types, or route names not present
  in the referenced schemas/design docs.

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally changed. The generated task brief is task-scoped context, not canonical truth. |
| Packet chain | Follow-up 5 does not supersede prior facts; it states how parent owner should use the packet chain. |
| Runtime accuracy | Route/client table matches inspected BFF router behavior and BFF tests. |
| Dependency honesty | `AG-FE-SW-002` remains a coordination gate for full conversation-card and completeness-rail integration. |
| Blocker clarity | Cards route, consultation projection, version compare, and stream-card integration stop lines are explicit. |
| No-order guardrail | `agora.research.v1` remains `research_only`; no trading/capital/runtime-binding action is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 parent-owner dispatch manifest, packet precedence, route-backed absorption sequence, and stop-line blockers are documented; no canonical truth, runtime, schema, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff dispatch manifest approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual correction, missing stop-line, or parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_5.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
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
# -> 27 passed in 23.25s

git diff --check
# -> passed
```
