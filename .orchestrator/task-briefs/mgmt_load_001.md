# Task Brief: MGMT-LOAD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Management load baseline and route-ready probes
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Delivery confirmed merged on both sides — execute-plans PR #130 (commit 7cd6060) and pantheon PR #2661 (commit 4ba70598) are both ancestors of their respective `dev` branches. Probe avoids `networkidle` (uses heading/API milestones) and fanout probe covers /health, /bff/management/evidence, /bff/alerts, /bff/approvals, /bff/jobs. Handed off to Codex for review.

## Summary
建立 /management/evidence hosted browser route-load baseline 與 BFF fanout baseline；readiness 改用 heading/row/API milestone，不用 networkidle 判定 SSE 頁面就緒。
