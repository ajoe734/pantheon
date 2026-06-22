# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 6

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It is a final intake delta for the parent owner and reviewer: it
compresses the prior AG-FE-RS-001 handoff packets into the facts that must be
checked before frontend implementation starts, and it names the exact stop lines
that should remain blockers instead of being filled in locally.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support artifacts do not override product truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_6.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support changes need explicit task scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, review, merge, then owner closeout. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001` | Parent remains `todo`; depends on `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004`; frontend must stop on unclear specs/design gaps. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-002` | Conversation/result cards and completeness rail remain `todo`; full stream-card integration is not yet delivered. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-RS-002` | Archived `done`; run/progress/result projection implementation and closeout are complete. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 2-5 document route inventory, envelope corrections, SSE runtime shape, stop lines, and parent-owner dispatch guidance. |
| `services/control-plane/bff/agora/research/router.py` | Implemented plan/run route family; create needs `Idempotency-Key`; approve/cancel/dispatch need `If-Match` and `Idempotency-Key`; run detail returns raw projection. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop versions, workshop-level research-runs, consultations, and conclude routes remain explicit `501` stubs; `/stream` is implemented. |
| `services/control-plane/bff/tests/test_agora_research_run_projection.py` | Tests assert raw run detail, list/artifact envelopes, no-order proof, and `research.run.queued` event publication. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests assert runtime SSE events use `type` and `data`; deferred workshop stubs still return `501`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 lists `/cards`, version-comparisons, and research routes; runtime does not implement every OpenAPI-listed workshop/card/version surface. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.research.v1` has `execution_authority: research_only`. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Plan no-order proof is `research_plan_no_order_route`. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Run projection requires execution status, progress, backend mode, no-order proof, and optional metrics/findings/warnings/blockers/artifacts/evidence. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

Follow-up 6 does not replace the previous packets. It adds a compact acceptance
gate for the parent owner and reviewer so AG-FE-RS-001 can start the frontend
slice without re-litigating the support packet chain.

| Use this packet for | Do not use this packet for |
|---|---|
| Final read-order and conflict-precedence guidance for the AG-FE-RS-001 support chain. | Changing canonical route, schema, capability, or runtime truth. |
| Reviewer checklist for whether the parent owner can safely absorb the handoff. | Claiming `ConsultResultCard`, `VersionCompareCard`, `/cards`, or stream-card integration is unblocked. |
| A short route-backed implementation gate before writing `research.ts` and card adapters. | Authorizing mocks, local fixture fallback, or frontend direct fanout to non-BFF services. |
| Stop-line wording the parent owner can copy into blockers. | Creating new BFF routes, OpenAPI deltas, JSON schema fields, or UI contracts. |

---

## Packet Chain Conflict Rules

Read the AG-FE-RS-001 support artifacts in this order:

1. `AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md`
2. `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
3. `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
4. `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
5. `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`
6. This packet

When the packets disagree, apply these rules:

| Conflict area | Use this source |
|---|---|
| Response envelope shapes | Follow-up 2 and later. Run detail is raw `ResearchRunProjection`; plan detail and commands use envelopes; list routes use `items[]`. |
| Run cancel behavior | Follow-up 2 and later. Terminal runs return conflict, not no-op success. |
| SSE event shape | Follow-up 2 and later. Current runtime stream uses `id`, `type`, `timestamp`, and `data`; do not assume the richer schema-only fields are present at runtime. |
| Parent absorption order | Follow-ups 3-6. Start with route-backed `research.ts` and adapter tests; hold full stream-card integration for AG-FE-SW-002/card projection handoff. |
| Stop-line blockers | Follow-ups 4-6. Consultation, version compare, workshop `/cards`, and stream-card integration remain blockers unless the owning task lands concrete runtime support. |

The base packet remains useful for operator journeys and initial context, but
the corrections above should be treated as the active handoff facts.

---

## Parent Owner Intake Gate

Before AG-FE-RS-001 writes frontend code, the parent owner should be able to
answer all rows below with "yes".

| Gate | Required answer |
|---|---|
| Clean frontend branch | Is AG-FE-RS-001 starting from a clean `execute-plans` task branch on the active delivery base? |
| Type source | Are v1.3 Agora research OpenAPI/schema types regenerated or explicitly mirrored before `research.ts` is written? |
| Dependency check | Has the owner re-checked `AG-FE-SW-002`, `AG-BE-RS-002`, and `AG-XR-OPENAPI-004` with the status CLI? |
| Route-backed first slice | Is the first implementation slice limited to BFF-only `research.ts` and route-backed adapters/tests? |
| Envelope discipline | Does the client distinguish list envelopes, plan detail envelopes, command envelopes, and raw run detail? |
| Header discipline | Do command methods send `Idempotency-Key`, and do approve/cancel/dispatch send fresh `If-Match` where runtime requires it? |
| Runtime SSE discipline | Does the frontend consume current runtime SSE fields, not unimplemented schema-only event fields? |
| Research-only authority | Are order placement, capital binding, canary/live promotion, direct governance writes, and `RuntimeBinding` writes absent from the client and cards? |
| Stop-line discipline | Are ConsultResultCard, VersionCompareCard, `/cards`, and full stream-card/completeness-rail integration blocked unless their owning tasks provide runtime support? |

If any row is "no", AG-FE-RS-001 should stop and record a blocker instead of
guessing, mocking, or widening scope.

---

## Route-backed Slice That Can Proceed

The parent frontend task can proceed only with route-backed research plan/run
surfaces that are implemented in the BFF router.

| Frontend method | Route | Active handling rule |
|---|---|---|
| `listWorkshopResearchPlans` | `GET /bff/agora/workshops/{workshop_id}/research-plans` | Read list envelope `items[]`. |
| `createWorkshopResearchPlan` | `POST /bff/agora/workshops/{workshop_id}/research-plans` | Send `Idempotency-Key`; read detail envelope `data` and `meta.etag`. |
| `getResearchPlan` | `GET /bff/agora/research-plans/{plan_id}` | Read detail envelope `data`, `allowedActions`, and `meta.etag`. |
| `approveResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/approve` | Send `If-Match` and `Idempotency-Key`; refetch plan for a fresh ETag. |
| `cancelResearchPlan` | `POST /bff/agora/research-plans/{plan_id}/cancel` | Send `If-Match` and `Idempotency-Key`; hide or disable outside cancellable statuses. |
| `listResearchPlanRuns` | `GET /bff/agora/research-plans/{plan_id}/runs` | Read list envelope `items[]`; items are run projections. |
| `dispatchResearchRun` | `POST /bff/agora/research-plans/{plan_id}/runs` | Send `If-Match` and `Idempotency-Key`; treat response as queued ids only, then fetch run detail. |
| `getResearchRun` | `GET /bff/agora/research-runs/{run_id}` | Raw `ResearchRunProjection`; do not unwrap `data`. |
| `cancelResearchRun` | `POST /bff/agora/research-runs/{run_id}/cancel` | Send `Idempotency-Key`; only `queued`, `dispatching`, and `running` are cancellable. |
| `listResearchRunArtifacts` | `GET /bff/agora/research-runs/{run_id}/artifacts` | Read list envelope `items[]`; items can include artifact refs and evidence refs. |

Route-backed card rendering is limited to:

| Component | Safe source | Boundary |
|---|---|---|
| `ResearchPlanCard` | `ResearchPlanExecution` detail envelope and metadata. | Stop if the intended source must be `WorkshopCard` payloads before `/cards` runtime support lands. |
| `ResearchRunCard` | Raw `ResearchRunProjection`, polling, and runtime `research.run.*` SSE events. | Preserve `backend.mode`, `warnings[]`, and `blocking_reasons[]`; do not synthesize green state. |
| `BacktestResultCard` | Succeeded backtest-like `ResearchRunProjection`. | Do not invent a separate BacktestResult route or schema. |

---

## Stop Lines That Remain Active

These are not frontend implementation details. They are blockers until an owner
lands or explicitly hands off the missing runtime/card projection support.

| Stop line | Current evidence | Required parent action |
|---|---|---|
| Workshop `/cards` projection | v1.3 OpenAPI lists `/bff/agora/workshops/{workshop_id}/cards`, but no matching inspected BFF runtime route was found. | Do not fabricate `WorkshopCard.payload`; open a backend/card-projection blocker or keep route-backed rendering explicit. |
| `ConsultResultCard` | Workshop consultation route remains a `501` stub and no Agora BFF consultation detail/projection GET route was found. | Do not call internal `/api/v1/consult/*` from the frontend; keep card blocked. |
| `VersionCompareCard` | Version-comparison surface exists in v1.3 docs/OpenAPI, while inspected workshop version routes remain deferred stubs. | Keep blocked for AG-FE-RS-001 unless a versioning/card-projection runtime task lands first. |
| Workshop-level research-run route | `POST /bff/agora/workshops/{workshop_id}/research-runs` remains a `501` stub. | Dispatch only through plan-scoped `POST /bff/agora/research-plans/{plan_id}/runs`. |
| Stream-card/completeness-rail integration | `AG-FE-SW-002` remains `todo` and owns conversation/result cards plus completeness rail. | Do not claim full conversation-card integration from the route-backed AG-FE-RS-001 slice alone. |
| Schema/runtime SSE mismatch | Runtime emits current `type`/`data` events; richer `WorkshopStreamEvent` fields are schema surface, not proven runtime fields here. | Consume runtime shape; open an alignment blocker if acceptance requires exact schema event shape. |

Suggested blocker text:

```text
AG-FE-RS-001 blocked on WorkshopCard projection source: v1.3 OpenAPI lists
GET /bff/agora/workshops/{workshop_id}/cards, but no matching inspected BFF
runtime route is available. Frontend must not fabricate typed WorkshopCard
payloads; need backend/card-projection handoff or an explicit route-backed
rendering decision.
```

```text
AG-FE-RS-001 blocked on ConsultResultCard: Agora workshop consultation route is
a 501 stub and no consultation detail/projection GET route was found. Frontend
must not fan out to internal consult routes or mock consultation payloads.
```

```text
AG-FE-RS-001 blocked on stream-card integration while AG-FE-SW-002 remains todo:
route-backed research client/card slices can proceed, but full conversation
card and completeness rail integration require the owning workshop-card task to
land or hand off a concrete card source.
```

---

## Reviewer Intake Checklist

Claude should verify this sidecar on the following basis:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally changed. The generated task brief is task-scoped context, not canonical truth. |
| Chain handling | Follow-up 6 does not supersede prior packets; it defines read order and conflict-precedence rules. |
| Runtime accuracy | Route-backed slice, envelope handling, header requirements, run cancel behavior, and 501 stop lines match inspected router/tests. |
| Dependency honesty | `AG-FE-SW-002` remains a coordination gate for stream-card and completeness-rail integration. |
| Blocker clarity | `/cards`, consultation, version compare, workshop-level research-run, and SSE shape issues remain explicit blockers or alignment tasks. |
| No-order guardrail | `agora.research.v1` remains `research_only`; no order, capital, broker write, canary/live promotion, governance mutation, or `RuntimeBinding` action is suggested. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 packet chain precedence, parent-owner intake gate, route-backed first slice, active stop lines, and no-order guardrails are documented; no canonical truth, runtime, schema, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff intake delta approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Describe the factual correction, missing stop-line, or parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
git status --short
# -> ?? .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_6.md
# -> ?? support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md

AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
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
# -> 27 passed in 22.33s

git diff --check
# -> passed
```
