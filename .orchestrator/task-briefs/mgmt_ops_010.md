# Task Brief: MGMT-OPS-010

This file is generated from the 2026-07-10 Persona Fleet mutation/evolution gap
packet for task-scoped execution context.

## Task
- Title: Hosted click-map regression for Persona Fleet links
- Status: todo
- Owner: Antigravity
- Reviewer: Codex
- Phase: Management Console Operations / Mutation Evolution Wave 2 hosted regression
- Next: Run hosted Persona Fleet click-map validation and prove target pages have correct content, not only rendered shells.

## Summary
完整點驗 Persona Fleet 連出去的頁面，特別是最近 mutation 到 Evolution Journal：formal、fallback、missing-data 三條路都要有 hosted evidence，不能只看有畫面就算正常。

## Dependencies
- MGMT-OPS-009

## Artifacts
- execute-plans:e2e
- execute-plans:test-results
- docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/MGMT-OPS-010-hosted-click-map-regression.md

## Relevant Canonical Files
- docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md
- docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md

## Working Rules
- Rendering is not enough; labels, counts, filters, diagnostics, and source confidence must match the clicked row.
- Keep screenshot or trace evidence for every target page checked.
