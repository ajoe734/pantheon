# MGMT-OPS-008 - Mutation / Evolution Contract For Persona Fleet

Owner: Claude2

Reviewer: Codex

Wave: 0

Dependencies: none

Source gap:

- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md`

## Goal

Lock the BFF and frontend-adapter contract for Persona Fleet recent-change
metadata so downstream pages do not infer mutation identity from optional text,
dates, or missing values.

## Required Work

- Audit the BFF payloads that feed Persona Fleet and Evolution Journal.
- Define explicit fields for:
  - `last_mutation_label`;
  - `last_mutation_at`;
  - `last_mutation_kind`;
  - `mutation_entry_id`;
  - `evolution_entry_id`;
  - `evolution_href`;
  - `mutation_confidence`;
  - `mutation_diagnostics`.
- Ensure missing formal ids are null/absent, not `nan`, date strings, or display
  labels.
- Ensure fallback summaries are labeled `fleet_summary` or equivalent, not
  formal mutation entries.
- Add tests for formal, fallback, unavailable, and invalid-id states.
- Document any upstream source that cannot yet emit formal mutation ids.

## Acceptance

- Persona Fleet rows expose mutation identity, timestamp, source kind, and
  confidence as separate fields.
- No BFF or adapter output uses `nan`, `NaN`, `undefined`, an empty string, or a
  date string as a mutation id.
- Contract tests cover the focus persona `persona-20260528-04688755` in a
  fallback-only state.
- At least one formal mutation/evolution row is covered when the source exists.
- The task records the exact payload shape consumed by `MGMT-OPS-009`.

## Artifacts

- `services/control-plane/bff`
- `services/control-plane/bff/tests`
- `execute-plans:src/lib/bff-v1`
- `execute-plans:src/management`
- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10`
