# TJ-E2E-011 - SLO And Data-quality Incidents

Owner: Claude  
Reviewer: Antigravity  
Wave: 4  
Repository: `ajoe734/pantheon`  
Dependencies: `TJ-E2E-004`, `TJ-E2E-007`, `TJ-E2E-010`

## Goal

Operationalize materializer lag, correlation completeness, stalled stages,
orphans, terminal conflicts, broker rejects and reconciliation mismatch aging.

## Required work and acceptance

- Publish metrics, dashboards, alert rules and operator runbook.
- Ensure alerts link to the exact Journey and evidence context.
- Add failure injection for lag, orphan, conflict, stale SSE and mismatch.
- Measure paper/canary SLOs and record capacity/rebuild baselines.
- Merge monitoring/config/docs changes with rollback instructions.
