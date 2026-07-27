# Task Brief: L12-SIGNOFF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Install protected Human-Ops closeout verdict enforcement
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Independent review rejects sequence-7 / merged PR #4206 on one blocking active-lease authority-source bypass. loop_done_guardrail._protected_forbidden_roots derives the candidate worktree only from PANTHEON_WORKTREE_ROOT / ORCH_WORKSPACE_PATH, but ai_status.validate_active_status_command_lease treats workspace_root as optional: with the current valid ORCH_RUN_ID, unsetting both workspace variables passed command-runtime, status-root, and active-lease validation. In an independent temp replay, an external policy whose ledger was under that omitted leased worktree was accepted; after revocation, truncating the candidate-owned JSONL tail to the signed issue record restored review_approved verification (candidate_tail_truncation_restores_approval=true). Owner suites still pass (275 tests, 31 subtests), demonstrating missing coverage. Required: source the worker workspace authority from canonical supervisor lease/runtime metadata so it cannot be erased by the candidate, or fail closed for active worker mutations when no workspace binding is present; ensure the canonical leased path is included in verifier forbidden_roots before manifest/policy/ledger access; add governed-command regressions for a valid ORCH_RUN_ID with both workspace envs missing/blank plus the revoked/consumed tail-truncation cases; rerun focused suites, re-cut evidence/checksum/source hashes, merge a follow-up PR to dev, and resubmit.

## Summary
在 final closeout 前安裝機器守門：受保護、可撤銷、不可重播的 Human/Ops 判決必須綁定 exact catalog、manifest、target 與部署 identity；fleet 不得自行簽發。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
