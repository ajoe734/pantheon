# Review: BFF-MGMT-DELTA-001

Reviewer: Claude
Owner: Codex
Date: 2026-05-24
Status: approved

## Scope

`GET /bff/management/persona-league/movers` — persona league movement endpoint,
execute-plans typed client surface, and delta audit documentation.

## Verification

```
git diff --check                    → clean
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
→ 18 passed, 3 warnings in 7.46s
```

## Implementation Review

### `services/control-plane/bff/main.py`

- `_PM12_LEAGUE_MOVER_DIRECTIONS` constant correctly enumerates accepted values.
- `_pm12_normalize_mover_direction` returns 422 with a structured error object
  on invalid input — consistent with other validation helpers in the module.
- `_pm12_persona_league_mover_items` builds movers from the current snapshot
  only. Since no historical baseline source exists yet, all items correctly
  receive `direction=new`, `baselineStatus=unavailable`, null delta fields, and
  an explicit `basis` string. This is a deliberate scope limit, not a gap.
- The endpoint handler applies auth guard, identity extraction, direction
  normalization, filtering, and surface status composition consistently with
  the adjacent persona-league endpoints.
- Sort logic in `_pm12_persona_league_mover_items` degrades gracefully when all
  items share the same unavailable baseline — secondary sort keys (score,
  rank) prevent non-deterministic ordering.
- Response envelope shape (`data`, `items`, `movers`, `summary`, `page_info`,
  `meta`) is consistent with existing management list endpoints.

### `execute-plans/src/lib/bff-v1/management.ts`

- `ManagementPersonaLeagueMover` correctly extends `ManagementPersonaLeagueRankingItem`.
- `ManagementPersonaLeagueMoversSummary` and `ManagementPersonaLeagueMoversResponse`
  mirror the BFF response shape exactly.
- `managementPersonaLeagueMoversPath` and `fetchManagementPersonaLeagueMovers`
  follow the same conventions used by all other management fetch helpers.
- Index signature `[key: string]: unknown` used consistently across all new
  interfaces.

### `execute-plans/src/lib/bff-v1/paths.ts`

- New path constant `managementPersonaLeagueMovers` inserted in the correct
  position adjacent to the other persona-league paths.

### Test coverage

- `test_pm12_persona_league_movers_returns_current_snapshot_movers`: covers the
  baseline-unavailable path, limit parameter, and key response shape invariants.
- `test_pm12_persona_league_movers_rejects_invalid_direction`: validates the
  422 contract.
- Auth check extended to include the new route.
- Both `FINAL_CONTRACT_METHOD_PATHS` and `LIVE_PROBE_CONCRETE_ROUTES` updated
  in the wiring contract test.

### Audit docs

- `BFF_API_GAP_delta_audit_spec.md` §DELTA-2 is complete: route, query params,
  response shape, composition sources, and baseline-unavailable note are all
  present.
- `bff-backend-gap-2026-05-24-delta.md` is a clear task-scoped summary with
  verification commands.

## Observations

- Direction filter for `up`, `down`, and `flat` will return zero items until a
  historical baseline source is wired in. This is correct and intentional;
  the `baselineStatus=unavailable` surface status communicates this to callers.
- No mutations, no live capital writes, no governance state changes.
- Commit trailers are correct (LLM-Agent, Task-ID, Reviewer).

## Decision

**Approved.** Implementation satisfies the task scope with proper auth gating,
input validation, explicit baseline-unavailable semantics, complete typed client
surface, and passing tests. Returning to Codex for closeout.
