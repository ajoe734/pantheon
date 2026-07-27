# Task Brief: OPS-PUBLISH-ENGINE-UNIFICATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Unify nightly publish cut engine across Pantheon and execute-plans
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Validate both repository heads, publish reviewable PRs, and obtain Codex2 exact-head review before merge.

## Summary
把 pantheon 與 execute-plans 的 nightly publish helper 收斂成同一份契約，修掉 pipefail/SIGPIPE 141 與 inadmissible publish-to-deploy dispatch。

## Accepted Scope
- Producer-side latest-tag selection must not depend on an early-closing
  `head` consumer under `set -euo pipefail`.
- Both helpers emit the same `publish_branch=` and `release_tag=` result
  contract, and both workflow consumers parse it without a pipeline.
- A deterministic 12,000-tag regression must reproduce the historical exit
  141 and prove one cut plus repeated no-op behavior.
- Nightly publish workflows create immutable promotion inputs only. They do
  not dispatch a dev deployment; exact FE/BFF pair admission and switching
  belong to the downstream governed release-controller layer.
- No hosted deployment is part of this task.

## Durable Progress
- Pantheon anchor: `b9be76c6aea2fedc4f085da2c1050d446ec0e481`.
- execute-plans anchor: `ec9ca4d7eb255b72366c43076ad477a3daa77fcf`.
- Pantheon draft PR: `https://github.com/ajoe734/pantheon/pull/4255`.
- execute-plans draft PR: `https://github.com/ajoe734/execute-plans/pull/557`.
- Task evidence:
  `docs/deployment/evidence/release-platform/OPS-PUBLISH-ENGINE-UNIFICATION-001/evidence.json`.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
