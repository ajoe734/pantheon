# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Independent review rejects the merged PR #4183 + follow-up PR #4205 delivery on one blocking split-root authority-boundary defect. scripts/loop_done_guardrail.validate_protected_closeout_transition passes only the immutable command ROOT and PANTHEON_STATUS_ROOT as forbidden_roots to load_verifier_service; it omits the supervisor-bound PANTHEON_WORKTREE_ROOT/ORCH_WORKSPACE_PATH even though that worktree is candidate-controlled. Independent temp probe: an external protected policy whose ledger_path was inside the task worktree was accepted; revocation initially failed verification as expected, but truncating the candidate-owned JSONL ledger to the signed issue record restored approval verification (candidate_tail_truncation_restores_approval=true). Supplying the worktree as a forbidden root rejected the same configuration. Required: derive policy/ledger forbidden roots from the validated split-root bindings (bound worktree, command root, status root), fail closed on conflicting/relative bindings before policy or ledger access, add regressions proving policy/ledger paths under either worker binding are rejected and candidate tail truncation cannot restore a revoked/consumed verdict, re-run the four focused suites, re-cut evidence.json/evidence.sha256/source hashes with this rejection, merge a follow-up PR to dev, and resubmit. Existing verification otherwise passed: 269 tests + 23 subtests, schema/checksum/source hashes/py_compile/diff check, and PR #4183/#4205 merge/check evidence.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
