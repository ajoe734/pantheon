# APP-003-PKT001-PUBLICATION-REPLAY-001 Support Note

**Task**: `APP-003-PKT001-PUBLICATION-REPLAY-001`  
**Owner**: `Codex`  
**Reviewer**: `Codex2`  
**Updated**: `2026-04-23`  
**Scope**: keep the remaining `PKT-001-deployment-review` publication replay
explicit, reviewer-visible, and closure-clean without reopening the already
closed Pantheon PKT-001 BFF / contract-alignment slice.

> Support artifact only. This note does not change canonical truth by itself.
> It packages the current replay state, the narrow reviewer boundary, and the
> reason the parent task is now waiting only for review approval.

## Summary

- `APP-003-PKT001-BFF-ALIGN-001` already closed the Pantheon-owned PKT-001 BFF
  gap.
- `docs/reviews/2026-04-22-pantheon-residual-followup-execution-packet.md`
  already materialized this replay residue as a named execution task.
- The replay bundle is now truthful and Git-visible on
  `origin/pkt-004-detail-fix` at
  `2c8ec3f74beddfba1ef73bb4df355b54f9b5cd2f`.
- The remaining broader open item is separate and front-owned:
  `meta.surfaces` still does not fail closed on the required PKT-001 key sets.

## Current Repo Truth

### Pantheon truth remains narrow

- `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`
  now records that:
  - the earlier Git-visible publication blocker is closed
  - PKT-001 list/detail/command routes remain the source of truth
  - runtime SSE stays explicit as the approved inherited `PKT-005` cross-cut
  - the only broader remaining blocker is the front-owned
    `meta.surfaces` validation gap
- `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md`
  preserves the same boundary: no new PKT-001 live-update endpoint is added,
  and any runtime stream stays incremental decoration only.

### Front publication replay is now truthful

Revalidated on `2026-04-23` against `../front-ai-trading-system`:

- current branch: `pkt-004-detail-fix`
- local `HEAD`:
  `2c8ec3f74beddfba1ef73bb4df355b54f9b5cd2f`
- remote tracked branch head:
  `2c8ec3f74beddfba1ef73bb4df355b54f9b5cd2f`
- remote tree at that commit contains:
  - `.coordination/requests/PKT-001-deployment-review-ui-done.yaml`
  - `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-001-deployment-review/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-001-deployment-review/QA_STATUS.md`
  - `docs/pantheon-feedback/PKT-001-deployment-review/UI_DECISIONS.md`

### Published request metadata is now truthful

- both published request files now set:
  - `source_branch: pkt-004-detail-fix`
  - `source_commit: dbc4a16dc0e9f0b8d33e1576908341ea056c660d`
- this keeps the reviewed UI snapshot explicit while publishing the replay
  bundle from later transport commit
  `2c8ec3f74beddfba1ef73bb4df355b54f9b5cd2f`
- the earlier broken local repoint is no longer the current published truth

Working conclusion:

- the parent residual is now closure-clean as a publication-replay slice
- the task is correctly parked in `review`
- approval should stay narrow and should not expand into the separate
  `meta.surfaces` follow-up

## Acceptance Readout

The parent acceptance bar is now met:

1. the follow-up exists as a named execution task
2. the closure criteria point at one truthful Git-visible commit containing the
   request pair and refreshed feedback bundle
3. the published request metadata points back to a real reviewed UI snapshot
4. no truth surface reopens PKT-001 as a missing Pantheon BFF route-family gap

## Reviewer Boundary

When `Codex2` reviews this task, the checks should stay narrow:

- confirm `origin/pkt-004-detail-fix` resolves to
  `2c8ec3f74beddfba1ef73bb4df355b54f9b5cd2f`
- confirm that remote tree contains the request pair plus the full PKT-001
  feedback bundle
- confirm both published request files point `source_commit` to
  `dbc4a16dc0e9f0b8d33e1576908341ea056c660d`
- confirm Pantheon truth surfaces still keep runtime SSE explicit as the
  inherited `PKT-005` cross-cut

Do not reopen these already-closed questions as part of this slice:

- whether PKT-001 still has a Pantheon BFF gap
- whether `GET /api/v1/operator/deployment-plans` exists
- whether runtime SSE belongs to PKT-001 instead of PKT-005
- whether the broader front `meta.surfaces` issue exists

## Immediate Next Step

No further Pantheon implementation change is needed for this task right now.
The correct next move is reviewer action:

- keep `APP-003-PKT001-PUBLICATION-REPLAY-001` in `review`
- wait for `Codex2` to approve the narrow replay evidence
- finalize to `done` only after `review_approved` is recorded
