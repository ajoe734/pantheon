# Task Brief: MGMT-OPS-008

This file is generated from the 2026-07-10 Persona Fleet mutation/evolution gap
packet for task-scoped execution context.

## Task
- Title: Mutation / Evolution contract for Persona Fleet
- Status: todo
- Owner: Claude2
- Reviewer: Codex
- Phase: Management Console Operations / Mutation Evolution Wave 0 contract
- Next: Lock BFF and adapter recent-change fields so Persona Fleet no longer infers mutation ids from dates, labels, or missing values.

## Summary
鎖定 Persona Fleet 最近 mutation 與 Evolution Journal 的共同契約：正式 mutation id、日期、fallback summary、confidence、diagnostics 必須分開，不准把 nan 或日期當成 mutation id。

## Dependencies
- none

## Artifacts
- services/control-plane/bff
- services/control-plane/bff/tests
- execute-plans:src/lib/bff-v1
- execute-plans:src/management
- docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-008-mutation-evolution-contract.md

## Relevant Canonical Files
- docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md
- docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md

## Working Rules
- Do not reintroduce demo/mock data.
- Do not use nan, undefined, empty strings, labels, or dates as mutation ids.
- Keep missing formal mutation ids explicit as diagnostics.
