# Task Evidence: SUP-WORKER-FAILURE-SIGTERM-TRANSCRIPT-GATE-20260809

## Summary
Prevent SIGTERM transcript source false positives by enforcing authoritative provider envelope gating and excluding supervisor SIGTERM preemption (`runner_status` = `terminated`, exit code 143) from plain-text regex scanning.

- Plain transcript lines require an authoritative `worker_runner` terminal failure marker (`RUNNER_FAILURE_STATUSES` = `error`, `failed`) before regex classification is allowed.
- `runner_status` = `terminated` (SIGTERM / exit code 143) is explicitly excluded from `RUNNER_FAILURE_STATUSES`, preventing preemption false positives when worker logs contain source code snippets referencing quota/rate-limits.
- Structured provider streams independently admit only explicit terminal result/error/failure envelopes and rejected rate-limit control events.
- Mixed-trust content (assistant prose, tool results, source/diff lines, search-result prefixes, captured orchestrator records, allowed rate-limit notices, SIGTERM logs) is strictly excluded from regex provider-failure classification.

## Live Captured Evidence
- Run ID: `codex-20260809T095151Z-200962bd`
- Captured Log: `20260809T095151094458Z-codex-codex2_2-0d4c19.log:27295`
- Evidence Document: `.orchestrator/evidence/codex_20260809t095151z_200962bd.json`

## Verification
- Provisioned python distribution: `.venv-pantheon/bin/python3`
- Focused supervisor tests: `93 passed, 533 deselected, 10 subtests passed`
- Full supervisor test suite: `626 passed, 74 subtests passed`
- Syntax compile check (`py_compile`): `passed`
- Git whitespace check (`git diff --check`): `passed`

## Rollout & Rollback
- Rollout: Per-task PR auto-merge into `dev`; supervisor picks up the updated `RUNNER_FAILURE_STATUSES` gate on next cycle.
- Rollback: Merge revert PR on `dev` or `master` if worker-failure classification regresses.
