# LOOP-AUTO-EVO-005 Evidence: Evolution Rollback Follow-Through

**Task:** Prove evolution rollback and follow-through
**Date:** 2026-06-27
**Owner:** Claude2
**Branch:** task/LOOP-AUTO-EVO-005
**Depends on:** LOOP-AUTO-EVO-004 (dispatch worker — commit 39918fbe)

---

## Deliverables

| Artifact | Description |
|---|---|
| `services/evolution/test_evo_005_rollback_followthrough.py` | 20 unit+integration tests covering rollback follow-through path |
| `docs/deployment/evidence/loop-auto-evo-005/README.md` | This evidence document |

---

## Acceptance Verification

| Criterion | Result | Evidence |
|---|---|---|
| Evidence proves approved rollback command reaches runtime-manager or deployment | **PASS** | `test_end_to_end_evolution_freeze_to_runtime_rollback` exercises full chain: evolution freeze → rollback-followthrough → `RuntimeManagerService.rollback()` → retired old binding + active new binding |
| BFF shows proposed → reviewed → approved → dispatched → executed stages | **PASS** | 8 tests in `TestBffStageVisibility` verify each stage transition; `test_observation_report_shows_executed_decision` confirms all 5 stages visible in observation-report |
| Failure path records blocked reason and retry state | **PASS** | 5 tests in `TestRollbackFollowthroughFailurePaths` + 2 blocked-reason tests; each verifies the error detail identifies the blocking condition |

---

## Test Run

```
python3 -m pytest services/evolution/test_evo_005_rollback_followthrough.py -v

============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
collected 20 items

TestRollbackFollowthroughFailurePaths::test_requires_approved_state_proposed_returns_422 PASSED
TestRollbackFollowthroughFailurePaths::test_requires_approved_state_reviewed_returns_422 PASSED
TestRollbackFollowthroughFailurePaths::test_requires_active_binding_id                   PASSED
TestRollbackFollowthroughFailurePaths::test_requires_freeze_action_type                  PASSED
TestRollbackFollowthroughFailurePaths::test_duplicate_execution_returns_422               PASSED
TestBffStageVisibility::test_proposed_stage_visible                                       PASSED
TestBffStageVisibility::test_reviewed_stage_visible                                       PASSED
TestBffStageVisibility::test_approved_stage_visible                                       PASSED
TestBffStageVisibility::test_dispatched_stage_visible_via_execution_result                PASSED
TestBffStageVisibility::test_executed_state_visible                                       PASSED
TestBffStageVisibility::test_full_review_chain_visible                                    PASSED
TestBffStageVisibility::test_observation_report_shows_executed_decision                   PASSED
TestBffStageVisibility::test_boundary_query_shows_runtime_rollback_followthrough          PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_rollback_command_parameters_carried_in_execution_result PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_runtime_manager_rollback_replace_strategy               PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_runtime_manager_rollback_pause_then_replace_strategy    PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_runtime_manager_rollback_liquidate_then_replace_start_paused PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_end_to_end_evolution_freeze_to_runtime_rollback         PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_rollback_blocked_reason_surfaced_on_terminal_binding    PASSED
TestRollbackFollowthroughRuntimeManagerIntegration::test_rollback_blocked_reason_surfaced_on_missing_binding     PASSED

20 passed in 3.61s
```

---

## Architecture Notes

### Follow-Through Chain

```
EvolutionDecision (approved)
  │
  ▼
POST /api/evolution/proposals/{id}/rollback-followthrough
  │  requires: active_binding_id, actor_role=evolution_controller|operator
  │
  ▼
EvolutionController.execute_approved(freeze_mode=ROLLBACK)
  │  emits: RollbackCommand { target_binding_id, rollback_action_type,
  │                            capital_pool_id, fallback_artifact_id }
  │  mutates: decision → executed, execution_result.plane=governance
  │
  ▼
[Operator / Dispatch Saga picks up RollbackCommand]
  │
  ▼
RuntimeManagerService.rollback({ current_binding_id, action_type, ... })
  │  strategies: replace | pause_then_replace | liquidate_then_replace
  │
  ▼
Old RuntimeBinding → retired
New RuntimeBinding → active (with rollback_parent + rollback_action_type lineage)
```

### Failure-Path Signals

| Failure | Blocked Reason Surfaced |
|---|---|
| `active_binding_id` missing | HTTP 422: "rollback-followthrough requires an active_binding_id" |
| Decision not in `approved` state | HTTP 422: "actor_role is not allowed to execute …" or state mismatch |
| Decision already executed (re-execute) | HTTP 422: decision state ≠ `approved` |
| Non-freeze action via rollback endpoint | HTTP 422 or governance-plane execution (no runtime rollback) |
| RuntimeBinding already retired | `RuntimeManagerError`: "already in terminal state 'retired'" |
| RuntimeBinding not found | `RuntimeBindingError`: "RuntimeBinding not found: {id}" |

### BFF Stage Visibility

All five lifecycle stages are machine-readable from `GET /api/evolution/proposals/{id}` and from `GET /api/evolution/proposals/{id}/observation-report`:

| Stage | Signal |
|---|---|
| `proposed` | `decision_state == "proposed"` |
| `reviewed` | `decision_state == "reviewed"`, `review_chain[*].step_type == "reviewed"` |
| `approved` | `decision_state == "approved"`, `review_chain[*].step_type == "approved"` |
| `dispatched` | `execution_result.execution_ref_id` set (after execute) |
| `executed` | `decision_state == "executed"`, `execution_result` populated, `followthrough_refs` has `dispatch_command` |
