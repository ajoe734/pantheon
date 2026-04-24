# APP-003-TRUTH-SYNC-004 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `APP-003-TRUTH-SYNC-004` - Rebaseline canonical truth against reopened front-default-branch gaps
**Parent Owner**: `Codex3`
**Parent Reviewer**: `Claude`
**Parent Status**: `review_approved` (as of 2026-04-24T06:01:57Z)
**Sidecar Task**: `APP-003-TRUTH-SYNC-004-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex3`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-24`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> runtime truth, registry or governance behavior, or the parent execution
> slice. It packages a reviewer-facing acceptance matrix and dependency map
> for the parallel rebaseline parent `APP-003-TRUTH-SYNC-004`.

## 1. Executive Summary

`APP-003-TRUTH-SYNC-004` exists because the 2026-04-24 cross-repo audit found
that Pantheon canonical surfaces were still describing several modules as
loop-complete while the current `front-ai-trading-system` default branch
continued to mount blocked shells. The parent is scoped to a truthful
rebaseline of active-surface wording — not new backend implementation and not
re-adjudication of already-archived closeouts.

The reopened truth covers:

- `EW-04` and `EW-05` — route-live in Pantheon, but front default branch still
  mounts `InspirationGraphBlocked` and `MutationReviewBlocked`
- `CW-01`, `CW-03`, `CW-04` — route-live in Pantheon, but front default branch
  still mounts `BlockedModuleShell` for consultation requests, committees, and
  memos
- `PKT-001` front-owned `meta.surfaces` fail-closed validation follow-up
- `PKT-003` replayability / `meta.staleness` / host-screen SSE reconciliation
  follow-up
- `EP5-002` deferred (human-gated canary/live proof, intentionally not
  materialized into the supervisor queue)

Current repo evidence shows the four reopened execution slices have already
been completed and archived as `done`:

- `APP-003-FRONT-REALIGN-EVOLUTION-001` (archived 2026-04-24T05:20:56Z)
- `APP-003-PKT001-CLOSEOUT-002` (archived 2026-04-24T05:34:33Z)
- `APP-003-FRONT-REALIGN-CONSULTATION-001` (archived 2026-04-24T05:40:58Z)
- `APP-003-PKT003-CLOSEOUT-001` (archived 2026-04-24T05:44:02Z)

What remains for the parent is the truthful rebaseline of canonical active
surfaces that pointed at those reopened lanes. Against the parent's three
acceptance points, the current repo already carries aligned wording in the
active backlog, Lovable SA, blueprint working source, and execution board.
The one non-blocking caveat is that the derived `current-work.md` Lovable
Coordination tracked-feature table still shows `loop_complete` for
`CW-01/CW-03/CW-04/EW-05` because those coordination records themselves still
carry `loop_complete`. That derivation is downstream of module-local
`.coordination/requests/*-ui-done.yaml` wording rather than the parent's
active-surface targets.

## 2. Scope Boundary

This sidecar only checks the support surfaces the parent brief names:

- `ai-status.json`
- `docs/reviews/2026-04-24-cross-repo-reopen-execution-packet.md`
- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md`
- `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md`
- `current-work.md` (only at the Task Board band, not re-generated here)
- `ai-task-archive/tasks/APP-003-FRONT-REALIGN-EVOLUTION-001.json`
- `ai-task-archive/tasks/APP-003-FRONT-REALIGN-CONSULTATION-001.json`
- `ai-task-archive/tasks/APP-003-PKT001-CLOSEOUT-002.json`
- `ai-task-archive/tasks/APP-003-PKT003-CLOSEOUT-001.json`

Out of scope:

- re-adjudicating whether the `front-ai-trading-system` mainline has actually
  shipped non-blocked EW-04/EW-05/CW-01/CW-03/CW-04 UI — that sits with the
  reopened front realignment lanes, which are themselves already archived `done`
- deciding whether derived coordination tables in `current-work.md` must be
  regenerated from updated `.coordination/requests/*-ui-done.yaml` records —
  that would be a separate `truth-sync` slice against the coordination record
  layer, not this parent's active-surface wording target
- any module-local contract truth about PKT-001 `meta.surfaces` or PKT-003
  staleness semantics — those remain in their own closeout lanes
- `EP5-002` canary/live proof — explicitly deferred by the reopen packet

## 3. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable truth for the active parent/sidecar owner, reviewer, lifecycle state, and artifact path. |
| `docs/reviews/2026-04-24-cross-repo-reopen-execution-packet.md` | Execution-origin packet that materialized the reopened lanes, deferred `EP5-002`, and named this parent `APP-003-TRUTH-SYNC-004` as the rebaseline task. |
| `WORKBENCH_DELIVERY_BACKLOG.md` | Current active backlog truth for the Evolution and Consultation module rows; must carry the reopened front-realignment wording rather than plain `loop_complete`. |
| `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` | Current Lovable-facing implementation brief; must state the EW-04/EW-05/CW-01/CW-03/CW-04 modules are route-live but reopened for front-default-branch realignment. |
| `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md` | Blueprint working source; must reflect the reopened execution lane and not leave already-archived task ids standing as active implementation lanes. |
| `current-work.md` Task Board | Execution board surface; must carry `APP-003-TRUTH-SYNC-004` and its sidecars as named tasks. |
| `ai-task-archive/tasks/APP-003-FRONT-REALIGN-EVOLUTION-001.json` | Archived record showing the reopened EW-04/EW-05 realignment lane is already `done` with review notes. |
| `ai-task-archive/tasks/APP-003-FRONT-REALIGN-CONSULTATION-001.json` | Archived record showing the reopened CW-01/CW-03/CW-04 realignment lane is already `done`. |
| `ai-task-archive/tasks/APP-003-PKT001-CLOSEOUT-002.json` | Archived record showing the reopened PKT-001 fail-closed validation closeout is already `done`. |
| `ai-task-archive/tasks/APP-003-PKT003-CLOSEOUT-001.json` | Archived record showing the reopened PKT-003 replayability/staleness/SSE closeout is already `done`. |

## 4. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Implication for review |
|---|---|---|
| The parent is a doc/active-surface rebaseline, not new backend work | `mutates_canonical: true` on the parent because it edits backlog/SA/blueprint/board wording, but the parent brief scopes it to truthful rebaseline only; the reopened runtime/UI work sits in the four already-archived realignment/closeout tasks. | Review should focus on wording accuracy, scope discipline, and board-level execution visibility — not runtime verification. |
| Active backlog rows describe reopened realignment truth for EW-04/EW-05 | `WORKBENCH_DELIVERY_BACKLOG.md` lines 60-61 say each is `route-live in Pantheon, but the current front default branch still mounts [blocked shell]`, with the next gate being to `keep the reopened front realignment lane open until front default branch ships a non-blocked [module] surface or returns a truthful contract-specific blocker`. | The active backlog no longer claims these modules are closed when the front mainline still mounts blocked shells. |
| Active backlog rows describe reopened realignment truth for CW-01/CW-03/CW-04 | `WORKBENCH_DELIVERY_BACKLOG.md` lines 99-103 carry the same `route-live in Pantheon, but the current front default branch still mounts blocked [page family]` framing for each module, and name the reopened front realignment lane as the next gate. | The active backlog no longer flattens these modules into plain `loop_complete`. |
| Lovable SA mirrors the reopened realignment framing | `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` lines 138/141/338-339/344/419-422/428 say EW-04/EW-05/CW-01/CW-03/CW-04 are `route-live in Pantheon, but reopened for front-default-branch realignment because the current front mainline still mounts blocked [shell]`, and line 428 explicitly says the reopened family should now be read as front-default-branch realignment work, not pending-BFF work. | The implementation-facing SA now carries the same reopened truth as the backlog. |
| Blueprint working source acknowledges the reopened execution lane | `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md` line 286 lists `front-ai-trading-system default branch realignment: EW-04, EW-05, CW-01, CW-03, CW-04` as part of the current remaining work, and lines 280-282 explicitly retire already-archived task ids from the active implementation lane. | The blueprint working source no longer leaves archived task ids standing as active implementation and now names the reopened execution lane. |
| Execution board carries named tasks for reopened work | `ai-status.json` task entries and `current-work.md` Task Board (lines 72-74) show `APP-003-TRUTH-SYNC-004` and its two sidecars as current tasks, alongside the already-archived realignment/closeout records for EW-04/EW-05, CW-01/CW-03/CW-04, PKT-001 follow-up, and PKT-003 follow-up. | The execution board carries the reopened work as named, trackable slices rather than only as prose in a review document. |
| Reopened realignment/closeout slices are already `done` in the archive | `ai-task-archive/tasks/APP-003-FRONT-REALIGN-EVOLUTION-001.json`, `APP-003-FRONT-REALIGN-CONSULTATION-001.json`, `APP-003-PKT001-CLOSEOUT-002.json`, and `APP-003-PKT003-CLOSEOUT-001.json` all record terminal status `done`. | Review should not reopen those archived slices; their existence as named closed work is exactly what satisfies the parent's board-visibility target. |
| Derived coordination tracked-feature rows still show `loop_complete` for CW-01/CW-03/CW-04/EW-05 | `current-work.md` lines 116-120 still label these modules `loop_complete` because the derived Lovable Coordination table reflects module-local `.coordination/requests/*-ui-done.yaml` state, not the reopened realignment truth. | Flag as a non-blocking boundary caveat; regenerating those derived rows would depend on first rewriting the per-module coordination records, which is outside this rebaseline's stated target of canonical active-surface wording. |

## 5. Parent Acceptance Checklist

Use this table to review `APP-003-TRUTH-SYNC-004` against its declared acceptance targets.

| Parent acceptance target | Verification | Status now |
|---|---|---|
| Canonical docs no longer claim EW-04, EW-05, CW-01, CW-03, and CW-04 are closed when front default branch still mounts blocked shells | `WORKBENCH_DELIVERY_BACKLOG.md` lines 60-61 and 99-103 now frame each of EW-04/EW-05/CW-01/CW-03/CW-04 as `route-live in Pantheon, but the current front default branch still mounts [blocked shell]`, with the next gate being the reopened front realignment lane. `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` mirrors this at lines 138/141/338-339/421-422/428, explicitly saying the reopened family should not be treated as pending-BFF. | PASS on the active canonical surfaces (backlog + Lovable SA) |
| Blueprint working source reflects the reopened execution lane | `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md` line 286 lists `front-ai-trading-system default branch realignment: EW-04, EW-05, CW-01, CW-03, CW-04` as current remaining work, and lines 280-282 retire the older already-archived task-id list from the active implementation lane. | PASS |
| Execution board carries named tasks for the reopened front realignment and closeout work | `ai-status.json` and the `current-work.md` Task Board carry `APP-003-TRUTH-SYNC-004` plus its sidecars; the `ai-task-archive/tasks/` directory holds the archived `done` records for `APP-003-FRONT-REALIGN-EVOLUTION-001`, `APP-003-FRONT-REALIGN-CONSULTATION-001`, `APP-003-PKT001-CLOSEOUT-002`, and `APP-003-PKT003-CLOSEOUT-001`. | PASS |
| (Non-blocking caveat) Derived coordination tracked-feature rows | `current-work.md` lines 116-120 still label CW-01/CW-03/CW-04/EW-05 `loop_complete` because the derivation reads module-local `.coordination/requests/*-ui-done.yaml`. The reopen packet targets canonical active-surface wording, not module coordination records. | NON-BLOCKING — flag as out-of-scope for this parent unless the reviewer decides to widen scope |

## 6. Dependency Map

### 6.1 Upstream Truth Anchors

| Dependency | Where recorded | Status | Relevance |
|---|---|---|---|
| Cross-repo reopen execution packet | `docs/reviews/2026-04-24-cross-repo-reopen-execution-packet.md` | COMPLETE | Establishes why this parent exists, defines its acceptance target, and lists the five materialized tasks (four execution + one rebaseline). |
| Reopened Evolution realignment slice | `ai-task-archive/tasks/APP-003-FRONT-REALIGN-EVOLUTION-001.json` | COMPLETE (archived `done`) | Provides the named execution work that the parent's board-visibility target points at for EW-04/EW-05. |
| Reopened Consultation realignment slice | `ai-task-archive/tasks/APP-003-FRONT-REALIGN-CONSULTATION-001.json` | COMPLETE (archived `done`) | Provides the named execution work that the parent's board-visibility target points at for CW-01/CW-03/CW-04. |
| Reopened PKT-001 fail-closed validation closeout | `ai-task-archive/tasks/APP-003-PKT001-CLOSEOUT-002.json` | COMPLETE (archived `done`) | Provides the named closeout that covers the `meta.surfaces` fail-closed follow-up named in the reopen packet. |
| Reopened PKT-003 replayability/staleness/SSE closeout | `ai-task-archive/tasks/APP-003-PKT003-CLOSEOUT-001.json` | COMPLETE (archived `done`) | Provides the named closeout that covers the PKT-003 follow-up named in the reopen packet. |
| Active backlog truth | `WORKBENCH_DELIVERY_BACKLOG.md` | COMPLETE | Carries the reopened front-realignment wording for the Evolution and Consultation module rows. |
| Active Lovable SA truth | `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` | COMPLETE | Mirrors the reopened-realignment wording and keeps the route-live-vs-pending-BFF boundary honest. |
| Blueprint working source | `docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md` | COMPLETE | Names the reopened execution lane and retires the already-archived task-id list from active implementation. |

### 6.2 Downstream Consumers

| Consumer | Current state | Relationship to the parent task |
|---|---|---|
| Future cross-repo reopen / rebaseline cycles | Dormant | Will depend on this parent having produced the truthful rebaseline so later reopen audits do not chase the same active-surface drift again. |
| `APP-003-TRUTH-SYNC-004-SIDECAR-REVIEW` | `todo` (Codex2 owner, Claude2 reviewer) | Consumes the same source set as this acceptance packet; review should remain about truthful support-packet framing, not about reopening the parent. |
| Coordination-record truth-sync (hypothetical) | NOT SCOPED | If a reviewer wants the derived `current-work.md` Lovable Coordination rows corrected, that would be a separate slice against `.coordination/requests/*-ui-done.yaml` — out of scope here. |

### 6.3 Machine vs. Semantic Dependency Note

`ai-status.json` records `depends_on: []` for both the parent and this sidecar.
The dependency map above is therefore semantic only. It is a review aid, not a
request to mutate task-board dependencies.

## 7. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Reopening `APP-003-FRONT-REALIGN-EVOLUTION-001`, `APP-003-FRONT-REALIGN-CONSULTATION-001`, `APP-003-PKT001-CLOSEOUT-002`, or `APP-003-PKT003-CLOSEOUT-001` as part of this parent | Those four slices are already archived `done` with review notes; the parent's board-visibility target is satisfied by their existence as named closed work, not by reopening them. |
| Blocking the parent because derived `current-work.md` coordination rows still show `loop_complete` for CW-01/CW-03/CW-04/EW-05 | Those rows are derived from module-local `.coordination/requests/*-ui-done.yaml` records. The parent acceptance target is canonical active-surface wording, not coordination-record truth. Widening scope to the coordination layer would be a separate slice. |
| Using this parent to re-adjudicate module-local PKT-001 `meta.surfaces` or PKT-003 staleness/SSE truth | Those questions belong to the reopened closeout lanes (`APP-003-PKT001-CLOSEOUT-002` and `APP-003-PKT003-CLOSEOUT-001`), both of which are archived `done`. |
| Treating `EP5-002` as part of this parent | The reopen packet explicitly defers `EP5-002` as a human-gated canary/live proof and does not materialize it into the supervisor queue. |
| Using this parent or sidecar to mutate L1 policy, core contracts, runtime code, registry behavior, or governance implementation | The task is explicitly scoped to truthful active-surface rebaseline only. |
| Rejecting this sidecar because older historical docs still mention pre-reopen wording | Archived records, historical review docs, and closed sidecars legitimately preserve prior wording; only active truth surfaces (backlog, SA, blueprint working source, execution board) must carry the reopened framing. |

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only `support/sidecars/APP-003-TRUTH-SYNC-004/APP-003-TRUTH-SYNC-004-SIDECAR-ACCEPTANCE.md` is added by this sidecar. |
| No canonical runtime/policy edits by sidecar | PASS | No L1 docs, runtime files, registry files, or governance files were modified here. |
| Parent acceptance targets mapped to active artifacts | PASS | Section 5 ties each acceptance item to the exact backlog/SA/blueprint/board surface now carrying the reopened wording. |
| Reopened execution slices are already named and archived | PASS | Section 6.1 cites the four `ai-task-archive/tasks/*.json` records and their `done` terminal status. |
| Historical-vs-active boundary made explicit | PASS | Sections 4, 5, and 7 distinguish the reopened active-surface wording from downstream coordination-record derivations and from archived historical wording. |
| Non-blocking caveat about derived coordination rows is called out rather than hidden | PASS | Section 5 rows 4 and Section 7 row 2 both flag the `current-work.md` Lovable Coordination rows as out-of-scope, so reviewer can decide whether to widen scope. |

## 9. Handoff to Reviewer (`Codex3`)

This sidecar packet is the acceptance packet for
`APP-003-TRUTH-SYNC-004-SIDECAR-ACCEPTANCE` and the reviewer-facing support
artifact for the parent `APP-003-TRUTH-SYNC-004` closeout.

What it gives you:

1. a direct acceptance matrix against the parent's three declared acceptance
   targets, citing the active backlog, Lovable SA, blueprint working source,
   and execution board lines that now carry the reopened wording
2. an explicit note that the four reopened execution slices
   (`APP-003-FRONT-REALIGN-EVOLUTION-001`, `APP-003-FRONT-REALIGN-CONSULTATION-001`,
   `APP-003-PKT001-CLOSEOUT-002`, `APP-003-PKT003-CLOSEOUT-001`) are already
   archived `done`, so the parent's board-visibility target is satisfied by
   their named existence rather than by reopening them
3. the one non-blocking boundary caveat about `current-work.md` Lovable
   Coordination tracked-feature rows still showing `loop_complete` for
   CW-01/CW-03/CW-04/EW-05, so it is not mistaken for active-surface drift
4. a dependency map that anchors this slice to truthful active-surface
   rebaseline rather than to new backend/runtime implementation, `EP5-002`, or
   module-local PKT closeout re-adjudication
5. a scope guard that the parent should not be widened into the
   `.coordination/requests/*-ui-done.yaml` coordination-record layer unless you
   explicitly decide that widening is correct

Recommended reviewer stance:

1. approve this sidecar if the active backlog, Lovable SA, blueprint working
   source, and execution board still match the reopened-realignment wording
   described in Section 5
2. treat the four archived reopened slices as named closed work, not
   candidates to reopen as part of this parent
3. ignore archive-only and historical-doc hits of older pre-reopen wording
   unless they leak back into active truth surfaces
4. if you do want the derived `current-work.md` coordination rows corrected,
   request a separate narrow truth-sync slice against
   `.coordination/requests/*-ui-done.yaml` rather than reopening this parent

## 10. Verification Commands

- `python3 scripts/ai_status.py show APP-003-TRUTH-SYNC-004`
- `python3 scripts/ai_status.py show APP-003-TRUTH-SYNC-004-SIDECAR-ACCEPTANCE`
- `sed -n '50,115p' WORKBENCH_DELIVERY_BACKLOG.md`
- `rg -n "EW-04|EW-05|CW-01|CW-03|CW-04|realignment|front-default-branch" docs/pantheon-handoffs/LOVABLE_MASTER_SA.md`
- `rg -n "front-ai-trading-system default branch realignment|APP-003-TRUTH-SYNC-004|archive-done" docs/reviews/2026-04-20-system-design-open-questions-for-architecture-team.md`
- `rg -n "APP-003-TRUTH-SYNC-004|APP-003-FRONT-REALIGN|APP-003-PKT001-CLOSEOUT-002|APP-003-PKT003-CLOSEOUT-001" ai-status.json`
- `ls ai-task-archive/tasks/APP-003-FRONT-REALIGN-*.json ai-task-archive/tasks/APP-003-PKT001-CLOSEOUT-002.json ai-task-archive/tasks/APP-003-PKT003-CLOSEOUT-001.json`
- `sed -n '114,135p' current-work.md`

---
*Prepared as a sidecar `acceptance_packet` helper for
`APP-003-TRUTH-SYNC-004`. This file is a support artifact and does not modify
canonical truth.*
