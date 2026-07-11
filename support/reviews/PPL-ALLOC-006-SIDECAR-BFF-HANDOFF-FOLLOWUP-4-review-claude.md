# Review: PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4

**Reviewer**: Claude
**Owner**: Codex
**Verdict**: Approved

## Scope check

- Diff vs. `origin/dev` merge-base (`f58ce9485`) touches exactly one file:
  `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
  (commit `1c21705a3`, PR #3118, already merged into `dev`).
- No canonical L1/L2 doc, BFF route, runtime, registry, governance, or
  frontend source file is touched. `Boundary` and `Composition And Review`
  sections' non-claims hold.

## Technical claim verification

Cross-checked the concrete, testable claims against
`services/control-plane/bff/main.py` on this branch:

- **Apply proposal gate / `409` recovery row**: the packet requires
  `approval_ref` for any live capital increase and expects a `409` with the
  precondition unchanged. Confirmed at `main.py:24485-24506`
  (`bff_apply_rebalance_proposal`): `increases_live` is computed from
  `stage == "live_running"` and `target_weight > current_weight`; when true
  and no `approval_ref` is present it raises
  `_bff_error(409, ErrorCode.PRECONDITION_FAILED, "human approval required", ...,
  precondition_failed="approval_ref")` without mutating the rebalance. Matches
  the packet's "Never infer approval from a recommendation or
  promotion-review label" and "Show the approval/precondition failure
  unchanged" rules exactly.
- **Create rebalance proposal / dry-run non-durable**: `main.py:24460-24469`
  shows `create_rebalance` builds a `proposal` with `"applied": False` and an
  `emergency_containment` vs `quarterly_rebalance` `proposal_type`, consistent
  with the packet's "Evaluation output with `applied: false`" gate and the
  row-state distinction between `proposal_created` and `apply_submitted`.
- **Emergency containment is server-owned**: `emergency_containment_policy.py`
  is imported and used in `_validate_emergency_containment`
  (`main.py:132`, `main.py:5271-5289`), and `CommandType.EMERGENCY_CONTAINMENT`
  is a distinct registered command validator (`main.py:5332`). This grounds
  the packet's "No sidecar-defined emergency route ... does not authorize a
  workbench-only endpoint" claim — the real containment path already exists
  server-side and the packet correctly declines to invent a parallel one.
- **Idempotency**: `_resolve_final_idempotency_key` /
  `IdempotencyRecord.reserve` machinery exists and is used on both rebalance
  create and apply paths (`main.py:1505-1601`, `24485-24513`), supporting the
  packet's "retry with the same idempotency key; merge an idempotent replay
  by returned review id" recovery rule and acceptance scenario 5.

## Consistency with prior packets

- Builds cleanly on FOLLOWUP-3's query-orchestration/mutation rules rather
  than duplicating them: FOLLOWUP-3 defines
  `recommendation_state, review_state, proposal_state, command_state`;
  FOLLOWUP-4's row state machine (`recommended -> review_submitted ->
  approved|rejected`, `target_calculated -> proposal_created ->
  apply_submitted -> applied_confirmed`) is a compatible refinement, not a
  contradiction.
- The capability-gate table maps 1:1 onto the parent frontend task's scope
  (`docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`):
  paper candidates -> submit gate, real ranking -> evaluate/apply gates,
  quarterly capital -> proposal/review/apply gates, emergency actions ->
  containment gate.
- All ten frontend acceptance scenarios stay consumer-side (rendering,
  gating, id-merge behavior) and none assert a new server guarantee or wire
  field beyond what FOLLOWUP/FOLLOWUP-2/FOLLOWUP-3 and the BFF source already
  establish.

## Notes

- Every fail-closed gate in the table pairs a concrete evidence requirement
  with a "missing fields are `unknown/unavailable`, not false/zero/empty"
  rule, which is the correct default for a consumer-side sketch: it cannot
  silently manufacture policy the BFF hasn't returned.
- No corrections required. Parent owner can absorb directly into
  adapter/component work; scenario-to-test mapping is explicitly left to the
  parent per the packet's own "Composition And Review" section.

LLM-Agent: Claude
