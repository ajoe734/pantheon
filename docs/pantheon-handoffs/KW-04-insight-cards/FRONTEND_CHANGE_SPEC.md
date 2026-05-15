# KW-04 Insight Cards — Frontend Change Spec

## Feature

- Feature ID: `KW-04-insight-cards`
- Screen IDs: `screen-knowledge-insight-card-list`, `screen-knowledge-insight-card-detail`
- Workbench: Knowledge Workbench
- Packet status: route-live — UI implementation may proceed against the live insight routes
- Task: `KW-04-INSIGHT-CARDS-001`

## Readiness Gate

Pantheon has confirmed **both** of the following routes are live and returning the published field shape:

1. `GET /api/v1/knowledge/insights` — returns the backend-owned card grid, filter metadata, pagination, and `meta.surfaces.insight_cards`.
2. `GET /api/v1/knowledge/insights/{insight_id}` — returns full card detail, scope context, supersession info, supporting evidence refs, linked sources, and per-panel surface states.

Build the production pages against these live surfaces. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/KW-04-insight-cards-bff-gap.yaml` instead of re-synthesizing cards, tags, linked-entity filters, or drilldown routes client-side.

## Summary

Build the **Insight Cards** list and detail screens inside `front-ai-trading-system`. This slice renders the card grid, backend-owned filter rail, confidence and lifecycle signals, evidence panel, and linked-source drilldown. All aggregation, provenance, filter vocabulary, and supersession semantics come from the Pantheon BFF. The frontend must not recreate the card synthesis pipeline in the browser.

## Files to Create or Modify

```text
src/pages/knowledge/InsightCardList.tsx      — new Insight Cards list page
src/pages/knowledge/InsightCardDetail.tsx    — new Insight Card detail page
src/pages/knowledge/InsightCardTypes.ts      — card, filter, and detail types
src/lib/bffClient.ts                         — add KW-04 list and detail calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### List insight cards

```http
GET /api/v1/knowledge/insights
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `status` | string | `active \| superseded \| archived \| all` |
| `tag` | string | Must match a backend-provided tag value |
| `linked_entity_type` | string | Backend-owned enum from `filter_metadata.linked_entity_types[]` |
| `linked_entity_ref` | string | Requires `linked_entity_type` |
| `recency` | string | Backend-owned bucket from `filter_metadata.recency_options[]` |
| `confidence_min` | number | Minimum accepted score |
| `page_token` | string | Opaque keyset cursor |
| `page_size` | number | Default 20, max 100 |
| `include_inactive` | boolean | Includes superseded and archived cards |

Expected response shape (see `docs/examples/KW-04-insight-cards.json` for full examples):

```typescript
interface InsightCardListResponse {
  insight_cards: InsightCardSummary[];
  filter_metadata: {
    tags: Array<{ value: string; display_label: string; count: number }>;
    linked_entity_types: Array<{ value: string; display_label: string; count: number }>;
    recency_options: Array<{ value: string; display_label: string }>;
    total_active_count: number;
  };
  pagination: {
    page_size: number;
    next_page_token: string | null;
    has_more: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: { insight_cards: "ok" | "degraded" | "unavailable" };
  };
}

interface InsightCardSummary {
  insight_id: string;
  summary: string;
  scope: "global" | "persona" | "strategy" | "experiment" | "incident";
  scope_ref: string | null;
  status: "active" | "superseded" | "archived";
  superseded_by_id: string | null;
  confidence: {
    score: number;
    label: "high" | "medium" | "low" | "insufficient_evidence";
  };
  tags: string[];
  evidence_count: number;
  primary_evidence_count: number;
  aggregated_at: string;
  route_href: string;
}
```

### Get insight card detail

```http
GET /api/v1/knowledge/insights/{insight_id}
```

Expected response shape:

```typescript
interface InsightCardDetail {
  insight_id: string;
  summary: string;
  scope: string;
  scope_context: {
    scope_ref: string | null;
    display_label: string | null;
    route_href: string | null;
  };
  status: "active" | "superseded" | "archived";
  superseded_by: {
    insight_id: string | null;
    summary: string | null;
    route_href: string | null;
  };
  confidence: {
    score: number;
    label: "high" | "medium" | "low" | "insufficient_evidence";
    basis: string;
  };
  tags: string[];
  source_ref: string;
  supporting_evidence_refs: Array<{
    ref_id: string;
    source_document_title: string;
    link_type: string;
    credibility_tier: string;
    resolved_link: {
      availability: "available" | "unavailable" | "external";
      route_href: string | null;
      display_label: string;
      open_in_new_tab: boolean;
    };
  }>;
  linked_sources: Array<{
    entity_type: string;
    entity_ref: string;
    display_label: string;
    route_href: string | null;
    relationship_note: string | null;
  }>;
  aggregation_provenance: {
    memory_entry_count: number;
    note_count: number;
    evidence_ref_count: number;
    primary_evidence_count: number;
    aggregated_at: string;
    aggregation_version: string;
  };
  created_at: string;
  updated_at: string;
  meta: {
    snapshot_at: string;
    surfaces: {
      insight_card_detail: "ok" | "degraded" | "unavailable";
      supporting_evidence_refs: "ok" | "degraded" | "unavailable";
      linked_sources: "ok" | "degraded" | "unavailable";
    };
  };
}
```

## Component Structure

### `InsightCardList.tsx`

- Route: `/knowledge/insights`
- Fetches `GET /api/v1/knowledge/insights` on mount and on filter change.
- Filter controls must be populated only from `filter_metadata.tags`, `filter_metadata.linked_entity_types`, and `filter_metadata.recency_options`.
- Do not submit `linked_entity_ref` unless `linked_entity_type` is explicitly set.
- Preserve backend ordering exactly. Do not re-sort cards by score or recency locally.
- `summary` is plain text; do not render it as markdown.
- Use `route_href` from the list payload as the canonical navigation target for detail drilldown.
- `confidence.label` is backend-owned. Do not recalculate it from `confidence.score`.

### `InsightCardDetail.tsx`

- Route: `/knowledge/insights/:insight_id`
- Fetches `GET /api/v1/knowledge/insights/{insight_id}` on mount.
- `scope_context.display_label` and `scope_context.route_href` are BFF-resolved. Do not derive them from `scope_ref`.
- `source_ref` is opaque. Do not display it, parse it, or use it for navigation.
- `supporting_evidence_refs[].resolved_link` is the only valid source for evidence navigation. Use it exactly as returned.
- `linked_sources[]` is a BFF-resolved panel. Do not reverse-resolve `entity_ref` into labels or routes client-side.
- When `status === "superseded"` and `superseded_by.route_href` is present, render a prominent supersession notice linking to the replacement card.
- Render `aggregation_provenance` as a read-only backend-authored provenance panel. Do not recompute counts.

## Degradation Handling

| Surface | Required behavior |
|---|---|
| `meta.surfaces.insight_cards` | `ok`: normal list; `degraded`: non-dismissable degradation banner with rows still visible; `unavailable`: replace list content with unavailable notice |
| `meta.surfaces.insight_card_detail` | `ok`: normal detail; `degraded`: non-dismissable degradation banner with current detail visible; `unavailable`: replace detail content with unavailable notice |
| `meta.surfaces.supporting_evidence_refs` | `ok`: normal evidence panel; `degraded`: inline partial-data indicator in the evidence panel; `unavailable`: panel-level unavailable notice |
| `meta.surfaces.linked_sources` | `ok`: normal linked-sources panel; `degraded`: inline partial-data indicator in the linked-sources panel; `unavailable`: panel-level unavailable notice |

## Constraints

- Use the existing BFF client only. Do not add raw network calls in component files.
- Do not aggregate insight cards by combining KW-01, KW-02, and KW-03 data in the browser.
- Do not invent filter vocabularies, recency buckets, or linked-entity labels.
- Do not construct URLs from raw `insight_id`, `source_ref`, `entity_ref`, or `scope_ref`.
- Do not hide superseded or archived cards when the backend includes them.
- Do not infer confidence or lifecycle semantics from local heuristics.

## References

- BFF contract: `docs/bff/KW-04-insight-cards.md`
- Example payload: `docs/examples/KW-04-insight-cards.json`
- Packet family: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
