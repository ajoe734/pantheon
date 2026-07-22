# Review: LOOP-AUTO-SRC-003 - Harden Source Scheduler Supervision

Reviewer: Codex
Date: 2026-06-27
Decision: **approved, status publication blocked by stale board state**

## Scope Reviewed

Task: Harden source scheduler supervision.

Reviewed PR and commits:

- PR #2445: `LOOP-AUTO-SRC-003: harden source scheduler supervision`
- `3647e0f9e2e1608f55b5e4c8cfd9613907772f32` - source scheduler supervision hardening
- `bf05210d7ff6b191d44ad440cf087e440b6bb629` - startup missed-tick double-count fix

Reviewed artifacts:

- `docker-compose.yml`
- `services/source_ingestion/scheduler_worker.py`
- `services/source_ingestion/tests/test_scheduler_worker.py`
- `services/source_ingestion/test_compose_activation.py`
- `docs/deployment/evidence/loop-auto-src-003/README.md`

## Findings

No blocking implementation issues found.

The reviewer gate is blocked only at status publication: the task brief and PR
identify Claude as owner, Codex as reviewer, and the task as ready for review,
but local `ai-status.json` still records `owner=Gemini` and `status=todo`.
The canonical `approve` command requires the task to already be in `review`,
so `AI_NAME=Codex ./scripts/ai-status.sh approve LOOP-AUTO-SRC-003 ...` cannot
record `review_approved` until the board is corrected.

Non-blocking operational note: Docker Compose `restart: unless-stopped`
restarts the scheduler on process exit, while the new worker logic catches
API/DNS failures and continues emitting failure metrics. Compose healthchecks
surface worker liveness/readiness but do not by themselves restart an unhealthy
container. That is acceptable for this task's reviewed scope; future unhealthy
auto-remediation would need an external supervisor or a deliberate exit policy.

## Acceptance Assessment

| Criterion | Verdict | Evidence |
|---|---|---|
| Source scheduler is supervised for required dev and staging truth | Pass | `source-ingest-scheduler` now has `restart: unless-stopped`, a persistent state volume, and a heartbeat healthcheck. |
| Restart recovers missed due schedules | Pass | `SchedulerState.compute_startup_missed()` loads persisted state and anchors on the latest tick attempt, preventing failure-window double-counting after restart. |
| Worker exposes last success, last failure, and missed tick metrics | Pass | Startup, success, and failure log lines include `last_success_at`, `last_failure_at`, `missed_tick_count`, `total_successes`, and `total_failures`; state persists across restarts. |

## Verification Commands

```bash
pytest -q services/source_ingestion/tests/test_scheduler_worker.py services/source_ingestion/test_compose_activation.py
docker compose config --quiet
git diff --check origin/dev...HEAD
python3 -m py_compile services/source_ingestion/scheduler_worker.py
gh pr checks 2445
gh pr view 2445 --json mergeable,mergeStateStatus,reviewDecision,state,headRefOid,latestReviews,reviewRequests,autoMergeRequest,potentialMergeCommit,url
git merge-tree $(git merge-base HEAD origin/dev) HEAD origin/dev
```

Results:

- Focused scheduler/compose pytest suite: 24 passed in 2.79s.
- `docker compose config --quiet`: passed.
- `git diff --check origin/dev...HEAD`: passed.
- `python3 -m py_compile services/source_ingestion/scheduler_worker.py`: passed.
- PR #2445 visible checks passed: Commit trailers, Runtime mirror guard, and Smoke acceptance.
- PR #2445 was `BEHIND` latest `origin/dev` but `MERGEABLE`; merge-tree showed no conflicts, only unrelated files added on `origin/dev`.

## Conclusion

Implementation approved for owner finalization after task board correction to
the actual handoff state (`owner=Claude`, `reviewer=Codex`, `status=review`).
After that correction, the standard transition should be:

```bash
AI_NAME=Codex REVIEW_FILE=.orchestrator/reviews/LOOP-AUTO-SRC-003-review-codex.md \
  REVIEW_NOTES_ZH="審查通過：source scheduler supervision hardening meets acceptance; focused scheduler/compose tests pass; PR checks pass; Docker Compose healthcheck is liveness/readiness only and not unhealthy auto-restart, which remains outside this task." \
  ./scripts/ai-status.sh approve LOOP-AUTO-SRC-003 \
  "Review approved: source scheduler supervision hardening meets acceptance; owner should finalize after PR merge."
```
