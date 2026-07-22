# Persona Fleet Mutation / Evolution Gap Execution Packet - 2026-07-10

Status: ready for fleet dispatch

Source gap spec:

- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md`

Extends:

- `docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md`
- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MGMT-OPS-004-performance-attribution-evidence.md`

## Dispatch Command

Validate without mutating live status:

```sh
python3 scripts/dispatch_persona_fleet_mutation_evolution_gap_2026-07-10.py --dry-run
```

Dispatch into the live supervisor status root:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/dispatch_persona_fleet_mutation_evolution_gap_2026-07-10.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync
```

The dispatch script is idempotent. It preserves progress fields for already
started tasks and only appends assignment events for newly created tasks.

## Execution Order

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `MGMT-OPS-008` | Claude2 | Codex | Lock the BFF/adapter contract for recent mutation identity, timestamp, fallback, and diagnostics. |
| 1 | `MGMT-OPS-009` | Codex | Claude2 | Fix Persona Fleet and Evolution Journal link semantics without removing row links. |
| 2 | `MGMT-OPS-010` | Antigravity | Codex | Run hosted cross-page click-map regression for Fleet linked pages and mutation fallback behavior. |
| 3 | `MGMT-OPS-011` | Codex2 | Human/Ops | Close the packet with PRs, deploy evidence, screenshots, and residual-risk notes. |

## Dependencies

```text
MGMT-OPS-008: none
MGMT-OPS-009: MGMT-OPS-008
MGMT-OPS-010: MGMT-OPS-009
MGMT-OPS-011: MGMT-OPS-010
```

The packet assumes the operations workflow foundation has already been merged
or superseded by equivalent code:

- `MGMT-OPS-001` source-confidence contract;
- `MGMT-OPS-002` frontend adapter/data-confidence helpers;
- `MGMT-OPS-004` performance attribution fallback diagnostics.

Do not block this packet only because those historical rows are absent from a
local `ai-status.json`; verify the corresponding code and evidence instead.

## Global Acceptance

Every `MGMT-OPS-008` through `MGMT-OPS-011` task in this packet must record:

1. branch and PR target;
2. changed files and owned scope;
3. local validation commands and output summary;
4. hosted browser evidence when UI routing changes;
5. reviewer approval or explicit blocker;
6. merge commit SHA when merged;
7. residual risk with owner and expiry.

This packet is not complete until the hosted Persona Fleet flow proves:

```text
Persona Fleet 最近 MUTATION link
  -> Evolution Journal exact formal entry, when a formal id exists
  -> Evolution Journal fleet-summary fallback, when no formal id exists
```

The correct fallback page says it is fallback. It does not show `mutation: nan`,
does not call a date an action, and does not count a synthetic summary as a
formal evolution match.

## Non-Negotiable Rules

- Do not remove Persona Fleet hyperlinks to avoid fixing target pages.
- Do not reintroduce demo/mock data.
- Do not treat `nan` or a date string as a mutation id.
- Do not create a new aggregate OODA page for this issue.
- Do not mark the work done from render-only screenshots.
- Do not mutate live capital, broker state, or persona state.

## Required Evidence

- BFF/adapter unit or contract tests for formal, fallback, unavailable, and
  invalid-id mutation states.
- Frontend tests proving `personaFleetMutationHref` never emits fake query
  values and keeps fallback routing honest.
- Evolution Journal tests proving banner, counts, fallback card labels, and date
  fields are correct.
- Hosted browser click evidence for at least:
  - `persona-20260528-04688755` fallback path;
  - one formal mutation path, if a formal fixture/real row exists;
  - no-data path where the cell is not a misleading link.
