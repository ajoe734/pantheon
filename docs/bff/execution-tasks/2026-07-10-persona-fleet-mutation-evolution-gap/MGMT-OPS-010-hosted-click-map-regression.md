# MGMT-OPS-010 - Hosted Click-Map Regression For Persona Fleet Links

Owner: Antigravity

Reviewer: Codex

Wave: 2

Dependencies:

- `MGMT-OPS-009`

Source gap:

- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md`

## Goal

Prove the linked Persona Fleet pages are semantically correct in the hosted
management console. A page that merely renders is not enough; the target content
must match the row link and source data confidence.

## Required Work

- Build or update a hosted browser smoke that starts on
  `/management/persona-fleet`.
- Click the Fleet row links that cover:
  - Persona detail/name;
  - OODA state;
  - capital pool;
  - ranking;
  - data source;
  - research project;
  - performance;
  - recent mutation.
- For this packet, record strict assertions for the recent mutation target:
  - formal path opens exact formal entry when one exists;
  - fallback path opens a Fleet status summary with no fake mutation id;
  - missing-data path does not create a misleading link.
- Keep screenshots or trace output for the Fleet row before click and each
  target page after click.

## Acceptance

- Hosted evidence shows `persona-20260528-04688755` fallback path has no
  `mutation: nan` and no `Action 2026-06-03` style field misuse.
- The click-map catches target pages that render but show wrong content.
- The smoke is repeatable against the dev FE/BFF target used by the management
  console deployment.
- Any unavailable formal mutation source is recorded as an explicit diagnostic,
  not hidden by a green check.

## Artifacts

- `execute-plans:e2e`
- `execute-plans:test-results`
- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10`
- hosted screenshots or trace artifact paths
