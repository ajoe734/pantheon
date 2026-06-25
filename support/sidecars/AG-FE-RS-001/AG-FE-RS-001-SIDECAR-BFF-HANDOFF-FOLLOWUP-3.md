# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 3

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This packet is a support artifact only. It does not edit L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance code, or execute-plans
frontend code. It complements:

- `support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`

The added value here is an absorption sequence and stop-line packet for the
parent frontend owner. It does not supersede the corrected route/envelope facts
in Follow-up 2.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 status coordinates task state; support artifacts do not override canonical product truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_3.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support changes must be committed with explicit scope and narrow ownership. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, review, merge, then final status closeout. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, helper kind `bff_handoff_packet`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001` | Parent remains `todo`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`; implementation must stop on unclear design/spec gaps. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002` | Still `todo`; owns conversation/result cards and completeness rail, including ResearchPlanCard and ConsultResultCard surfaces from the workshop stream. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Archived `done`; run/progress/result projection is implemented and reviewed. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is merged. |
| Prior AG-FE-RS-001 handoff packets | Initial surface, operator journeys, envelope corrections, terminal cancel behavior, SSE runtime shape, and card projection mismatches are already documented. |
| `support/sidecars/AG-BE-RS-002/AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Corrected AG-FE-RS-001 client guidance after AG-BE-RS-002 completion; AG-FE-RS-001 remains blocked on AG-FE-SW-002. |
| `services/control-plane/bff/agora/research/router.py` | Plan/run route family is implemented; dispatch returns queued confirmation; run detail returns raw projection; cancel active run returns accepted envelope. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop-level versions, research-runs, consultations, and conclude routes remain `501`; `/stream` is implemented. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Includes `/bff/agora/workshops/{workshop_id}/cards`, research plan/run routes, and version comparison route. |
| `services/control-plane/specs/agora/v4/workshop_card.schema.json` | WorkshopCard has 12 typed `card_type` values and payload definitions; frontend must not infer card type from free LLM text. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | ResearchPlanExecution schema defines plan/stage status and no-order proof. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | ResearchRunProjection schema defines execution status, outcome, progress, backend mode, metrics, findings, warnings, blockers, refs, and no-order proof. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run projection, schema validation, list/artifact envelope behavior, and queued SSE events. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert runtime SSE events use `id`, `type`, `timestamp`, and `data`. |
| `/home/lupin/code/execute-plans` | Inspected checkout is detached at `574cc54`; `src/lib/bff-v1/agora/` has old `types.ts` only; no `research.ts` and no `src/agora/components/` directory found in that checkout. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Dependency Honesty For Parent Absorption

`AG-FE-RS-001` is not fully unblocked just because the BFF research route family
is done.

| Dependency | Current state | Frontend consequence |
|---|---|---|
| `AG-BE-RS-002` | `done` (archived) | `research.ts`, run polling, run detail, artifact list, dispatch, and cancel can target the implemented BFF route family. |
| `AG-XR-OPENAPI-004` | `done` (archived) | v1.3 schemas/OpenAPI exist as the authoritative type source. |
| `AG-FE-SW-002` | `todo` | Conversation stream card composition, completeness rail integration, and shared card component placement are still not delivered. Parent must not claim full UI card completion before this dependency resolves. |

Recommended absorption posture:

1. Treat `research.ts` and route-backed data adapters as the only clearly
   implementable first slice.
2. Render or test card components only where the parent can bind to explicit
   v1.3 schemas or implemented BFF route responses.
3. Open a blocker before using workshop stream cards or completeness rail
   integration if `AG-FE-SW-002` is still `todo`.

---

## Implementation Stop Lines

These are the places where the parent owner should stop and request clarification
instead of filling gaps locally.

| Stop line | Why it matters | Required action |
|---|---|---|
| `GET /bff/agora/workshops/{workshop_id}/cards` | OpenAPI lists it, but no matching runtime route was found in the BFF routers inspected. | Do not fabricate WorkshopCard payloads in the frontend. Bind to route-backed plan/run data, or open a backend/card-projection blocker. |
| `POST /bff/agora/workshops/{workshop_id}/consultations` | Runtime route is a `501` stub; no consultation detail/projection GET route was found. | Do not implement ConsultResultCard with mocks or internal `/api/v1/consult/*` fanout. Keep it blocked. |
| Workshop-level `POST /bff/agora/workshops/{workshop_id}/research-runs` | Runtime route is a `501` stub; implemented dispatch route is plan-scoped. | Dispatch through `POST /bff/agora/research-plans/{plan_id}/runs` only. |
| Version comparison / version cards | OpenAPI has version comparison surfaces, while strategy_workshop version routes remain `501` stubs. | Keep VersionCompareCard out of AG-FE-RS-001 unless a dedicated versioning task provides the runtime route. |
| SSE schema vs runtime shape | Runtime stream emits `id`, `type`, `timestamp`, `data`; schema names richer event fields. | Consume current runtime shape only, and open a schema/runtime alignment blocker if acceptance demands exact schema shape. |
| Generated frontend types | Inspected execute-plans checkout has older AG-XR-001/v1.0 generated types. | Start from a clean execute-plans task branch and regenerate or add v1.3 types before coding cards/client methods. |
| In-memory research store | `MemoryResearchPlanStore` is the wired backend; state resets on BFF restart. | Do not treat reset state as a frontend bug. If persistence is required for acceptance, open a backend persistence follow-up. |

---

## Safe Absorption Sequence

### Slice 1: `research.ts` client only

Create `execute-plans/src/lib/bff-v1/agora/research.ts` after refreshing the
v1.3 type source. The client should:

- use BFF route paths only;
- send `credentials: "include"`;
- run live-strict with no local fixture fallback;
- generate a unique `Idempotency-Key` for commands;
- preserve `If-Match` from plan detail `meta.etag` for approve/cancel/dispatch;
- unwrap envelopes per route, not with one generic assumption;
- map `409`, `412`, and `428` to refresh-required/precondition states.

Minimum method set:

| Method | Runtime route | Response handling |
|---|---|---|
| `listWorkshopResearchPlans` | `GET /bff/agora/workshops/{workshop_id}/research-plans` | list envelope, read `items[]` |
| `createWorkshopResearchPlan` | `POST /bff/agora/workshops/{workshop_id}/research-plans` | detail envelope, read `data` and `meta.etag` |
| `getResearchPlan` | `GET /bff/agora/research-plans/{plan_id}` | detail envelope, read `data`, `allowedActions`, `meta.etag` |
| `approveResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/approve` | command envelope |
| `cancelResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/cancel` | command envelope |
| `listResearchPlanRuns` | `GET /bff/agora/research-plans/{plan_id}/runs` | list envelope with full `ResearchRunProjection` items |
| `dispatchResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/runs` | queued confirmation only; follow with `getResearchRun(run_id)` |
| `getResearchRun` | `GET /bff/agora/research-runs/{run_id}` | raw `ResearchRunProjection`, no `data` wrapper |
| `cancelResearchRun` | `POST /bff/agora/research-runs/{run_id}/cancel` | accepted envelope; hide action for terminal statuses |
| `listResearchRunArtifacts` | `GET /bff/agora/research-runs/{run_id}/artifacts` | list envelope; evidence refs may be mixed object shapes |

### Slice 2: reducer/state adapter tests

Before card UI, add small reducer or adapter tests that prove the frontend
handles:

- dispatch returning queued confirmation instead of a full projection;
- plan ETag refresh after approve/cancel/dispatch;
- run detail as raw projection;
- terminal run cancel returning `409`;
- SSE events shaped as `{ id, type, timestamp, data }`;
- `research.run.queued` followed by polling `getResearchRun(run_id)`;
- `research.run.progress` patching only known progress fields;
- `backend.mode` always retained for card rendering.

### Slice 3: route-backed research card components

Implement only route-backed components where the data source is explicit:

| Component | Safe source | Boundary |
|---|---|---|
| `ResearchPlanCard` | `ResearchPlanExecution` from plan detail envelope, plus `allowedActions` and `meta.etag`. | If AG-FE-SW-002 requires WorkshopCard stream payloads instead, stop and coordinate with that task. |
| `ResearchRunCard` | Raw `ResearchRunProjection` and runtime SSE `research.run.*` events. | Do not invent missing backend/blocker fields in SSE; re-fetch run detail when needed. |
| `BacktestResultCard` | Succeeded `ResearchRunProjection` where `stage_type` is a backtest-like stage. | Do not create a separate BacktestResult schema or route. |
| `ConsultResultCard` | None currently available from Agora BFF. | Blocked until consultation projection route exists. |

### Slice 4: workshop/card integration

Only after `AG-FE-SW-002` or a backend card projection task lands, wire these
cards into the conversation stream and completeness rail. Until then, keep UI
integration behind the parent task's blocker discipline.

---

## No-Order And Authority Guardrail

Research cards are read/research-only. They must never expose:

- order placement;
- capital binding;
- broker write;
- canary/live promotion;
- RuntimeBinding write;
- direct registry/governance mutation.

`ResearchPlanExecution.no_order_route_proof` must remain
`research_plan_no_order_route`.

`ResearchRunProjection.no_order_route_proof` must remain
`research_only_not_direct_action`.

The capability surface is `agora.research.v1`; parent frontend code must not
broaden capability allowlists or call non-BFF research services directly.

---

## Parent Owner Checklist

Before AG-FE-RS-001 implementation starts:

| Check | Required result |
|---|---|
| Branch hygiene | Use a clean `execute-plans` task branch from the active delivery base, not the inspected detached checkout. |
| Type source | v1.3 OpenAPI/schema types are regenerated or explicitly mirrored before writing `research.ts`. |
| Dependency honesty | `AG-FE-SW-002` state is checked; full card/conversation integration is not claimed while it remains `todo`. |
| Route binding | The client binds only implemented route-backed plan/run surfaces. |
| Stop lines | Cards route, consultation route, version compare, and workshop-level research-run route are not guessed. |
| Tests | Add client/adapter tests for envelope shapes, ETag/idempotency behavior, SSE runtime shape, terminal cancel conflict, and no fixture fallback. |
| Visual scope | UI follows the existing execute-plans design system and the referenced SD/card specs; no invented layout or copy. |

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally changed. The generated task brief remains outside the commit scope. |
| Canonical boundary | No L1 docs, OpenAPI, schemas, BFF runtime, registry/governance code, or frontend code are modified. |
| Non-duplication | The packet does not restate Follow-up 2 as a replacement; it adds absorption order and stop-lines. |
| Dependency honesty | `AG-FE-RS-001` is not described as fully unblocked while `AG-FE-SW-002` remains `todo`. |
| Runtime accuracy | Cards route gap, consultation 501, workshop research-run 501, route-backed plan/run guidance, and SSE runtime shape match inspected code/tests. |
| Parent handoff | The parent owner receives concrete first slices, blocker points, and validation expectations. |
| No-order guardrail | Research-only authority is preserved; no trading/capital/runtime-binding UI action is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: absorption order and stop-lines are documented for AG-FE-RS-001; dependency honesty preserved while AG-FE-SW-002 remains todo; no canonical truth, runtime, schema, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only AG-FE-RS-001 BFF/frontend follow-up packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction, missing stop-line, or parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_3.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
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
# -> all passed

python3 -m pytest services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py -q
# -> 27 passed in 21.61s

git diff --check --no-index /dev/null support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
# -> no whitespace diagnostics; command exits 1 because the file is new
```
