# TJ-E2E-007 - Live SSE And Attention Model

Owner: Claude
Reviewer: Antigravity
Wave: 3
Repository: `ajoe734/pantheon` and `ajoe734/execute-plans`
Dependencies: `TJ-E2E-005`, `TJ-E2E-006`

## Goal

Provide revisioned journey SSE, freshness, reconnect/dedup, stalled detection
and Cockpit Needs Attention monitoring.

## Required work and acceptance

- Implement cursor/Last-Event-ID semantics and snapshot refetch on revision gaps.
- Display stale state when live transport is unavailable.
- Configure stage/environment stalled thresholds and alert severities.
- Prove disconnect, reconnect, duplicate, out-of-order and lag behavior.
- Merge separate scoped PRs in each affected repository and record both SHAs.
