# KW-01 Institutional Memory — Screen Spec

Last updated: 2026-04-19
Status: delivery-ready — BFF routes live
Tier: screen spec
Feature ID: `KW-01-institutional-memory`
Task: `KW-01-FOUNDATION-001`

---

## Purpose

The Institutional Memory module gives operators a navigable, queryable browse surface over durable knowledge entries produced by system-level services (incident postmortems, evolution decisions, cross-persona observations, and research findings). All data comes from the Pantheon BFF. No browse ranking, lifecycle state, or filter vocab may be inferred client-side.

---

## Routes

```
/knowledge/memory                   — paginated list of memory entries
/knowledge/memory/:entry_id         — full detail for one entry
```

---

## Readiness Gate

Pantheon has verified both production read routes in the current BFF workspace:

1. `GET /api/v1/knowledge/memory` is live and returning the published field shape.
2. `GET /api/v1/knowledge/memory/{entry_id}` is live and returning the published detail shape.

Build against those live routes. If a runtime payload diverges from the synced contract, stop implementation and emit the canonical KW-01 `bff-gap` handoff. No invented browse state.

---

## Surface Panels

### 1. Memory List (`/knowledge/memory`)

Paginated list of institutional memory entries.

| Field | Source | Notes |
|---|---|---|
| `entry_id` | BFF list response | Used as the navigation key to detail |
| `knowledge_type` | BFF list response | Enum label — display as readable badge |
| `headline` | BFF list response | Primary text row |
| `scope` | BFF list response | `system_wide` / `strategy_family` / `instrument_class` |
| `scope_filter` | BFF list response | Specific scope value, shown when non-null |
| `written_at` | BFF list response | ISO timestamp |
| `write_authority` | BFF list response | Service identifier |
| `tags` | BFF list response | Tag badges |
| `reuse_count` | BFF list response | Numeric badge |
| `is_superseded` | BFF list response | Show a visual indicator when `true` |
| `route_href` | BFF list response | Use this href for navigation to detail |
| `pagination.*` | BFF list response | Render backend-provided pagination controls |

#### Filter Rail

Filter controls are backend-shaped. Do not hardcode type labels beyond the canonical schema enums.

| Filter | Query param | Valid values |
|---|---|---|
| Knowledge type | `knowledge_type` | `incident_lesson`, `regime_pattern`, `policy_precedent`, `research_finding`, `evolution_rationale`, `cross_persona_observation` |
| Scope | `scope` | `system_wide`, `strategy_family`, `instrument_class` |
| Scope filter | `scope_filter` | free text — pass through, do not validate locally |
| Tags | `tags` | comma-separated |
| Page | `page` | integer, default 1 |
| Page size | `page_size` | integer, default 20 |

#### Degradation

When `meta.surfaces.memory_list` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner (inherited from `PKT-005`). Do not show "no entries" as authoritative when the read surface is stale.

---

### 2. Memory Detail (`/knowledge/memory/:entry_id`)

Full entry view for one institutional memory record.

#### Entry Header

| Field | Source | Notes |
|---|---|---|
| `entry_id` | BFF detail response | Display as identifier badge |
| `knowledge_type` | BFF detail response | Display as readable label |
| `content.headline` | BFF detail response | Primary heading |
| `lifecycle.status` | BFF detail response | `active` / `archived` / `superseded` badge |
| `lifecycle.superseded_by` | BFF detail response | Link to replacement entry when non-null |
| `written_at` | BFF detail response | ISO timestamp |
| `write_authority` | BFF detail response | Service identifier |

#### Content Panel

| Field | Source | Notes |
|---|---|---|
| `content.body` | BFF detail response | Markdown-rendered body |
| `content.structured_payload` | BFF detail response | Render key-value pairs when non-null; do not flatten raw JSON |
| `content.tags` | BFF detail response | Tag badges |

#### Scope Panel

| Field | Source | Notes |
|---|---|---|
| `scope.type` | BFF detail response | `system_wide` / `strategy_family` / `instrument_class` |
| `scope.filter` | BFF detail response | Specific value when non-null |

#### Source Event Panel

| Field | Source | Notes |
|---|---|---|
| `source_event.type` | BFF detail response | Event type label |
| `source_event.id` | BFF detail response | Event identifier |
| `source_event.href` | BFF detail response | Internal link to source surface when non-null — use exactly as provided, do not construct from raw id |

If `source_event.href` is null, show the event type and id as read-only text with no link.

#### Contributing Personas

- Render `contributing_persona_ids[]` as a labeled list.
- If the list is empty, show "No contributing personas recorded."

#### Usage Panel

| Field | Source | Notes |
|---|---|---|
| `usage.reuse_count` | BFF detail response | Numeric display |
| `usage.last_cited_at` | BFF detail response | ISO timestamp |

#### Degradation

| `meta.surfaces.entry_detail` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; data visible with caveat |
| `unavailable` | Replace panel content with degradation notice |

When `meta.surfaces.source_context` is `degraded` or `unavailable`, show a degradation indicator on the Source Event panel. Do not hide it or replace it with invented context.

---

## State Requirements

Every data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `stale`: stale banner with available data
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

Do not map `stale` to `empty`.

---

## Constraints

- All fields come from the BFF. No client-side inference of lifecycle state, ranking, or filter vocab.
- `route_href` from the BFF is the canonical navigation target. Do not construct `/knowledge/memory/{entry_id}` from raw `entry_id` values.
- `source_event.href` is the canonical source link. Do not construct incident, evolution, or research-task URLs from raw event type and id.
- The module is read-only. No create, update, archive, or supersede actions may be exposed.
- Superseded entries must be shown, not hidden — display a visual indicator and the `superseded_by` reference.
- Degradation banner is inherited from `PKT-005` and must be non-dismissable.
- If any required field is absent from the BFF response, write a `bff-gap` coordination file and stop implementation.

---

## Navigation Context

This module sits inside the Knowledge Workbench sidebar section.

- Knowledge Overview (`/knowledge`) links to the memory list.
- The list row `route_href` navigates to the detail.
- Detail breadcrumb: Knowledge Overview → Institutional Memory → Entry headline.

---

## References

- BFF contract: `docs/bff/KW-01-institutional-memory.md`
- Example payload (detail): `docs/examples/KW-01-institutional-memory.json`
- Frontend change spec: `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
- Overview packet: `docs/bff/PKT-knowledge-workbench.md`
- Memory object schema: `services/memory/institutional_memory_entry.schema.json`
- Degradation substrate: `PKT-005`
