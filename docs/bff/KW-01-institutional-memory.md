# KW-01 Institutional Memory BFF Contract

## Purpose

Provide the first real browse module for the Knowledge Workbench. This contract defines the identity, lifecycle, and browse projection for Institutional Memory entries, enabling the transition from a shell-only overview to a truthful navigable module.

## Primary Read Routes

### 1. List Institutional Memory Entries
- `GET /api/v1/knowledge/memory`

**Query Parameters:**
- `knowledge_type` (optional): Filter by `incident_lesson`, `regime_pattern`, `policy_precedent`, `research_finding`, `evolution_rationale`, `cross_persona_observation`.
- `scope` (optional): Filter by `system_wide`, `strategy_family`, `instrument_class`.
- `scope_filter` (optional): Specific value for scope (e.g., 'momentum').
- `tags` (optional): Comma-separated tags.
- `page` (optional): Default 1.
- `page_size` (optional): Default 20.

**Response Shape:**
```json
{
  "entries": [
    {
      "entry_id": "string (UUID)",
      "knowledge_type": "string (enum)",
      "headline": "string",
      "scope": "string (enum)",
      "scope_filter": "string | null",
      "written_at": "string (ISO-8601)",
      "write_authority": "string (enum)",
      "tags": ["string"],
      "reuse_count": "number",
      "is_superseded": "boolean",
      "route_href": "string (/knowledge/memory/{entry_id})"
    }
  ],
  "pagination": {
    "total_count": 120,
    "page": 1,
    "page_size": 20,
    "total_pages": 6
  },
  "meta": {
    "snapshot_at": "2026-04-19T10:00:00Z",
    "surfaces": {
      "memory_list": "ok | degraded | unavailable"
    }
  }
}
```

### 2. Get Institutional Memory Detail
- `GET /api/v1/knowledge/memory/{entry_id}`

**Response Shape:**
```json
{
  "entry_id": "string (UUID)",
  "knowledge_type": "string (enum)",
  "content": {
    "headline": "string",
    "body": "string (markdown supported)",
    "structured_payload": "object | null",
    "tags": ["string"]
  },
  "source_event": {
    "type": "string (enum)",
    "id": "string",
    "href": "string | null (internal link to the source incident, evolution, or research task)"
  },
  "contributing_persona_ids": ["string"],
  "written_at": "string (ISO-8601)",
  "write_authority": "string (enum)",
  "scope": {
    "type": "string (enum)",
    "filter": "string | null"
  },
  "lifecycle": {
    "status": "active | archived | superseded",
    "superseded_by": "string (entry_id) | null"
  },
  "usage": {
    "reuse_count": 15,
    "last_cited_at": "2026-04-18T14:20:00Z"
  },
  "meta": {
    "snapshot_at": "2026-04-19T10:00:00Z",
    "surfaces": {
      "entry_detail": "ok | degraded | unavailable",
      "source_context": "ok | degraded | unavailable"
    }
  }
}
```

## Design Rules

- **Identity First**: The `entry_id` is the anchor for all downstream Knowledge Workbench modules (Notes, Evidence, Insights).
- **Backend-Owned Ranking**: The list order is determined by the BFF/Memory Plane (e.g., by `written_at` or `reuse_count`). The UI must not sort locally.
- **Surface Health**: Use `meta.surfaces` to signal if the memory store or source event resolution is degraded.
- **Read-Only**: This module is currently read-only for the Knowledge Workbench. Writing is performed by authority services (Incident, Evolution, etc.).

## Relationship to other Modules

- **KW-02 Research Notes**: Notes will link to `entry_id` as an anchor.
- **KW-03 Evidence Refs**: Evidence links will resolve through Institutional Memory entries.
- **KW-04 Insight Cards**: Uses these entries as a primary aggregation source.
