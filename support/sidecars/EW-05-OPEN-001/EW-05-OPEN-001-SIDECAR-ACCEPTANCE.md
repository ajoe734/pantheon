# EW-05-OPEN-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `EW-05-OPEN-001` - Open Mutation Review contract and command vocabulary
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `done` (archived at `2026-04-19T17:40:46Z`)
**Sidecar Task**: `EW-05-OPEN-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-19`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations. It
> summarizes the closed `EW-05-OPEN-001` delivery, verifies the acceptance
> boundary, and maps the remaining activation gates for downstream BFF and UI
> work.

---

## 1. Executive Summary

`EW-05-OPEN-001` closed the contract-publication gap for the Evolution
Workbench's Mutation Review surface.

The parent task did **not** make the production screen live. It published the
truthful contract package that downstream implementers now consume:

1. the read-route contract for
   `GET /api/v1/operator/mutation-review/{decision_id}`
2. the `ApproveMutation` / `RejectMutation` command vocabulary for
   `POST /api/v1/operator/commands`
3. the backend-owned authority signals
   `allowedActions.canApproveMutation` / `canRejectMutation`
4. the staleness signal `meta.surfaces.mutation_review`
5. the frontend handoff bundle and blocked-placeholder guidance

The critical truth boundary remains intact across the archive, review packet,
and canonical docs: `EW-05` is **contract published** but still
`pending-bff`. UI implementation must remain on the blocked placeholder until
Pantheon confirms the live BFF route and live command extension.

---

## 2. Canonical Evidence Crosswalk

| Source | What it establishes |
|---|---|
| `ai-task-archive/tasks/EW-05-OPEN-001.json` | Parent task closed as `done`, archived with final commit `ad263cd9cd5b6f053cc34c004d5bffd36e19a9ec`, acceptance met, and reviewer approval recorded |
| `docs/reviews/2026-04-19-ew-05-open-001-review.md` | Original Codex review findings that had to be corrected before approval |
| `docs/reviews/2026-04-19-ew-05-open-001-claude-review.md` | Final approval that all four publication-truth drifts were fixed and the task met acceptance |
| `docs/bff/EW-05-mutation-review.md` | Published service-level BFF contract for the mutation-review route, command vocabulary, authority rules, and degradation semantics |
| `docs/screens/EW-05-mutation-review.md` | Screen-spec truth for panel structure, CTA gating, and unavailable/stale behavior |
| `docs/examples/EW-05-mutation-review.json` | Example payload aligned to normalized `EvolutionActionType` |
| `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md` | Frontend handoff bundle with explicit blocked-placeholder rule and route path |
| `.coordination/responses/EW-05-mutation-review-contract-ready.yaml` | Coordination packet that publishes the contract bundle while explicitly keeping BFF gates blocked |
| `.coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml` | Frontend task packet kept at `pending-bff`, not `ready` |
| `WORKBENCH_DELIVERY_BACKLOG.md` | Repo-level backlog truth: `EW-05 Mutation Review` is contract-published and still waiting on BFF implementation |
| `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md` | Family-level ordering and activation gate: `EW-05` comes after `EW-02`/`EW-03`/`EW-04` context and still needs live BFF support |

---

## 3. Acceptance Checklist Verification

The archived parent acceptance list was:

1. `mutation review route and command vocabulary are published`
2. `authority fields are explicit`
3. `lovable is no longer limited to shell-only IA for mutation review`

Verification:

| Acceptance item | Verification method | Status |
|---|---|---|
| Mutation review route and command vocabulary are published | Verified `docs/bff/EW-05-mutation-review.md` publishes both the read route and `ApproveMutation` / `RejectMutation` payloads; `.coordination/responses/EW-05-mutation-review-contract-ready.yaml` references the same endpoints and artifacts | PASS |
| Authority fields are explicit | Verified `MutationReviewAllowedActions` plus authority evaluation rules in `docs/bff/EW-05-mutation-review.md`; screen spec and frontend change spec both gate CTAs only from `allowedActions` and `meta.surfaces.mutation_review` | PASS |
| Lovable is no longer limited to shell-only IA for mutation review | Verified `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md` now defines the full page contract, data panels, CTA rules, and blocked-placeholder behavior; `.coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml` captures the future UI work as `pending-bff` rather than leaving it undefined | PASS |

Important boundary check:

| Truth boundary | Verification method | Status |
|---|---|---|
| `EW-05` remains `pending-bff`, not frontend-ready | Verified final Claude review, `WORKBENCH_DELIVERY_BACKLOG.md`, `PANTHEON_FRONTEND_SA.md`, `PACKET_FAMILY.md`, and both coordination packets all agree that production UI work must wait for live BFF route + command extension | PASS |

---

## 4. Dependency Map

### 4.1 What `EW-05-OPEN-001` unblocked

| Downstream consumer | Dependency provided by parent |
|---|---|
| BFF implementation work for Mutation Review | Stable published route path, response shape, command vocabulary, authority rules, and degradation semantics |
| Frontend/Lovable implementation for Mutation Review | Stable screen spec, frontend change spec, example payload, and blocked-placeholder contract |
| Evolution Workbench family packetization | Truthful `EW-05` row in `PACKET_FAMILY.md`, `LOVABLE_MASTER_SA.md`, and `WORKBENCH_DELIVERY_BACKLOG.md` |
| Review and acceptance follow-up slices | Archived review trail and canonical handoff bundle now exist for later support work and reviewer cross-checks |

### 4.2 Remaining live-gate dependencies

`EW-05-OPEN-001` did **not** close the following gaps:

| Remaining gate | Why it still blocks activation |
|---|---|
| `GET /api/v1/operator/mutation-review/{decision_id}` live in Pantheon BFF | Frontend cannot leave placeholder mode until the route returns the published field shape |
| `POST /api/v1/operator/commands` accepts `ApproveMutation` / `RejectMutation` live | Mutation Review CTAs are contractual only until the operator command extension is implemented |
| Live read projection includes `allowedActions.canApproveMutation` / `canRejectMutation` | CTA visibility must come from the BFF response; the client cannot infer it |

### 4.3 Workbench ordering dependencies

Per `docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md`,
`EW-05 Mutation Review` stays downstream of the read-only Evolution context:

| Upstream context | Relationship to `EW-05` |
|---|---|
| `EW-02 Evolution Center` | Provides the stable `decision_id` and core decision context consumed by Mutation Review |
| `EW-03 Lineage View` | Establishes the read-only evidence and lineage context before approval/rejection actions extend it |
| `EW-04 Inspiration Graph` | Family ordering keeps `EW-05` after the broader read-only review context is stable, even though both `EW-04` and `EW-05` still wait on BFF activation |

---

## 5. Verification Snapshot

Current repo checks performed during this sidecar run:

| Check | Result |
|---|---|
| Parent archive exists and shows final `done` closure | PASS |
| Final approval packet still matches archived review note | PASS |
| Coordination packets remain aligned to `pending-bff` truth | PASS |
| Targeted regression tests | `pytest -q services/control-plane/bff/test_ew05_mutation_review_contract.py services/control-plane/bff/test_governance_command_submission.py` → `8 passed, 5 warnings in 1.61s` |

Residual note:

- The test run emitted existing Pydantic v2 deprecation warnings from
  `services/control-plane/bff/command_queue.py:53` (`dict()` -> `model_dump()`).
  This sidecar does not treat that as an `EW-05` acceptance blocker because the
  parent task is already closed and the warnings do not contradict the
  published Mutation Review contract.

---

## 6. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this sidecar file is introduced for `EW-05` |
| No canonical truth edited | PASS | No L0/L1/L2 canonical record is changed by this sidecar |
| Parent acceptance accurately summarized | PASS | Cross-checked against archive + final Claude approval review |
| `pending-bff` boundary preserved | PASS | Cross-checked against backlog, frontend SA, packet family, and coordination packets |
| Dependency map distinguishes published contract vs. still-blocked live gate | PASS | Sections 4.1 through 4.3 |

---

## 7. Handoff to Reviewer (`Claude`)

This sidecar is ready for review as the acceptance packet for the already-closed
`EW-05-OPEN-001` parent task.

What it gives you:

1. A concise acceptance closure summary anchored to the archived parent record.
2. A dependency map separating what `EW-05-OPEN-001` actually published from
   the BFF/live gates it did **not** close.
3. A reviewer-ready check that the repo still truthfully describes Mutation
   Review as `contract published` and `pending-bff`.

Suggested approval message:

> Support packet complete. It accurately summarizes the closed EW-05 contract publication work, preserves the pending-bff boundary, and cleanly maps the remaining BFF activation gates.

---
*Generated by Codex as a sidecar `acceptance_packet` helper for `EW-05-OPEN-001`. This file is a support artifact and does not modify canonical truth.*
