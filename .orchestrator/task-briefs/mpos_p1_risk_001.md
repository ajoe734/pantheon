# Task Brief: MPOS-P1-RISK-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Create first class RiskPolicy evaluator contract
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Owner closeout in progress - reviewer approved all 5 acceptance criteria; closeout verification now has 123 focused tests passing across the RiskPolicy evaluator and consuming services.

## Summary
把 risk_policy_ref 升級為可執行的統一風控 evaluator contract，讓 optimizer、promotion、deployment、runtime manager 都能用同一套風控判斷。

## Review
- Claude approved this task on 2026-06-09.
- Reviewer evidence: `/tmp/mpos_p1_risk_001_review.md`.
- Approval summary: RiskPolicy evaluator is implemented in `services/capital/risk_policy.py`, exposed through governance, and consumed by optimizer, deployment, promotion, and both runtime manager paths.
- Veto precedence: `test_risk_veto_precedes_committee_escalation` verifies risk veto outranks committee escalation.

## Closeout Evidence
- Owner: Codex.
- Verification: `python3 -m pytest services/capital/test_risk_policy.py services/control-plane/governance/test_deployment_plan.py services/deployment/test_service.py services/execution/runtime-manager/test_runtime_manager_risk_policy.py services/optimizer-svc/test_portfolio_synthesis.py services/promotion/test_service.py services/runtime-manager/test_runtime_manager.py`
- Result: 123 passed in 19.04s on 2026-06-09.
- Pending terminal closeout: merge the task closeout PR into `dev`, then run `AI_NAME=Codex ./scripts/ai-status.sh done MPOS-P1-RISK-001 "<checkpoint>"`.
