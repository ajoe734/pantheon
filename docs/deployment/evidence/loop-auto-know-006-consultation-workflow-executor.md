# Evidence: LOOP-AUTO-KNOW-006 — Consultation Workflow Executor

**Task:** LOOP-AUTO-KNOW-006  
**Title:** Add consultation workflow executor  
**Owner:** Claude  
**Reviewer:** Codex  
**Date:** 2026-06-27  
**Branch:** task/LOOP-AUTO-KNOW-006

## Deliverable Summary

Added `services/consultation/workflow_executor.py` — a durable, supervised
workflow executor that consumes ConsultRequests in actionable states
(submitted / assigned / in_progress / memo_pending) and drives each through
the appropriate committee or red-team workflow to produce a published memo and
a governance gate handoff.

## Acceptance Criteria Verification

### 1. Committee and red-team workflow executes from ConsultRequest

- Executor polls `/api/consult/requests?status=<state>` for all actionable states.
- Requests with `request_type` in `{strategy_review, capital_pool, execution_risk, incident, persona_policy}` are routed to the committee workflow.
- Requests with `request_type` in `{redteam, data_leakage}` are routed to the red-team workflow.
- Each workflow assigns the appropriate participant, posts a transcript event, submits a typed memo (`committee_summary` or `redteam_report`), publishes the memo, and creates a gate handoff.

Test evidence:
```
services/consultation/test_workflow_executor.py::TestExecuteWorkflow::test_committee_workflow_runs_from_submitted_request PASSED
services/consultation/test_workflow_executor.py::TestExecuteWorkflow::test_redteam_workflow_runs_from_submitted_request PASSED
services/consultation/test_workflow_executor.py::TestHelpers::test_committee_types_route_to_primary_reviewer PASSED
services/consultation/test_workflow_executor.py::TestHelpers::test_redteam_types_route_to_red_team PASSED
```

### 2. Memo generation and governance handoff are durable

- All workflow steps check current API state before mutating (idempotent by
  design: skips steps already done on retry).
- Memo is submitted and published in the same tick if absent; subsequent ticks
  find the existing published memo and skip re-creation.
- Gate handoff is created only when no handoff exists for the request; existing
  handoffs are not duplicated.
- The store's lifecycle log and outbox ensure event durability across restarts.

Test evidence:
```
services/consultation/test_workflow_executor.py::TestExecuteWorkflow::test_handoff_idempotent_on_second_tick PASSED
services/consultation/test_workflow_executor.py::TestExecuteWorkflow::test_memo_published_before_handoff_created PASSED
services/consultation/test_workflow_executor.py::TestExecuteWorkflow::test_second_tick_result_is_completed_not_advanced PASSED
```

### 3. Handoffs are consumed exactly once or reported blocked

- Gate handoff creation is guarded by a list-before-create check: if a handoff
  already exists for the request, no second handoff is created.
- Requests with unknown `request_type` are reported as `blocked` with a
  diagnostic message; they are not silently skipped.
- No published memo → `blocked` outcome, not silent completion.

Test evidence:
```
services/consultation/test_workflow_executor.py::TestExecuteWorkflow::test_unknown_request_type_is_blocked PASSED
services/consultation/test_workflow_executor.py::TestHelpers::test_non_actionable_status_skipped PASSED
```

## Test Run Results

```
$ python3 -m pytest services/consultation/test_workflow_executor.py -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3
collected 17 items

test_workflow_executor.py::TestExecuteWorkflow::test_already_published_request_is_skipped PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_committee_workflow_runs_from_submitted_request PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_handoff_idempotent_on_second_tick PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_memo_published_before_handoff_created PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_redteam_workflow_runs_from_submitted_request PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_run_tick_processes_all_pending_requests PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_run_tick_with_no_pending_requests PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_second_tick_result_is_completed_not_advanced PASSED
test_workflow_executor.py::TestExecuteWorkflow::test_unknown_request_type_is_blocked PASSED
test_workflow_executor.py::TestHelpers::test_committee_types_route_to_primary_reviewer PASSED
test_workflow_executor.py::TestHelpers::test_gate_routing PASSED
test_workflow_executor.py::TestHelpers::test_memo_type_routing PASSED
test_workflow_executor.py::TestHelpers::test_non_actionable_status_skipped PASSED
test_workflow_executor.py::TestHelpers::test_redteam_types_route_to_red_team PASSED
test_workflow_executor.py::TestHelpers::test_stable_id_differs_per_request PASSED
test_workflow_executor.py::TestHelpers::test_stable_id_is_deterministic PASSED
test_workflow_executor.py::TestHelpers::test_unknown_type_returns_none PASSED

============================= 17 passed in 3.78s ==============================

$ python3 -m pytest services/consultation/smoke_test.py services/consultation/test_models.py -v
12 passed in 3.51s
```

## Non-goals

- No live-capital execution.
- No approval gate bypass.
- No panel-only closure.
- No seed fixture as live proof.
