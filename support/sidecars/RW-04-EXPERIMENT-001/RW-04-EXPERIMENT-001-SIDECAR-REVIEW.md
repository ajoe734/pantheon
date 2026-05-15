# RW-04 Sidecar Review Packet

**Sidecar task:** `RW-04-EXPERIMENT-001-SIDECAR-REVIEW`
**Parent task:** `RW-04-EXPERIMENT-001` - Publish Experiment Launch lifecycle and async run contract
**Packet type:** `review_packet` (support artifact only - does not modify canonical truth)
**Prepared by:** `Codex2`
**Reviewer:** `Codex`
**Prepared at:** `2026-04-19`

---

## Status Summary

| Field | Value |
|---|---|
| Parent status | `done` (archived after formal finalization) |
| Parent owner | `Claude` |
| Parent reviewer | `Codex` |
| Parent acceptance | `launch and run status routes are published`; `async state machine and cancel authority are explicit`; `experiment history no longer depends on inferred runtime state` |
| Parent canonical contract | `docs/bff/RW-04-experiment-launch.md` |
| Parent example payloads | `docs/examples/RW-04-experiment-launch.json` |
| Parent final review | `docs/reviews/2026-04-19-rw-04-experiment-launch-review.md` |
| Parent archive snapshot | `ai-task-archive/tasks/RW-04-EXPERIMENT-001.json` |
| Current sidecar objective | provide a compact reviewer handoff packet for the already-completed parent task |

---

## Sidecar Scope

This packet exists only to help the assigned reviewer validate the current `RW-04` state without
re-reading the full task history.

- no canonical truth is introduced here
- no runtime, registry, governance, or L1 policy implementation is changed here
- the parent task and its archived snapshot remain the source of record for actual `RW-04` delivery

---

## Parent Delivery Snapshot

The parent task is already complete and archived. The delivered canonical package is:

1. `docs/bff/RW-04-experiment-launch.md` publishes the launch route, history route, detail route,
   cancel route, canonical lifecycle states, legal transition graph, illegal transitions,
   terminal-state cancel invariants, read models, degradation semantics, and durable-history
   requirements.
2. `docs/examples/RW-04-experiment-launch.json` publishes example launch, history, running detail,
   terminal detail, cancel response, and queued-cancel response payloads.
3. `docs/reviews/2026-04-19-rw-04-experiment-launch-review.md` records the formal reviewer
   conclusion that all three acceptance criteria were met.
4. `ai-task-archive/tasks/RW-04-EXPERIMENT-001.json` records the finalized `done` state, the full
   review/handoff chain, and the final delivery commit metadata.

This sidecar therefore summarizes a finished parent task rather than an open review dispute.

---

## Review History Snapshot

The archived parent handoff chain shows a short but important review loop:

1. **Initial gap:** the contract originally listed lifecycle states but did not make the legal
   transition graph or terminal `allowedActions.canCancel=false` invariants explicit.
2. **Second gap:** the first fix still contradicted itself by treating `queued -> canceled` as
   illegal while also returning `status: queued` with `canCancel: true` from the launch response.
3. **Resolved final state:** the final parent handoff and formal review aligned the contract and
   example payloads on these semantics:
   - `queued -> canceled` is legal for pre-execution cancel
   - `allowedActions.canCancel` may be true only in non-terminal cancelable states
   - terminal payloads and cancel responses must report `canCancel: false`
   - experiment history must come from persisted run records rather than inferred live worker state

The reviewer does not need to reopen those findings. They are already resolved in the parent's
archived final state.

---

## Canonical Evidence Crosswalk

| Canonical source | What it establishes |
|---|---|
| `docs/bff/RW-04-experiment-launch.md` | canonical launch/history/detail/cancel routes, lifecycle graph, cancel authority rules, and persisted history requirement |
| `docs/examples/RW-04-experiment-launch.json` | example payloads for queued launch, running detail, terminal detail, normal cancel response, and direct queued-cancel response |
| `docs/reviews/2026-04-19-rw-04-experiment-launch-review.md` | reviewer-confirmed acceptance: routes published, state machine explicit, and history truth persisted rather than inferred |
| `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` | `RW-04` is represented as `contract-published — pending BFF implementation` rather than blocked |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | `RW-04` is represented as `contract-published` / `pending-bff placeholder only`, consistent with the parent review outcome |
| `ai-task-archive/tasks/RW-04-EXPERIMENT-001.json` | archived terminal truth, including the reviewer feedback loop and final delivery commit |

---

## Consistency Read

Unlike some other sidecar review slices, this one does **not** expose a remaining repo-visible
contradiction between the canonical contract and the frontend-facing summaries.

Current cross-document read:

- `docs/bff/RW-04-experiment-launch.md` says the contract is published
- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` says `RW-04` is
  `contract-published — pending BFF implementation`
- `docs/lovable/PANTHEON_FRONTEND_SA.md` says `/research/experiments` is `contract-published` and
  still placeholder-only until live BFF routes exist

Those statements are consistent with the parent final review and archive snapshot.

---

## Acceptance Readiness Snapshot

Against the parent acceptance recorded in `ai-status.json` / archive:

| Criterion | Current read |
|---|---|
| `launch and run status routes are published` | **Met** — published in `docs/bff/RW-04-experiment-launch.md` |
| `async state machine and cancel authority are explicit` | **Met** — legal transitions, illegal transitions, and terminal `canCancel` invariants are explicit in the contract and examples |
| `experiment history no longer depends on inferred runtime state` | **Met** — contract explicitly requires persisted run records for history |

This sidecar does not identify any remaining blocker against the already-completed parent task.

---

## Downstream Note

The parent final review explicitly records one important downstream dependency:

- `RW-05-ARTIFACT-COMPARE-001` depends on `RW-04` producing stable `experiment_id` and durable
  `artifact_ids[]`

This packet does not change that dependency; it only surfaces it for quick reviewer context.

---

## Reviewer Checklist For Codex

Please verify the following:

1. This sidecar cites only already-existing canonical artifacts and archived task state.
2. The packet correctly describes `RW-04` as already finalized rather than still awaiting canonical
   repair.
3. The review-loop summary accurately captures the two resolved findings: missing transition/cancel
   invariants first, then the queued-cancel contradiction.
4. `PACKET_FAMILY.md` and `PANTHEON_FRONTEND_SA.md` are accurately described here as aligned with
   the published `RW-04` contract state.
5. No non-support files were changed by this sidecar slice.

If all five checks pass, this sidecar can move to `review_approved`.

Suggested approval message:

> Support packet complete. It accurately summarizes the finalized RW-04 state: the experiment launch/history/detail/cancel contract and examples are published, the earlier review-loop contradictions are resolved, and frontend-facing summaries already align on contract-published pending BFF implementation.

---

## Sidecar Constraints

- this file is a support artifact only
- it does not replace the canonical `RW-04` BFF contract
- it does not replace the Research Workbench packet family
- it does not replace the frontend SA
- it does not replace the archived task snapshot or final review record
- parent owner decides whether any part of this packet is later absorbed elsewhere
