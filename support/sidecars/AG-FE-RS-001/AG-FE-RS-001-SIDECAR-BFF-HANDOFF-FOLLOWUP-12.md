# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 12

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, registry/governance code, broker/order paths,
RuntimeBinding, canary/live-promotion behavior, or execute-plans frontend code.

Follow-up 12 is a parent-intake and stop-loop memo. Follow-up 11 already
declared the AG-FE-RS-001 sidecar chain converged; this packet turns that rule
into an explicit reviewer/parent handoff: do not create another BFF handoff
sidecar unless new backend/runtime evidence, parent implementation evidence,
review findings, or ownership collisions change the facts.

It does not add new route facts. Use the base packet plus Follow-ups 7-11 for
the detailed route inventory, parser/header/refetch rules, card ownership
boundaries, parent absorption order, and first-PR start gate.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership and lifecycle; support artifacts do not override architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_12.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, then owner closeout when review-approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` | Follow-up 10 is archived `done`; parent absorption order and PR evidence contract were approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` | Follow-up 11 is archived `done`; parent start gate, AG-FE-SW-002 boundary, convergence rule, and stop lines were approved. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Parent remains `todo`; owner `Claude`, reviewer `Codex`; artifacts are `research.ts`, `ResearchRunCard.tsx`, and `BacktestResultCard.tsx`; task requires STOP on unclear specs or code/spec mismatch. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-002` | Conversation/result cards and completeness rail remain `todo`; it owns `ResearchPlanCard.tsx`, `ConsultResultCard.tsx`, and the rail coordination surface. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001` | Archived `done`; plan CRUD/approve/cancel/stage routing facade is complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Archived `done`; run/progress/result projection, artifact list, and research SSE publication are complete. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema/capability bundle is complete. |
| Prior AG-FE-RS-001 sidecar packets | Base packet plus Follow-ups 7-11 already cover the actionable route-backed handoff and stop lines. |
| `git fetch origin`; `git merge --ff-only origin/dev`; `git log --oneline --decorate -5` | Task branch was fast-forwarded to current `origin/dev` at `1f312800924886dea37d5037e218ad899985acf8` before this packet was written. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

| Added item | Why it matters now |
|---|---|
| Stop-loop disposition | Makes Follow-up 11's convergence rule reviewable: no more support-only repeats without changed facts. |
| Parent intake checklist | Gives Claude a short, executable checklist for starting AG-FE-RS-001 from the approved sidecar chain. |
| Reviewer closeout rule | Gives Claude a narrow review question for this packet: did it preserve the support-only boundary and stop further duplication. |
| Handoff index | Points the parent owner to the exact packet family to consume without rereading unrelated history. |

This packet should be the last support-only AG-FE-RS-001 BFF handoff packet
under the current facts.

---

## Current Dispatch State

| Surface | State |
|---|---|
| `AG-FE-RS-001` | Active `todo`; owner `Claude`; reviewer `Codex`; first route-backed slice can start only inside the parent task. |
| `AG-FE-SW-002` | Active `todo`; owns conversation/result cards, `ResearchPlanCard`, `ConsultResultCard`, and completeness rail coordination. |
| `AG-BE-RS-001` | Archived `done`; plan facade is available. |
| `AG-BE-RS-002` | Archived `done`; run projection, artifacts, and SSE publication are available. |
| `AG-XR-OPENAPI-004` | Archived `done`; v1.3 bundle is available. |
| Follow-up 10 | Archived `done`; parent absorption order and PR evidence contract approved. |
| Follow-up 11 | Archived `done`; parent start gate and sidecar convergence rule approved. |

The parent owner should not wait for another support packet before starting the
route-backed AG-FE-RS-001 implementation slice.

---

## Parent Intake Checklist

Claude can treat the sidecar chain as ready for parent intake when starting
AG-FE-RS-001:

| Check | Required parent handling |
|---|---|
| Scope | First PR claims only `research.ts`, route-backed `ResearchRunCard`, route-backed `BacktestResultCard`, adapters, and focused tests. |
| BFF data source | Use configured `/bff/agora/*` routes only; no direct research orchestrator or internal consultation service fanout. |
| Response shapes | Preserve distinct parsers for list envelopes, plan detail envelopes, command acknowledgements, raw run projection, and artifact list envelopes. |
| Mutation headers | Generate and test `Idempotency-Key` and fresh `If-Match` where required. |
| Refetch discipline | Approve refetches plan before dispatch; dispatch fetches run detail before rendering authoritative run state. |
| Degraded states | Render `backend.mode`, `warnings[]`, `blocking_reasons[]`, and `no_order_route_proof` without hiding fixture/stub/blocked states. |
| Authority boundary | No order placement, capital binding, RuntimeBinding write, registry/governance mutation, canary/live promotion, or broker path. |
| Cross-task boundary | Coordinate `ResearchPlanCard`, `ConsultResultCard`, full stream cards, and completeness rail with `AG-FE-SW-002`; do not silently absorb them. |
| Stop conditions | If design, schema, runtime, or ownership facts do not line up, open a blocker or reviewer handoff instead of inventing fields/routes/widgets. |

Suggested parent PR claim remains:

```text
This AG-FE-RS-001 PR claims only the route-backed research client/run/result
slice: research.ts, ResearchRunCard, BacktestResultCard, supporting adapters,
and focused tests. It does not claim ConsultResultCard, VersionCompareCard,
WorkshopCard projection, workshop-level research-run dispatch, or full
conversation/completeness integration.
```

---

## Stop Lines That Still Apply

| Stop line | Required handling |
|---|---|
| `ConsultResultCard` | Keep blocked for live-strict AG-FE-RS-001 until an Agora BFF consultation projection exists; do not call internal `/api/v1/consult/*`. |
| `VersionCompareCard` | Keep outside the first route-backed AG-FE-RS-001 slice until backend/card-projection runtime support is landed and verified. |
| `WorkshopCard` projection | Do not fabricate typed card payloads; coordinate backend/card-projection work and AG-FE-SW-002. |
| Workshop-level research-run dispatch | Use only plan-scoped dispatch through `POST /bff/agora/research-plans/{plan_id}/runs`. |
| Full conversation/completeness integration | Remains AG-FE-SW-002 coordination work, not a hidden acceptance item for the first AG-FE-RS-001 route-backed PR. |

---

## Handoff Index

| Packet | Parent should use it for |
|---|---|
| Base `AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md` | Route inventory, operator journeys, field-binding overview, no-order guardrail. |
| Follow-ups 2-6 | Earlier corrections and accumulated supporting facts. |
| Follow-up 7 | Missing BFF/card-projection handoff queue and stop-line wording. |
| Follow-up 8 | Query/parser/header/refetch and frontend smoke-test contract. |
| Follow-up 9 | Parent implementation cut and shared component ownership guidance. |
| Follow-up 10 | Parent absorption order, parent PR evidence contract, and reviewer decision table. |
| Follow-up 11 | Parent start gate, cross-task boundary, and sidecar convergence rule. |
| Follow-up 12 | Stop-loop disposition and parent intake checklist. |

If a future packet is proposed, it should name the changed fact before the work
starts. Valid triggers are: a new BFF/runtime route or response-shape change, a
parent implementation mismatch, a reviewer correction, or an AG-FE-RS-001 /
AG-FE-SW-002 ownership collision.

---

## Reviewer Handoff

Claude should review this packet as support-only stop-loop material.

| Review question | Approve if | Reopen if |
|---|---|---|
| Scope | Only the generated task brief and this support artifact changed. | Runtime, schema, OpenAPI, canonical truth, execute-plans frontend, governance, or broker/order files changed. |
| Added value | Packet turns Follow-up 11 convergence into a parent intake and no-more-repeat handoff. | Packet merely repeats route tables or introduces new unsupported API facts. |
| Parent safety | Parent first PR remains route-backed and narrow; blocked surfaces stay blocked. | Packet encourages mocks, direct internal service calls, fabricated payloads, or order/capital/governance actions. |
| Next action | Parent owner can start AG-FE-RS-001 or open a concrete blocker; no new sidecar is needed under current facts. | Packet leaves ambiguity that would cause another support-only handoff loop. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 stop-loop disposition, parent intake checklist, handoff index, and no-order/live-strict stop lines are documented; no canonical truth, runtime, schema, OpenAPI, frontend, governance, broker/order, RuntimeBinding, or canary/live-promotion files changed. Parent AG-FE-RS-001 should start or raise a concrete blocker; no further support-only repeat packet is needed under current facts." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12 \
  "Support-only AG-FE-RS-001 BFF/frontend stop-loop handoff approved for parent owner intake."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12 \
  "Describe the factual correction, unsafe parent intake guidance, missing stop line, or scope leak that must be fixed before approval."
```

---

## Validation

Focused validation for this support-only packet:

```bash
git status --short
# expected before commit: generated task brief plus this support artifact

git diff --check -- \
  .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_12.md \
  support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12.md
# expected: no whitespace errors

AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12
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

No runtime, schema, OpenAPI, canonical truth, frontend implementation,
governance, broker/order, RuntimeBinding, or canary/live-promotion tests are
required for this support-only packet.

*Prepared by Codex for the `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-12`
support slice.*
