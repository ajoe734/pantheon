# PPLG-003 - Persona Fleet Readiness Projection And Payload Cleanup

Priority: P0

Area: BFF read model, management console performance

Depends on: `PPLG-001`

## Goal

Make Persona Fleet show paper setup, paper evaluation, review, capital scope,
and live status clearly while removing duplicate heavy payload branches.

## Required Work

- Add a `PersonaReadinessProjection` to persona fleet rows.
- Surface:
  - paper runtime status
  - evaluation status
  - review status
  - capital scope
  - live status
  - setup failed step and repair action
- Remove duplicate `items`, `data.items`, and `data.persona_fleet` style payload
  bloat where possible without breaking contract compatibility.
- Remove duplicated snake/camel heavy nested fields from row payloads or move
  them behind detail endpoints.
- Add payload size and latency tests for persona fleet.

## Acceptance Criteria

- Fleet rows do not label a paper-running persona as needing a startup wizard.
- Setup failures show repair action and failed step.
- Paper, canary, live, quarterly review, risk-off, and frozen states are distinct.
- Fleet payload is materially smaller than the current duplicated form.
- Contract tests cover row shape and backward-compatible aliases if retained.

## Artifacts

- `services/control-plane/bff/main.py` or management persona module
- `services/control-plane/bff/tests/test_bff_b3_persona_fleet.py`
- `services/control-plane/bff/tests/*persona_readiness*`
- Frontend DTO references if this repo owns them
