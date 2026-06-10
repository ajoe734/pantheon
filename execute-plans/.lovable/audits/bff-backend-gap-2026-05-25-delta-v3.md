# execute-plans BFF Backend Gap Delta V3 - 2026-05-25

Status: task-scoped audit record
Task: OPS-DOC-BFF-NAMING-CANONICAL-001

This document is the execute-plans frontend side of the BFF naming alignment audit. For the full
canonical naming decision record, see:
- `docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/CANONICAL_PATH_NAMING.md`
- `docs/04/pantheon_bff_api_gap_2026-05-25_delta_v3/BFF_API_GAP_delta_v3_spec.md`

---

## NAMING-ALIGN-001: URL Path Segments

No change required in execute-plans.

`execute-plans/src/lib/bff-v1/paths.ts` already generates correct kebab-case URL paths for all
BFF routes. The TypeScript builder function names (camelCase) do not affect the URL path value.

Verification: `grep -c 'management/portfolio-book\|management/performance-attribution\|management/persona-league' execute-plans/src/lib/bff-v1/paths.ts` → 4

---

## NAMING-ALIGN-002: Path Parameters in URL

No change required in execute-plans.

Path parameter variable names (`{strategy_id}` vs `{strategyId}`) are invisible to the HTTP
client. The URL shape produced by the TypeScript builders is identical either way.

---

## NAMING-ALIGN-003: `persona_id` / `personaId` Query Parameter

Route: `GET /bff/management/intervention-stream`

Frontend contract:
- `paths.managementInterventionStream()` → `/bff/management/intervention-stream`
- `managementInterventionStreamPath(query)` → query object passed as URL params
- `fetchManagementInterventionStream(query, init, baseUrl)` → fetches from canonical path

The BE accepts both `persona_id` and `personaId` as query parameters. FE callers should prefer
`persona_id` (snake_case canonical) but either form works. The BE coalesces them with
`persona_id or personaId`.

**execute-plans action:** No immediate code change required. Future FE refactors should use
`persona_id` as the canonical query param key, not `personaId`.

Backend acceptance (verified, no code change):
- `?persona_id=X` → filtered by persona X ✓
- `?personaId=X` → filtered by persona X (alias) ✓
- unauthenticated: HTTP 401 ✓

---

## NAMING-ALIGN-004: `window_hours` / `windowHours` Query Parameter

Route: `GET /bff/management/intervention-stream`

Frontend contract:
- `paths.managementInterventionStream()` → `/bff/management/intervention-stream`
- supported query: `persona_id`, `personaId`, `status`, `kind`, `q`, `window_hours`, `windowHours`,
  `page_token`, `page_size`
- canonical time-window key: `window_hours` (integer, 1–720, default 24)
- FE-compatibility alias: `windowHours`

The BE accepts both `window_hours` and `windowHours`. FE callers should prefer `window_hours`.

**execute-plans action:** No immediate code change required. Future FE refactors should use
`window_hours` as the canonical query param key.

Backend acceptance (verified, no code change):
- `?window_hours=48` → window set to 48 hours ✓
- `?windowHours=48` → window set to 48 hours (alias) ✓
- no param → default 24 hours ✓

---

## NAMING-ALIGN-005: `source_type` / `sourceType` in Command Request Body

Route: `POST /bff/v1/commands`

Frontend contract:
- canonical request body field: `source_type`
- FE-compatibility alias: `sourceType`

FE callers submitting commands should use `source_type` as the canonical key. The BE command
executor normalizes both forms to `source_type` before processing.

**execute-plans action:** Where FE currently sends `sourceType` in command bodies, plan a migration
to `source_type`. This is not breaking (both forms work) and can be phased.

Backend acceptance (verified, no code change):
- body with `source_type` → command processed correctly ✓
- body with `sourceType` → command processed correctly (alias) ✓
- post-normalization both keys carry the same value ✓

---

## SNAKE-DUP-001 through SNAKE-DUP-012: Response Field Dual Emission

Routes: trading-pulse, human-inbox, intervention-stream, evidence refs

The following response fields are present in both snake_case and camelCase form. The execute-plans
FE may read either form; the camelCase form is the recommended form for TypeScript callers.

| ID | snake_case (canonical) | camelCase (FE-friendly) | Route |
|---|---|---|---|
| DUP-001 | `runtime_id` | `runtimeId` | trading-pulse, strategy-allocation rows |
| DUP-002 | `deployment_stage` | `deploymentStage` | trading-pulse, capital-flow rows |
| DUP-003 | `runtime_binding_id` | `runtimeBindingId` | trading-pulse baseline object |
| DUP-004 | `paper_live_drift` | `paperLiveDrift` | trading-pulse baseline object |
| DUP-005 | `drift_groups` | `driftGroups` | trading-pulse baseline object |
| DUP-006 | `threshold_evaluation` | `thresholdEvaluation` | trading-pulse baseline object |
| DUP-007 | `inbox_type` | `inboxType` | human-inbox items |
| DUP-008 | `source_dataset` | `sourceDataset` | human-inbox, intervention-stream items |
| DUP-009 | `risk_level` | `riskLevel` | human-inbox, HIQ backlog items |
| DUP-010 | `source_record` | `sourceRecord` | human-inbox items |
| DUP-011 | `stream_sequence` | `streamSequence` | intervention-stream events |
| DUP-012 | `management_href` | `managementHref` | evidence ref links |

**execute-plans action:** TypeScript callers should consistently access the camelCase form (e.g.,
`row.runtimeId`, `item.inboxType`). The snake_case form is also stable if already in use; there is
no urgency to migrate existing code that reads the snake_case form.

Backend acceptance (all 12 pairs, no code change):
- Both forms carry identical values in all responses ✓
- Removing either form would be a breaking change and requires a deprecation notice ✓
