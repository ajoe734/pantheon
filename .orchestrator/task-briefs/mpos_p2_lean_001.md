# Task Brief: MPOS-P2-LEAN-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden LEAN runtime adapter contract for approved artifact only execution
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Owner closeout confirmed implementation PR #1251 and closeout PR #1253 merged to dev; final post-merge record should merge, then Codex can run `ai-status.sh done`.

## Summary
強化 LEAN execution substrate contract，證明 runtime 只吃 approved artifact、approved config、pool risk policy、RuntimeBinding，且 broker credential/runtime state/PnL/position 按 capital pool 隔離。

## Owner Closeout

- Implementation PR: https://github.com/ajoe734/pantheon/pull/1251 merged into `dev` at `2bcc3079503a90bb7962cc0c642d35b94342fc8f`.
- Closeout PR: https://github.com/ajoe734/pantheon/pull/1253 merged into `dev` at `a852d5af8f2a989e8c051e1a3f413e420949b691`.
- Final owner-tip record is based on `origin/dev` at `a852d5af8f2a989e8c051e1a3f413e420949b691` so `ai-status.sh done` can record a task-scoped latest commit after merge.
- Reviewer approval: Claude2 approved the task and returned it to Codex for owner finalization.
- Validation rerun during closeout:
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.execution.lean_runtime.test_bootstrap_contract services.execution.lean_runtime.test_runtime_context services.execution.lean_runtime.test_paper_runtime services.execution.lean_runtime.test_paper_runtime_smoke services.execution.lean_runtime.test_algorithm_smoke` -> 52 tests passed.
  - `PYTHONDONTWRITEBYTECODE=1 python3 services/control-plane/governance/test_paper_runtime_binding.py` -> 37 PASS, 0 FAIL.
  - `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.control-plane.bff.test_p0_paper_operating_loop_smoke` -> 1 test passed.
  - `PYTHONDONTWRITEBYTECODE=1 python3 services/runtime-manager/test_runtime_manager.py` -> 54 tests passed.
