# EW-05-OPEN-001 Review

Reviewer: `Claude`
Date: `2026-04-19`
Disposition: `approved`

## Review against prior Codex findings

All four issues flagged in `docs/reviews/2026-04-19-ew-05-open-001-review.md` are resolved:

1. **pending-bff truth alignment** — `EW-05-mutation-review-contract-ready.yaml` now has `bff_route_live: false`, `command_vocabulary_live: false`, and an explicit `readiness_gate` that blocks frontend production work. `EW-05-mutation-review-lovable-ui-task.yaml` carries `status: pending-bff`. `WORKBENCH_DELIVERY_BACKLOG.md` row describes "contract published — BFF route and command vocabulary pending implementation". All three canonical locations are consistent. ✓

2. **Route path alignment** — `PANTHEON_FRONTEND_SA.md` lines 279 and 459 now both read `/evolution/mutation-review/:decision_id`, matching `docs/screens/EW-05-mutation-review.md` and `FRONTEND_CHANGE_SPEC.md`. ✓

3. **`degraded` surface state removed** — The screen spec degradation table and state-requirements section now enumerate only `fresh | stale | unavailable`, matching the BFF contract and frontend change spec. ✓

4. **action_type normalization** — Example payload `action_type` is `"freeze"` (not `"freeze_canary"`), aligned with the normalized `EvolutionActionType` contract. `target_stage: "canary"` is carried separately in `proposed_changes`. ✓

## Contract quality

- `GET /api/v1/operator/mutation-review/{decision_id}` read route, composed `MutationReviewProjection`, authority signal rules, and staleness semantics are well-specified.
- `ApproveMutation` / `RejectMutation` command vocabulary, preconditions, effects, and error surfaces are complete.
- Write-owner boundary is explicit: BFF does not directly execute downstream follow-through.
- 503 degradation path and `meta.surfaces.mutation_review = "unavailable"` semantics are consistent across BFF contract, screen spec, and frontend change spec.

## Test verification

```
pytest -q services/control-plane/bff/test_ew05_mutation_review_contract.py \
         services/control-plane/bff/test_governance_command_submission.py
7 passed in 1.67s
```

Tests cover: projection contract shape, reviewer vs. approver `allowedActions` visibility, and 503 degradation path.

## Acceptance criteria

| Criterion | Met |
|---|---|
| Mutation review route and command vocabulary published | ✓ |
| Authority fields are explicit | ✓ |
| Lovable no longer limited to shell-only IA for mutation review | ✓ |

## Disposition

APPROVED — task returns to Codex (owner) for finalization to `done`.
