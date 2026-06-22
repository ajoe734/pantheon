# AG-FE-RS-001 BFF and Frontend Handoff Follow-up 15

| Field | Value |
|---|---|
| Task ID | `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-RS-001` - Research plan/run/consult/backtest cards |
| Owner / reviewer | `Claude2` / `Claude` |
| Date | 2026-06-22 |
| Pantheon dev base inspected | `1a7d0289132753286017cedd10cab946b8cbad47` |
| Prior AG-FE-RS packet | Follow-up 14 archived `done` at `2026-06-22T10:14:31Z` (PR #2249 merged) |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff after task PR |

This is a support artifact only. It does not edit L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, route registries, governance/runtime code,
broker/order paths, RuntimeBinding, canary/live-promotion behavior, or
execute-plans frontend source.

Follow-up 15 records the narrow current-dev delta after Follow-up 14. Two
material changes have occurred: (1) AG-FE-SW-002 is now `done`, delivering
stream-card versions of `ResearchPlanCard.tsx` and `ConsultResultCard.tsx` to
Pantheon dev; (2) AG-FE-RS-001 parent is now `in_progress`, having started
implementation of `ResearchRunCard`, `BacktestResultCard`, and `research.ts`.
This packet documents the cross-task boundary between SW-002 stream cards and
the AG-FE-RS-001 route-backed artifacts to prevent scope confusion.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support artifacts do not override architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_15.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` | Active task is `in_progress`, owner `Claude2`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` | Follow-up 14 is archived `done`; records AG-FE-SW mirror delta; no new AG-FE-RS route facts; stop-loop disposition intact. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001` | Parent is `in_progress` (was `todo`); owner `Claude`; next: "Started implementation: ResearchRunCard, BacktestResultCard, research.ts BFF client"; last update `2026-06-22T11:16:17Z`. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-SW-001` | Archived `done` at `2026-06-22T10:13:10Z`; was `review_approved` in Follow-up 14. |
| `AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-SW-002` | Archived `done` at `2026-06-22T11:09:29Z`; delivered `StrategyCompletenessRail.tsx`, `ResearchPlanCard.tsx`, `ConsultResultCard.tsx` to Pantheon dev. |
| `gh pr list --repo ajoe734/pantheon --state merged --limit 10` | PRs #2250 (10:29), #2251 (10:37), #2252 (11:00) delivered AG-FE-SW-002 acceptance + closeout to Pantheon dev. |
| `gh pr list --repo ajoe734/execute-plans --state all --limit 10` | execute-plans PR #69 (AG-FE-SW-001) still OPEN, `UNSTABLE`, no merge at `476aa043`. PR #66 (AG-FE-ID-001) merged at `40fef876` on `2026-06-22T08:10:45Z`; execute-plans dev head is `40fef876`. |
| `git diff --name-status 93d8aa33..origin/dev -- <AG-FE-RS pathset>` | `A execute-plans/src/agora/components/ConsultResultCard.tsx` and `A execute-plans/src/agora/components/ResearchPlanCard.tsx` added since FU-14 merge; no `research.ts`, no `ResearchRunCard.tsx`, no `BacktestResultCard.tsx`. |
| `ls execute-plans/src/lib/bff-v1/agora/` | Contains `contract-snapshot.json`, `dashboard.ts`, `types.ts`, `workshops.ts`; no `research.ts`. |
| `ls execute-plans/src/agora/components/` | Contains `ConsultResultCard.tsx`, `ResearchPlanCard.tsx`, `StrategyCompletenessRail.tsx`, `WorkshopCardRenderer.tsx`, `workshop-card-types.ts` (and test files); no `ResearchRunCard.tsx`, no `BacktestResultCard.tsx`. |
| `head -40 execute-plans/src/agora/components/ResearchPlanCard.tsx` | Imports `WorkshopCard` from `@/lib/bff-v1/agora/workshops`; renders `PayloadResearchPlanProposal` from workshop stream card. This is the conversation-stream version, NOT the route-backed BFF client version. |
| `head -40 execute-plans/src/agora/components/ConsultResultCard.tsx` | Imports `WorkshopCard` and `PayloadConsultResult`; renders consultation result from stream card payload. This is NOT driven by Agora BFF consultation projection route. |
| `execute-plans/src/agora/components/workshop-card-types.ts` | Defines `PayloadResearchPlanProposal`, `PayloadResearchProgress`, `PayloadResearchResult`, `PayloadConsultResult` aligned with `workshop_card.schema.json`; these are workshop-stream card payload types only. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## What This Follow-up Adds

| Added item | Why it matters |
|---|---|
| AG-FE-SW-002 completion record | Confirms stream-card `ResearchPlanCard` and `ConsultResultCard` now exist on Pantheon dev; prevents parent from re-implementing them as new files. |
| Stream-vs-route-backed boundary | Distinguishes the SW-002 conversation-stream card implementations from the AG-FE-RS-001 route-backed `research.ts` + `ResearchRunCard` + `BacktestResultCard` scope. |
| Parent in_progress status record | Confirms parent has started on its 3 canonical artifacts; this sidecar is now a live-implementation cross-check, not a pre-start gate. |
| execute-plans source gap update | Confirms `research.ts`, `ResearchRunCard.tsx`, and `BacktestResultCard.tsx` are not yet in execute-plans source. |
| ConsultResultCard disposition revision | The SW-002 stream card version exists and is driven by workshop conversation stream data, resolving the stream-card path. The Agora BFF route path is still `501`; route-backed consultation remains out of AG-FE-RS-001 scope. |
| Cross-task type reuse note | `workshop-card-types.ts` `PayloadResearchProgress` and `PayloadResearchResult` types may be reused in the route-backed cards for the SSE-driven display subset; full `ResearchRunProjection` schema is authoritative for route-backed data. |

---

## Delta Since Follow-up 14

| Surface | Follow-up 14 state | Current state (2026-06-22) |
|---|---|---|
| AG-FE-SW-001 status | `review_approved`; execute-plans PR #69 OPEN/UNSTABLE | Archived `done` on Pantheon at `2026-06-22T10:13:10Z`; execute-plans PR #69 still OPEN/UNSTABLE |
| AG-FE-SW-002 status | Not yet done | Archived `done` at `2026-06-22T11:09:29Z`; PRs #2250–2252 merged to Pantheon dev |
| `ResearchPlanCard.tsx` in execute-plans | Did not exist | Added via AG-FE-SW-002 (Pantheon PR #2250); stream-card version bound to `WorkshopCard + PayloadResearchPlanProposal` |
| `ConsultResultCard.tsx` in execute-plans | Did not exist | Added via AG-FE-SW-002 (Pantheon PR #2250); stream-card version bound to `WorkshopCard + PayloadConsultResult` |
| `research.ts` in execute-plans | Did not exist | Still does not exist; parent implementation started but not yet pushed |
| `ResearchRunCard.tsx` in execute-plans | Did not exist | Still does not exist |
| `BacktestResultCard.tsx` in execute-plans | Did not exist | Still does not exist |
| AG-FE-RS-001 parent status | `todo` | `in_progress`; started `ResearchRunCard`, `BacktestResultCard`, `research.ts` |
| Pantheon dev head | `55a3b65087aa4ba1b8adc3e604cbb28448ff6368` | `1a7d0289132753286017cedd10cab946b8cbad47` |
| execute-plans dev head | `40fef8769435fa479c87c2892417a76186913ecf` | `40fef8769435fa479c87c2892417a76186913ecf` (unchanged; execute-plans PR #69 still open) |

---

## Cross-Task Boundary: Stream Cards vs Route-Backed Cards

This is the critical boundary to preserve as the parent implements its slice.

### AG-FE-SW-002 Delivered (Stream-Card Path)

| Component | Source of data | Card type | Status |
|---|---|---|---|
| `ResearchPlanCard.tsx` | `WorkshopCard.payload` typed as `PayloadResearchPlanProposal`; data from workshop conversation stream card | `research_plan_proposal` (E7) | Done; do not re-implement as a new file |
| `ConsultResultCard.tsx` | `WorkshopCard.payload` typed as `PayloadConsultResult`; data from workshop conversation stream card | `consult_result` (E10) | Done as stream-card path; Agora BFF route path still `501` |

### AG-FE-RS-001 Remaining (Route-Backed Path)

| Component | Source of data | Card type | Status |
|---|---|---|---|
| `research.ts` BFF client | `GET/POST /bff/agora/research-plans/*`, `GET/POST /bff/agora/research-runs/*` | — (data access layer) | Not yet in execute-plans |
| `ResearchRunCard.tsx` | `GET /bff/agora/research-runs/{run_id}` returning `ResearchRunProjection` | `research_progress` (E8) | Not yet in execute-plans |
| `BacktestResultCard.tsx` | `GET /bff/agora/research-runs/{run_id}` returning `ResearchRunProjection` with `execution_status=succeeded` | `research_result` (E9) | Not yet in execute-plans |

### Boundary Rules For The Parent Owner

1. **Do not re-create** `ResearchPlanCard.tsx` or `ConsultResultCard.tsx` — these exist from SW-002 and render the conversation-stream card type.
2. **Route-backed plan detail** (e.g., the plan-detail panel opened by `getResearchPlan(planId)`) may re-use or compose with the SW-002 stream card component, but the BFF client call must go through `research.ts`, not through `workshops.ts`.
3. **ConsultResultCard stream path** is now satisfied by SW-002; the AG-FE-RS-001 stop line about the Agora BFF consultation projection route (`501`) still applies to any route-backed consultation path. Do not attempt to wire consultation through the Agora BFF within this task.
4. **Type reuse**: `PayloadResearchProgress` and `PayloadResearchResult` in `workshop-card-types.ts` cover the workshop-stream view of run state. For the route-backed `ResearchRunCard` and `BacktestResultCard`, the authoritative schema is `services/control-plane/specs/agora/v4/research_run_projection.schema.json` — use that as the data contract for `research.ts` return types.
5. **execute-plans PR #69** (SW-001 TradingDeskShell) is still OPEN and UNSTABLE. The parent's `research.ts` and run cards will be implemented before the Shell merges to execute-plans dev. The parent PR should target execute-plans `dev`; the CI gate failure on PR #69 does not block the parent's own PR.

---

## Current AG-FE-RS Handoff State

| Topic | Current handoff |
|---|---|
| Parent scope | `ResearchRunCard.tsx`, `BacktestResultCard.tsx`, `research.ts` — the three artifacts in `AG-FE-RS-001`. Do not add `ResearchPlanCard.tsx` or `ConsultResultCard.tsx` (delivered by SW-002). |
| BFF source | Use configured `/bff/agora/*` routes only through `research.ts`; do not page-fetch directly and do not call internal research orchestrator or consultation services. |
| Parser/header rules | Preserve distinct parsers for list/detail/command/run/artifact envelopes; generate fresh `Idempotency-Key` and `If-Match` where required (see base packet for the full header table). |
| Operator journey | Load plan detail, approve/cancel plan, dispatch plan-scoped run, load run detail, list artifacts, render backtest/result evidence, and surface degraded/no-order state. |
| Degraded state | Render `backend.mode`, `warnings[]`, `blocking_reasons[]`, and `no_order_route_proof`; do not hide fixture/stub/blocked states. |
| Authority boundary | No order placement, broker/capital binding, RuntimeBinding write, registry/governance mutation, canary/live promotion, or Management route reuse. |
| Cross-task boundary | `ResearchPlanCard` stream version is from SW-002. `ConsultResultCard` stream version is from SW-002. Full conversation/completeness rail coordination is adjacent SW-002 work. |
| execute-plans source gap | `research.ts`, `ResearchRunCard.tsx`, `BacktestResultCard.tsx` are not yet in execute-plans — parent is `in_progress` on these. |

---

## Stop Lines Still In Force

| Stop line | Required handling |
|---|---|
| Agora BFF consultation route | `POST /bff/agora/workshops/{id}/consultations` is still `501 Not Implemented`; no GET route exists. Do not wire consultation through the Agora BFF in AG-FE-RS-001. The SW-002 `ConsultResultCard` is stream-card-only. |
| `VersionCompareCard` | Keep outside AG-FE-RS-001 scope until design gap A (VersionPatchProposal/VersionCompare semantics) is resolved. `version_compare.schema.json` exists but has no implementing BFF route. |
| No-order guardrails | `no_order_route_proof` is an invariant. `backend.mode=fixture/stub` must show a visible marker. BacktestResultCard must not link to RuntimeBinding, candidate promotion, or live trading paths. |
| execute-plans PR #69 | Do not treat the parent's research PR as blocked by execute-plans PR #69. Open a separate PR to execute-plans dev targeting research artifacts. |
| Live strict | `research.ts` must not include a local fixture fallback, synthetic run data, or direct service fanout. Live strict only. |

---

## Packet Family Index (Updated)

| Packet | Use it for |
|---|---|
| Base `AG-FE-RS-001-SIDECAR-BFF-HANDOFF.md` | Route inventory, operator journeys, card binding overview, no-order guardrail. |
| Follow-ups 7-10 | Missing-surface blockers, parser/header/refetch rules, parent absorption order, PR evidence contract. |
| Follow-up 11 | Parent start gate, cross-task boundary with `AG-FE-SW-002`, sidecar convergence rule. |
| Follow-up 12 | Stop-loop disposition, parent intake checklist, handoff index. |
| Follow-up 13 | Duplicate-dispatch/no-new-facts disposition. |
| Follow-up 14 | Current-dev delta check after adjacent AG-FE-SW mirror/support merges. |
| Follow-up 15 (this) | AG-FE-SW-002 done delta; stream-card vs route-backed boundary; parent now in_progress. |

Valid triggers for a future support packet remain limited to changed facts:

| Trigger | Example |
|---|---|
| New backend/runtime evidence | A BFF route lands, is removed, or changes response shape. |
| Parent implementation evidence | A parent PR is opened and exposes a concrete mismatch with handoff guidance. |
| Review finding | Claude or Codex identifies an inaccurate stop line or unsafe frontend suggestion. |
| Ownership collision | AG-FE-RS-001 and AG-FE-SW-002 need the same file in incompatible ways. |

---

## Reviewer Handoff

Claude should review this packet as a support-only current-dev delta recording the
AG-FE-SW-002 completion, stream-vs-route-backed boundary, and parent in_progress status.

| Review question | Approve if | Reopen if |
|---|---|---|
| Scope | Only the generated task brief and this support artifact changed. | Runtime, schema, OpenAPI, canonical truth, execute-plans frontend source, governance, broker/order, RuntimeBinding, or canary/live-promotion files changed. |
| SW-002 delta accuracy | Packet accurately records that SW-002 added stream-card `ResearchPlanCard` and `ConsultResultCard` to Pantheon dev; no route-backed research files added. | Packet misses an AG-FE-RS route/schema/source delta or overstates SW-002 completion scope. |
| Boundary clarity | Stream-card vs route-backed boundary is clear; parent scope remains `research.ts`, `ResearchRunCard`, `BacktestResultCard` only. | Packet encourages the parent to re-implement stream cards or conflates stream-card path with route-backed path. |
| ConsultResultCard disposition | SW-002 stream path is noted as done; Agora BFF `501` stop line is preserved for route-backed path. | Packet claims the Agora BFF consultation route is available or lifts the consultation stop line. |
| Parent safety | Parent AG-FE-RS-001 is `in_progress` and implementation guidance is clear; no new support-only repeat is needed unless a concrete mismatch is found. | Packet encourages another support-only loop before parent produces a PR. |
| Stop lines | Existing no-order/live-strict and missing-surface blockers remain intact. | Packet weakens blocker handling, permits mocks/direct internal calls, or permits order/capital/governance actions. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-RS-001 follow-up 15 records AG-FE-SW-002 done delta (stream-card ResearchPlanCard+ConsultResultCard added to Pantheon dev), clarifies stream-vs-route-backed boundary, notes parent in_progress on ResearchRunCard+BacktestResultCard+research.ts, preserves Agora BFF consultation 501 stop line and no-order/live-strict guardrails, does not change canonical truth or runtime files." \
  ./scripts/ai-status.sh approve AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15 \
  "Support-only AG-FE-RS-001 follow-up 15 approved for parent owner intake."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15 \
  "Describe the factual correction, unsafe parent guidance, missed AG-FE-RS delta, or scope leak that must be fixed before approval."
```

---

## Validation

Focused validation for this support-only packet:

```bash
git status --short
# expected before commit: generated task brief plus this support artifact

LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15.md
# expected: no output

git diff --check -- \
  .orchestrator/task-briefs/ag_fe_rs_001_sidecar_bff_handoff_followup_15.md \
  support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15.md
# expected after staging or commit: no whitespace errors

AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-RS-001
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-SW-001
AI_NAME=Claude2 python3 scripts/ai_status.py show AG-FE-SW-002

gh pr view 2250 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefOid,url
gh pr view 2251 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefOid,url
gh pr view 2252 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefOid,url
gh pr view 69 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,mergedAt,headRefOid,url

git diff --name-status 93d8aa33abaa6f89ce9d1bd3338997fb8201ca75..origin/dev -- \
  execute-plans/src/agora/components/ResearchRunCard.tsx \
  execute-plans/src/agora/components/BacktestResultCard.tsx \
  execute-plans/src/agora/components/ResearchPlanCard.tsx \
  execute-plans/src/agora/components/ConsultResultCard.tsx \
  execute-plans/src/lib/bff-v1/agora/research.ts \
  services/control-plane/bff/agora/research/router.py \
  services/control-plane/specs/agora \
  openapi/agora_v1.openapi.yaml \
  support/sidecars/AG-FE-RS-001

ls execute-plans/src/agora/components/
ls execute-plans/src/lib/bff-v1/agora/
head -5 execute-plans/src/agora/components/ResearchPlanCard.tsx
head -5 execute-plans/src/agora/components/ConsultResultCard.tsx
# expected: stream-card imports (WorkshopCard, workshops.ts)
```

No runtime, schema, OpenAPI, canonical truth, frontend implementation,
governance, broker/order, RuntimeBinding, or canary/live-promotion tests are
required for this support-only packet.

Results:

- `git status --short`: only the generated task brief and this support artifact
  are untracked before commit.
- ASCII scan for this packet: no output.
- Trailing-whitespace scan across the task brief and packet: no output.
- AG-FE-RS pathset delta from FU-14 merge (`93d8aa33`) to `origin/dev`: two additions —
  `A execute-plans/src/agora/components/ConsultResultCard.tsx` and
  `A execute-plans/src/agora/components/ResearchPlanCard.tsx`; no `research.ts`,
  `ResearchRunCard.tsx`, `BacktestResultCard.tsx`, BFF router, schema, or OpenAPI delta.
- `ls execute-plans/src/agora/components/`: `ConsultResultCard.tsx`,
  `ResearchPlanCard.tsx`, `StrategyCompletenessRail.tsx`, `WorkshopCardRenderer.tsx`,
  `workshop-card-types.ts` present; no `ResearchRunCard.tsx` or `BacktestResultCard.tsx`.
- `ls execute-plans/src/lib/bff-v1/agora/`: `contract-snapshot.json`, `dashboard.ts`,
  `types.ts`, `workshops.ts`; no `research.ts`.
- `head -5 ResearchPlanCard.tsx`: imports `WorkshopCard` from `workshops.ts` — stream-card path confirmed.
- `head -5 ConsultResultCard.tsx`: imports `WorkshopCard` from `workshops.ts` — stream-card path confirmed.
- Status checks: this task is active `in_progress`; FU-14 is archived `done`; parent
  `AG-FE-RS-001` is active `in_progress` (started `2026-06-22T11:16:17Z`); adjacent
  `AG-FE-SW-001` is archived `done`; adjacent `AG-FE-SW-002` is archived `done`.
- PR checks: Pantheon PRs #2250, #2251, #2252 are `MERGED` (AG-FE-SW-002 acceptance +
  closeout); execute-plans PR #69 remains `OPEN` / `UNSTABLE` at `476aa043`.

*Prepared by Claude2 for the `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-15`
support slice.*
