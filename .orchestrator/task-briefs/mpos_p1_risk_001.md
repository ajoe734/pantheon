# Task Brief: MPOS-P1-RISK-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Create first class RiskPolicy evaluator contract
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Implement and validate shared evaluator contract

## Summary
把 risk_policy_ref 升級為可執行的統一風控 evaluator contract，讓 optimizer、promotion、deployment、runtime manager 都能用同一套風控判斷。

## Acceptance
- RiskPolicy evaluator exposes limits for exposure, asset class, liquidity, drawdown, canary scale, and kill switch triggers.
- Optimizer hard veto uses the evaluator or an adapter with equivalent contract.
- Promotion and deployment reject policy violations before runtime binding.
- Runtime manager can verify pool risk policy identity and stage-scoped limits.
- Tests prove risk veto outranks committee aggregator and persona suggestion.

## Owned scope
- `services/capital`
- `services/control-plane/governance`
- `services/optimizer-svc`
- `services/deployment`
- `services/promotion`
- `services/runtime-manager`
- `services/execution/runtime-manager`
