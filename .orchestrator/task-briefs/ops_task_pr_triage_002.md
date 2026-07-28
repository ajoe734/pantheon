# Task Brief: OPS-TASK-PR-TRIAGE-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Architecture-level fleet recovery: after OPS-PR-REVIEW-BEFORE-MERGE-GATE-001 lands, drain stale task PR backlog using active Codex2/Codex lanes instead of unavailable Claude. Keep stale-PR classification evidence-based and do not close or merge PRs without task-state proof.
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Reviewer Claude independent verification at PR #4296 exact head d3ece4f503dc557ef7d7bb40fc5dbdc9e8b22396 (base dev, autoMergeRequest=null, not draft, 9/9 checks pass). Cohort: triage-report.json has 24 rows, 24 unique PR numbers, 24 unique head SHAs, 0 null/empty dispositions; tally 12 active-repair + 8 conflict-needs-owner + 4 protected-retain matches brief and README table. Fail-closed closure: closure_candidates=0, close_authorized=true count 0, close-superseded on the committed report emitted {mode:dry-run, actions:[]}. Branch manifest: mode=dry-run-only, task_id=OPS-TASK-PR-TRIAGE-002, candidate_count=1305 == candidates array length, guard string states no deletion is implemented, zero delete-command strings anywhere in the manifest, and CLI exposes only generate/validate/close-superseded with no branch-delete code path. Ancestry re-verified exhaustively, not sampled: all 1305 candidate head SHAs are contained in git rev-list 65802d99bf5ddca1213f6742af74dc125216fa82 (0 unreachable), min age_days 30.038 >= retention_days 30, all dispositions merged-reachable, and 0 overlap with the 24 open-PR head refs. Live exact-head readback of all 24 cohort PRs via gh: every one still OPEN at its recorded head, 0 drift. Tool validate on the committed artifact pair returned 'valid: 24 PRs, 2214 branches, 1305 deletion dry-run candidates'. Re-ran 26 unittests (OK) and py_compile (OK) under the shared venv. sha256 of triage-report.json/README.md/branch-deletion-dry-run.json match evidence.json exactly. Prerequisite confirmed: archive OPS-PR-REVIEW-BEFORE-MERGE-GATE-001 terminal_status=done and merge commit 9e4bb8e1fa9495d8802da58336b05ae68c7756ad is an ancestor of origin/dev. Code diff is identity propagation only (--task-id threaded into classify_pr/build_report/report/manifest/markdown, default preserved as OPS-TASK-PR-TRIAGE-001) plus a new validate guard requiring manifest task_id == report task_id; no classification, closure, or deletion policy changed. PR scope is 8 files, additive except the 4 identity-string lines; git diff --check clean; both commit subjects <=49 chars with full trailers. Owner closeout notes: PR is MERGEABLE but BEHIND dev so auto-merge will not resolve it on its own, and evidence.json still carries status=in_progress, review.status=pending, reviewed_head=null, delivery_via_review_gate=pending to refresh at closeout.

## Summary
盤點並治理 stale task PR，避免 fleet throughput 被舊 PR、失效 review、或無證據 closeout 卡住。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
