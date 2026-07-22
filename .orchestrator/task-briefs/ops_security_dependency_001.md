# Task Brief: OPS-SECURITY-DEPENDENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile and remediate current dependency alerts
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude
- Next: Codex2 revalidation passed on current dev; PR #3975 is ready for Claude's independent security review.

## Summary
重新綁定 20 個 Dependabot alerts 到 current dev reachable graph；修復或 fail-closed 隔離 MLflow/Ray/Torch critical/high，並以 commit/path evidence 清掉已刪除 FE manifest 的歷史 alert。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Commit Scope

scope:
- .github/pantheon-stage0-matrix.json
- .github/workflows/dependency-alert-reachability.yml
- .orchestrator/task-briefs/ops_security_dependency_001.md
- docker-compose.yml
- services/research/mlflow
- services/research/rllib
- services/research/finrl
- services/research/requirements.txt
- services/registry/experiments
- scripts/security
- docs/04/pantheon_agora_remaining_work_2026-07-22/archive
- docs/bff/execution-tasks/2026-07-22-pantheon-agora-remaining-work/OPS-SECURITY-DEPENDENCY-001.md
