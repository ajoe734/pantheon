# Task Brief: MGMT-OPS-009

This file is generated from the 2026-07-10 Persona Fleet mutation/evolution gap
packet for task-scoped execution context.

## Task
- Title: Persona Fleet and Evolution Journal link semantics
- Status: todo
- Owner: Codex
- Reviewer: Claude2
- Phase: Management Console Operations / Mutation Evolution Wave 1 frontend
- Next: Fix Persona Fleet recent mutation links and Evolution Journal focus/fallback rendering without deleting useful hyperlinks.

## Summary
修正 Persona Fleet 最近 MUTATION 點到 Evolution Journal 的語義：有正式 id 就進正式 entry，沒有正式 id 就進 persona fallback summary；頁面不得顯示 mutation:nan，也不得把日期顯示成 Action。

## Dependencies
- MGMT-OPS-008

## Artifacts
- execute-plans:src/management/pages/oversight/personaFleetLinks.ts
- execute-plans:src/management/pages/oversight/evolutionJournalFocus.ts
- execute-plans:src/management/pages/oversight/_core.tsx
- execute-plans:e2e
- docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-009-persona-fleet-evolution-links.md

## Relevant Canonical Files
- docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md
- docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md

## Working Rules
- Do not remove links as a substitute for fixing wrong targets.
- Do not create a new aggregate OODA page.
- Tests must fail if fallback pages show fake formal mutation text.
