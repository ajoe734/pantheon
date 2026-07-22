# TJ-E2E-006 - Trade Journey Frontend P0 Workbench

Owner: Antigravity
Reviewer: Claude
Wave: 2
Repository: `ajoe734/execute-plans`
Dependencies: `TJ-E2E-005`

## Goal

Implement `/management/trade-journeys`, detail and resolve routes using only
the canonical BFF journey contract.

## Required work and acceptance

- Build searchable server-paginated list, saved attention views and URL filters.
- Build detail header, stage rail, attention panel, timeline and evidence links.
- Render unknown/incomplete/degraded/freshness honestly on desktop and mobile.
- Cover happy path, risk reject, broker reject, partial fill and recon mismatch.
- Pass unit/a11y/build/Playwright checks and merge to `execute-plans/main`.
