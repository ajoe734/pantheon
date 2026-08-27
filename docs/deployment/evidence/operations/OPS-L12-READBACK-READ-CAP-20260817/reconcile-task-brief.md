# Task Brief: OPS-L12-READBACK-READ-CAP-20260817

- Status: review_approved
- Owner: Claude
- Reviewer: Antigravity2
- Repository: ajoe734/pantheon
- Delivery PR: #5001
- Delivery commit: a880d036233e8515641cf76a268dd76f0d42c647

## Why this reconciliation exists

The task's normal owner `done` closeout is blocked by `enforce_delivery_merged_gate`,
not by any defect in the actual delivery. GitHub squash-merged PR #5001 as a
new commit (`a880d036233e8515641cf76a268dd76f0d42c647`, single parent
`c7fe921fd`) whose tree is byte-identical to the task branch tip
(`d3e01fde0b9b2ff45b65e35f4bc197c7c83b2bf5`), but a GitHub squash-merge
commit is never a git descendant of the branch it squashes. The gate's
`git merge-base --is-ancestor <task-branch-tip HEAD> origin/dev` check
therefore fails even though the delivery is genuinely merged and reviewed:

- `2026-08-20T06:49:04Z` — Antigravity2 independently reviewed and approved
  the exact merged head, confirming `_http_json`'s read cap was raised to
  16MiB in `tests/integration/l12/test_current_research_loops_deployed_e2e.py`,
  the PR carries a complete `evidence.json`, and no product code changed.
- `git diff a05e1646c..a880d036` on the evidence files shows only additive,
  matching content, and `a880d036233e8515641cf76a268dd76f0d42c647` is a
  confirmed ancestor of `origin/dev`.

This document lets a Human/Ops actor reconcile through the explicit,
audited `reconcile_merged_done` recovery path instead of the ancestry gate,
which does not yet handle squash-merge commit identity.

Verified locally against the exact regex/substring checks
`validate_merged_done_evidence` enforces before opening this PR.
