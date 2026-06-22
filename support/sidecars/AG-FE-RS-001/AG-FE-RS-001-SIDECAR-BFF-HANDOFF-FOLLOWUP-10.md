# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 10

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It packages the existing AG-FE-RS-001 sidecar chain into a final
parent-owner handoff checklist: what Claude can implement first, what evidence
the parent PR should carry, and what must remain blocked until a backend or
workshop-card owner lands the missing BFF/card-projection surfaces.

Follow-up 10 should be read after Follow-ups 8 and 9. It does not supersede the
route facts, response-shape corrections, component ownership split, or stop
lines already recorded there.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task ownership; support artifacts do not override architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_10.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, review/merge, then owner closeout when review-approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Parent remains `todo`; artifacts are `research.ts`, `ResearchRunCard.tsx`, and `BacktestResultCard.tsx`; summary says to stop on unclear specs or code/spec mismatch. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002` | Workshop conversation/result cards and completeness rail remain `todo`; `ResearchPlanCard.tsx` and `ConsultResultCard.tsx` are coordination surfaces. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001` | Archived `done`; plan CRUD/approve/cancel/stage routing facade is complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Archived `done`; run/progress/result projection, artifact list, and research SSE publication are complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 2-9 document route inventory, mixed response shapes, SSE runtime shape, parent intake gates, BFF handoff queue, parser/refetch guidance, and implementation/review cut. |
| `services/control-plane/bff/agora/research/router.py` | Implemented plan-scoped research plan/run route family; list routes return `items[]`; plan create/detail return detail envelopes; commands return command envelopes; run detail returns raw `ResearchRunProjection`. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop version routes, workshop-level research-runs, consultations, and conclude remain `501`; `/stream` is implemented with runtime SSE events. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run detail has no `data`, list/artifact routes return `items[]`, no-order proof is present, and dispatch publishes `research.run.queued`. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert runtime `research.run.progress` event data and keep deferred workshop routes at `501`. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Plan projection requires identity/status/stages/no-order proof and carries optional approval/budget/run refs. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Run projection requires identity, status, progress, backend, outcome, no-order proof, and carries metrics/findings/warnings/blockers/artifact/evidence refs. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists `/cards`, `version-comparisons`, research plans/runs, and artifacts; runtime support is not present for every listed workshop/card/version surface. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

Follow-up 8 gives query/parser/refetch details. Follow-up 9 gives the
implementation and review cut. This packet adds a handoff-ready execution
checklist for the parent owner and reviewer:

| Added item | Why parent needs it |
|---|---|
| Absorption order | Keeps the first AG-FE-RS-001 PR small enough to prove live-strict BFF behavior before broader workshop-card integration. |
| Evidence contract | Names the minimum parent PR evidence that should be attached before claiming the route-backed slice. |
| Reviewer decision table | Lets review approve the route-backed client/run/result slice without accidentally approving consultation, version compare, `/cards`, or completeness rail work. |
| Stop-line carry-forward | Keeps missing BFF/card-projection surfaces as explicit blockers instead of frontend mock opportunities. |

This packet does not add new route facts. It is a packaging layer for handoff
and review.

---

## Parent Absorption Order

Claude can absorb the sidecar chain into AG-FE-RS-001 in this order:

| Step | Parent-owned work | Evidence expected before claim |
|---|---|---|
| 1 | Add `execute-plans/src/lib/bff-v1/agora/research.ts`. | Client tests prove list/detail/command/raw-run parsers are distinct and all calls stay under configured `/bff/agora/*`. |
| 2 | Add header/refetch discipline around plan commands and dispatch. | Tests prove create sends `Idempotency-Key`; approve/cancel/dispatch send fresh `If-Match` plus `Idempotency-Key`; approve and dispatch refetch before rendering the next state. |
| 3 | Add route-backed `ResearchRunCard` adapter/rendering. | Tests prove raw run projection fields render directly: `execution_status`, `progress`, `backend.mode`, `warnings[]`, `blocking_reasons[]`, and `no_order_route_proof`. |
| 4 | Add route-backed `BacktestResultCard` rendering from succeeded backtest-like runs. | Tests prove metrics/findings/artifacts/evidence/data cutoff/backend mode render only after `execution_status=succeeded`; no promotion/order/capital actions appear. |
| 5 | Coordinate or block `ResearchPlanCard` stream placement. | Parent records whether AG-FE-SW-002 owns full card placement; do not claim full conversation-card integration from this route-backed slice alone. |
| 6 | Keep consultation, version compare, `/cards`, and workshop-level dispatch blocked. | Parent PR includes blocker text or follow-up task refs; no local fixtures, internal consult calls, or fabricated `WorkshopCard.payload`. |

The first four steps are the route-backed slice. Steps five and six are
coordination and blocker hygiene, not hidden implementation work for AG-FE-RS-001.

---

## Parent PR Evidence Contract

The parent implementation should not be accepted as route-backed and live
strict unless it carries evidence for these checks:

| Evidence | Pass condition | Fail condition |
|---|---|---|
| Client route map | Every implemented research plan/run BFF route has one typed client method. | Missing route method, direct service fanout, or use of workshop-level research-run dispatch. |
| Response-shape tests | List routes parse `items[]`; plan create/detail parse detail envelopes; commands parse acknowledgements; run detail parses raw projection. | Universal parser expects every response under `data`, or raw run detail is wrapped locally. |
| Mutation headers | Required idempotency and ETag headers are generated and tested. | Approve/cancel/dispatch can run without required headers or use stale ETags. |
| Refetch behavior | Approve refetches plan before dispatch; dispatch fetches run detail before rendering `ResearchRunCard`. | UI derives authoritative run state only from command acknowledgements or partial SSE data. |
| Degraded/blocking visibility | `backend.mode`, `warnings[]`, and `blocking_reasons[]` remain visible. | Fixture/stub/blocked state is hidden or converted to green success. |
| No-order guardrail | No order, capital, RuntimeBinding, canary/live promotion, or governance mutation UI/actions. | Any research card offers trading, capital binding, promotion, or registry/governance writes. |
| Stop-line coverage | Tests or explicit blockers cover consult, version, `/cards`, and workshop-level dispatch unavailability. | Missing BFF surfaces are filled with mocks, freeform text parsing, or internal `/api/v1/consult/*` calls. |

Suggested parent PR acceptance wording:

```text
This AG-FE-RS-001 PR claims only the route-backed research client/run/result
slice: research.ts, ResearchRunCard, BacktestResultCard, and supporting
adapters/tests. It does not claim ConsultResultCard, VersionCompareCard,
WorkshopCard projection, workshop-level research-run dispatch, or full
conversation/completeness integration.
```

---

## Handoff Matrix For Existing Sidecar Packets

| Packet | Parent should use it for | Do not use it for |
|---|---|---|
| Base `AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md` | Route inventory, operator journeys, field-binding overview, no-order guardrail. | Treating consultation/version/card projection as implemented runtime. |
| Follow-ups 2-6 | Corrections and supporting route/runtime facts accumulated during the sidecar chain. | Superseding later mixed-envelope and ownership guidance. |
| Follow-up 7 | Backend/card-projection handoff queue and missing-surface blocker text. | Frontend workarounds for missing `/cards`, consult, version, or workshop dispatch surfaces. |
| Follow-up 8 | Query/parser/header/refetch/smoke-test contract. | A universal frontend parser or schema rewrite. |
| Follow-up 9 | Implementation/review cut and shared component ownership guidance. | Claiming full `ResearchPlanCard`/`ConsultResultCard` stream integration without AG-FE-SW-002 or backend coordination. |
| Follow-up 10 | Parent absorption order, PR evidence contract, and reviewer decision checklist. | New canonical truth or runtime/API changes. |

---

## Reviewer Decision Table

Claude should review this sidecar packet as a support handoff, not as a runtime
or frontend implementation.

| Review question | Approve if | Reopen if |
|---|---|---|
| Scope | Only the task brief and this support artifact changed. | Runtime, schema, OpenAPI, canonical docs, or execute-plans files changed. |
| Added value | Packet adds parent absorption order and PR evidence contract beyond Follow-ups 8/9. | Packet merely repeats route tables without a new handoff or review use. |
| Runtime accuracy | Implemented and missing surfaces match inspected BFF routes/tests. | Any route status, response shape, or 501 stop line is inaccurate. |
| Parent safety | Missing consultation/version/card-projection surfaces remain blockers. | Packet encourages mocks, direct internal service calls, or freeform payload inference. |
| Authority boundary | Research remains display/dispatch only with no order/capital/RuntimeBinding/governance mutation. | Packet permits Agora research UI to mutate trading or governance surfaces. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 parent absorption order, PR evidence contract, reviewer decision table, stop-line carry-forward, and no-order/live-strict guardrails are documented; no canonical truth, runtime, schema, OpenAPI, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10 \
  "Describe the factual correction, missing parent evidence item, or stop-line wording needed before approval."
```

---

## Stop Lines To Carry Forward

| Stop line | Current evidence | Parent action |
|---|---|---|
| `ConsultResultCard` | `POST /bff/agora/workshops/{workshop_id}/consultations` is a `501` stub; no Agora BFF consultation detail/projection GET route was found. | Keep blocked; do not call internal `/api/v1/consult/*`; file a BFF consultation projection follow-up if needed. |
| `VersionCompareCard` | v1.3 lists version comparison surfaces, while inspected workshop version routes remain `501`. | Keep blocked until a backend versioning/card-projection owner lands runtime support. |
| `WorkshopCard` projection | v1.3 lists `GET /bff/agora/workshops/{workshop_id}/cards`; no matching inspected BFF runtime route was found in this pass. | Do not fabricate typed `WorkshopCard.payload`; coordinate with AG-FE-SW-002 and backend/card-projection work. |
| Workshop-level research-run dispatch | `POST /bff/agora/workshops/{workshop_id}/research-runs` remains `501`; plan-scoped dispatch is implemented. | Dispatch only through `POST /bff/agora/research-plans/{plan_id}/runs`. |
| Full conversation/completeness integration | AG-FE-SW-002 remains `todo` and owns conversation/result cards plus completeness rail. | Do not claim this from AG-FE-RS-001's route-backed slice. |

---

## Validation

Focused validation for this support-only packet:

```bash
git status --short
# expected before commit: generated task brief plus this support artifact

git diff --check -- \
  .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_10.md \
  support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10.md
# expected: no whitespace errors

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
# source: active; status: in_progress; owner: Codex; reviewer: Claude

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
# source: active; status: todo; parent artifacts include research.ts, ResearchRunCard.tsx, BacktestResultCard.tsx

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002
# source: active; status: todo; owns ResearchPlanCard.tsx, ConsultResultCard.tsx, and completeness rail

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001
# source: archive; terminal_status: done

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002
# source: archive; terminal_status: done

AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004
# source: archive; terminal_status: done
```

No runtime, schema, OpenAPI, canonical truth, or frontend implementation tests
are required for this support-only packet.
