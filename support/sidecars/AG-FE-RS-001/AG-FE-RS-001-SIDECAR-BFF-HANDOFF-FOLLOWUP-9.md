# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 9

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Review approved; owner closeout note ready |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It turns the existing AG-FE-RS-001 sidecar chain into a parent-owner
implementation and review cut: which frontend slice can be claimed, which
files/components should be treated as coordination points, and which blocker
language should remain active until a backend/card-projection owner lands the
missing surfaces.

Follow-up 9 should be read after Follow-up 8. It does not supersede the route
facts, response-shape corrections, or stop lines already recorded there.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support artifacts do not override product or architecture truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_9.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support artifact changes need narrow task scope and explicit worker commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, then owner closeout. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` | Active task is `review_approved`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Parent remains `todo`; artifacts name `research.ts`, `ResearchRunCard.tsx`, and `BacktestResultCard.tsx`; summary also mentions research plan and consult cards but requires STOP on unclear specs. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002` | Still `todo`; owns conversation/result cards and completeness rail, with `ResearchPlanCard.tsx` and `ConsultResultCard.tsx` in its artifact list. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Archived `done`; run/progress/result projection implementation and closeout are complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 2-8 document route inventory, mixed envelopes, runtime SSE shape, card-projection gaps, parent intake gates, and query/parser/refetch guidance. |
| `services/control-plane/bff/agora/research/router.py` | Plan-scoped research plan/run route family is implemented; run detail is raw `ResearchRunProjection`; command routes require idempotency and, where applicable, fresh plan ETags. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop version routes, workshop-level research-runs, consultations, and conclude remain `501`; `/stream` is implemented. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run detail, list/artifact envelopes, no-order proof, and research run SSE publication. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert runtime stream events use `id`, `type`, `timestamp`, and `data`; deferred workshop stubs still return `501`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists `/cards`, version-comparisons, research plans/runs, and typed responses; runtime support is not present for every listed workshop/card/version surface. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Plan projection carries `status`, `stages[]`, `run_ids[]`, and `no_order_route_proof=research_plan_no_order_route`. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Run projection carries status, progress, backend mode, metrics, findings, warnings, blockers, artifacts/evidence, and `no_order_route_proof=research_only_not_direct_action`. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` has `execution_authority: research_only`; workshop and trading capabilities do not grant order or capital-write authority. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

Follow-up 8 already gives the parent owner the query/parser/header/refetch
contract. This packet adds the implementation cut that should prevent the
parent work from accidentally claiming surfaces owned by AG-FE-SW-002 or by
future backend/card-projection tasks.

| Added item | Why parent needs it |
|---|---|
| Frontend ownership split | `AG-FE-RS-001` and `AG-FE-SW-002` both mention research cards; parent should avoid broad card-stream edits that belong to the workshop/card task. |
| Claimable vs non-claimable acceptance cut | Review can approve a route-backed research slice without treating consultation, version compare, or `/cards` projection as complete. |
| File/component coordination map | Prevents `ResearchPlanCard` and `ConsultResultCard` changes from being swept into the research route-backed PR without explicit owner coordination. |
| Blocker-ready wording | Gives the parent owner exact stop-line text when acceptance tries to require unavailable runtime/card projection surfaces. |

This packet does not add a route, schema, UI layout, card payload field, or test
fixture. Missing BFF surfaces remain blockers, not frontend TODOs to fill with
mock data.

---

## Parent Implementation Cut

### Claimable Route-backed Slice

AG-FE-RS-001 can make a narrow claim if it implements and tests only the
route-backed research plan/run surfaces that are already implemented in the
BFF:

| Parent deliverable | Scope that can be claimed | Review stop line |
|---|---|---|
| `execute-plans/src/lib/bff-v1/agora/research.ts` | Typed BFF client methods for research plan list/create/detail/approve/cancel, plan-scoped run list/dispatch, run detail/cancel, and run artifacts. | No direct calls to research services, consult services, OpenClaw, object stores, registry/governance, order routes, or RuntimeBinding. |
| Research client tests | Distinct parsing for list envelopes, plan detail envelopes, command envelopes, raw run detail, and artifact list envelopes. | No universal response wrapper that treats raw run detail as `data`. |
| Run-card adapter/rendering | `ResearchRunCard` from raw `ResearchRunProjection`, polling, and current runtime `research.run.*` SSE signals. | No invented fields from schema-only stream names; refetch run detail when authoritative state is needed. |
| Backtest-like result rendering | `BacktestResultCard` from a succeeded backtest-stage `ResearchRunProjection`, including metrics/findings/artifacts/evidence/backend mode. | No separate BacktestResult route, score, schema, or local fixture unless a backend owner lands it. |
| Plan-facing helpers | Reusable plan detail/action adapters can be added if needed for run dispatch flow. | Do not claim full `ResearchPlanCard` stream integration unless AG-FE-SW-002/card projection ownership is explicitly coordinated. |

The claimable slice is enough for the parent to prove live-strict BFF access,
header discipline, mixed response parsing, no-order guardrails, and route-backed
research run/result rendering.

### Coordination Surfaces

These surfaces are close to AG-FE-RS-001 but should not be silently absorbed
into the route-backed research PR:

| Surface | Current owner signal | Parent handling |
|---|---|---|
| `ResearchPlanCard.tsx` | AG-FE-SW-002 artifact list includes it; AG-FE-RS-001 summary references plan cards. | Treat as shared. Parent may build plan adapters used by run dispatch, but should coordinate before owning full card component or stream-card placement. |
| `ConsultResultCard.tsx` | AG-FE-SW-002 artifact list includes it; Agora consultation route is `501` and no detail/projection GET route was found. | Keep blocked. Do not call internal `/api/v1/consult/*`; do not mock consultation payloads. |
| Workshop conversation cards and completeness rail | AG-FE-SW-002 owns conversation/result cards and completeness rail. | Do not claim full conversation-card integration from AG-FE-RS-001's route-backed research slice. |
| `VersionCompareCard` / version surfaces | v1.3 lists version comparison surfaces, while inspected workshop version routes remain `501`. | Keep outside AG-FE-RS-001 unless a versioning/card-projection owner lands runtime support. |
| `GET /bff/agora/workshops/{workshop_id}/cards` | v1.3 OpenAPI lists it; no matching inspected BFF runtime route was found in this pass. | Do not fabricate typed `WorkshopCard.payload`; open or wait for backend/card-projection work. |

---

## Acceptance Cut For Parent Review

Reviewer can treat AG-FE-RS-001 as partially deliverable only under this cut:

| Claim | Accept if | Reject or block if |
|---|---|---|
| Research BFF client is ready | All implemented research plan/run routes are covered, headers are sent, response shapes are parsed separately, and live strict mode has no fallback. | Client calls internal services, assumes all responses are envelopes, or skips idempotency/ETag handling. |
| ResearchRunCard is ready | It renders raw projection fields, preserves `backend.mode`, `warnings[]`, `blocking_reasons[]`, progress, run status, and no-order proof. | It hides degraded/blocking state, derives success from SSE alone, or invents fields not present in runtime/schema. |
| BacktestResultCard is ready | It renders only succeeded backtest-like run projections and shows metrics/findings/artifacts/evidence/data cutoff/backend mode. | It renders from queued/running/failed runs, invents a separate route/schema, or offers promotion/order/capital actions. |
| ResearchPlan flow is usable for dispatch | The client fetches plan detail, stores fresh `meta.etag`, approves with `If-Match` and `Idempotency-Key`, refetches, then dispatches with fresh ETag. | Dispatch uses stale pre-approval ETag or a workshop-level research-run route. |
| Consultation card is complete | Not currently claimable from inspected runtime. | Any implementation uses internal consult routes, local mock payloads, or freeform LLM text as a consultation projection. |
| Full workshop card stream is complete | Not currently claimable from AG-FE-RS-001 alone. | Acceptance implies `/cards`, version compare, consultation projection, or completeness rail is complete without the owning task landing runtime support. |

Useful review framing:

```text
Approve the route-backed AG-FE-RS-001 slice only for research.ts,
ResearchRunCard, BacktestResultCard, and supporting adapters/tests. Keep
ConsultResultCard, VersionCompareCard, WorkshopCard projection, and full
conversation/completeness integration as explicit blockers or AG-FE-SW-002 /
backend handoffs.
```

---

## Parent Work Items To Create Or Keep

| Work item | Suggested owner path | Reason |
|---|---|---|
| Implement `research.ts` BFF client | AG-FE-RS-001 in execute-plans | Directly tied to implemented BFF research routes. |
| Add research client adapter tests | AG-FE-RS-001 in execute-plans | Proves mixed envelopes, raw run detail, headers, and no fallback. |
| Implement route-backed `ResearchRunCard` | AG-FE-RS-001 in execute-plans | Parent artifact list names this component; data source is implemented. |
| Implement route-backed `BacktestResultCard` | AG-FE-RS-001 in execute-plans | Parent artifact list names this component; source is succeeded `ResearchRunProjection`. |
| Decide `ResearchPlanCard` ownership | Claude parent owner with AG-FE-SW-002 owner/reviewer | Artifact ownership is split across parent summary and AG-FE-SW-002 list. |
| File consultation BFF projection follow-up | Backend consultation/BFF owner | Required before `ConsultResultCard` can be live strict. |
| File WorkshopCard projection follow-up | Backend workshop/card-projection owner with AG-FE-SW-002 | Required before typed stream-card rendering from `/cards`. |
| File version compare/runtime follow-up | Backend versioning/card-projection owner | Required before `VersionCompareCard` can be claimed. |

If the parent owner starts frontend work before the coordination items land, the
safe implementation order is:

1. `research.ts` typed route client and response adapters.
2. Client tests for headers, envelopes, raw projection, refetch, and live strict.
3. `ResearchRunCard` route-backed rendering.
4. `BacktestResultCard` route-backed rendering.
5. Blockers for consult, version compare, `/cards`, and full stream/completeness
   integration.

---

## Blocker Text Parent May Reuse

```text
AG-FE-RS-001 split required: the route-backed research client/run/result slice
can proceed, but ResearchPlanCard stream placement overlaps AG-FE-SW-002.
Before claiming full plan-card integration, parent owner must coordinate file
ownership and card-source expectations with the workshop-card task.
```

```text
AG-FE-RS-001 blocked on ConsultResultCard: Agora workshop consultation route is
a 501 stub and no consultation detail/projection GET route was found. Frontend
must not call internal /api/v1/consult/* routes, parse freeform text, or mock
consultation payloads in live strict mode.
```

```text
AG-FE-RS-001 blocked on WorkshopCard projection: v1.3 OpenAPI lists
GET /bff/agora/workshops/{workshop_id}/cards, but the inspected BFF runtime
does not expose a matching route. Frontend must not fabricate typed
WorkshopCard.payload values.
```

```text
AG-FE-RS-001 blocked on VersionCompareCard: version comparison appears in the
v1.3 surface, but inspected workshop version routes remain 501. Need backend
versioning/card-projection runtime support before frontend implementation.
```

---

## No-order And Live-strict Guardrails

The parent implementation must preserve these guardrails from the prior packet
chain:

| Guardrail | Required behavior |
|---|---|
| `agora.research.v1` authority | Research-only display and dispatch. No order placement, capital binding, RuntimeBinding write, canary/live promotion, or governance mutation. |
| BFF-only frontend access | `execute-plans` uses configured BFF base URL and `/bff/agora/*` routes only for this slice. |
| Missing surface handling | Missing consultation, version, `/cards`, or workshop-level dispatch routes are blockers, not local mock opportunities. |
| Degraded/backend mode visibility | `backend.mode`, `warnings[]`, and `blocking_reasons[]` stay visible; fixture/stub/blocking state is not converted to green success. |
| Runtime SSE handling | Consume current runtime event shape `{ id, type, timestamp, data }`; refetch run detail for authoritative projection. |

---

## Reviewer Intake Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally changed; no runtime/frontend/schema/OpenAPI/canonical files are modified. |
| Added value | Follow-up 9 adds implementation/review cut and component ownership guidance rather than repeating Follow-up 8's query/parser table. |
| Runtime accuracy | Implemented plan/run routes, mixed response shapes, raw run detail, 501 stubs, and SSE runtime fields match inspected code/tests. |
| Parent safety | AG-FE-RS-001 has a route-backed claimable slice and explicit coordination/blocker points for ResearchPlanCard, ConsultResultCard, VersionCompareCard, `/cards`, and AG-FE-SW-002. |
| No-order guardrail | No order, capital, canary/live, governance mutation, RuntimeBinding, direct service fanout, or local fixture fallback is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 implementation/review cut, component ownership coordination, active blocker wording, and no-order/live-strict guardrails are documented; no canonical truth, runtime, schema, OpenAPI, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9 \
  "Describe the factual correction, missing ownership split, or blocker wording needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# expected before closeout commit: this support artifact plus the generated task brief as task-scoped context

git diff --check -- support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_9.md
# expected: no whitespace errors

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9
# source: active; status: review_approved; owner: Codex; reviewer: Claude

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
# source: active; status: todo; parent artifacts include research.ts, ResearchRunCard.tsx, BacktestResultCard.tsx

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002
# source: active; status: todo; owns conversation/result cards and completeness rail

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002
# source: archive; terminal_status: done

AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004
# source: archive; terminal_status: done
```

No runtime, schema, OpenAPI, canonical truth, or frontend implementation tests
are required for this support-only packet.

## Owner Closeout Note

Claude approved this packet through the `review_approved` status transition
with `review_file` set to this support artifact. The packet PR `#2227` merged
into `dev` at merge commit `25b2ca03469bdc06584921adb4eabdf8169c00c4`; the
reviewed task commit was `c7d6b172c932812daebc83a7e0651af31233cc93`.

This owner closeout note only records finalization context for the support
sidecar. It does not broaden the packet, promote canonical truth, change BFF
runtime/OpenAPI/schema/frontend code, or change parent `AG-FE-RS-001` status.
Parent owner/reviewer still decide whether and how to absorb the route-backed
research slice, component ownership split, blocker wording, and no-order
guardrails into the main AG-FE-RS-001 implementation.

After this closeout commit merges, Codex should run:

```bash
AI_NAME=Codex ./scripts/ai-status.sh done AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-9 \
  "Closeout complete. Support-only AG-FE-RS-001 BFF/frontend handoff packet follow-up merged; parent owner may absorb the approved route-backed research cut and blocker wording."
```
