# TJ-E2E-005 - Canonical BFF Read API

Owner: Claude  
Reviewer: Antigravity  
Wave: 2  
Repository: `ajoe734/pantheon`  
Dependencies: `TJ-E2E-004`

## Goal

Expose server-composed list, detail, timeline, graph, resolve, evidence, replay
and metrics contracts under `/bff/management/trade-journeys`.

## Required work and acceptance

- Implement cursor pagination, filters, ambiguity-aware resolve and revisions.
- Return explicit formal/partial/degraded/unavailable and source freshness states.
- Apply row-level scope and identifier-existence protection.
- Add OpenAPI, DTO, authorization, route-shadowing, performance and degraded tests.
- Prove frontend needs no cross-domain join; merge to Pantheon `dev`.
