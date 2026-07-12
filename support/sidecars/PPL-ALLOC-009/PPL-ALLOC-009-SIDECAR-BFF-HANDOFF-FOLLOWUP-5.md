# PPL-ALLOC-009 Parent-Owner Intake Handoff

- **Sidecar Task**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`
- **Parent Task**: `PPL-ALLOC-009`
- **Owner / Reviewer**: `Codex` / `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Scope**: support-only; no canonical, runtime, registry, governance,
  frontend, deployment, or capital-state mutation
- **Snapshot date**: 2026-07-12

## Purpose

This packet gives the newly assigned parent owner a fail-closed intake order
for the existing BFF/frontend handoff material. It does not add API contract
truth and does not repeat the hosted acceptance work. The parent remains
`todo`; a dependency absent from the active task list is not thereby `done`.

## Current Durable Snapshot

At this snapshot, `ai-status.json` records:

- `PPL-ALLOC-009`: owner `Antigravity`, reviewer `Claude`, status `todo`.
- `PPL-ALLOC-007`: status `blocked`; its recorded blocker is an unclean
  Execute Plans task branch containing prior-task and unrelated artifacts plus
  an ownership conflict.
- `PPL-ALLOC-002` through `006` and `008` are not present in the active task
  array. Their terminal truth must be established from canonical task archives
  and merged PR evidence, not inferred from absence.

These are coordination facts only. They do not prove a deployed BFF route,
frontend consumption, command completion, authoritative readback, or safe
capital behavior.

## Parent-Owner Intake Order

1. Build a dependency ledger for `PPL-ALLOC-002` through `008`. For every row,
   cite the task archive or reviewed supersession record, task PR, merge SHA,
   validation, and any deployed SHA that its behavior requires.
2. Keep parent closeout blocked while `PPL-ALLOC-007` remains blocked. Resolve
   its Execute Plans ownership/worktree conflict in that repository without
   copying or committing frontend files here.
3. Select one exact merged Pantheon SHA and one exact merged Execute Plans SHA
   for deployment. Record BFF/frontend origins and strict live-mode build
   settings before probing.
4. Run the earlier packet's evidence sequence: paper creation and isolation,
   promotion linkage, allocation universe, governed apply, emergency
   containment, then authenticated desktop/mobile operator flows.
5. Join writes to command status and authoritative Fleet/Capital/binding reads
   using returned IDs, idempotency keys, and correlation references. Treat
   admission, terminal execution, and readback as separate verdicts.
6. Archive sanitized positive and negative evidence plus residual risks. Only
   then may the parent owner request review of the closeout.

## BFF Query And Frontend Handoff Decision Table

| Observation | Parent disposition | Frontend disposition |
| --- | --- | --- |
| Dependency has no cited archive/merged PR | Blocked/unknown | Do not expose completion |
| Write returns admission only | Pending; query command status | Render admitted/pending, not success |
| Command succeeds but authoritative read disagrees or is missing | Fail closeout | Show reconciliation failure/unknown |
| Query is stale, degraded, unauthorized, or unavailable | Preserve exact condition | Distinguish `401`, `403`, stale/degraded, and network failure |
| Strict live request falls back to seed/mock/fixture | Fail hosted proof | Surface failure; no synthetic operational state |
| Desktop passes but mobile fails a required request | Hosted proof incomplete | Keep viewport-specific failure evidence |
| Emergency reduce/freeze/suspend succeeds while promotion/increase probes fail closed | Candidate safety pass | Show containment result and rejected unsafe intent separately |

## Evidence Record Shape

The parent archive should use one row per request or operator action with:

- timestamp, actor/role, frontend and BFF origins;
- deployed Pantheon and Execute Plans SHAs;
- method/path or UI action and viewport;
- sanitized request plus idempotency/correlation/trace identity;
- returned persona, ledger, binding, review, proposal, command, approval, and
  audit identities as applicable;
- HTTP/admission verdict, terminal command verdict, and authoritative readback
  verdict as separate fields;
- required-request/fallback observation and evidence path;
- residual risk, owner, blocking status, and objective recheck condition.

Missing fields remain `unknown`; they must not be reconstructed from labels,
timestamps, page order, or zero/default values.

## Composition Boundary

Owned layer: parent-owner intake ordering, durable-state interpretation,
fail-closed BFF/frontend evidence disposition, and evidence record guidance.

Not changing: L1 canonical truth, schemas or route contracts, BFF/frontend
implementation, task dependency state, deployment configuration, runtime or
registry state, approval state, or capital allocation.

Composes with: the original handoff packet, follow-up-2 readiness sequence,
follow-up-3 stop/go decision matrix, follow-up-4 closeout gap register, and the
parent owner's final closeout archive. Antigravity decides whether to absorb
this support material into `PPL-ALLOC-009`; Claude retains parent review.

## Reviewer Checklist

- [x] Snapshot statements match current durable task state.
- [x] Missing active dependency rows are not treated as terminal success.
- [x] The `PPL-ALLOC-007` blocker remains explicit and is not worked around.
- [x] Admission, execution, readback, and frontend rendering remain distinct.
- [x] No new route, schema, governance, deployment, or capital truth is claimed.
- [x] Output is suitable for parent-owner composition as support material only.

## References

- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`
