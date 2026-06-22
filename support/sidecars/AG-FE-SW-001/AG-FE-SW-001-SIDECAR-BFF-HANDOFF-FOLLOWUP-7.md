# AG-FE-SW-001 Sidecar Follow-up 7: Review-Approved Cross-Repo Gap Ledger

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | `2026-06-22` |
| Pantheon dev base inspected | `32133839ef0713929f76f2a9cb6e139addb0d9a3` |
| Pantheon parent PR inspected | `ajoe734/pantheon#2245`, open at `88043260a439a108f2216ee73fc6ffcdee4610d5`, merge state `BEHIND` |
| Prior Pantheon parent PR inspected | `ajoe734/pantheon#2235`, merged at `08fdd92e98be142c6b8caf870272c61a1d76c89e` |
| execute-plans dev ref inspected | `origin/dev` at `40fef8769435fa479c87c2892417a76186913ecf` |
| execute-plans parent PR inspected | `ajoe734/execute-plans#69`, open at `476aa043c3b5196823a50106f956331262123b40`, merge state `UNSTABLE` |
| Prior packets | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md`, `FOLLOWUP-2.md`, `FOLLOWUP-3.md`, `FOLLOWUP-4.md`, `FOLLOWUP-5.md`, `FOLLOWUP-6.md` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support-only follow-up for the `AG-FE-SW-001` parent lane. It does
not edit L1 truth, OpenAPI, JSON schemas, BFF runtime, route registries,
governance/runtime code, or execute-plans frontend source. The parent owner
decides whether to absorb this packet into the main frontend task.

---

## Sources Rechecked

| Source | Follow-up finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets are support records and do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff_followup_7.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-file progress must be committed with explicit task scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001` | Parent now records `review_approved`; next text says the supervisor resumed it for finalize after successful dispatch. |
| `gh pr list --repo ajoe734/pantheon --head task/AG-FE-SW-001 --state all` | Pantheon PR #2245 is open, base `dev`, head `88043260`, merge state `BEHIND`; PR #2235 is the prior merged mirror PR. |
| `gh pr view 2245 --repo ajoe734/pantheon` | PR #2245 includes commits `1bde7a60` and `88043260`; visible Branch CI Gate and Orchestrator Sync checks are green, but merge state remains `BEHIND`. |
| `git show FETCH_HEAD:execute-plans/src/lib/bff-v1/agora/workshops.ts` | Pantheon mirror `workshops.ts` now calls `/cards` and `/readiness` as live routes, manually declares v1.3 types, and uses direct `fetch()` inside the BFF client module. It still has no create-workshop or post-message methods. |
| `git show FETCH_HEAD:execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` | Pantheon mirror page lists workshops, loads detail/completeness/readiness/cards, but has no create action and an inert composer with no submit path. |
| `git show HEAD:execute-plans/src/entries/agora-main.tsx` | Pantheon dev mirror entry wires `TradingDeskLayout` through a standalone Agora entry and redirects legacy paths there; this is mirror code, not execute-plans source PR #69. |
| `git show HEAD:execute-plans/src/agora/pages/strategy-workshop/StrategyWorkshopPage.test.tsx` | Mirror tests mock `listWorkshopCards` and `getWorkshopReadiness`, so they can pass while runtime has no `/cards` or `/readiness` handlers. |
| `gh pr view 69 --repo ajoe734/execute-plans` | execute-plans source PR #69 remains open, head `476aa04`, merge state `UNSTABLE`, with `integration-gate` failed in run `27940249664`. |
| `git -C /home/lupin/code/execute-plans ls-remote origin refs/heads/task/AG-FE-SW-001 refs/pull/69/head` | execute-plans branch and PR #69 still point at `476aa04`; they have not absorbed Pantheon mirror commits `1bde7a60` or `88043260`. |
| `git -C /home/lupin/code/execute-plans show origin/task/AG-FE-SW-001:src/App.tsx` | Source PR #69 still has duplicate sibling `/agora` route trees: new `TradingDeskLayout`, then legacy `AgoraLayout`. |
| `git -C /home/lupin/code/execute-plans show origin/task/AG-FE-SW-001:src/lib/bff-v1/agora/workshops.ts` | Source PR #69 still carries the older client shape: wrong create body, no ETag/If-Match boundary, `after` instead of `after_sequence`, and deferred/stubbed routes exposed. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime-live workshop routes are list/create/get/messages/events/completeness/stream; versions, legacy research-runs, consultations, and conclude are 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Plan-first research routes remain separate from the legacy workshop-level `research-runs` stub. |
| `rg` over `services/control-plane/bff` and tests | No inspected runtime handler exists for `/bff/agora/workshops/{id}/cards`, `/readiness`, `/readiness/reassess`, patch proposals, or version comparisons. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Current Cross-Repo State

The parent lane is marked `review_approved` in status, but the durable delivery
state is still split.

| Surface | Current state | Handoff meaning |
|---|---|---|
| Parent status | `AG-FE-SW-001` is `review_approved`. | Do not run owner `done` until the required repo flow is actually merged and cross-repo source truth is clear. |
| Pantheon PR #2245 | Open, head `88043260`, merge state `BEHIND`. | The latest Pantheon mirror implementation is under PR review but is not mergeable until refreshed against current `dev`. |
| Pantheon PR #2235 | Merged into `dev` at `08fdd92e`. | Prior mirror tests and `agora-main.tsx` entry are durable, but this did not merge the latest parent source files. |
| Pantheon `dev` | `32133839`, after follow-up sidecar merges. | PR #2245 is behind current dev and shows stale-base sidecar deletions if compared directly from current dev. |
| execute-plans PR #69 | Open, head `476aa04`, `UNSTABLE`, integration-gate failed. | Actual frontend source repo has not absorbed the Pantheon mirror fixes and still carries the older route/client gaps. |
| BFF runtime | No inspected `/cards` or `/readiness` handlers. | Contract v1.3 surfaces exist, but AG-FE-SW-001 cannot treat these as runtime-live without a BFF route owner landing them. |

Stop line:

```text
Do not close AG-FE-SW-001 from the `review_approved` status alone. Pantheon PR
#2245 is still open and behind dev, execute-plans PR #69 is still open and
failing, and the latest mirror client calls runtime routes that are not
registered in the inspected BFF.
```

---

## Delta From Follow-up 6

New facts since follow-up 6:

1. Pantheon PR #2245 now exists for `task/AG-FE-SW-001`, so the prior "no open
   Pantheon PR" finding is superseded.
2. The parent branch advanced from `1bde7a60` to `88043260`.
3. Parent status moved from `review` to `review_approved`.
4. PR #2245 is still open and `BEHIND`, so it is not merged into Pantheon `dev`.
5. The `88043260` mirror commit changed the handoff risk: it aligns some manual
   type names with v1.3 contract files, but it now calls `/cards` and
   `/readiness` as live data even though inspected runtime handlers are absent.
6. execute-plans source PR #69 is unchanged at `476aa04` and still has the
   old branch findings from follow-ups 4-6.

No new runtime implementation was found for:

- `GET /bff/agora/workshops/{workshop_id}/cards`
- `GET /bff/agora/workshops/{workshop_id}/readiness`
- `POST /bff/agora/workshops/{workshop_id}/readiness/reassess`
- `/bff/agora/workshops/{workshop_id}/patch-proposals*`
- `/bff/agora/workshops/{workshop_id}/version-comparisons`

---

## Parent Branch Findings

### Pantheon mirror PR #2245

| Area | Observed state at `88043260` | Parent action |
|---|---|---|
| PR mergeability | PR #2245 is `BEHIND` current `dev`. | Refresh the branch before treating review approval as finalizable. |
| Source files | Adds `TradingDeskLayout.tsx`, `StrategyWorkshopPage.tsx`, and `workshops.ts` in the Pantheon mirror tree. | These files still need to compose with current dev and actual execute-plans source. |
| BFF client boundary | `workshops.ts` is under `src/lib/bff-v1/agora`, but it uses direct `fetch()` rather than the existing execute-plans `bffFetch` pattern. | Parent/reviewer should decide whether this mirror client is acceptable or must match execute-plans BFF client conventions. |
| Runtime route use | Client calls `/cards` and `/readiness` as live routes. | Blocking runtime mismatch unless the BFF route owner lands handlers first. |
| Create journey | No `createWorkshop()` method and no create UI. | Does not satisfy parent acceptance that the page can create a workshop. |
| Message journey | Composer is inert; no post-message method or submit path. | Does not satisfy the continue-conversation journey and avoids the required ETag path entirely. |
| ETag/If-Match | No detail ETag capture and no message mutation. | Parent acceptance cannot be closed for writes until `If-Match` and idempotency are implemented. |
| Event history | Page does not render runtime `/events`; it renders typed card projections. | Runtime-correct baseline should use `/events` or explicitly show card projection unavailable. |
| Tests | Mirror tests mock `/cards` and `/readiness` clients. | Tests should fail if AG-FE-SW-001 calls unavailable live routes. |

### execute-plans source PR #69

| Area | Observed state at `476aa04` | Parent action |
|---|---|---|
| Source PR | Still open and `UNSTABLE`; `integration-gate` failed. | Source delivery remains incomplete. |
| Route tree | Duplicate sibling `/agora` route trees remain in `src/App.tsx`. | Keep one canonical TradingDeskShell route tree or explicit redirects. |
| Client shape | Older `workshops.ts` has wrong create request, no ETag/If-Match, `after` instead of `after_sequence`, and deferred/stubbed routes. | Rework around runtime-live routes before source review. |
| Runtime stop lines | Source branch still exposes versions, singular research-run, singular consultation, conclude, cards, readiness, and reassess methods. | Remove or quarantine deferred and missing routes from AG-FE-SW-001. |
| Cross-repo drift | It has not absorbed Pantheon mirror commits `1bde7a60` or `88043260`. | Decide whether fixes belong in execute-plans PR #69, Pantheon mirror PR #2245, or both, then keep them synchronized. |

---

## Runtime-Live Surface To Keep

The parent implementation should stay on these runtime-live BFF routes first.

| Need | Runtime route | Required parent behavior |
|---|---|---|
| List workshops | `GET /bff/agora/workshops` | Parse the runtime envelope and preserve cursor metadata when paging. |
| Create workshop | `POST /bff/agora/workshops` | Body is `initial_message`, optional `title`, `strategy_spec_ref`, `metadata`; send `Idempotency-Key`; keep raw prompt transient. |
| Load detail | `GET /bff/agora/workshops/{workshop_id}` | Capture HTTP `ETag` and/or `meta.etag` for future mutations. |
| Append message | `POST /bff/agora/workshops/{workshop_id}/messages` | Body is `content` plus optional `attachment_refs`; send exact `If-Match` and `Idempotency-Key`; handle 428 and 409. |
| Event history | `GET /bff/agora/workshops/{workshop_id}/events` | Query is `after_sequence`; render private-content refs/redacted records only. |
| Completeness baseline | `GET /bff/agora/workshops/{workshop_id}/completeness` | Treat `data: null` as unassessed; do not invent grades. |
| SSE stream | `GET /bff/agora/workshops/{workshop_id}/stream` | Expect `workshop.connected` first; support `Last-Event-ID`, dedupe by SSE id, and refetch snapshot on gaps. |

Plan-first research routes remain runtime-live but outside this shell-first
slice unless the parent explicitly narrows into research cards:

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
Do not treat Pantheon PR #2245 review approval or open auto-merge as completed
delivery. The PR is open and behind current dev, and owner closeout should wait
until the required repo flow has merged.
```

```text
Do not treat execute-plans source delivery as complete. PR #69 remains open at
the older `476aa04` head and has not absorbed Pantheon mirror commits
`1bde7a60` or `88043260`.
```

```text
Do not call `/bff/agora/workshops/{workshop_id}/cards`, `/readiness`, or
`/readiness/reassess` as runtime-live AG-FE-SW-001 data. The v1.3 contract
names those surfaces, but the inspected BFF runtime does not expose handlers.
```

```text
Do not satisfy Strategy Workshop create/load/message acceptance without
create-workshop and post-message journeys. Runtime requires `Idempotency-Key`
for create and `If-Match` plus `Idempotency-Key` for message append.
```

```text
Do not let tests that mock unavailable routes stand in for runtime integration
proof. The parent tests should fail when the page/client calls missing runtime
routes or omits the ETag mutation boundary.
```

```text
Do not expose versions, conclude, legacy workshop-level research-runs, or
consultations as successful frontend actions. Those workshop routes remain 501
stubs in the inspected runtime; plan-first research routes live in the research
router.
```

---

## Parent Correction Order

Recommended order for the parent owner:

1. Refresh Pantheon PR #2245 onto current `dev`, or keep it open until the
   latest sidecar merges are incorporated.
2. Decide source of truth: execute-plans PR #69, Pantheon mirror PR #2245, or a
   deliberate two-repo sync. Do not let the two branches keep diverging.
3. Remove live `/cards` and `/readiness` calls from AG-FE-SW-001 until BFF
   runtime handlers exist, or mark the UI as unavailable instead of successful.
4. Implement the runtime-live `workshops.ts` baseline: list, create, get with
   ETag, post message with `If-Match` and idempotency, events with
   `after_sequence`, completeness, and optional stream.
5. Add the create-workshop empty-state flow and wire the composer submit flow.
6. Align tests with runtime stop lines: fail on unavailable route calls, missing
   create journey, missing `If-Match`, missing `Idempotency-Key`, duplicate
   `/agora` route trees, and direct page `fetch()`.
7. Re-run the actual source PR checks and separate global integration-gate
   blockers from AG-FE-SW route/client correctness.
8. Only after the selected delivery PR has merged should the parent owner run
   closeout from `review_approved` to `done`.

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | This sidecar authored only this support artifact. |
| Status accuracy | Packet records that parent status is `review_approved` but delivery is not merged. |
| Pantheon PR accuracy | Packet distinguishes merged PR #2235 from open/behind PR #2245 at `88043260`. |
| execute-plans accuracy | Packet records source PR #69 remains open at `476aa04` and did not absorb mirror commits. |
| Runtime accuracy | Route ledger matches `strategy_workshop/router.py`: list/create/get/messages/events/completeness/stream live; versions/research-runs/consultations/conclude 501; no cards/readiness handler claimed live. |
| Test gap accuracy | Packet notes that mirror tests mock `/cards` and `/readiness`, so they do not prove runtime availability. |
| Safety boundary | Packet preserves Agora no-order/no-capital/no-RuntimeBinding authority and does not suggest Management route reuse. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: it distinguishes parent review_approved status from unmerged delivery, records Pantheon PR #2245 open/behind, execute-plans PR #69 still open, runtime stop lines for cards/readiness/ETag writes, and the remaining parent correction order without canonical/runtime/schema/frontend source changes." \
  ./scripts/ai-status.sh approve AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Support-only AG-FE-SW-001 review-approved cross-repo gap ledger approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7 \
  "Describe the factual correction, missing cross-repo status, or BFF route distinction needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md
git diff --no-index --check /dev/null support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-SW-001
gh pr view 2245 --repo ajoe734/pantheon --json number,state,mergeStateStatus,headRefOid,url,statusCheckRollup
gh pr view 69 --repo ajoe734/execute-plans --json number,state,mergeStateStatus,headRefOid,url,statusCheckRollup
git -C /home/lupin/code/execute-plans ls-remote origin refs/heads/task/AG-FE-SW-001 refs/heads/dev refs/pull/69/head
rg -n "/bff/agora/workshops/.+(cards|readiness|patch-proposals|version-comparisons)|readiness|cards" services/control-plane/bff/agora services/control-plane/bff/tests services/control-plane/tests/agora
```

No canonical truth, runtime, schema, or execute-plans source files are changed
by this sidecar.

Results:

- ASCII scan: no output.
- New-file whitespace check: no output.
- Sidecar status show: active `in_progress`, owner `Codex`, reviewer `Claude`.
- Parent status show: active `review_approved`.
- Pantheon PR #2245 metadata: `OPEN`, head `88043260a439a108f2216ee73fc6ffcdee4610d5`, merge state `BEHIND`, visible checks `SUCCESS`.
- execute-plans PR #69 metadata: `OPEN`, head `476aa043c3b5196823a50106f956331262123b40`, merge state `UNSTABLE`, `integration-gate` `FAILURE`.
- execute-plans remote refs: `dev` is `40fef8769435fa479c87c2892417a76186913ecf`; `task/AG-FE-SW-001` and PR #69 are both `476aa043c3b5196823a50106f956331262123b40`.
- Targeted Agora runtime handler search for workshop cards/readiness/patch/version-comparison routes: no output under `services/control-plane/bff/agora`.
