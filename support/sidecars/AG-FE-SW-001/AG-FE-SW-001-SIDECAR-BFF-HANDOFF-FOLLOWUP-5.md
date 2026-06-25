# AG-FE-SW-001 Sidecar Follow-up 5: Cross-Repo Handoff Status

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Pantheon dev base inspected | `08fdd92e98be142c6b8caf870272c61a1d76c89e` |
| Pantheon parent mirror PR inspected | `ajoe734/pantheon#2235`, merged at `2026-06-22T09:15:23Z` |
| execute-plans dev ref inspected | `origin/dev` at `40fef8769435fa479c87c2892417a76186913ecf` |
| execute-plans parent PR inspected | `ajoe734/execute-plans#69`, open at `476aa043c3b5196823a50106f956331262123b40` |
| Prior packets | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md`, `FOLLOWUP-2.md`, `FOLLOWUP-3.md`, `FOLLOWUP-4.md` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support-only follow-up for the active AG-FE-SW-001 parent lane. It
does not edit L1 truth, OpenAPI, JSON schemas, BFF runtime, route registry,
governance/runtime code, or execute-plans frontend source. The parent owner
decides whether to absorb this status packet into the main frontend task.

---

## Sources Rechecked

| Source | Follow-up finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets are support records and do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff_followup_5.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-file progress must be committed with explicit task scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001` | Parent status root still records AG-FE-SW-001 as `in_progress` and references Pantheon PR #2235 as awaiting merge. External PR inspection shows #2235 has since merged, so the status text is stale. |
| `gh pr view 2235 --repo ajoe734/pantheon` | Pantheon mirror PR #2235 is merged into `dev` at `08fdd92e98be142c6b8caf870272c61a1d76c89e`; Branch CI Gate and Orchestrator Sync checks are green. |
| `git show 08fdd92e --name-status` | Pantheon PR #2235 changed only frontend mirror/test files under `execute-plans/src/entries/agora-main.tsx`, `execute-plans/src/agora/TradingDeskLayout.test.tsx`, and `execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx`. |
| `gh pr list --repo ajoe734/execute-plans --head task/AG-FE-SW-001 --state all` | execute-plans PR #69 remains `OPEN`, base `dev`, head `476aa043c3b5196823a50106f956331262123b40`, merge state `UNSTABLE`. |
| `gh run view 27940249664 --repo ajoe734/execute-plans --log-failed` | execute-plans PR #69 `integration-gate` failed. Static/build/unit/contract checks pass, but Gate 5 F05 Sentinel, Gate 6 performance/SSE rerender, and Gate 7 release decision fail. |
| `/home/lupin/code/execute-plans origin/task/AG-FE-SW-001` | Parent source branch still contains the original skeleton commit `476aa04`; it has not absorbed the Pantheon mirror routing patch. |
| `origin/task/AG-FE-SW-001:src/App.tsx` | execute-plans source still has two sibling `/agora` route trees: one for `TradingDeskLayout`, then the legacy `AgoraLayout`. |
| `origin/task/AG-FE-SW-001:src/lib/bff-v1/agora/workshops.ts` | Client still hardcodes workshop paths, uses non-runtime request/response shapes, omits the required ETag/If-Match public boundary, uses `after` instead of `after_sequence`, and exposes deferred or missing routes. |
| `origin/task/AG-FE-SW-001:src/agora/pages/StrategyWorkshopPage.tsx` | Page still loads `/cards` and `/readiness`, renders card payload JSON, has no create-workshop journey, and sends messages without a current ETag. |
| `origin/task/AG-FE-SW-001:src/lib/bff-v1/paths.ts` | No workshop path builders exist. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime-live workshop routes remain list/create/get/messages/events/completeness/stream. Versions, legacy `research-runs`, consultations, and conclude remain explicit 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Plan-first research routes remain under `/research-plans` and `/research-runs`; they are not the legacy workshop-level `research-runs` stub. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 still documents cards, patch proposals, version comparisons, and readiness routes as contract surfaces, but inspected BFF runtime does not expose those handlers. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Cross-Repo State

Pantheon and execute-plans are currently split:

| Surface | Current state | Handoff meaning |
|---|---|---|
| Pantheon PR #2235 | Merged into Pantheon `dev` at `08fdd92e`. | This made the Pantheon mirror branch green and records a route/test patch, but it is not the execute-plans source PR merge. |
| execute-plans PR #69 | Still open and `UNSTABLE` at `476aa04`. | Parent AG-FE-SW-001 is not source-complete until this PR is corrected and merged into execute-plans `dev`. |
| Status root text | Still says Pantheon PR #2235 is open/awaiting merge. | Treat that as stale coordination text; external PR state shows #2235 is merged. |
| Runtime BFF | Stop lines from follow-up 4 still hold. | Do not treat card/readiness/version/conclude edges as runtime-live. |

The parent branch should not be closed as fully delivered on the strength of
Pantheon PR #2235 alone. The source frontend PR is still open and still carries
the contract gaps listed below.

---

## Delta From Follow-up 4

New facts since follow-up 4:

1. Pantheon PR #2235 merged successfully. It added mirror tests and an
   `execute-plans/src/entries/agora-main.tsx` route shell in the Pantheon repo.
2. execute-plans PR #69 is still open and failing its integration gate.
3. The execute-plans source branch still has the same high-risk BFF contract
   mismatches called out in follow-up 4.
4. The execute-plans source branch has not absorbed the Pantheon mirror route
   patch, so duplicate `/agora` route ambiguity remains in `src/App.tsx`.

No new runtime implementation was found for:

- `GET /bff/agora/workshops/{workshop_id}/cards`
- `/bff/agora/workshops/{workshop_id}/patch-proposals*`
- `/bff/agora/workshops/{workshop_id}/version-comparisons`
- `/bff/agora/workshops/{workshop_id}/readiness*`

---

## execute-plans PR #69 Findings

| Area | Current source branch state | Parent action |
|---|---|---|
| Source PR status | `ajoe734/execute-plans#69` is open, merge state `UNSTABLE`, head `476aa04`. | Fix and re-run the PR gate before claiming source delivery. |
| Integration gate | `integration-gate` failed in run `27940249664`. Static/build/unit/contract pass; F05 Sentinel, perf/SSE rerender, and release decision fail. | Separate global release-gate blockers from AG-FE-SW contract blockers, but do not merge while the required gate is red. |
| Route mount | `src/App.tsx` still has two sibling `/agora` route trees. | Keep only one unambiguous route tree or explicitly redirect legacy routes. |
| Pantheon mirror patch | Pantheon `execute-plans/src/entries/agora-main.tsx` has a route-shell patch and tests. | Port or reconcile this into the actual execute-plans source path if that is the intended delivery model. |
| Path builders | `src/lib/bff-v1/paths.ts` still lacks workshop builders. | Add canonical `/bff/agora/workshops*` builders for runtime-live routes only. |
| Client envelopes | `listWorkshops()` expects `{items,cursor}` and `getWorkshop()` expects a bare object. | Runtime returns `{data, meta}` envelopes and GET detail exposes `ETag` / `meta.etag`. |
| Create request | `createWorkshop()` sends `subject` and `participant_persona_ids`. | Runtime requires `initial_message`, optional `title`, optional `strategy_spec_ref`, optional `metadata`. |
| Message mutation | `postWorkshopMessage()` sends no `If-Match` and exposes no required ETag boundary. | Runtime requires current `If-Match` and idempotency for message append. |
| Event query | `listWorkshopEvents()` sends `after`. | Runtime expects `after_sequence`. |
| Page data source | `StrategyWorkshopPage` calls `listWorkshopCards()` and `getWorkshopReadiness()`. | Stop using these as live data until runtime handlers exist. |
| Conversation rendering | Page renders `WorkshopCard.payload` JSON. | Render runtime event history and completeness baseline, or show card projection unavailable. |
| Create journey | List view has an empty state but no create form/action. | Add the create-workshop journey or declare the parent slice blocked. |
| Deferred methods | Client exposes versions, singular `research-run`, singular `consultation`, conclude, cards, readiness, and reassess. | Remove or quarantine these behind later tasks. Versions/conclude are 501; singular research/consultation paths do not match runtime stubs. |
| Source tests | Tests mock `cards` and `readiness`, so they can pass while runtime stop lines are violated. | Add tests that enforce runtime-live routes and reject missing/deferred routes in AG-FE-SW-001. |

---

## Runtime-Live Surface To Keep

The parent source branch should narrow to these runtime-live routes first.

| Need | Runtime route | Required parent behavior |
|---|---|---|
| List workshops | `GET /bff/agora/workshops` | Parse `{data, meta.next_cursor}` and keep user/tenant/workshop-aware query keys. |
| Create workshop | `POST /bff/agora/workshops` | Body is `initial_message`, optional `title`, `strategy_spec_ref`, `metadata`; send `Idempotency-Key`; do not persist raw prompt. |
| Load detail | `GET /bff/agora/workshops/{workshop_id}` | Capture HTTP `ETag` and/or `meta.etag` for future mutations. |
| Append message | `POST /bff/agora/workshops/{workshop_id}/messages` | Body is `content` plus optional `attachment_refs`; send `If-Match` and `Idempotency-Key`; handle 428 and 409. |
| Event history | `GET /bff/agora/workshops/{workshop_id}/events` | Query is `after_sequence`; render private-content refs/redacted records only. |
| Completeness baseline | `GET /bff/agora/workshops/{workshop_id}/completeness` | Treat `data: null` as unassessed; do not invent grades. |
| SSE stream | `GET /bff/agora/workshops/{workshop_id}/stream` | Expect `workshop.connected` first; support `Last-Event-ID`, dedupe by SSE id, and refetch snapshot on gaps. |

Plan-first research routes are runtime-live, but should stay out of the parent
shell slice unless the parent explicitly narrows into research cards:

```text
GET|POST /bff/agora/workshops/{workshop_id}/research-plans
GET      /bff/agora/research-plans/{plan_id}
POST     /bff/agora/research-plans/{plan_id}/approve
POST     /bff/agora/research-plans/{plan_id}/cancel
GET|POST /bff/agora/research-plans/{plan_id}/runs
GET      /bff/agora/research-runs/{run_id}
POST     /bff/agora/research-runs/{run_id}/cancel
GET      /bff/agora/research-runs/{run_id}/artifacts
```

---

## Stop Lines To Preserve

```text
Do not close AG-FE-SW-001 from the Pantheon mirror PR alone. The execute-plans
source PR #69 remains open and failing integration-gate.
```

```text
Do not leave duplicate sibling /agora route trees in execute-plans App.tsx.
The source frontend route tree must make the TradingDeskShell reachable without
ambiguity and must define what happens to legacy Agora routes.
```

```text
Do not let AG-FE-SW-001 call GET /bff/agora/workshops/{workshop_id}/cards or
readiness routes as live data. The v1.3 contract exists, but the inspected BFF
runtime does not expose those handlers.
```

```text
Do not expose versions, conclude, legacy workshop-level research-runs, or
consultations as successful frontend actions. The inspected runtime returns
explicit 501 stubs for versions, research-runs, consultations, and conclude;
the active source branch also uses singular research-run/consultation paths
that do not match the registered stubs.
```

```text
Do not satisfy Strategy Workshop mutation acceptance without an ETag path.
Runtime requires If-Match for POST /messages and returns 428 without it.
```

```text
Do not treat manually declared WorkshopCard, WorkshopReadinessAssessment, or
WorkshopStreamEvent types in workshops.ts as generated v1.3 frontend truth.
Regenerate or explicitly mirror the v1.3 bundle before claiming typed support.
```

---

## Parent Correction Order

Recommended order for the parent owner:

1. Decide the real frontend source of truth: update execute-plans PR #69, not
   only the Pantheon mirror files.
2. Fix the execute-plans `/agora` route tree so there is a single canonical
   TradingDeskShell path and explicit legacy redirects.
3. Rework `src/lib/bff-v1/agora/workshops.ts` around path builders,
   `{data, meta}` envelopes, create request shape, `ETag` / `If-Match`,
   idempotency, and `after_sequence`.
4. Remove live `/cards` and `/readiness` calls from AG-FE-SW-001, or replace
   them with an explicit unavailable state.
5. Render runtime event history and completeness baseline instead of fabricated
   card projections.
6. Add the create-workshop journey, or hand off a blocker saying create is not
   implemented in this parent slice.
7. Remove/quarantine deferred versions, conclude, singular research-run,
   singular consultation, cards, readiness, and reassess methods.
8. Add tests that fail on direct page fetches, missing mutation headers,
   deferred live route calls, and duplicate `/agora` route trees.
9. Re-run execute-plans PR #69 gate and separate any remaining global
   release-gate failure from AG-FE-SW route/client correctness.

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | This sidecar authored only this support artifact. |
| Cross-repo accuracy | Packet distinguishes merged Pantheon PR #2235 from open execute-plans PR #69. |
| PR status accuracy | execute-plans PR #69 is open/UNSTABLE with failed integration-gate run `27940249664`. |
| Source branch accuracy | Findings refer to `origin/task/AG-FE-SW-001` at `476aa04`, not only the Pantheon mirror files. |
| Runtime accuracy | Route ledger matches latest `strategy_workshop/router.py` and `research/router.py`; no `/cards`, readiness, patch, version-compare route is claimed live. |
| Safety boundary | Packet preserves Agora no-order/no-capital/no-RuntimeBinding authority and does not suggest Management route reuse. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: it distinguishes merged Pantheon mirror PR #2235 from open execute-plans PR #69, records the current CI blocker, preserves BFF runtime stop lines, and lists the remaining parent source-branch corrections without canonical/runtime/schema/frontend source changes." \
  ./scripts/ai-status.sh approve AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Support-only AG-FE-SW-001 cross-repo BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5 \
  "Describe the factual correction, missing cross-repo status, or BFF route distinction needed before approval."
```

---

## Validation

Focused validation planned from this task worktree:

```bash
LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
git diff --check -- support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
gh pr view 2235 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,statusCheckRollup
gh pr list --repo ajoe734/execute-plans --head task/AG-FE-SW-001 --state all --json number,state,mergeStateStatus,headRefOid,url,statusCheckRollup
```

No canonical truth, runtime, schema, or execute-plans source files are changed
by this sidecar.

Results:

- ASCII scan: no output.
- Diff whitespace check: no output.
- Status show: active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`.
- Pantheon PR #2235 metadata: `MERGED`, merge commit
  `08fdd92e98be142c6b8caf870272c61a1d76c89e`, visible checks `SUCCESS`.
- execute-plans PR #69 metadata: `OPEN`, merge state `UNSTABLE`, head
  `476aa043c3b5196823a50106f956331262123b40`, `integration-gate` `FAILURE`.
