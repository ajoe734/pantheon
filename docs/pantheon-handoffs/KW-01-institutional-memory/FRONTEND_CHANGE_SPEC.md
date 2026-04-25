# KW-01 Institutional Memory — Frontend Change Spec

## Feature

- Feature ID: `KW-01-institutional-memory`
- Screen IDs: `screen-knowledge-memory-list`, `screen-knowledge-memory-detail`
- Workbench: Knowledge Workbench
- Packet status: delivery-ready — BFF routes are live
- Task: `KW-01-FOUNDATION-001`

## Readiness Gate

Pantheon has verified **both** of the following in the current BFF workspace:

1. `GET /api/v1/knowledge/memory` is live and returning the published field shape.
2. `GET /api/v1/knowledge/memory/{entry_id}` is live and returning the published detail shape.

Build against those live routes. If the runtime payload diverges from this synced contract, emit `.coordination/requests/KW-01-institutional-memory-bff-gap.yaml` and stop. No invented browse state or dummy entries.

## Summary

Build the **Institutional Memory** list and detail screens inside `front-ai-trading-system`. These screens let operators browse and inspect institutional knowledge entries written by system-level services. All data comes from the Pantheon BFF — no client-side ranking, lifecycle inference, or filter-vocab invention.

## Files to Create or Modify

```
src/pages/knowledge/InstitutionalMemoryList.tsx   — new Memory List page
src/pages/knowledge/InstitutionalMemoryDetail.tsx — new Memory Detail page
src/pages/knowledge/InstitutionalMemoryTypes.ts   — add memory entry types
src/lib/bffClient.ts                              — add memory list and detail fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch memory list (KW-01 list read)

```
GET /api/v1/knowledge/memory
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `knowledge_type` | string | `incident_lesson` / `regime_pattern` / `policy_precedent` / `research_finding` / `evolution_rationale` / `cross_persona_observation` |
| `scope` | string | `system_wide` / `strategy_family` / `instrument_class` |
| `scope_filter` | string | free-text scope value |
| `tags` | string | comma-separated tags |
| `page` | number | default 1 |
| `page_size` | number | default 20 |

Expected response shape:

```typescript
interface MemoryListResponse {
  entries: Array<{
    entry_id: string;
    knowledge_type: string;
    headline: string;
    scope: string;
    scope_filter: string | null;
    written_at: string;
    write_authority: string;
    tags: string[];
    reuse_count: number;
    is_superseded: boolean;
    route_href: string;
  }>;
  pagination: {
    total_count: number;
    page: number;
    page_size: number;
    total_pages: number;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      memory_list: "ok" | "degraded" | "unavailable";
    };
  };
}
```

### Fetch memory detail (KW-01 detail read)

```
GET /api/v1/knowledge/memory/{entry_id}
Path param: entry_id (required)
```

Expected response shape (see `docs/examples/KW-01-institutional-memory.json` for a full example):

```typescript
interface MemoryDetailResponse {
  entry_id: string;
  knowledge_type: string;
  content: {
    headline: string;
    body: string;
    structured_payload: Record<string, unknown> | null;
    tags: string[];
  };
  source_event: {
    type: string;
    id: string;
    href: string | null;
  };
  contributing_persona_ids: string[];
  written_at: string;
  write_authority: string;
  scope: {
    type: string;
    filter: string | null;
  };
  lifecycle: {
    status: "active" | "archived" | "superseded";
    superseded_by: string | null;
  };
  usage: {
    reuse_count: number;
    last_cited_at: string;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      entry_detail: "ok" | "degraded" | "unavailable";
      source_context: "ok" | "degraded" | "unavailable";
    };
  };
}
```

## Component Structure

### `InstitutionalMemoryList.tsx`

- Route: `/knowledge/memory`.
- Fetches `GET /api/v1/knowledge/memory` with active filter state on mount and on filter change.
- Renders filter rail with `knowledge_type`, `scope`, `scope_filter`, and `tags` controls. Pass query params to BFF; do not filter locally.
- Renders paginated list of entries. Each row links to detail via `entry_id` (use `route_href` from BFF response as the navigation target).
- Shows a visual indicator on rows where `is_superseded` is `true`.
- When `meta.surfaces.memory_list` is `degraded` or `unavailable`, shows the non-dismissable PKT-005 degradation banner.

### `InstitutionalMemoryDetail.tsx`

- Route: `/knowledge/memory/:entry_id`.
- If `entry_id` is absent from the route, render an explicit prompt. Do not render an empty detail panel.
- Fetches `GET /api/v1/knowledge/memory/{entry_id}` on mount.

#### Entry Header

- Render `entry_id`, `knowledge_type`, `content.headline`, `lifecycle.status` as a badge.
- When `lifecycle.status` is `superseded` and `lifecycle.superseded_by` is non-null, render a link to the replacement entry using `/knowledge/memory/{lifecycle.superseded_by}`.
- Render `written_at` and `write_authority`.

#### Content Panel

- Render `content.body` as markdown.
- Render `content.structured_payload` as a key-value grid when non-null. Do not flatten raw JSON into a string.
- Render `content.tags` as tag badges.

#### Scope Panel

- Render `scope.type` as a readable label.
- Render `scope.filter` when non-null.

#### Source Event Panel

- Render `source_event.type` and `source_event.id` as labeled fields.
- When `source_event.href` is non-null, render it as a navigation link — use the href exactly as provided. Do not construct incident, evolution, or research URLs from raw `type` and `id`.
- When `source_event.href` is null, display type and id as read-only text with no link.
- When `meta.surfaces.source_context` is `degraded` or `unavailable`, show a degradation indicator on this panel.

#### Contributing Personas

- Render `contributing_persona_ids[]` as a labeled list.
- If the array is empty, show "No contributing personas recorded." Do not hide the panel.

#### Usage Panel

- Render `usage.reuse_count` and `usage.last_cited_at`.

## Degradation Handling

| `meta.surfaces.memory_list` | Required behavior (list) |
|---|---|
| `"ok"` | Normal display |
| `"degraded"` | Non-dismissable PKT-005 banner; data visible with stale caveat |
| `"unavailable"` | Replace list with degradation notice |

| `meta.surfaces.entry_detail` | Required behavior (detail) |
|---|---|
| `"ok"` | Normal display |
| `"degraded"` | Non-dismissable PKT-005 banner; data visible with stale caveat |
| `"unavailable"` | Replace panel content with degradation notice |

## State Requirements

Each data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `stale`: stale banner with available data
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

Do not map `stale` to `empty`.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- `route_href` from BFF list rows is the canonical navigation target. Do not construct detail URLs from `entry_id` directly.
- `source_event.href` is the canonical source link. Do not construct incident or evolution URLs from raw `type` and `id`.
- The module is read-only. Do not expose any create, archive, supersede, or update actions.
- Superseded entries must be displayed, not hidden. Show a visual indicator on the list row and a banner on the detail page.
- Filter vocab (knowledge_type, scope values) must match the BFF enum values exactly. Do not add undocumented type labels.
- Degradation banner is inherited from `PKT-005` and must be non-dismissable.
- If any required field is absent from the BFF response, write `.coordination/requests/KW-01-institutional-memory-bff-gap.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/KW-01-institutional-memory-ui-done.yaml`.

## References

- Screen spec: `docs/screens/KW-01-institutional-memory.md`
- BFF contract: `docs/bff/KW-01-institutional-memory.md`
- Example payload (detail): `docs/examples/KW-01-institutional-memory.json`
- Contract-ready coordination: `.coordination/responses/KW-01-institutional-memory-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/KW-01-institutional-memory-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
- Memory object schema: `services/memory/institutional_memory_entry.schema.json`
- Degradation substrate: `PKT-005`
