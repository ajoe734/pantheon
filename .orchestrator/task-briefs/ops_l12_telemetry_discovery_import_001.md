# Task Brief: OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Eliminate telemetry unittest discovery loader errors
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Codex2 rejects exact merged PR #4222 tree 55b17612=28e62b8f. Independent positives: base 71aea154b reproduced 197 tests/2 errors/1 skip; merged tree repo-root discovery 280/0/1, regression module 10 OK, repo-root direct unittest 75 OK, foreign-cwd pytest 85 passed; production modules unchanged and checksum passes. Required fixes: (1) acceptance 2 is false as written: from foreign cwd under env -i with no PYTHONPATH, module unittest and direct execution of both files fail ModuleNotFoundError: services; test_discovery_imports._child_env always injects repo root into PYTHONPATH, so add a genuine no-PYTHONPATH regression and make it pass without process-global sys.path mutation, or governably narrow the acceptance/evidence claim. (2) Re-cut evidence via follow-up task PR using actual observed times: committed bytes contain evidence_cut_at/record at 21:55 and record at 22:20 although evidence commit was 21:54:05; bind final follow-up head plus PR #/checks/merge SHA, not anchor d5c8d9a5f, and record exact rerun counts. (3) Make evidence.json validate against schemas/product-evidence.schema.json; current formalized manifest has incompatible delivery, validation, deployment, behavioral, acceptance, residual-risk, integrity, and record_log shapes. (4) Add the previously required fail-closed regression/schema check rejecting future task.evidence_cut_at, validation.validated_at, and record_log timestamps. (5) Preserve no production/config changes. PR #4222 merged at 21:55:32 before canonical handoff/review at 21:58 and had no reviews; merge is retained historical delivery only, not approval.

## Summary
修正 telemetry 完整 unittest discovery 的兩個裸模組 import error，讓乾淨 repo-root 與 package discovery 都能零 loader error。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
