# MGMT-OPS-009 - Persona Fleet And Evolution Journal Link Semantics

Owner: Codex

Reviewer: Claude2

Wave: 1

Dependencies:

- `MGMT-OPS-008`

Source gap:

- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md`

## Goal

Fix the actual Persona Fleet -> Evolution Journal navigation. Keep useful row
hyperlinks, but make every target page honest about whether it is showing a
formal mutation entry or a Fleet-derived fallback summary.

## Required Work

- Update `personaFleetMutationHref` and related row-link helpers so they:
  - prefer formal `mutation_entry_id` / `evolution_entry_id` link targets;
  - use persona-scoped fallback links only when formal ids are absent;
  - never emit query params with `nan`, `NaN`, `undefined`, empty strings, or
    date strings as ids;
  - keep the row hyperlink when fallback context is useful.
- Update Evolution Journal focus parsing so invalid/missing mutation focus is
  not displayed as `mutation: nan`.
- Rename fallback cards from mutation summaries to Fleet status summaries.
- Move date values into `changed_at` / `落地時間` fields, not `Action`.
- Split formal match counts from fallback summary counts.
- Add focused frontend tests for:
  - formal mutation link;
  - fallback-only link for `persona-20260528-04688755`;
  - invalid id suppression;
  - no-data/no-link state.

## Acceptance

- Persona Fleet `最近 MUTATION` remains linked for formal and useful fallback
  states.
- Evolution Journal shows exact formal entry content when a formal id exists.
- Evolution Journal fallback content says it is a `Persona Fleet status summary`
  and says no formal mutation id is available.
- Operator-facing UI and URL strings do not contain `mutation=nan`,
  `mutation: nan`, `source=nan`, or equivalent fake keys.
- Date strings are rendered only as dates/timestamps, never as action ids.
- Tests fail if someone later removes the link instead of fixing the target.

## Artifacts

- `execute-plans:src/management/pages/oversight/personaFleetLinks.ts`
- `execute-plans:src/management/pages/oversight/evolutionJournalFocus.ts`
- `execute-plans:src/management/pages/oversight/_core.tsx`
- `execute-plans:src/management/**/*.test.*`
- `execute-plans:e2e`
