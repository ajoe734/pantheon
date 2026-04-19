# CW-04 Red-team Memo BFF Contract

## Status

**Contract published** — the memo list/detail routes, `ConsultMemo` read model, session-to-memo mapping, and `canInitiateGovernanceReview` authority signal are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the routes are live and returning this field shape.

Task: `CW-04-REDTEAM-MEMO-001`

## Purpose

Provide one backend-composed red-team memo surface so operators can list published findings, inspect per-memo recommendations and evidence, and initiate downstream governance review without deriving memo state, evidence links, or handoff authority from client-side synthesis.

## Dependencies

- `CW-01-FOUNDATION-001` for stable `ConsultRequest` identity (`linked_request_id`)
- `CW-02-TRANSCRIPT-001` for ordered session evidence semantics (`linked_session_id`)

## Routes

### List red-team memos

- `GET /api/v1/consult/memos`

Supported query params:

- `status` — `"draft"` | `"published"`
- `page_token`
- `page_size`

Required response fields:

- `data[]`
  - `memo_id`
  - `memo_type` — must be `"red_team_findings"`
  - `status` — `"draft"` | `"published"`
  - `author_ref`
  - `linked_request_id`
  - `recommendation_count`
  - `published_at` — nullable; populated when `status = "published"`
  - `created_at`
  - `route_href`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.redteam_memo` — `"ok"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get red-team memo detail

- `GET /api/v1/consult/memos/{memo_id}`

Required response fields:

- `memo_id`
- `memo_type` — `"red_team_findings"`
- `status` — `"draft"` | `"published"`
- `author_ref`
- `linked_request_id`
- `linked_session_id`
- `session_to_memo_mapping` — explicit relationship object; see Session-to-Memo Mapping section
- `summary`
- `recommendations[]` — plain list anchored to L3 `recommendations_json`; see Recommendation Object section
- `evidence_refs[]` — BFF-resolved evidence link objects; see Evidence Link Object section
- `published_at` — nullable
- `created_at`
- `allowedActions.canInitiateGovernanceReview`
- `meta.snapshot_at`
- `meta.surfaces.redteam_memo`

## ConsultMemo Read Model

The `ConsultMemo` read model promotes the L3 design intent (L3 schema §6.10, `Pantheon_資料表_Schema_設計版.md`) to canonical BFF truth.

### Lifecycle

```
draft → published
```

- `draft`: memo is being authored; `allowedActions.canInitiateGovernanceReview` must be `false`
- `published`: memo is finalized and findable by downstream governance; `canInitiateGovernanceReview` may be `true` when governance routing is available

`archived` is **not** in the current L3 design intent. If `archived` is needed it must be introduced as an explicit net-new contract decision and cannot be assumed as promoted L3 truth.

### Identity

- Primary key: `memo_id`
- Linked request: `linked_request_id` (L3 field: `request_id`) — the originating `ConsultRequest`
- Linked session: `linked_session_id` — the red-team session that produced the memo

### Recommendation Object

Anchored to L3 `recommendations_json`. The current L3 shape is a plain string list. Per-recommendation severity tiers, workflow status, or priority fields are **not** part of the current CW-04 scope and must not be added without an explicit net-new contract decision.

Each entry in `recommendations[]`:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `index` | integer | no | 1-based position in the memo |
| `body` | string | no | recommendation text |
| `evidence_refs[]` | string[] | no | identifiers for linked evidence objects; BFF-resolved via `evidence_refs` top-level list |

### Evidence Link Object

Each `evidence_refs[]` entry:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | string | no | canonical evidence identifier |
| `evidence_type` | string | no | `"telemetry"` \| `"lineage"` \| `"incident"` \| `"consult_session"` \| `"deployment_plan"` |
| `artifact_ref` | string | yes | linked artifact identity when applicable |
| `description` | string | yes | BFF-resolved human-readable label |
| `link` | string | no | BFF-resolved canonical navigation path; client must not construct this from `id` |

## Session-to-Memo Mapping

The relationship between a `red_team` session and the published `ConsultMemo` must be explicit in the BFF response. Clients must not derive this relationship from raw session data.

`session_to_memo_mapping` object on the detail response:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `session_id` | string | no | the `red_team` session that produced this memo |
| `request_id` | string | no | the originating `ConsultRequest`; same as `linked_request_id` |
| `session_type` | string | no | must be `"red_team"` |
| `mapped_at` | string | no | ISO 8601 timestamp when the session was mapped to this memo |

## Authority Rules

### `allowedActions.canInitiateGovernanceReview`

This is the sole authority signal for the downstream review handoff CTA.

The signal must be `false` when:
- `status != "published"`
- the memo detail surface is unavailable (`meta.surfaces.redteam_memo = "unavailable"`)
- governance routing is not available for this memo

The signal must never be derived client-side from `status` alone. Governance routing availability is a backend concern.

## Degradation Semantics

| `meta.surfaces.redteam_memo` | Behavior |
|---|---|
| `"ok"` | full memo content |
| `"stale"` | show last-known memo state with a staleness banner |
| `"degraded"` | show last-known memo state with a staleness banner; `canInitiateGovernanceReview` must be `false` |
| `"unavailable"` | show canonical unavailable banner; no memo content; `canInitiateGovernanceReview` must be `false` |

## Non-Goals

- The client must not construct evidence link URLs from raw `id` or `artifact_ref` values.
- The client must not derive `allowedActions.canInitiateGovernanceReview` from `status` field alone.
- The client must not assume `archived` is a valid lifecycle state.
- Per-recommendation severity, priority, or workflow status are out of scope for CW-04.
- The client must not derive the session-to-memo relationship from raw session objects.

## Example Payload

- `docs/examples/CW-04-redteam-memo.json`
