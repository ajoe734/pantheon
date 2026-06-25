# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 8

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This support artifact does not edit L1 canonical truth, OpenAPI, JSON schemas,
BFF runtime, registry/governance code, or execute-plans frontend code. It turns
the prior AG-FE-RS-001 sidecar chain into an implementation-facing query,
parser, header, refetch, and smoke-test handoff for the parent frontend owner.

Follow-up 8 should be read as a frontend intake checklist. It is not a new
source of product truth and it does not make missing BFF/card-projection
surfaces available.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support artifacts do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_8.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, review/merge, then owner closeout. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Parent remains `todo`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`; frontend must stop on spec/code mismatch instead of guessing. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002` | Conversation/result cards and completeness rail remain `todo`; full stream-card integration is still a coordination gate. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Archived `done`; run/progress/result projection and closeout are complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 2-7 document route inventory, envelope corrections, SSE runtime shape, parent intake gates, active stop lines, and BFF handoff queue. |
| `services/control-plane/bff/agora/research/router.py` | Plan/run route family is implemented; list routes use `items[]`; plan detail uses a detail envelope; command routes use command envelopes; run detail is a raw projection. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop versions, workshop-level research-runs, consultations, and conclude routes remain `501` stubs; `/stream` is implemented. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run detail, list/artifact envelopes, no-order proof, and `research.run.queued` event publication. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert SSE runtime event fields and that deferred workshop stubs still return `501`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists `/cards`, version-comparisons, research plans/runs, and typed response references; runtime support is not present for every listed workshop/card/version surface. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` has `execution_authority: research_only`. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

Follow-ups 2-7 already define the route facts and stop lines. This packet adds
a parent-owner implementation handoff:

| Added item | Why parent needs it |
|---|---|
| Query/parser matrix | Prevents a generated or handwritten `research.ts` client from unwrapping every response the same way. |
| Header/refetch discipline | Keeps mutation calls idempotent and keeps plan ETags fresh after commands that do not return a new plan envelope. |
| Operator journey smoke plan | Gives the parent owner a minimum live-strict route-backed flow to prove before card UI claims. |
| Test expectations for execute-plans | Names focused frontend client/card tests without requiring this sidecar to edit the frontend repo. |
| Active blocker wording | Keeps `/cards`, consultation, version compare, workshop-level dispatch, and stream-card integration outside the route-backed AG-FE-RS-001 slice. |

This does not supersede the prior packet chain. It makes the route-backed first
slice easier to implement and review.

---

## Frontend Query Contract

The parent owner should model these as separate client operations rather than a
single generic envelope helper.

| Client operation | Runtime route | Required request discipline | Response parser | Immediate follow-up |
|---|---|---|---|---|
| `listWorkshopResearchPlans` | `GET /bff/agora/workshops/{workshop_id}/research-plans` | BFF auth headers only; optional pagination if exposed by UI. | List envelope: read `items[]`, `page_info`, `meta`. | None. |
| `createWorkshopResearchPlan` | `POST /bff/agora/workshops/{workshop_id}/research-plans` | Send `Idempotency-Key`. Runtime accepts `If-Match` but does not require it. | Detail envelope: read `data`, `allowedActions`, `meta.etag`. | Store returned plan and ETag. |
| `getResearchPlan` | `GET /bff/agora/research-plans/{plan_id}` | BFF auth headers only. | Detail envelope: read `data`, `allowedActions`, `meta.etag`. | Use this before any plan command needing `If-Match`. |
| `approveResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/approve` | Send fresh `If-Match` from plan detail and `Idempotency-Key`. | Command envelope: read `status`, `data.plan_id`, `data.status`, `meta`. | Refetch plan detail before dispatch. |
| `cancelResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/cancel` | Send fresh `If-Match` and `Idempotency-Key`; enable only for draft, approved, or running. | Command envelope. | Refetch plan detail or return to list. |
| `listResearchPlanRuns` | `GET /bff/agora/research-plans/{plan_id}/runs` | BFF auth headers only. | List envelope: read `items[]`, `page_info`, `meta`. | Items are run projections; do not expect `data`. |
| `dispatchResearchRun` | `POST /bff/agora/research-plans/{plan_id}/runs` | Send fresh `If-Match` from an approved plan and `Idempotency-Key`. | Command envelope with queued identifiers only. | Fetch `GET /research-runs/{run_id}` before rendering run detail. |
| `getResearchRun` | `GET /bff/agora/research-runs/{run_id}` | BFF auth headers only. | Raw `ResearchRunProjection`; do not unwrap `data`. | Poll or merge runtime SSE progress without inventing missing fields. |
| `cancelResearchRun` | `POST /bff/agora/research-runs/{run_id}/cancel` | Send `Idempotency-Key`; enable only for queued, dispatching, or running. | Command envelope with accepted cancellation state. | Refetch run detail. Terminal statuses may return conflict. |
| `listResearchRunArtifacts` | `GET /bff/agora/research-runs/{run_id}/artifacts` | BFF auth headers only. | List envelope: read `items[]`; items may include artifact refs and evidence refs. | Render refs only; do not fetch non-BFF stores directly. |

### Parser Rules

| Parser | Use for | Do not use for |
|---|---|---|
| `parseListEnvelope` | Research plan list, run list, artifact/evidence refs. | Run detail. |
| `parseDetailEnvelope` | Plan create/detail responses with `data`, `allowedActions`, `meta.etag`. | Command responses. |
| `parseCommandEnvelope` | Approve, cancel, dispatch, run cancel. | Long-lived plan/run state rendering. |
| `parseResearchRunProjection` | `GET /bff/agora/research-runs/{run_id}` and list item shape checks. | Plan detail envelope or `WorkshopCard.payload`. |

If generated v1.3 types blur these response shapes, AG-FE-RS-001 should add
typed client adapters/tests in execute-plans rather than weakening the runtime
facts or inventing a universal response wrapper.

---

## Refetch And State Discipline

The parent frontend should preserve these state transitions:

| Event | Required client behavior | Reason |
|---|---|---|
| Plan create returns detail envelope | Store `data` plus `meta.etag`. | This is the first fresh plan ETag. |
| Plan approve returns command envelope | Treat command response as an acknowledgement, then refetch plan. | Approval increments plan lock version but does not return a fresh detail envelope. |
| Dispatch returns queued command envelope | Read `data.run_id`, then fetch raw run detail. | The queued response is not the full run projection for card rendering. |
| Run progress arrives through SSE | Merge only fields present in runtime event `data`, then refetch/poll run detail for authoritative projection. | Runtime SSE is progress signal, not a full `ResearchRunProjection`. |
| Run enters succeeded/failed/cancelled/timed_out | Disable run cancel. | Terminal cancel is a conflict, not a successful no-op. |
| Plan/run shows `warnings[]`, `blocking_reasons[]`, or degraded backend mode | Render the degraded/blocking state. | Parent must not synthesize green state from partial success. |

The route-backed first slice can be live strict without direct service fanout:
all calls stay under `/bff/agora/*`, use the configured BFF base URL, and have
no fixture fallback path in production/dev live mode.

---

## Operator Journey Smoke Plan

This is the minimum route-backed flow the parent owner can prove before claiming
research card readiness:

| Step | BFF interaction | UI/card expectation | Test assertion |
|---|---|---|---|
| Open workshop research view | `GET /workshops/{workshop_id}/research-plans` | Empty, loading, or populated plan list from `items[]`. | Client does not unwrap `data.items`. |
| Create draft plan | `POST /workshops/{workshop_id}/research-plans` with `Idempotency-Key`. | Draft plan appears with allowed actions and ETag-backed command affordances. | Missing idempotency key is rejected in client test. |
| Approve draft plan | `POST /research-plans/{plan_id}/approve` with fresh `If-Match` and `Idempotency-Key`. | UI shows acknowledgement then refetches plan. | Dispatch is not called with stale pre-approval ETag. |
| Dispatch approved plan | `POST /research-plans/{plan_id}/runs` with fresh `If-Match` and `Idempotency-Key`. | UI shows queued state and run id. | Client immediately calls `GET /research-runs/{run_id}`. |
| Render run detail | `GET /research-runs/{run_id}` | `ResearchRunCard` reads raw projection fields, including backend mode and no-order proof. | Test fails if parser expects `data`. |
| Render progress | `/workshops/{workshop_id}/stream` current runtime SSE or polling. | Progress updates only from `type/timestamp/data`; authoritative state still comes from run detail. | Client ignores schema-only event fields not present at runtime. |
| Render artifacts/evidence | `GET /research-runs/{run_id}/artifacts` | Artifact/evidence refs list renders from `items[]`. | No direct object-store or internal service fetch. |
| Cancel active run | `POST /research-runs/{run_id}/cancel` with `Idempotency-Key`. | Cancel action only appears for queued/dispatching/running. | Terminal run cancel conflict is handled as disabled/error state. |

Backtest rendering remains a route-backed view of a succeeded
`prototype_backtest` style `ResearchRunProjection`. AG-FE-RS-001 should not add
a separate BacktestResult route, schema, score, or local fixture.

---

## Frontend Test Expectations For Parent

Suggested execute-plans tests for `AG-FE-RS-001`:

| Test | Expected coverage |
|---|---|
| Research client envelope split | List/detail/command/raw-run responses are parsed by distinct adapters. |
| Header discipline | Create/approve/cancel/dispatch/run-cancel methods send required `Idempotency-Key`; plan commands send fresh `If-Match`. |
| Refetch after command | Approve refetches plan before dispatch; dispatch fetches run detail before rendering. |
| Raw run projection card | `ResearchRunCard` and backtest-like result view preserve `backend.mode`, `warnings[]`, `blocking_reasons[]`, artifacts/evidence refs, and `no_order_route_proof`. |
| Runtime SSE shape | Stream listener consumes `id`, `type`, `timestamp`, and `data`; it does not require schema-only event field names. |
| Stop-line blockers | ConsultResultCard, VersionCompareCard, `/cards` projection, and workshop-level research-run route stay blocked when BFF returns 501 or route is absent. |
| Live strict mode | Client uses BFF base only; no direct `/api/v1/consult/*`, OpenClaw, object-store, registry, order, RuntimeBinding, or fixture fallback calls. |

These are frontend tests for the parent task. This sidecar does not create or
modify execute-plans files.

---

## Stop Lines To Carry Forward

| Stop line | Current evidence | Parent action |
|---|---|---|
| Workshop `/cards` projection | v1.3 OpenAPI lists `/bff/agora/workshops/{workshop_id}/cards`; no inspected BFF runtime route was found. | Do not fabricate typed `WorkshopCard.payload`; open or wait for backend/card-projection work. |
| `ConsultResultCard` | Workshop consultation route is a `501` stub; no Agora BFF consultation detail/projection GET route was found. | Do not call internal `/api/v1/consult/*`; keep the card blocked. |
| `VersionCompareCard` | v1.3 lists version-comparison surfaces; inspected workshop version routes remain `501` stubs. | Keep blocked unless a versioning/card-projection owner lands runtime support. |
| Workshop-level research-run dispatch | `POST /bff/agora/workshops/{workshop_id}/research-runs` remains `501`. | Dispatch only through plan-scoped `POST /bff/agora/research-plans/{plan_id}/runs`. |
| Full conversation stream cards and completeness rail | `AG-FE-SW-002` remains `todo`. | Do not claim full stream-card/completeness integration from AG-FE-RS-001's route-backed slice. |
| Exact v4 stream schema shape | Runtime/test shape is `id`, `type`, `timestamp`, and `data`. | Consume runtime shape; open backend alignment work if exact schema names are required. |

Suggested blocker text:

```text
AG-FE-RS-001 blocked on query/client contract mismatch: the BFF research
routes use mixed list/detail/command/raw-run response shapes. Parent must add
typed client adapters/tests or request a backend contract change; frontend must
not paper over this with a universal envelope parser.
```

```text
AG-FE-RS-001 blocked on ConsultResultCard: Agora workshop consultation route is
a 501 stub and no consultation detail/projection GET route was found. Frontend
must not call internal consult routes or mock consultation payloads.
```

```text
AG-FE-RS-001 blocked on WorkshopCard projection source: v1.3 OpenAPI lists
GET /bff/agora/workshops/{workshop_id}/cards, but no matching inspected BFF
runtime route is available. Frontend must not fabricate typed card payloads.
```

---

## No-order Guardrail

This packet preserves the same authority boundary as the prior chain:

- `agora.research.v1` remains `research_only`.
- Research cards may display plans, runs, progress, findings, metrics,
  blockers, warnings, artifacts, evidence, and backend mode.
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
| Added value | Follow-up 8 adds query/parser/header/refetch/smoke-test guidance and does not duplicate the BFF handoff queue from Follow-up 7. |
| Runtime accuracy | Mixed response shapes, plan/run command headers, plan refetch after approve/dispatch, raw run detail, 501 stubs, and SSE runtime fields match inspected code/tests. |
| Parent safety | AG-FE-RS-001 can implement route-backed `research.ts`, `ResearchPlanCard`, `ResearchRunCard`, and backtest-like run rendering while preserving active stop lines. |
| No-order guardrail | No order, capital, canary/live, governance mutation, `RuntimeBinding`, direct service fanout, or local fixture fallback is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 query/parser/header/refetch contract, operator smoke plan, frontend test expectations, active stop lines, and no-order guardrails are documented; no canonical truth, runtime, schema, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8 \
  "Describe the factual correction, missing query contract detail, or parent blocker text needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_8.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-8
# -> source: active; status: in_progress; owner: Codex; reviewer: Claude

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
# -> source: active; status: todo; depends_on includes AG-FE-SW-002, AG-BE-RS-002, AG-XR-OPENAPI-004

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002
# -> source: active; status: todo

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002
# -> source: archive; terminal_status: done

AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004
# -> source: archive; terminal_status: done

python3 -m json.tool services/control-plane/specs/agora/v4/research_plan_execution.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/research_run_projection.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/workshop_card.schema.json > /dev/null
python3 -m json.tool services/control-plane/specs/agora/v4/capability_manifest_v1_3.json > /dev/null
# -> all passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_agora_research_run_projection.py services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py -q -p no:cacheprovider
# -> 27 passed in 21.66s

git diff --check
# -> passed
```
