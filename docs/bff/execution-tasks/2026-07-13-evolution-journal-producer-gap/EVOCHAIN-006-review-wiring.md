# EVOCHAIN-006: Console Mutation Review Wiring

Status: implemented

Task: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`
(Wave 0, owner Claude, reviewer Codex2)

## Scope

Wire the console mutation-review actions (review / approve / reject /
execute on an `EvolutionDecision` proposal) through BFF commands to the
existing evolution service proposal APIs, so action outcomes project back
onto the same formal Evolution Journal entry as a status transition.

## What was already wired (verified, unchanged)

- `ApproveMutation` / `RejectMutation` BFF commands already called
  `POST /api/evolution/proposals/{id}/approve|reject` end to end (payload
  model -> `OperatorCommand` normalization -> `_validate_*` gate ->
  `command_executor._execute_*` -> evolution service).
- `services/evolution/main.py` already implements the full
  `review|approve|reject|cancel|execute` proposal lifecycle
  (`/api/evolution/proposals/{decision_id}/...`) with actor-role
  enforcement per `EVOLUTION_REVIEW_AND_THRESHOLDS.md`; no changes were
  needed there.
- `services/control-plane/bff/main.py` composes the Evolution Journal
  `mutation_review` entry directly from `read_store.get_evolution_decision_by_id`
  (`_evolution_journal_mutation_review_item`), so once a command mutates the
  decision at the evolution service, the next journal read reflects the new
  `decision_state` on the same `decision_id` — no separate journal-write step
  was required.

## Gap closed by this task

The console only had `ApproveMutation`/`RejectMutation` wired; `review` and
`execute` were missing. Added, mirroring the existing pattern exactly:

- `CommandType.REVIEW_MUTATION` ("ReviewMutation") and
  `CommandType.EXECUTE_MUTATION` ("ExecuteMutation")
  (`services/control-plane/bff/models.py`).
- `ReviewMutationCommandPayload` (`decision_id`, `approval_decision_id`,
  `note`) and `ExecuteMutationCommandPayload` (`decision_id` + the same
  execution-context fields `ExecuteEvolutionAction` already accepts)
  (`services/control-plane/bff/models.py`).
- Command normalization branches in `_normalize_operator_command_payload`
  (`services/control-plane/bff/main.py`).
- `_validate_review_mutation` / `_validate_execute_mutation`, gated by two
  new projection flags on `_mutation_review_allowed_actions`:
  `canReviewMutation` (true only when `decision_state == "proposed"` and the
  operator holds a review role for the risk level) and `canExecuteMutation`
  (true only when `decision_state == "approved"` and the operator holds an
  execution role) (`services/control-plane/bff/main.py`).
- `_execute_review_mutation` / `_execute_execute_mutation` in
  `services/control-plane/bff/command_executor.py`, dispatching to
  `POST /api/evolution/proposals/{decision_id}/review` and
  `.../execute` respectively — same governance-URL resolution and
  actor-context handling as the existing mutation executors.
- Catalog entries for `ReviewMutation` / `ExecuteMutation` in
  `services/control-plane/bff/action_catalog.py` so `/bff/v1/commands`
  (the final command contract) enforces the same `requires_approval`
  evidence gate as `ApproveEvolutionDecision`/`ExecuteEvolutionAction`.

## Role gates (BFF-level, evolution service re-validates `actor_role`)

- `canReviewMutation`: `reviewer`/`approver`/`admin` (low/medium risk),
  `approver`/`admin` (high risk) — mirrors the existing approve/reject
  risk-tiered role tables.
- `canExecuteMutation`: `operator`/`admin` (flat, not risk-tiered — matches
  `EXECUTION_ROLES = {evolution_controller, operator}` in
  `services/control-plane/governance/evolution_decision.py`).

## Verification

```sh
cd services/control-plane/bff
python3 -m pytest test_governance_command_submission.py test_ew05_mutation_review_contract.py test_command_executor.py -q
python3 -m pytest tests/test_bff_b3_evolution_journal.py -q
```

New/updated tests:

- `test_command_executor.py::TestMutationReviewExecutors` — added
  `test_review_mutation_governance_api`,
  `test_review_mutation_requires_approval_decision_id`,
  `test_execute_mutation_governance_api`.
- `test_ew05_mutation_review_contract.py` — added
  `test_mutation_review_review_action_allowed_when_proposed`,
  `test_mutation_review_execute_action_allowed_when_approved`, and extended
  the existing contract assertion to cover `canReviewMutation` /
  `canExecuteMutation` on the `evo-dec-88f3a2c1` seed (`reviewed` state, so
  both are correctly `False`).
- `test_governance_command_submission.py` — added
  `test_submit_command_accepts_review_mutation_published_payload`,
  `test_submit_command_accepts_execute_mutation_published_payload`.

Pre-existing failures unrelated to this change (`test_cors_origin_env_parser_trims_and_normalizes`,
`test_mutation_review_returns_503_when_required_evidence_is_unavailable`,
`TestExecuteCommandWithStatus::test_url_error_returns_failed`,
`TestRemediateSentinelInterventionExecutor::test_remediate_sentinel_downstream_failure_returns_failed_status`)
were confirmed present on `dev` HEAD before this change (same failures,
same assertions, unrelated modules) and are out of scope for EVOCHAIN-006.

## Out of scope / residual

- FE console wiring (buttons calling these new command types) is
  `execute-plans` work, covered by `EVOCHAIN-008`/`EVOCHAIN-009` in the
  same packet, not this task's artifact list.
- Live producer chain (threshold breach -> incident -> sweep -> proposal)
  is `EVOCHAIN-001`/`-002`/`-003`; this task only wires the review/approve/
  reject/execute side once a proposal exists.
- Owner: Claude. Reviewer: Antigravity (reassigned from Codex2 after the
  implementation PR merged, because the Codex2 lane became unavailable;
  Antigravity reviewed the merged diff and approved it — see
  `review_notes_zh` on this task in the status board). Expiry: re-check
  after EVOCHAIN-010 (producer-chain live verifier) lands, since that is
  the first task that exercises this wiring against a real (non-seed)
  proposal end to end.

## Closeout note

Implementation merged as PR #3512 (`086f96951`) into `dev` before the
reviewer reassignment above took effect, so that commit's `Reviewer`
trailer reads `Codex2` (correct at the time it was written). This
closeout commit updates the record to reflect the reviewer who actually
approved the task (`Antigravity`) and carries the trailer required by
the current task metadata.
