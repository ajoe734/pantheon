# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 2

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude2` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It is a follow-up to
`support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md` and focuses
on absorption risks for the parent frontend owner.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 status coordinates task state; support artifacts do not override canonical architecture or policy. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_2.md` | Sidecar scope is support-only BFF query gap, operator journey, and frontend handoff material. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task changes must be committed with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task PR flow before terminal closeout; `review_approved` is not terminal. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Active task is `in_progress`, owner `Codex`, reviewer `Claude2`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001` | Parent remains `todo`; frontend scope includes `ResearchRunCard.tsx`, `BacktestResultCard.tsx`, and `research.ts`; implementation must stop on unclear spec gaps. |
| Prior packet `AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md` | Initial BFF surface, journeys, card bindings, and blocked consultation/version-compare notes already exist. |
| `services/control-plane/bff/agora/research/router.py` | Plan/run facade is implemented; detail plan responses use `data` envelopes; run detail returns `ResearchRunProjection` directly. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | `/consultations`, workshop-level `/research-runs`, and version routes remain 501 stubs; `/stream` emits runtime SSE events. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Research plan schema has plan status, stages, routing, budget, run ids, and `research_plan_no_order_route`. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Research run schema has direct projection fields, backend mode, metrics/findings/warnings/blockers, artifacts/evidence, and no-order proof. |
| `services/control-plane/specs/agora/v4/workshop_card.schema.json` | Workshop card payloads are typed; research card payloads are not identical to raw route envelopes. |
| `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` | Schema names canonical fields as `event_id`, `event_type`, `payload`, etc.; current runtime stream emits `id`, `type`, `timestamp`, `data`. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` v1.1 has `execution_authority: research_only`. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert run detail is not wrapped in `data`, validates schema, and queues `research.run.queued`. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert workshop SSE helper emits `type` and `data` shaped runtime events. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/agora/types.ts` | Local frontend checkout is generated from older AG-XR-001/v1.0 snapshot; no v1.3 research routes are present in this file. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/agora/` | Only `types.ts` exists in the inspected checkout; no `research.ts` exists. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Follow-up Corrections For Parent Absorption

### 1. Response envelope shapes are mixed by route

The frontend client cannot assume every research response is a `data` envelope.

| Route | Runtime response shape observed |
|---|---|
| `GET /bff/agora/workshops/{workshop_id}/research-plans` | `{ items, page_info, meta }` list envelope. |
| `POST /bff/agora/workshops/{workshop_id}/research-plans` | Detail envelope with `data: ResearchPlanExecution` and `meta.etag`. |
| `GET /bff/agora/research-plans/{plan_id}` | Detail envelope with `data: ResearchPlanExecution` and `meta.etag`. |
| `POST /bff/agora/research-plans/{plan_id}/approve` | Command response `{ status, data, meta }`. |
| `POST /bff/agora/research-plans/{plan_id}/cancel` | Command response `{ status, data, meta }`. |
| `GET /bff/agora/research-plans/{plan_id}/runs` | `{ items, page_info, meta }` list envelope. |
| `POST /bff/agora/research-plans/{plan_id}/runs` | Command response `{ status: "queued", data: { run_id, plan_id, stage_id, stage_type }, meta }`. |
| `GET /bff/agora/research-runs/{run_id}` | Raw `ResearchRunProjection`, not a `data` envelope. |
| `POST /bff/agora/research-runs/{run_id}/cancel` | Command response `{ status: "accepted", data, meta }` when status is cancellable. |
| `GET /bff/agora/research-runs/{run_id}/artifacts` | `{ items, page_info, meta }`; items combine artifact refs and evidence refs. |

Parent frontend impact:

- `getResearchRun()` should return the raw projection.
- `getResearchPlan()` should unwrap `response.data`.
- List methods should unwrap `response.items`.
- Card code should not parse freeform markdown or infer card type from LLM text.

### 2. Create-plan header contract has an OpenAPI/runtime mismatch

`services/control-plane/openapi/agora_v1_3.openapi.yaml` lists both
`IfMatch` and `IdempotencyKey` for
`POST /bff/agora/workshops/{workshop_id}/research-plans`.

The current runtime route accepts an optional `If-Match` parameter but only
requires `Idempotency-Key`. The BFF test helper creates a plan with
`Idempotency-Key` only.

Parent frontend impact:

- If the frontend client is generated strictly from OpenAPI, it may require
  `If-Match` for create.
- If the client is hand-written, it must at least send `Idempotency-Key`.
- Do not "fix" this inside AG-FE-RS-001 by inventing an ETag source. If this
  blocks generated-client implementation, open a backend/OpenAPI alignment
  blocker.

### 3. Run cancel is not currently a no-op on terminal runs

The previous packet described an already-cancelled run cancel as a no-op `202`.
The observed runtime is stricter:

- `queued`, `dispatching`, and `running` can be cancelled.
- `succeeded`, `failed`, `cancelled`, and `timed_out` return `409`.
- Reusing an `Idempotency-Key` also returns a conflict from the idempotency
  guard.

Parent frontend impact:

- Disable or hide the cancel action unless `execution_status` is one of
  `queued`, `dispatching`, or `running`.
- Map `409` from cancel to refresh-required/conflict state, then refetch the run.
- Do not present terminal run cancel as accepted unless backend behavior changes
  in a separate task.

### 4. SSE runtime shape differs from `WorkshopStreamEvent` schema

The v4 schema defines `WorkshopStreamEvent` fields such as `event_id`,
`event_type`, `aggregate_type`, `sequence_no`, `event_time`, `emitted_at`,
`trace_id`, `idempotency_key`, and `payload`.

The implemented workshop SSE helper currently formats events with:

```text
id
type
timestamp
data
```

`publish_research_progress()` writes `type: "research.run.progress"` with
`data: { workshop_id, run_id, phase, percent, message }`.
Dispatch writes `research.run.queued` with `data: { workshop_id, run_id,
plan_id, stage_id, stage_type, percent }`.

Parent frontend impact:

- A live frontend subscription must read the current runtime fields
  `event.type` and `event.data` when consuming `/stream`.
- If AG-FE-RS-001 acceptance requires the v4 `WorkshopStreamEvent` schema
  exactly, the frontend owner should open a schema/runtime alignment blocker
  rather than silently adapting to an unimplemented shape.
- Do not invent missing SSE fields such as `sequence_no` or `payload_schema` on
  the frontend.

### 5. Workshop card payloads are projections, not raw route responses

`workshop_card.schema.json` defines typed card payloads. Those payloads do not
match raw research route responses one-to-one:

| Card payload | Important mismatch |
|---|---|
| `payload_research_plan_proposal` | Requires `objectives` and `stages[].purpose`; these are not required by `ResearchPlanExecution`. |
| `payload_research_progress` | Uses numeric `progress` and string `backend`, while `ResearchRunProjection` uses `progress.{phase, percent, message, updated_at}` and `backend.{requested, effective, mode}`. |
| `payload_research_result` | Allows result-only metric/findings shape and backend `{ effective, mode }`; raw projection has richer backend and evidence refs. |
| `payload_consult_result` | Requires consultation projection fields, but no Agora BFF consultation projection route exists. |

Parent frontend impact:

- If the card components render directly from research plan/run routes, name
  that as route-backed card rendering and keep the mapping explicit.
- If the card components render `WorkshopCard` payloads, they need a BFF card
  projection source. `GET /bff/agora/workshops/{workshop_id}/cards` appears in
  OpenAPI, but no matching runtime route was found in `services/control-plane/bff/agora`.
- Do not stuff raw `ResearchRunProjection` into a `WorkshopCard.payload` and
  call it schema-conformant.

### 6. Frontend checkout needs branch and contract hygiene before coding

The inspected `/home/lupin/code/execute-plans` checkout is detached at
`574cc54`. Its Agora generated contract snapshot is older AG-XR-001/v1.0 and
does not include the v1.3 research plan/run route family. The inspected
`src/lib/bff-v1/agora/` directory contains only `types.ts`; no `research.ts`
exists.

Parent frontend impact:

- Start AG-FE-RS-001 from a clean `execute-plans` task branch, not this detached
  checkout.
- Regenerate or add types from the merged AG-XR-OPENAPI-004/v1.3 bundle before
  writing `research.ts`.
- Keep the client in live strict mode with no local fixture fallback.

---

## Implementable vs Blocked Cards

| Component | Current disposition |
|---|---|
| `ResearchPlanCard` | Implementable if it binds either `ResearchPlanExecution` detail envelope or a verified `WorkshopCard` projection. Must handle ETag from `meta.etag`. |
| `ResearchRunCard` | Implementable from raw `ResearchRunProjection` plus runtime SSE `type/data` updates. Must show `backend.mode` and `blocking_reasons`. |
| `BacktestResultCard` | Implementable only as a `research_result` rendering of succeeded backtest-like `ResearchRunProjection` data. No separate backtest result route exists. |
| `ConsultResultCard` | Blocked. Agora BFF has `POST /bff/agora/workshops/{workshop_id}/consultations` as 501 and no consultation detail/projection GET. |
| `VersionCompareCard` | Blocked for AG-FE-RS-001. Version compare schemas exist, but no implementing runtime route was found in the inspected BFF router. |

No card should expose RuntimeBinding, broker order, capital binding, canary, or
live trading controls. The manifest authority is `research_only`.

---

## Reviewer Checklist

Claude2 should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally changed. The generated task brief remains outside the commit scope. |
| Canonical boundary | No L1 docs, OpenAPI, schemas, runtime code, registry/governance code, or frontend code are modified. |
| Envelope accuracy | The mixed response-envelope table matches `research/router.py` and BFF tests. |
| Runtime mismatch accuracy | Create-plan `If-Match`, SSE schema/runtime, card payload/raw projection, and cancel-terminal behavior mismatches are documented without changing truth. |
| Parent handoff | The packet gives AG-FE-RS-001 concrete implementation guardrails and explicit blocker points. |
| No-order guardrail | The packet keeps research cards read/research-only and rejects trading/capital actions. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: envelope/header/SSE/card-projection/cancel semantics are documented as parent AG-FE-RS-001 handoff constraints; no canonical truth or runtime/frontend code changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Describe the factual correction, scope issue, or missing parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_2.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md

python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/workshop_card.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/workshop_stream_event.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/capability_manifest_v1_3.json > /dev/null
# -> all passed

python3 -m pytest services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py -q
# -> 27 passed in 22.07s

git diff --check
# -> passed
```
