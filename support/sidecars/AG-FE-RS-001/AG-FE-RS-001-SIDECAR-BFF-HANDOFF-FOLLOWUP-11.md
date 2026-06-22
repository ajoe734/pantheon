# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 11

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, or execute-plans frontend
code. It consolidates the existing AG-FE-RS-001 sidecar chain into a parent
start gate: what the parent owner can implement now, what must stay blocked,
and what Claude should verify before accepting this as useful handoff material.

Follow-up 11 does not add new route facts. The factual route inventory,
response-shape corrections, card-projection gaps, and parent absorption order
remain in the base packet plus Follow-ups 7, 8, 9, and 10. This packet is the
handoff convergence note for starting the parent work without widening scope.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support artifacts do not override architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_11.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, then owner closeout when review-approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Follow-up 10 is archived `done`; PR `#2238` merged; parent absorption order and evidence contract are already approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Parent remains `todo`; owner `Claude`, reviewer `Codex`; artifacts are `research.ts`, `ResearchRunCard.tsx`, and `BacktestResultCard.tsx`; the brief requires STOP on unclear specs or code/spec mismatch. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002` | Conversation/result cards and completeness rail remain `todo`; it owns `ResearchPlanCard.tsx`, `ConsultResultCard.tsx`, and the completeness rail coordination surface. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001` | Archived `done`; plan CRUD/approve/cancel/stage routing facade is complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Archived `done`; run/progress/result projection, artifact list, and research SSE publication are complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 7-10 already cover route inventory, missing-surface handoff queue, parser/header/refetch rules, implementation/review cut, and parent PR evidence. |
| `git fetch origin`; `git rev-parse HEAD origin/dev` | Worktree HEAD equals current `origin/dev` at `08fdd92e98be142c6b8caf870272c61a1d76c89e` before this packet was written. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

| Added item | Why it matters now |
|---|---|
| Parent start gate | Clarifies that AG-FE-RS-001 can start only the route-backed research client/run/result slice. |
| Cross-task boundary | Keeps `ResearchPlanCard`, `ConsultResultCard`, full stream cards, and completeness rail coordinated with `AG-FE-SW-002` instead of silently absorbed. |
| Sidecar convergence rule | Prevents more BFF handoff follow-ups unless new backend/runtime facts or parent implementation evidence changes the route picture. |
| Reviewer handoff rubric | Gives Claude a narrow review question: is this packet useful, accurate, and support-only. |

This packet should be read as a go/no-go memo for the parent owner, not as a
new source of API truth.

---

## Parent Start Gate

AG-FE-RS-001 may start a narrow parent PR if all of these are true:

| Gate | Required state |
|---|---|
| Backend route dependency | `AG-BE-RS-001` and `AG-BE-RS-002` are archived `done`. |
| Schema/OpenAPI dependency | `AG-XR-OPENAPI-004` is archived `done`; v1.3 bundle exists. |
| Frontend scope | First PR is limited to `research.ts`, `ResearchRunCard`, `BacktestResultCard`, route-backed adapters, and tests. |
| Data source | Frontend uses only configured BFF `/bff/agora/*` routes for this slice. |
| Response parsing | List/detail/command/raw-run shapes are handled separately. |
| Mutation discipline | Idempotency keys and fresh plan ETags are generated and tested. |
| Authority guardrail | UI does not offer order, capital binding, RuntimeBinding, canary/live promotion, or governance mutation actions. |

If any of these fail, the parent owner should open a blocker or reviewer
handoff rather than patching around the gap locally.

### Suggested first parent PR claim

```text
This AG-FE-RS-001 PR claims only the route-backed research client/run/result
slice: research.ts, ResearchRunCard, BacktestResultCard, supporting adapters,
and focused tests. It does not claim ConsultResultCard, VersionCompareCard,
WorkshopCard projection, workshop-level research-run dispatch, or full
conversation/completeness integration.
```

---

## What Can Start Now

| Parent work | Reason it can start | Evidence to attach |
|---|---|---|
| `execute-plans/src/lib/bff-v1/agora/research.ts` | Plan/run BFF routes are implemented and previous packets document mixed response shapes. | Client tests for list envelopes, plan detail envelopes, command envelopes, raw run projection, and artifact list envelopes. |
| `ResearchRunCard` route-backed rendering | `GET /bff/agora/research-runs/{run_id}` returns raw `ResearchRunProjection`. | UI tests render `execution_status`, `progress`, `backend.mode`, `warnings[]`, `blocking_reasons[]`, and `no_order_route_proof`. |
| `BacktestResultCard` from succeeded research runs | Succeeded backtest-like runs are represented by `ResearchRunProjection`; no distinct route is needed for first slice. | Tests render metrics/findings/artifacts/evidence/data cutoff/backend mode only after `execution_status=succeeded`. |
| Header/refetch helpers | Plan commands require fresh `If-Match` and `Idempotency-Key`; command responses are acknowledgements. | Tests prove approve refetches plan before dispatch and dispatch fetches run detail before rendering. |

These are enough to prove live-strict BFF behavior without full workshop card
projection.

---

## What Must Stay Blocked

| Surface | Current blocker | Required handling |
|---|---|---|
| `ConsultResultCard` | Agora workshop consultation route remains unavailable for live strict card projection; no inspected Agora BFF consultation detail/projection GET route is available. | Keep blocked. Do not call internal `/api/v1/consult/*`, parse freeform text, or mock consultation payloads. |
| `VersionCompareCard` | Version comparison appears in v1.3 surfaces, but previous packets record inspected runtime gaps. | Keep outside AG-FE-RS-001 until a versioning/card-projection owner lands runtime support. |
| `WorkshopCard` projection | `/cards` is a listed surface in v1.3, but previous packets did not find matching inspected runtime support. | Do not fabricate typed `WorkshopCard.payload`; coordinate backend/card-projection work and `AG-FE-SW-002`. |
| Workshop-level research-run dispatch | Plan-scoped dispatch is implemented; workshop-level dispatch remains a stop line from the previous packets. | Dispatch only through `POST /bff/agora/research-plans/{plan_id}/runs`. |
| Full conversation and completeness integration | `AG-FE-SW-002` is still `todo` and owns conversation/result cards plus completeness rail. | Do not claim this from the route-backed AG-FE-RS-001 slice. |

Blockers should be explicit in the parent PR body or task handoff if acceptance
pressure tries to include these surfaces.

---

## Cross-task Boundary With AG-FE-SW-002

| File or concern | Treat as | Parent rule |
|---|---|---|
| `research.ts` | AG-FE-RS-001-owned for the route-backed research slice. | Implement and test BFF-only client methods here. |
| `ResearchRunCard.tsx` | AG-FE-RS-001-owned for route-backed run state. | Bind to raw `ResearchRunProjection`; preserve degraded and blocking states. |
| `BacktestResultCard.tsx` | AG-FE-RS-001-owned for succeeded backtest-like run results. | Render from `ResearchRunProjection`; do not create a separate unbacked result route. |
| `ResearchPlanCard.tsx` | Shared/coordination surface because `AG-FE-SW-002` lists it. | Parent may add adapters needed by dispatch flow, but should not claim full stream-card placement without coordination. |
| `ConsultResultCard.tsx` | `AG-FE-SW-002` listed surface and BFF-blocked for AG-FE-RS-001. | Keep blocked until consultation projection exists. |
| Completeness rail and full conversation stream | AG-FE-SW-002. | Out of scope for the first AG-FE-RS-001 parent PR. |

This boundary lets the parent ship a route-backed first slice without waiting
for every workshop card and rail dependency.

---

## Sidecar Convergence Rule

The AG-FE-RS-001 support chain is already dense:

| Packet family | Use it for |
|---|---|
| Base packet | Route inventory, operator journeys, card binding guide, no-order guardrail. |
| Follow-up 7 | Backend/card-projection handoff queue and active missing-surface blockers. |
| Follow-up 8 | Query/parser/header/refetch and frontend smoke-test contract. |
| Follow-up 9 | Parent implementation cut, shared component ownership, and blocker wording. |
| Follow-up 10 | Parent absorption order, parent PR evidence contract, and reviewer decision table. |
| Follow-up 11 | Parent start gate, cross-task boundary, and sidecar convergence rule. |

Do not create another BFF handoff sidecar only to restate the same route facts.
Create a new sidecar only if one of these changes:

| Trigger | Example |
|---|---|
| New backend/runtime evidence | A BFF route lands or changes response shape. |
| New parent implementation evidence | The execute-plans parent PR exposes a concrete mismatch needing reviewer guidance. |
| New review finding | Claude or Codex identifies an inaccurate stop line or unsafe frontend suggestion. |
| New ownership collision | AG-FE-RS-001 and AG-FE-SW-002 both need the same file in incompatible ways. |

Otherwise, the next useful move is parent implementation, not more packet
material.

---

## Reviewer Handoff

Claude should review this packet as support-only convergence material.

| Review question | Approve if | Reopen if |
|---|---|---|
| Scope | Only the generated task brief and this support artifact changed. | Runtime, schema, OpenAPI, canonical truth, execute-plans frontend, or governance files changed. |
| Added value | Packet adds a parent start gate and convergence rule beyond Follow-up 10. | Packet merely repeats route tables without a new parent decision point. |
| Factual alignment | Parent/dependency status and previous packet references match the status commands above. | Any dependency state, owner/reviewer, or stop line is stale or false. |
| Parent safety | Route-backed first slice remains narrow and missing surfaces remain blockers. | Packet encourages mocks, direct internal service calls, fabricated card payloads, or order/capital/governance actions. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 parent start gate, cross-task boundary with AG-FE-SW-002, sidecar convergence rule, and no-order/live-strict stop lines are documented; no canonical truth, runtime, schema, OpenAPI, or frontend files changed." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11 \
  "Support-only AG-FE-RS-001 BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11 \
  "Describe the factual correction, missing start gate, ownership-boundary issue, or stop-line wording needed before approval."
```

---

## Validation

Focused validation for this support-only packet:

```bash
git status --short
# expected before commit: generated task brief plus this support artifact

git diff --check -- \
  .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_11.md \
  support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11.md
# expected: no whitespace errors

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11
# source: active; status: in_progress; owner: Codex; reviewer: Claude

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
# source: active; status: todo; parent route-backed artifacts include research.ts, ResearchRunCard.tsx, BacktestResultCard.tsx

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002
# source: active; status: todo; owns ResearchPlanCard.tsx, ConsultResultCard.tsx, and completeness rail coordination surface

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001
# source: archive; terminal_status: done

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002
# source: archive; terminal_status: done

AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004
# source: archive; terminal_status: done
```

No runtime, schema, OpenAPI, canonical truth, or frontend implementation tests
are required for this support-only packet.

*Prepared by Codex for the `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11`
support slice.*
