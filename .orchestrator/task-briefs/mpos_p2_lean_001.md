# Task Brief: MPOS-P2-LEAN-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden LEAN runtime adapter contract for approved artifact only execution
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved by Claude2. Bootstrap contract enforces approved artifact/config/risk-policy gates; drain requires resolved RuntimeBinding; HTTP endpoints pool-scoped; live broker locked out. 52+37+1+54 tests pass. Returned to Codex for finalization.

## Summary
強化 LEAN execution substrate contract，證明 runtime 只吃 approved artifact、approved config、pool risk policy、RuntimeBinding，且 broker credential/runtime state/PnL/position 按 capital pool 隔離。
