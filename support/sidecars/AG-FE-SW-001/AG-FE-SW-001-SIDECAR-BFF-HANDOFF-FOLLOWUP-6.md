# AG-FE-SW-001 Sidecar Follow-up 6: Cross-Repo Handoff Status

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-22` |
| Pantheon dev base inspected | `06ab2ff2475c7e64a142e4806143e6ffded070ff` |
| Pantheon parent branch inspected | `origin/task/AG-FE-SW-001` at `1bde7a60202f090686cf041856a33e8d80c532da` |
| Pantheon merged parent PR inspected | `ajoe734/pantheon#2235`, merged at `08fdd92e98be142c6b8caf870272c61a1d76c89e` |
| execute-plans dev ref inspected | `origin/dev` at `40fef8769435fa479c87c2892417a76186913ecf` |
| execute-plans parent PR inspected | `ajoe734/execute-plans#69`, open at `476aa043c3b5196823a50106f956331262123b40` |
| Prior packets | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md`, `FOLLOWUP-2.md`, `FOLLOWUP-3.md`, `FOLLOWUP-4.md`, `FOLLOWUP-5.md` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support-only follow-up for the active `AG-FE-SW-001` parent lane. It
does not edit L1 truth, OpenAPI, JSON schemas, BFF runtime, route registries,
governance/runtime code, or execute-plans frontend source. The parent owner
decides whether to absorb this packet into the main frontend task.

---

## Sources Rechecked

| Source | Follow-up finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets are support records and do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff_followup_6.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-file progress must be committed with explicit task scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001` | Parent is `review`; status text says commit `1bde7a60` was pushed to `task/AG-FE-SW-001` and the task is ready for re-review. |
| `gh pr view 2235 --repo ajoe734/pantheon` | Prior Pantheon mirror PR #2235 is merged into `dev` at `08fdd92e`; visible checks are green. |
| `gh pr list --repo ajoe734/pantheon --head task/AG-FE-SW-001 --state open` | No open Pantheon PR exists for the later `1bde7a60` branch tip. |
| `git ls-remote origin refs/heads/task/AG-FE-SW-001 refs/heads/dev` | Pantheon remote branch tip is `1bde7a60`; `dev` is `06ab2ff2`, so the branch is not merged into current `dev`. |
| `git show 1bde7a60` | Adds only `execute-plans/src/agora/TradingDeskLayout.tsx`, `StrategyWorkshopPage.tsx`, and `src/lib/bff-v1/agora/workshops.ts` in the Pantheon mirror tree. |
| `gh pr view 69 --repo ajoe734/execute-plans` | execute-plans source PR #69 remains `OPEN`, head `476aa04`, merge state `UNSTABLE`; `integration-gate` is still failed. |
| `git -C /home/lupin/code/execute-plans ls-remote origin refs/heads/task/AG-FE-SW-001 refs/heads/dev refs/pull/69/head` | The actual execute-plans branch and PR head still point at `476aa04`; they do not contain Pantheon mirror commit `1bde7a60`. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime-live workshop routes are list/create/get/messages/events/completeness/stream; versions, legacy research-runs, consultations, and conclude are 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Plan-first research routes remain separate from the legacy workshop-level `research-runs` stub. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 still lists cards, readiness, patch, and version surfaces, but no inspected runtime handlers expose cards/readiness for AG-FE-SW-001. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Cross-Repo State

The parent lane has advanced in Pantheon mirror state but not in the actual
execute-plans source PR.

| Surface | Current state | Handoff meaning |
|---|---|---|
| Pantheon PR #2235 | Merged into `dev` at `08fdd92e`. | Prior route-shell/test mirror patch is durable in Pantheon. |
| Pantheon `task/AG-FE-SW-001` | Remote branch tip is `1bde7a60`; no open PR exists for that tip. | The latest mirror source-file commit is not merged into current Pantheon `dev` and is not under PR review yet. |
| Pantheon `dev` | `06ab2ff2`, one sidecar merge ahead of the parent branch. | The parent branch needs a PR and likely a refresh before merge. |
| execute-plans PR #69 | Still open, head `476aa04`, `UNSTABLE`, `integration-gate` failed. | The actual frontend source repo has not absorbed `1bde7a60`; source delivery remains open. |
| Parent status text | Says `1bde7a60` was pushed and the task is ready for re-review. | Treat this as incomplete cross-repo state: the branch exists, but no new Pantheon PR is open and execute-plans PR #69 is unchanged. |

Stop line:

```text
Do not close AG-FE-SW-001 from Pantheon mirror branch status alone. The latest
Pantheon mirror source-file commit is not merged, and the execute-plans source
PR still points at the older failing head.
```

---

## Delta From Follow-up 5

New facts since follow-up 5:

1. Pantheon remote branch `task/AG-FE-SW-001` gained commit `1bde7a60`, which
   adds the three implementation files that were missing from the earlier
   mirror patch.
2. That branch has no open Pantheon PR after #2235, so the new commit is not
   merged into current `dev`.
3. execute-plans PR #69 still points at `476aa04`; it has not absorbed the new
   Pantheon mirror source files.
4. The newly added mirror implementation still leaves major AG-FE-SW-001 BFF
   contract gaps unresolved.

No new runtime implementation was found for:

- `GET /bff/agora/workshops/{workshop_id}/cards`
- `GET /bff/agora/workshops/{workshop_id}/readiness`
- `POST /bff/agora/workshops/{workshop_id}/readiness/reassess`
- `/bff/agora/workshops/{workshop_id}/patch-proposals*`
- `/bff/agora/workshops/{workshop_id}/version-comparisons`

---

## BFF Query Gap Matrix

| Need | Runtime route | 1bde7a60 mirror state | Parent action |
|---|---|---|---|
| List workshops | `GET /bff/agora/workshops` | `listWorkshops()` calls the correct route but returns a plain array and drops `meta.next_cursor`. | Parse the `{data, meta}` envelope and keep cursor metadata if the list UI pages. |
| Create workshop | `POST /bff/agora/workshops` | Missing. `StrategyWorkshopPage` empty state has no create action. | Add `createWorkshop()` with `initial_message`, optional `title`, `strategy_spec_ref`, `metadata`, and required `Idempotency-Key`. |
| Load detail | `GET /bff/agora/workshops/{id}` | `getWorkshop()` calls the route but returns only body data. | Return/carry the HTTP `ETag` or `meta.etag` so mutations can use the exact value. |
| Append message | `POST /bff/agora/workshops/{id}/messages` | Missing. Composer is an inert input with no submit handler. | Add `postWorkshopMessage()` with body `{content, attachment_refs?}`, exact `If-Match`, and `Idempotency-Key`; handle 428/409. |
| Read event history | `GET /bff/agora/workshops/{id}/events` | `listWorkshopCards()` calls `/events` and maps events as `WorkshopCard`. It has no `after_sequence` support. | Rename/reshape as event history, use `after_sequence`, and do not pretend events are card projections. |
| Completeness baseline | `GET /bff/agora/workshops/{id}/completeness` | `getWorkshopCompleteness()` calls the route. | Preserve this as the rail baseline; render `data: null` as unassessed. |
| Readiness | No inspected runtime handler for `/readiness`. | `getWorkshopReadiness()` calls `/readiness` as live data. | Remove or gate this call until the runtime route lands; tests should fail if the page calls it in AG-FE-SW-001. |
| Typed cards | v1.3 schema exists, but no inspected runtime `/cards` handler. | Manual `WorkshopCard` type is declared; page renders cards from `/events`. | Do not fabricate `WorkshopCard` from event rows. Card rendering belongs to later card/runtime work. |
| SSE stream | `GET /bff/agora/workshops/{id}/stream` | No stream client/subscription. | If included in this parent slice, use `Last-Event-ID`, dedupe by SSE `id`, and refetch snapshot on gaps. |
| Path builders | Frontend convention expects BFF route helpers. | `workshops.ts` hardcodes URL strings and does not update `src/lib/bff-v1/paths.ts`. | Either add canonical path builders or document why this BFF client is allowed to own paths directly. |
| Source delivery | execute-plans PR #69 | Unchanged at `476aa04`; does not contain `1bde7a60`. | Update the actual execute-plans branch/PR or stop claiming source delivery. |

---

## Runtime Stop Lines To Preserve

Keep these stop lines from follow-up 5:

```text
Do not call GET /bff/agora/workshops/{workshop_id}/readiness as live data in
AG-FE-SW-001. The v1.3 OpenAPI names the route, but the inspected runtime does
not expose a handler for it.
```

```text
Do not treat GET /bff/agora/workshops/{workshop_id}/events as typed
WorkshopCard projection. Runtime events are not the v1.3 card route.
```

```text
Do not satisfy Strategy Workshop mutation acceptance without an exact ETag path.
Runtime requires If-Match for POST /messages and returns 428 without it.
```

```text
Do not leave AG-FE-SW-001 without create-workshop and post-message journeys if
the parent acceptance still says the Strategy Workshop page can create/load a
workshop.
```

```text
Do not expose versions, conclude, legacy workshop-level research-runs, or
consultations as successful frontend actions. Those workshop routes are 501
stubs in the inspected runtime; plan-first research routes live in the research
router.
```

```text
Do not treat manually declared WorkshopCard, WorkshopReadiness, or stream types
as generated v1.3 frontend truth.
```

---

## Operator Journey Correction

The current `1bde7a60` mirror implementation supports only a narrow read path:

```text
open /agora/strategy-workshop
  -> list workshops
open /agora/strategy-workshop/{workshopId}
  -> get detail
  -> get completeness
  -> incorrectly try readiness
  -> read events through a function named listWorkshopCards
```

The minimum runtime-correct journey for AG-FE-SW-001 should be:

```text
open /agora/strategy-workshop
  -> list workshops through GET /bff/agora/workshops
  -> if empty, offer create workshop
create workshop
  -> POST /bff/agora/workshops with Idempotency-Key and initial_message
  -> navigate to returned workshop id
load workshop detail
  -> GET /bff/agora/workshops/{id}
  -> store exact ETag from response header or meta.etag
  -> GET /events?after_sequence=...
  -> GET /completeness
  -> optionally connect /stream
send message
  -> POST /messages with If-Match equal to the last returned ETag
  -> send Idempotency-Key
  -> on 428 fetch detail first
  -> on 409 refetch detail/events and ask the operator to retry
```

The UI must keep raw message text transient in component state only. It must
not persist raw workshop prompts to local storage, analytics, snapshots, or
durable cache.

---

## Parent Correction Order

Recommended order for the parent owner:

1. Decide the real source delivery path. If the source of truth is
   execute-plans, push these fixes to execute-plans PR #69. If the Pantheon
   mirror branch is intentional, open a new Pantheon PR for `1bde7a60` and
   refresh it onto current `dev`.
2. Rework `src/lib/bff-v1/agora/workshops.ts` around runtime-live routes:
   list, create, get with ETag, post message with If-Match/idempotency, events
   with `after_sequence`, completeness, and optional stream.
3. Remove the live `/readiness` call from AG-FE-SW-001 until the runtime handler
   exists.
4. Rename or replace `listWorkshopCards()` so the page does not treat event rows
   as card projections.
5. Implement the create-workshop empty-state flow.
6. Wire the composer submit flow to `POST /messages` with exact ETag handling.
7. Add tests that fail on missing create, missing If-Match, missing
   Idempotency-Key, `/readiness` calls, fabricated cards, and direct page fetch.
8. Re-run the actual source PR checks and separate global release-gate failures
   from AG-FE-SW BFF contract correctness.

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | This sidecar authored only this support artifact. |
| Cross-repo accuracy | Packet distinguishes Pantheon merged PR #2235, Pantheon unmerged branch `1bde7a60`, and execute-plans open PR #69 at `476aa04`. |
| Runtime accuracy | Route ledger matches latest `strategy_workshop/router.py`: list/create/get/messages/events/completeness/stream live; versions/research-runs/consultations/conclude 501; no readiness/cards handler claimed live. |
| Parent correction accuracy | Packet records that the new mirror source files still lack create, post message, ETag/If-Match, idempotency, stream, and correct event/card separation. |
| Safety boundary | Packet preserves Agora no-order/no-capital/no-RuntimeBinding authority and does not suggest Management route reuse. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: it distinguishes merged Pantheon PR #2235, unmerged Pantheon branch 1bde7a60, and unchanged execute-plans PR #69; preserves runtime stop lines for readiness/cards/ETag writes; and lists the remaining parent source corrections without canonical/runtime/schema/frontend source changes." \
  ./scripts/ai-status.sh approve AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Support-only AG-FE-SW-001 cross-repo BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6 \
  "Describe the factual correction, missing cross-repo status, or BFF route distinction needed before approval."
```

---

## Validation

Focused validation planned from this task worktree:

```bash
LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
git diff --no-index --check /dev/null support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001
gh pr view 2235 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,url,statusCheckRollup
gh pr list --repo ajoe734/pantheon --head task/AG-FE-SW-001 --state open --json number,state,headRefOid,url
gh pr view 69 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefOid,url,statusCheckRollup
git -C /home/lupin/code/execute-plans ls-remote origin refs/heads/task/AG-FE-SW-001 refs/heads/dev refs/pull/69/head
```

Results:

- ASCII scan: no output.
- Diff whitespace check: no output.
- Sidecar status show: active `in_progress`, owner `Codex`, reviewer `Claude`.
- Parent status show: active `review`, status text references commit `1bde7a60`.
- Pantheon PR #2235 metadata: `MERGED`, merge commit `08fdd92e98be142c6b8caf870272c61a1d76c89e`, visible checks `SUCCESS`.
- Pantheon open PR list for `task/AG-FE-SW-001`: empty.
- execute-plans PR #69 metadata: `OPEN`, merge state `UNSTABLE`, head `476aa043c3b5196823a50106f956331262123b40`, `integration-gate` `FAILURE`.
- execute-plans remote refs: `dev` is `40fef8769435fa479c87c2892417a76186913ecf`; `task/AG-FE-SW-001` and PR #69 are both `476aa043c3b5196823a50106f956331262123b40`.

No canonical truth, runtime, schema, or execute-plans source files are changed
by this sidecar.
