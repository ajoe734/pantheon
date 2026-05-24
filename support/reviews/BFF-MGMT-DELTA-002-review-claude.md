# Review: BFF-MGMT-DELTA-002
# GET /bff/management/persona-league/heatmap

Reviewer: Claude
Owner: Codex
Date: 2026-05-24
PR: #513 (merged → dev, commit bf5f2cf2)
Task commit: 4e9a0a7c

## Verdict: APPROVED

## Review Scope

Reviewed 5 changed files from task commit 4e9a0a7c:

- `services/control-plane/bff/main.py` (+399 lines)
- `services/control-plane/bff/test_bff_management_delta_routes.py` (+96 lines)
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py` (+2 lines)
- `execute-plans/src/lib/bff-v1/management.ts` (+131 lines)
- `execute-plans/src/lib/bff-v1/paths.ts` (+1 line)

## Backend Route (main.py)

Route registered at `GET /bff/management/persona-league/heatmap` (line 27013).

- Auth gate: `_require_read_role(identity)` enforced before any data access.
- Query params: `state`, `archetype`, `q`, `bucket` (default "day"), `bucket_count` (default 7, range 1–90), `limit` (default 50, range 1–200). Types and ranges correct.
- Response shape: `data`, `items`, `rows`, `buckets`, `cells`, `summary`, `page_info`, `meta` — matches declared type contract in management.ts.
- Meta policy: `read_only_governance_advisory`. Correct for a read-only governance aggregate.
- Surfaces: `persona_league_heatmap` aggregate surface + source surfaces. Degraded-state propagation via `_aggregate_group_surface` is consistent with other management routes.
- Composition sources list four upstream read surfaces. Correct.

Helper functions reviewed:

- `_pm12_heatmap_bucket_delta`: Validates bucket key (hour/day/week) and raises HTTP 422 on invalid input. Clean.
- `_pm12_floor_bucket_start`: Flooring logic for hour/week/day alignment is correct.
- `_pm12_heatmap_bucket_label`: Formats correctly per bucket type.
- `_pm12_heatmap_buckets`: Generates `bucket_count` buckets backward from current bucket floor. Both `id`/`bucketId`/`bucket_id` aliases present.
- `_pm12_records_for_heatmap_bucket`: Three-tier fallback (observed → carried_forward → latest_available) is well-defined and deterministic.
- `_pm12_persona_league_heatmap_cell`: Computes score via `_pm12_persona_league_scores`, attaches all field aliases (camelCase + snake_case), formula version, source label, and telemetry count.
- `_pm12_persona_league_heatmap_rows`: Builds the flat `cells` list in parallel with per-row `row_cells`, computes summary stats (min/max/avg score, personaCount, bucketCount, cellCount).

## Frontend Contract (management.ts, paths.ts)

- `ManagementPersonaLeagueHeatmapQuery`: All five query params typed correctly with union literal for `bucket`.
- `ManagementPersonaLeagueHeatmapBucket`: All fields including both alias forms and `[key: string]: unknown` escape hatch. Correct.
- `ManagementPersonaLeagueHeatmapCell`: Complete including `components`, `metrics`, `formulaVersion`/`formula_version`, `source`, `observedTelemetryCount`, `latestTelemetryAt`.
- `ManagementPersonaLeagueHeatmapRow`: All persona metadata fields plus `cells: ManagementPersonaLeagueHeatmapCell[]`.
- `ManagementPersonaLeagueHeatmapResponse`: Top-level shape matches backend JSON exactly.
- `managementPersonaLeagueHeatmapPath()`: Correctly wraps `paths.managementPersonaLeagueHeatmap()` with `withQuery`.
- `fetchManagementPersonaLeagueHeatmap()`: Standard GET fetch helper with `Accept: application/json`, `!response.ok` guard, typed return.
- Path: `managementPersonaLeagueHeatmap: () => \`${BASE}/management/persona-league/heatmap\`` registered in paths.ts.

## Test Coverage

`test_bff_management_delta_routes.py`:

- `test_persona_league_heatmap`: Authenticated GET with `bucket_count=3`; asserts HTTP 200, data shape, `items == rows == data.rows`, `buckets == data.buckets`, `cells == data.cells`, correct bucket count, summary.bucket, `cellCount == len(rows) * len(buckets)`, policy, surfaces, composition_sources, per-persona cell structure, formula version, component score keys.
- `test_persona_league_heatmap_requires_auth`: Anonymous GET returns HTTP 401.
- `test_persona_league_heatmap_cors_preflight`: OPTIONS with Lovable origin returns HTTP 200 or 204 with matching `Access-Control-Allow-Origin`.

`test_execute_plans_final_live_wiring_contract.py`: Route appears in both route catalog (line 65) and live wiring assertions (line 195).

Task commit trailer: `Verified: python3 -m pytest ... -q; python3 -m compileall -q ...; git diff --check` — all three checks documented.

## No Issues Found

The implementation is narrow, well-tested, and consistent with the existing persona-league route family. No scope creep, no missing auth, no unrelated diff in the task commit.
