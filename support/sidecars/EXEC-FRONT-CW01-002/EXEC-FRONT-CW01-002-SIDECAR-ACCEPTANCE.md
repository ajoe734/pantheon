# EXEC-FRONT-CW01-002 Acceptance Packet and Dependency Map (Sidecar)

**Parent Task**: `EXEC-FRONT-CW01-002` - Republish the CW-01 consult request UI cycle with truthful feedback and pagination fixes  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Claude`  
**Parent Status**: `todo` (follow-up slice is dispatched but not yet closed)  
**Sidecar Task**: `EXEC-FRONT-CW01-002-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-21`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance / main frontend
> implementations. It packages the current CW-01 frontend follow-up state into a
> reviewer-ready acceptance packet for parent-owner execution and closeout.

---

## 1. Executive Summary

`EXEC-FRONT-CW01-002` is not a new CW-01 contract or BFF task. The contract and
route-live truth already landed earlier; this follow-up exists because the
returned frontend publication cycle was not replay-clean, and because the
materialized follow-up record still compresses publication replay together with
an earlier UI-fix checklist.

Current state, condensed:

- `CW-01-FOUNDATION-001` already published the consult request contract,
  request-to-session handoff semantics, and frontend handoff bundle.
- `LUV-REACTIVATE-CW01-001` later revalidated that bundle and recorded the
  original `bff_route_live: false` blocker accurately.
- Pantheon then moved CW-01 to route-live truth, and the front lane returned a
  CW-01 implementation that fixed the major request/detail issues in the code.
- Pantheon's current review packet says the open blocker is no longer backend
  availability, and that the current Git-visible CW-01 tree already contains
  the previously missing list/detail fixes.
- The machine-readable frontend-feedback response, frontend SA summary, and the
  materialized parent task still frame closeout as "publication truth plus four
  UI contract items": `page_size` forwarding, degraded empty-state suppression,
  degraded cancel gating based on explicit `allowedActions`, and request-row
  `target_type` visibility.
- The parent task should therefore be read as a **truthful republish and
  follow-up integrity** slice. It should not reopen canonical contract writing
  or BFF implementation.

This sidecar does not approve the parent task. It gives the assigned parent
owner and reviewer a precise acceptance checklist, dependency map, and stop
conditions for the CW-01 republish loop.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical owner / reviewer / lifecycle truth for the parent task and this sidecar |
| `ai-task-archive/tasks/CW-01-FOUNDATION-001.json` | Archived upstream contract-publication record for CW-01 |
| `ai-task-archive/tasks/LUV-REACTIVATE-CW01-001.json` | Archived reactivation record confirming the earlier handoff bundle and blocker state |
| `.coordination/reviews/CW-01-consult-request-review.md` | Latest repo-specific Pantheon review packet for the returned CW-01 tree; it records that the current Git-visible code already resolves the earlier UI gaps and that publication replay is the active blocker |
| `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml` | Structured follow-up response that still carries the earlier publication-plus-UI-fix close checklist; useful as the durable execution handoff, but older than the later Git-visible re-review |
| `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md` | Frontend contract rules, degradation handling, CTA authority rules, and completion-handoff requirements |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Front-lane summary showing CW-01 route-live truth and the remaining republish gate; its CW-01 note still compresses replay cleanup together with the earlier UI-fix checklist |
| `support/sidecars/CW-01-FOUNDATION-001/CW-01-FOUNDATION-001-SIDECAR-BFF-HANDOFF.md` | Prior support packet summarizing the published CW-01 contract / handoff truth |

---

## 3. Parent Acceptance Verification

The parent task acceptance in durable state says it must:

1. produce one Git-visible front commit containing the CW-01 UI files
2. include the `ui-done` request, the `frontend-feedback` request, and the
   `docs/pantheon-feedback/CW-01-consult-request` bundle in that same
   publication set
3. resolve the remaining pagination and degraded-state contract findings
4. point both request payloads at the same immutable publication commit

This sidecar verifies those points against the current CW-01 evidence:

| Acceptance Item | Verification | Status |
|---|---|---|
| Parent is follow-up-only, not a fresh contract slice | `CW-01-FOUNDATION-001` is archived `done`, and `FRONTEND_CHANGE_SPEC.md` already publishes the request/list/detail/cancel contract | PASS |
| Backend route-live truth already exists | `docs/lovable/PANTHEON_FRONTEND_SA.md` now records CW-01 as `contract-ready` with live BFF routes; the review packet says backend-owned route family remains live and contract-shaped | PASS |
| Returned CW-01 code fixed the earlier major UI gaps | Review packet records that the current Git-visible implementation already includes the `context_refs[]` composer, `/sessions/:linked_session_id` routing, `page_size` forwarding, degraded empty-state suppression, explicit degraded cancel authority handling, and request-row `target_type` rendering | PASS |
| Current request pair is replay-clean and points at the full publication commit | Review packet says both handoff files still point `source_commit` at `d51274b...`, while the first commit containing the full UI + feedback bundle set is `d9d64fe` | FAIL |
| Canonical frontend-feedback request exists in Pantheon for the follow-up | `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml` exists and still records the materialized close checklist explicitly | PASS |
| Pagination contract is present in the reviewed tree and must survive republish | Review packet says `ConsultRequestList` now forwards `page_size`; frontend-feedback still lists it because the durable follow-up artifact predates that later re-review | PASS |
| Degraded list behavior is truthful in the reviewed tree and must survive republish | Review packet says the Git-visible implementation suppresses degraded empty-state claims; frontend-feedback still carries the earlier failure state | PASS |
| Degraded detail cancel gating is truthful in the reviewed tree and must survive republish | Review packet says `ConsultRequestDetail` now respects explicit `allowedActions.canCancel` on degraded responses; frontend-feedback still carries the earlier failure state | PASS |
| Request rows show `target_type` in the reviewed tree and must survive republish | Review packet says the Git-visible list row now renders `target_type`; frontend-feedback still carries the earlier failure state | PASS |

### Evidence Reconciliation

- `.coordination/reviews/CW-01-consult-request-review.md` is the newest
  repo-specific review of the returned CW-01 tree and should be read as the
  current evidence for code-state.
- `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml`,
  `docs/lovable/PANTHEON_FRONTEND_SA.md`, and the materialized parent task
  still encode the earlier follow-up framing that grouped publication replay
  together with those same UI fixes.
- For this sidecar, the review packet defines what is already present in the
  reviewed tree; the response / SA / parent-task wording define what the final
  republished commit must still preserve without regression.

### Closure Gate Interpretation

The parent task can close only after two categories are both satisfied:

- **publication truth**
  both returned handoff payloads must cite the same immutable front commit that
  actually contains the CW-01 UI files and the feedback bundle
- **final publication integrity**
  the final published UI tree must preserve the already-reviewed `page_size`
  forwarding, degraded empty-state suppression, degraded cancel authority
  handling, and request-row `target_type` visibility; if the republished tree
  regresses any of them, the parent must reopen review instead of claiming
  closeout

The blocker is **not**:

- missing CW-01 BFF routes
- reopening `docs/bff/CW-01-consult-request.md`
- revisiting request-to-session lifecycle semantics
- changing L1 architecture or workbench policy

If the final front publication matches the current review response and loop-close
condition, the parent should move into review. If the republished front return
still drifts from the published contract, the correct next step is a new review
or `bff-gap` escalation, not silent local truth changes in Pantheon.

---

## 4. Dependency Map

### 4.1 Upstream Truth Providers

| Task / artifact | Status | Contribution to the parent slice |
|---|---|---|
| `CW-01-FOUNDATION-001` | `done` | Published consult request create/list/detail/cancel truth, request-to-session semantics, example payload, and frontend handoff bundle |
| `LUV-REACTIVATE-CW01-001` | `done` | Revalidated the earlier handoff packet and preserved the original blocked-while-routes-missing truth |
| `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md` | `published` | Defines the frontend contract, degradation rules, CTA authority rules, and completion-handoff behavior |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | `updated` | Records that CW-01 routes are now live and that the follow-up remains a republish gate, even though its CW-01 note still compresses replay cleanup together with the earlier UI-fix checklist |

### 4.2 Parent Follow-up Evidence Chain

| Stage | Evidence | Meaning |
|---|---|---|
| Contract published | `CW-01-FOUNDATION-001` | CW-01 request lifecycle and route contract are already canonical |
| Front-lane bundle reactivated | `LUV-REACTIVATE-CW01-001` | Historical blocker and handoff bundle were preserved cleanly |
| Route-live truth established | `docs/lovable/PANTHEON_FRONTEND_SA.md`, `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml` | CW-01 is no longer blocked on BFF availability |
| Returned front implementation reviewed | `.coordination/reviews/CW-01-consult-request-review.md` | Pantheon confirmed the current Git-visible tree resolves the earlier UI contract gaps and that truthful publication replay is the active blocker |
| Parent task materialized | `ai-status.json` task `EXEC-FRONT-CW01-002` | Sidecar's target execution slice is the truthful republish loop, with the final published commit required to preserve the already-reviewed UI fixes |

### 4.3 Artifact Flow

```text
CW-01-FOUNDATION-001
  -> docs/bff/CW-01-consult-request.md
  -> docs/examples/CW-01-consult-request.json
  -> docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md
  -> initial coordination bundle

LUV-REACTIVATE-CW01-001
  -> .coordination/reviews/CW-01-consult-request-reactivation.md
  -> preserves bundle integrity and the original readiness gate

Route-live transition + Pantheon review
  -> docs/lovable/PANTHEON_FRONTEND_SA.md
  -> .coordination/responses/CW-01-consult-request-frontend-feedback.yaml
  -> .coordination/reviews/CW-01-consult-request-review.md

EXEC-FRONT-CW01-002
  -> republish final front commit containing:
       .coordination/requests/CW-01-consult-request-ui-done.yaml
       .coordination/requests/CW-01-consult-request-frontend-feedback.yaml
       docs/pantheon-feedback/CW-01-consult-request/*
       src/pages/consultation/ConsultRequestList.tsx
       src/pages/consultation/ConsultRequestDetail.tsx
       src/pages/consultation/types.ts
       src/lib/bffClient.ts
       src/App.tsx
  -> request pair points source_commit at that same immutable commit
  -> parent returns to review
```

### 4.4 Expected Output Set for the Parent Owner

The parent owner should treat the following front-repo outputs as the minimum
truthful publication set:

- `.coordination/requests/CW-01-consult-request-ui-done.yaml`
- `.coordination/requests/CW-01-consult-request-frontend-feedback.yaml`
- `docs/pantheon-feedback/CW-01-consult-request/LOVABLE_CHANGE_FEEDBACK.md`
- `docs/pantheon-feedback/CW-01-consult-request/API_GAP_REQUESTS.json`
- `docs/pantheon-feedback/CW-01-consult-request/UI_DECISIONS.md`
- `docs/pantheon-feedback/CW-01-consult-request/QA_STATUS.md`
- the reviewed CW-01 UI source files named in the frontend feedback response

Absence of that full set in one Git-visible commit should be treated as a failed
publication attempt, not a closeable partial return.

---

## 5. Parent-Owner Action Summary

For `Codex` as parent owner, the support recommendation is:

1. treat `EXEC-FRONT-CW01-002` as a front-repo republish / follow-up fix slice,
   not a Pantheon contract-authoring slice
2. make one Git-visible front commit that contains the reviewed CW-01 UI files,
   the request pair, and the full feedback bundle together
3. ensure both request bodies set `source_commit` to that exact immutable
   publication commit
4. keep the existing route-live and lifecycle truth backend-owned; do not edit
   Pantheon canonical contract docs as part of this follow-up
5. make sure the final publication commit preserves the four CW-01 UI contract
   fixes already observed in the review packet; if the republished tree drops
   any of them, treat that as an active regression and refresh the review /
   frontend-feedback record truthfully
6. return the parent task to review only after the final publication commit is
   replay-clean and the feedback bundle truthfully matches the code in that same
   commit

---

## 6. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar adds only `support/sidecars/EXEC-FRONT-CW01-002/EXEC-FRONT-CW01-002-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited | PASS | No L0/L1 product docs, coordination payloads, runtime code, or frontend source files are modified by this sidecar |
| Packet keeps parent scope in follow-up-only mode | PASS | Sections 1, 3, and 5 restrict the parent task to truthful republish plus preservation of the already-reviewed UI contract fixes |
| Dependency chain is complete enough for execution | PASS | Packet names the contract publication, reactivation, route-live truth, current review packet, and expected final publication set |
| Reviewer can use this as a start / finish checklist for the parent task | PASS | Packet states the remaining blockers, exact output set, and exact loop-close condition |

---

## 7. Handoff to Reviewer (`Codex`)

This sidecar is ready for review as the acceptance packet for
`EXEC-FRONT-CW01-002`.

What it gives you:

1. a precise split between already-landed CW-01 truth and the remaining
   republish-only follow-up work
2. the dependency chain from contract publication to the current frontend review
   blocker
3. the concrete close condition for the parent owner: one replay-clean front
   publication commit that preserves the already-reviewed CW-01 list/detail
   fixes

Recommended reviewer stance:

1. approve this sidecar if it matches the repo's current CW-01 follow-up state
2. keep the parent task scoped to front publication truth and preservation of
   the already-reviewed UI fixes only
3. send the parent owner back through review after the corrected front commit
   and feedback bundle are published

---
*Generated by Codex2 as a sidecar `acceptance_packet` helper for `EXEC-FRONT-CW01-002`. This file is a support artifact and does not modify canonical truth.*
