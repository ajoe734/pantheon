# KW-05 Strategy Spec — Frontend Change Spec

## Feature

- Feature ID: `KW-05-strategy-spec`
- Screen IDs: `screen-knowledge-strategy-spec-list`, `screen-knowledge-strategy-spec-detail`, `screen-knowledge-strategy-spec-compare`
- Workbench: Knowledge Workbench
- Packet status: route-live — UI implementation may proceed against the live strategy-spec routes
- Task: `KW-05-STRATEGY-SPEC-001`

## Readiness Gate

Pantheon has confirmed **all four** of the following routes are live and returning the published field shape:

1. `GET /api/v1/knowledge/strategy-specs` — returns the strategy family list, filters, pagination, and `meta.surfaces.strategy_spec_list`.
2. `GET /api/v1/knowledge/strategy-specs/{strategy_id}` — returns one immutable spec version with canonical identity, ancestry, lifecycle, citations, and `allowedActions`.
3. `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions` — returns version history rows and canonical `route_href` values for version navigation.
4. `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare` — returns backend-generated compare output keyed by canonical left/right version identities.

Build the production pages against these live surfaces. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/KW-05-strategy-spec-bff-gap.yaml` instead of diffing raw spec JSON, reconstructing ancestry, or inventing version lifecycle logic client-side.

## Summary

Build the **Strategy Spec** list, detail, version-history, and compare surfaces inside `front-ai-trading-system`. This slice lets operators browse strategy families, inspect immutable versions, follow evidence and memory citations, and compare two versions using a backend-generated diff. All version identity, lifecycle, ancestry, citation links, and compare semantics come from the Pantheon BFF.

## Files to Create or Modify

```text
src/pages/knowledge/StrategySpecList.tsx      — new strategy-spec list page
src/pages/knowledge/StrategySpecDetail.tsx    — new versioned detail page
src/pages/knowledge/StrategySpecCompare.tsx   — new compare surface
src/pages/knowledge/StrategySpecTypes.ts      — list, detail, history, and compare types
src/lib/bffClient.ts                          — add KW-05 list, detail, versions, and compare calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### List strategy specs

```http
GET /api/v1/knowledge/strategy-specs
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `lifecycle_state` | string | `draft \| candidate \| approved \| retired \| all` |
| `source_kind` | string | Backend-owned source taxonomy |
| `persona_id` | string | Filter by bound persona |
| `include_retired` | boolean | Include retired families in the list |
| `page_token` | string | Opaque pagination cursor |
| `page_size` | number | Default 20 |

Expected response shape (see `docs/examples/KW-05-strategy-spec.json` for full examples):

```typescript
interface StrategySpecListResponse {
  items: StrategySpecSummary[];
  page_info: {
    next_page_token: string | null;
    page_size: number;
    has_more: boolean;
  };
  meta: {
    snapshot_at: string;
    staleness: Record<string, unknown>;
    surfaces: { strategy_spec_list: "ok" | "degraded" | "unavailable" };
  };
}

interface StrategySpecSummary {
  object_ref: { type: "StrategySpec"; id: string };
  strategy_id: string;
  current_spec_version_id: string;
  current_spec_version: string;
  title: string;
  lifecycle_state: "draft" | "candidate" | "approved" | "retired";
  source_kind: string;
  hypothesis_excerpt: string;
  version_count: number;
  last_modified_at: string;
  route_href: string;
}
```

### Get versioned strategy spec detail

```http
GET /api/v1/knowledge/strategy-specs/{strategy_id}
```

Supported query parameter:

| Param | Type | Notes |
|---|---|---|
| `version` | string | `current`, a `spec_version_id`, or a human-readable `spec_version` label |

Required response fields:

```typescript
interface StrategySpecDetail {
  object_ref: { type: "StrategySpec"; id: string };
  strategy_id: string;
  spec_version_id: string;
  spec_version: string;
  parent_spec_version_id: string | null;
  derived_from_source_refs: string[];
  lifecycle_state: "draft" | "candidate" | "approved" | "retired";
  title: string;
  hypothesis: string;
  objective: string;
  market_scope: Record<string, unknown>;
  execution_profile: Record<string, unknown>;
  evaluation_plan: Record<string, unknown>;
  governance: Record<string, unknown>;
  citation_bundle: {
    evidence_refs: Array<{
      ref_id: string;
      source_document_title: string;
      link_type: string;
      credibility_tier: string;
      association?: string;
      resolved_link: {
        availability: "available" | "unavailable" | "external";
        route_href: string | null;
        display_label: string;
        open_in_new_tab: boolean;
      };
    }>;
    memory_anchors: Array<{
      entry_id: string;
      knowledge_type: string;
      content_headline: string;
      route_href: string | null;
    }>;
    insight_citations: Array<{
      insight_id: string;
      summary: string;
      route_href: string | null;
    }>;
  };
  allowedActions: {
    canSubmitForApproval: boolean;
    canRetire: boolean;
    canCompare: boolean;
  };
  meta: {
    snapshot_at: string;
    staleness: Record<string, unknown>;
    surfaces: {
      strategy_spec_detail: "ok" | "degraded" | "unavailable";
      citation_bundle: "ok" | "partial" | "degraded" | "unavailable";
      version_ancestry: "ok" | "degraded" | "unavailable";
    };
  };
}
```

### List version history

```http
GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions
```

Expected response fields:

```typescript
interface StrategySpecVersionHistory {
  strategy_id: string;
  versions: Array<{
    spec_version_id: string;
    spec_version: string;
    lifecycle_state: "draft" | "candidate" | "approved" | "retired";
    created_at: string;
    created_by: string;
    parent_spec_version_id: string | null;
    route_href: string;
  }>;
  meta: {
    snapshot_at: string;
    surfaces: { version_history: "ok" | "degraded" | "unavailable" };
  };
}
```

### Compare two versions

```http
GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare
```

Accepted query aliases:

- `left_version` and `right_version`
- `base_version` and `target_version`

Expected response fields:

```typescript
interface StrategySpecCompareResponse {
  strategy_id: string;
  left_spec_version_id: string;
  right_spec_version_id: string;
  changed_sections: Array<{ section: string; summary: string }>;
  breaking_changes: Array<{ section: string; summary: string; severity?: string }>;
  evidence_refs: string[];
  meta: {
    snapshot_at: string;
    staleness: Record<string, unknown>;
    surfaces: { strategy_spec_compare: "ok" | "degraded" | "unavailable" };
  };
}
```

## Component Structure

### `StrategySpecList.tsx`

- Route: `/knowledge/strategy-specs`
- Fetches `GET /api/v1/knowledge/strategy-specs` on mount and on filter change.
- Use backend query params exactly as published. Do not translate lifecycle into local booleans or hidden tabs.
- Respect `include_retired` explicitly. Do not assume retired strategies are always excluded or always visible.
- Use each row's `route_href` as the canonical navigation target to the current version.

### `StrategySpecDetail.tsx`

- Route: `/knowledge/strategy-specs/:strategy_id`
- Reads optional `version` from the query string and forwards it to the BFF detail route.
- Render lifecycle badges directly from `lifecycle_state`. Do not infer lifecycle from approval metadata, timestamps, or ancestry position.
- `parent_spec_version_id` and `derived_from_source_refs[]` are backend-owned ancestry fields. Do not reconstruct lineage from version labels.
- `citation_bundle` links are BFF-resolved. Use `resolved_link`, `route_href`, and `display_label` exactly as returned.
- `allowedActions.canCompare` is the sole source of truth for showing an active compare CTA.

### `StrategySpecCompare.tsx`

- Route: `/knowledge/strategy-specs/:strategy_id/compare` or an equivalent compare panel scoped to one `strategy_id`.
- Submit compare requests only when two distinct comparable versions are selected.
- Accept either `left/right` or `base/target` query conventions, but do not send duplicate or single-version requests.
- Render `changed_sections[]` and `breaking_changes[]` exactly as returned. Do not diff raw spec JSON locally.
- Surface BFF validation errors for missing compare params, duplicate versions, or invalid lifecycle states instead of silently coercing the selection.

### Version History Rail

- Fetch `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions`.
- Use each version row's `route_href` exactly as returned. Do not construct version links from `spec_version_id` or `spec_version`.
- Preserve backend order in the history list.

## Degradation Handling

| Surface | Required behavior |
|---|---|
| `meta.surfaces.strategy_spec_list` | `ok`: normal list; `degraded`: non-dismissable degradation banner with rows still visible; `unavailable`: replace list with unavailable notice |
| `meta.surfaces.strategy_spec_detail` | `ok`: normal detail; `degraded`: non-dismissable degradation banner with current version still visible; `unavailable`: replace detail with unavailable notice |
| `meta.surfaces.citation_bundle` | `ok`: normal citations; `partial`: inline partial-data indicator while keeping citations visible; `degraded`: panel-level degraded indicator; `unavailable`: panel-level unavailable notice |
| `meta.surfaces.version_ancestry` | `ok`: normal ancestry display; `degraded`: show ancestry with degraded indicator; `unavailable`: hide ancestry graph and show unavailable notice |
| `meta.surfaces.version_history` | `ok`: normal version rail; `degraded`: show current rows with degraded indicator; `unavailable`: suppress version history and show unavailable notice |
| `meta.surfaces.strategy_spec_compare` | `ok`: normal compare display; `degraded`: show last-known comparison with a non-dismissable degraded banner; `unavailable`: replace compare surface with unavailable notice |

## Constraints

- Use the existing BFF client only. Do not add raw network calls in component files.
- Do not diff arbitrary JSON locally.
- Do not derive version ancestry from timestamps, version labels, or list position.
- Do not construct citation or version navigation links from raw ids when the BFF already provides `route_href`.
- Do not infer compare eligibility from lifecycle labels alone; honor `allowedActions.canCompare` and BFF compare errors.
- Do not mutate or author strategy specs in this packet. This slice is read and compare only.

## References

- BFF contract: `docs/bff/KW-05-strategy-spec.md`
- Example payload: `docs/examples/KW-05-strategy-spec.json`
- Packet family: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
