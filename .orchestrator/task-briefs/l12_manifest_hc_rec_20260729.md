# Task Brief: L12-MANIFEST-HC-REC-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest reconciliation health heartbeat workstream
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Exact-head review of PR #4332 at a73d583502b382c4d64f7d0d09e258c3b5ddedfe requires one evidence correction: evidence.json records validation.validated_base_sha=6fe626252f19adf9223c5f35268ef43dc74ec445, which is not a repository object and contradicts the actual implementation anchor 982f224a2953d948fcc46d0342c611f3d0b22389 parent/PR merge-base 6fe626252d10af27eed0aba79530506d192857ca. The manifest says contradicted proof fails closed. Correct the base SHA, reseal evidence.sha256, append a correction record, resync/push the PR, and request fresh exact-head review. Independent reruns otherwise passed: ProductEvidenceManifest schema and both checksums; 107 reconciliation/foundation tests; 10 adjacent worker/Compose tests; py_compile; docker compose config; all three resolved healthcheck commands and separate-process heartbeat probes. Preserve the explicit no-hosted-proof boundary.

## Summary
補三個 reconciliation drift worker 的 health/heartbeat 證據或 waiver，輸出給 L12-MANIFEST-001 owner 整合。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
