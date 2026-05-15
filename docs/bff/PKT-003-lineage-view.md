# PKT-003 Lineage View BFF Contract

## Purpose

Provide lineage list, edge detail, and graph surfaces so the Lineage View can render artifact derivation chains without client-side graph construction.

## Primary Read Routes

### List lineage records (LN-01)

- `GET /api/v1/lineage`
- Query parameters: `artifact_id`, `page_token`, `page_size`

Required response fields per item:

- `artifact_id`
- `edge_count`
- `last_edge_at`

Required list-level fields:

- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`

### Get lineage edge detail (LN-02)

- `GET /api/v1/lineage/edges/{edge_id}`

Required response fields:

- `id`
- `from_artifact_id`
- `to_artifact_id`
- `relationship`
- `created_at`
- `meta.snapshot_at`

### Get lineage graph (LN-03)

- `GET /api/v1/lineage/graph`
- Query parameters: `root_id` (required), `depth` (integer, clamped 1–10 by BFF)
- **Known v1 limitation**: `root_type` parameter is accepted but not applied — edges are returned by `root_id` only. Do not expose `root_type` as a UI control until confirmed live in a later BFF version.

Required response fields:

- `nodes[]` with `artifact_id`, `artifact_version`, `artifact_type`
- `edges[]` with `id`, `from_artifact_id`, `to_artifact_id`, `relationship`
- `meta.snapshot_at`
- `meta.staleness` (present when BFF read surface state is not fresh)

## UI Gating Rules

- `root_type` must not be exposed as a filter UI control in v1.
- When `meta.staleness` is present, render a non-dismissable staleness banner.
- When `edges[]` is empty for a given `root_id`, display "No lineage recorded for {artifact_id}" rather than a blank graph canvas.
- Only `operator`, `approver`, `admin`, and `reviewer` role tokens are accepted.

## Error Handling

- 404 on `{edge_id}`: render "Lineage edge not found".
- Empty graph response: render explicit "No lineage recorded" copy — not a blank canvas.
- Any missing required field: emit a `bff-gap` handoff.

## Write Actions

None. Lineage is a read-only audit surface.
