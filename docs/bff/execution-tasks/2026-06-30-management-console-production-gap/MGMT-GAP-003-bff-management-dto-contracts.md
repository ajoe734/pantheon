# MGMT-GAP-003 - BFF Management DTO Contract Hardening

Owner: Claude2
Reviewer: Codex
Batch: 2
Fleet lane: BFF/control-plane contract

## Problem

The former missing endpoints now appear in OpenAPI, but production FE wiring
requires stable DTOs, explicit degraded envelopes, and contract tests.

## Scope

Harden the BFF contracts for:

- `/bff/management/data-sources`
- `/bff/management/permissions`
- `/bff/management/memory-governance`
- `/bff/management/consult-rules`
- `/bff/lineage`
- `/bff/workflows`
- `/bff/hooks`
- `/bff/knowledge`

For each endpoint:

- confirm auth and CORS behavior;
- document response envelope;
- ensure empty source is explicit degraded/unavailable, not ambiguous `[]`;
- add contract tests;
- keep OpenAPI current.

## Non-Scope

- Do not add FE fallback rows to satisfy tests.
- Do not remove old compatibility routes unless a caller audit proves they are
  unused.

## Acceptance

- BFF tests pass for all endpoint success/degraded shapes.
- OpenAPI includes the documented schemas.
- Hosted curl with dev operator auth returns 200 for each endpoint.
- FE integration can consume the DTO without custom synthetic projections.
