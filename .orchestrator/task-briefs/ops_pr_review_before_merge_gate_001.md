# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: Auto-reassigned OPS-PR-REVIEW-BEFORE-MERGE-GATE-001 away from unavailable lane Codex2 (disabled, paused, sidecar-only, or auth-down); reviewer Codex2 -> Codex.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Owner Closeout Record

- Delivery PR: [#4218](https://github.com/ajoe734/pantheon/pull/4218) MERGED at
  2026-07-28T12:27:55Z, merge commit `9e4bb8e1fa9495d8802da58336b05ae68c7756ad`.
- Exact reviewed head `1deadaed884378eea4455af9eed16ae499020552` is an ancestor of
  `origin/dev`; reviewer-bound `review_file` is
  `docs/deployment/evidence/supervisor/OPS-PR-REVIEW-BEFORE-MERGE-GATE-001/evidence.json`.
- Owner re-verification in the task worktree (2026-07-28):
  - `pytest scripts/git/test_task_review_merge_gate.py scripts/git/test_auto_integrator.py scripts/test_ai_status.py`
    → 253 passed, 31 subtests passed
  - `pytest scripts/git/` → 208 passed
  - `bash -n scripts/git/task_finalize.sh scripts/git/safe_pr.sh` → OK
  - `python -m py_compile scripts/git/auto_integrator.py` → OK
  - `python scripts/git/task_review_merge_gate.py policy OPS-PR-REVIEW-BEFORE-MERGE-GATE-001`
    → `review_before_merge`
  - evidence manifest parses; schema `supervisor_runtime_repair_evidence.v1`, `task_scoped`
- This closeout commit exists because the merged delivery commits carry the
  pre-reassignment trailers (`LLM-Agent: Codex`, `Reviewer: Codex2`), while the
  canonical row is now owner `Claude` / reviewer `Codex`. `ai_status.py done`
  requires the finalizing HEAD to name the current owner and reviewer.
- Under the gate this task delivered, this closeout head is a new exact head:
  it needs a fresh `review_approved` from reviewer `Codex` bound to the new PR
  number and head sha before `auto_integrator.py --execute` may merge it.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
