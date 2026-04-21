# EXEC-FRONT-RW01-001 Acceptance Packet and Dependency Map (Sidecar)

**Parent Task**: `EXEC-FRONT-RW01-001` - Implement RW-01 research ticket front-end flow against live Pantheon APIs  
**Parent Owner**: `Codex2`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `done` (`2026-04-21T16:51:01Z`; this packet was reviewed while the parent was still runtime-blocked)  
**Sidecar Task**: `EXEC-FRONT-RW01-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-21`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance / main frontend
> implementations. It packages the current RW-01 parent-task acceptance state,
> dependency chain, and remaining runtime blocker into a reviewer-ready packet.

> Finalization note: Claude approved this packet while `EXEC-FRONT-RW01-001`
> was still blocked on runtime refresh. The parent task later finalized to
> `done` after the live operator-bff runtime exposed the RW-01 route family and
> the closeout review passed. Sections below preserve the review-time snapshot
> and dependency map that supported that approval.

---

## 1. Executive Summary

`EXEC-FRONT-RW01-001` should no longer be read as "frontend still missing." The
front repo had already returned a readiness-gated RW-01 implementation and a
replay-clean `ui-done` / `frontend-feedback` pair before this sidecar review.
At approval time, the remaining blocker sat outside the front slice: the active
operator-bff runtime that Pantheon was checking over live HTTP was still stale
and did not expose the RW-01 route family yet.

Current state, condensed:

- `RW-01-FOUNDATION-001` already published the create/list/detail/patch
  contract, lifecycle model, and frontend handoff bundle.
- `LUV-REACTIVATE-RW01-001` later re-verified that bundle and recorded the
  historical readiness gate: `bff_route_live: false`.
- `EXEC-FRONT-RW01-001` then produced a contract-aligned front implementation
  from `origin/pkt-004-detail-fix`, with truthful handoff metadata pointing to
  source commit `7b807fbe9ebcd5c84baca77de966121c0b2d1d73`.
- Pantheon re-review on `2026-04-21` confirmed the current workspace app
  implements the RW-01 slice locally, targeted RW-01 contract tests pass, and
  seeded local FastAPI probes return truthful degraded/unavailable envelopes.
- At approval time, the parent task remained blocked because the active runtime at
  `http://127.0.0.1:18001` still returns `404` for
  `GET /api/v1/research/tickets` and exposes no `/api/v1/research/tickets*`
  paths in `/openapi.json`.
- The parent task later finalized to `done` after runtime refresh exposed the
  live route family and the closeout review completed.

This sidecar does not modify canonical truth. It gives the assigned reviewer
and parent owner a precise answer to what was already done in the front slice,
what remained blocked at review time, and which runtime-refresh step had to
happen before the parent could close.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical owner / reviewer / lifecycle truth for the parent task and this sidecar |
| `ai-task-archive/tasks/RW-01-FOUNDATION-001.json` | Archived upstream contract-publication record for the RW-01 ticket family |
| `ai-task-archive/tasks/LUV-REACTIVATE-RW01-001.json` | Archived reactivation record confirming the bundle stayed aligned and documenting the original BFF gate |
| `.coordination/responses/RW-01-research-ticket-contract-ready.yaml` | Published RW-01 contract-ready handoff with the four canonical endpoints and initial readiness gate |
| `.coordination/responses/RW-01-research-ticket-lovable-ui-task.yaml` | Frontend constraints, accepted routes, and required returned feedback bundle |
| `docs/pantheon-handoffs/RW-01-research-ticket/FRONTEND_CHANGE_SPEC.md` | Main frontend integration brief, readiness-gate behavior, degradation rules, and CTA authority rules |
| `.coordination/reviews/RW-01-research-ticket-review.md` | Current parent review packet showing the front slice is contract-aligned and replay-clean, but still blocked on live runtime freshness |
| `.coordination/requests/RW-01-research-ticket-ui-done.yaml` | Returned front-owned completion handoff with the truthful `source_commit` and blocked disposition |
| `.coordination/responses/RW-01-research-ticket-frontend-feedback.yaml` | Structured Pantheon feedback response capturing which acceptance points passed and which runtime gate still fails |
| `.coordination/requests/RW-01-research-ticket-needs-runtime.yaml` | Active Pantheon runtime-refresh request that tracks the remaining blocker |

---

## 3. Parent Acceptance and Closure Verification (Review-Time Snapshot)

The parent task acceptance in durable state says it must:

1. align the research ticket create/list/detail UI with the live routes and
   lifecycle contract
2. keep authority signals and lifecycle history backend-owned
3. submit a `ui-done` handoff after completion

This sidecar verifies those points against the current RW-01 evidence:

| Acceptance Item | Verification | Status |
|---|---|---|
| RW-01 list/detail routes are mounted in the reviewed front router | Review packet confirms `/research/tickets` and `/research/tickets/:ticket_id` are mounted, and both surfaces route traffic through `rw01TicketApi` only | PASS |
| UI follows the published lifecycle contract instead of inventing local truth | Review packet confirms lifecycle history renders strictly from `lifecycle_history[]`, owner selection uses backend-provided persona identities, and no component-level raw fetch path was introduced | PASS |
| Action authority stays backend-owned | Review packet confirms generic save now patches editable fields only, while close/archive remain separate explicit CTAs gated by `allowedActions.canClose` and `allowedActions.canArchive` | PASS |
| Returned completion bundle exists and is replay-clean | `ui-done.yaml` and `frontend-feedback.yaml` both point to source commit `7b807fbe9ebcd5c84baca77de966121c0b2d1d73`; review notes say the remote branch contains the request pair and feedback docs | PASS |
| Reviewed slice passes local verification | Review packet records `python3 -m pytest services/control-plane/bff/test_rw01_research_ticket_contract.py -q` with `6 passed`, plus targeted sibling `eslint`, `tsc`, and `build` success | PASS |
| Parent can now truthfully claim success against live Pantheon APIs | The active runtime still returns `404` for RW-01 list/detail over live HTTP and advertises no RW-01 paths in `/openapi.json` | BLOCKED |

### Closure Gate Interpretation

At review time, the parent task was blocked on one remaining gate only:

- **runtime freshness / live HTTP truth**

The blocker is **not**:

- front source-commit replayability
- missing `ui-done` or feedback bundle
- client-side lifecycle invention
- CTA authority drift
- speculative linked-entity navigation

If the live runtime refresh surfaces the published RW-01 field shape, the parent
should return to review without asking the front repo to re-implement the slice.
If the refreshed runtime reveals contract drift, the correct next step is to
emit `.coordination/requests/RW-01-research-ticket-bff-gap.yaml`, not to widen
the UI locally.

---

## 4. Dependency Map

### 4.1 Upstream Truth Providers

| Task / artifact | Status | Contribution to the parent slice |
|---|---|---|
| `RW-01-FOUNDATION-001` | `done` | Published the four RW-01 endpoints, lifecycle model, `allowedActions` semantics, example payload, and frontend handoff bundle |
| `LUV-REACTIVATE-RW01-001` | `done` | Re-validated that the RW-01 bundle still matched architecture truth and recorded the pre-runtime-live gate cleanly |
| `RW-01-research-ticket-contract-ready.yaml` | `published` | Declared the canonical route family, target repo, and "do not start production UI until routes are live" constraint |
| `RW-01-research-ticket-lovable-ui-task.yaml` | `ready` | Locked the frontend to shared BFF-client usage, backend-owned authority, and returned feedback artifacts |
| `FRONTEND_CHANGE_SPEC.md` | `published` | Defined the blocked-placeholder behavior, degradation handling, patch rules, and no-optimistic-mutation boundary |

### 4.2 Parent Execution Evidence Chain

| Stage | Evidence | Meaning |
|---|---|---|
| Front implementation published | source commit `7b807fbe9ebcd5c84baca77de966121c0b2d1d73` | The RW-01 front slice and feedback bundle exist in a Git-visible front branch |
| Canonical request pair repointed truthfully | later branch commit `4ff0651` | The returned request files now cite the truthful source commit instead of stale metadata |
| Pantheon review passed the front-owned acceptance surface | `.coordination/responses/RW-01-research-ticket-frontend-feedback.yaml` | All UI/contract alignment checks pass except live runtime exposure |
| Pantheon raised a runtime-only blocker | `.coordination/requests/RW-01-research-ticket-needs-runtime.yaml` | Remaining work is operator-bff refresh / redeploy and live HTTP recheck |
| Parent task stays blocked | `ai-status.json` → `waiting_for: Gemini` | Owner should not close the loop until the live runtime serves RW-01 truthfully over HTTP |

### 4.3 Artifact Flow

```text
RW-01-FOUNDATION-001
  -> docs/bff/RW-01-research-ticket.md
  -> docs/examples/RW-01-research-ticket.json
  -> .coordination/responses/RW-01-research-ticket-contract-ready.yaml
  -> .coordination/responses/RW-01-research-ticket-lovable-ui-task.yaml
  -> docs/pantheon-handoffs/RW-01-research-ticket/FRONTEND_CHANGE_SPEC.md

LUV-REACTIVATE-RW01-001
  -> .coordination/reviews/RW-01-research-ticket-reactivation.md
  -> confirms bundle alignment and records the readiness gate

EXEC-FRONT-RW01-001 front return
  -> .coordination/requests/RW-01-research-ticket-ui-done.yaml
  -> .coordination/responses/RW-01-research-ticket-frontend-feedback.yaml
  -> replay-clean feedback bundle in front-ai-trading-system

Pantheon re-review
  -> .coordination/reviews/RW-01-research-ticket-review.md
  -> .coordination/requests/RW-01-research-ticket-needs-runtime.yaml

Next truthful step
  -> Gemini refreshes/redeploys the active operator-bff runtime
  -> Pantheon reruns live HTTP RW-01 verification
  -> parent task either re-enters review or emits RW-01 bff-gap if the runtime drifts
```

### 4.4 Not-Yet-Produced Escalation Output

This file does **not** currently exist:

- `.coordination/requests/RW-01-research-ticket-bff-gap.yaml`

That absence is consistent with current evidence. Pantheon has detected runtime
staleness, but not yet a refreshed live payload that contradicts the published
RW-01 field shape.

---

## 5. Parent-Owner Action Summary (Historical)

For `Codex2` as parent owner, the support recommendation at approval time was:

1. treat the front slice as **delivered pending runtime verification**, not as a
   missing UI implementation
2. keep the parent task blocked on
   `.coordination/requests/RW-01-research-ticket-needs-runtime.yaml` until the
   active runtime is refreshed
3. after runtime refresh, verify over live HTTP that:
   - `POST /api/v1/research/tickets`
   - `GET /api/v1/research/tickets`
   - `GET /api/v1/research/tickets/{ticket_id}`
   - `PATCH /api/v1/research/tickets/{ticket_id}`
   are exposed with the published RW-01 field shape
4. re-check that degraded and unavailable responses still carry truthful
   `meta.surfaces.ticket_list` and `meta.surfaces.ticket_detail` envelopes over
   real HTTP
5. if the live runtime matches the published contract, move the parent back into
   review instead of reopening the front task for speculative fixes
6. if the refreshed runtime diverges from the published shape, emit
   `.coordination/requests/RW-01-research-ticket-bff-gap.yaml` and keep the UI
   locked behind the readiness gate

Those steps are now satisfied by the archived parent closeout in
`ai-task-archive/tasks/EXEC-FRONT-RW01-001.json`.

---

## 6. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | New support content is limited to `support/sidecars/EXEC-FRONT-RW01-001/EXEC-FRONT-RW01-001-SIDECAR-ACCEPTANCE.md`; non-support edits are only status-tracker sync from the required lifecycle commands |
| No canonical product truth edited | PASS | No L1 product/policy docs, coordination payloads, runtime files, or frontend source files are changed by this sidecar; L0 execution state may update via `scripts/ai_status.py` only |
| Packet distinguishes delivered UI work from runtime blocker | PASS | Sections 1, 3, and 5 separate replay-clean front delivery from the still-blocking live HTTP gate |
| Dependency chain is complete enough for reviewer use | PASS | Packet names contract publication, reactivation, returned handoff bundle, review packet, and runtime-refresh request |
| Scope stays support-only | PASS | Content is limited to acceptance verification, dependency mapping, and next-step guidance for the parent owner |

---

## 7. Review Outcome (`Claude`)

This sidecar served as the acceptance packet for `EXEC-FRONT-RW01-001` and was
approved by `Claude`.

What it gives you:

1. a precise split between what the front repo already delivered and what still
   blocks the parent task
2. the RW-01 dependency chain from contract publication to current runtime
   follow-up
3. the exact next move: refresh the active runtime and rerun live HTTP checks,
   not another speculative frontend rewrite

Recorded reviewer stance:

1. approve this sidecar if it matches the repo's current RW-01 state
2. keep the parent task blocked until the active runtime exposes RW-01
   truthfully over live HTTP
3. use this packet as the short acceptance summary when the runtime refresh
   comes back and the parent task needs to re-enter review

That review outcome was later absorbed into the parent closeout once the
runtime refresh completed and the parent task moved to `done`.

---
*Generated by Codex as a sidecar `acceptance_packet` helper for `EXEC-FRONT-RW01-001`. This file is a support artifact and does not modify canonical truth.*
