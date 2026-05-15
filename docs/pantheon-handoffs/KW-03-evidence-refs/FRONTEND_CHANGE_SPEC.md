# KW-03 Evidence Refs — Frontend Change Spec

## Feature

- Feature ID: `KW-03-evidence-refs`
- Screen IDs: `screen-knowledge-evidence-list`, `screen-knowledge-evidence-detail`
- Workbench: Knowledge Workbench
- Packet status: route-live — UI implementation may proceed against the live BFF routes
- Task: `KW-03-EVIDENCE-REFS-001`

## Readiness Gate

Pantheon has confirmed **both** of the following routes are live and returning the published field shape:

1. `GET /api/v1/knowledge/evidence` — returns a paginated list with `ref_id`, source-document identity, `link_type`, credibility metadata, `linked_object_summary`, `resolved_link`, and `meta.surfaces.evidence_refs_list`.
2. `GET /api/v1/knowledge/evidence/{ref_id}` — returns full evidence reference detail with source-document fields, `credibility`, `resolved_link`, `linked_decisions`, `source_note_context`, `source_memory_context`, and per-panel surface state.

Build the production pages against these live surfaces. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/KW-03-evidence-refs-bff-gap.yaml` instead of inventing state or constructing URLs from raw `ref_id`, `source_ref`, or `storage_ref` values.

## Summary

Build the **Evidence Refs** list and detail screens inside `front-ai-trading-system`. This slice lets operators browse and inspect evidence references that back institutional-memory decisions, research notes, insight cards, and strategy-spec citations. All link resolution, credibility metadata, and linked-decision panels come from the Pantheon BFF — no client-side URL construction from raw storage refs, no credibility inference from field names or file extensions, no reverse-resolution of linked decisions from raw entity refs.

## Files to Create or Modify

```
src/pages/knowledge/EvidenceRefList.tsx       — new Evidence Refs list page
src/pages/knowledge/EvidenceRefDetail.tsx     — new Evidence Ref detail page
src/pages/knowledge/EvidenceRefTypes.ts       — add evidence ref, link type, and credibility types
src/lib/bffClient.ts                          — add KW-03 evidence list and detail fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### List evidence references

```
GET /api/v1/knowledge/evidence
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `linked_entity_type` | string | One of `memory_entry \| research_note \| insight_card \| strategy_spec \| experiment \| artifact` |
| `linked_entity_ref` | string | Requires `linked_entity_type`; filter to refs linked to a specific entity id |
| `link_type` | string | One of `supporting_evidence \| counter_evidence \| citation \| provenance \| corroboration` |
| `credibility_tier` | string | One of `primary \| secondary \| tertiary \| unverified` |
| `verified` | boolean | `true` returns only verified refs; `false` returns only unverified |
| `page_token` | string | Opaque keyset cursor |
| `page_size` | number | Default 20, max 100 |

Expected response shape (see `docs/examples/KW-03-evidence-refs.json` for full example):

```typescript
interface EvidenceRefListResponse {
  evidence_refs: EvidenceRefSummary[];
  pagination: {
    page_size: number;
    next_page_token: string | null;
    has_more: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: { evidence_refs_list: "ok" | "degraded" | "unavailable" };
  };
}

interface EvidenceRefSummary {
  ref_id: string;                          // format: evref-{UUID}
  source_document: {
    title: string;
    source_type: "research_note" | "memory_entry" | "external_paper" | "experiment_artifact" | "incident_report" | "postmortem" | "audit_log";
    source_ref: string;                    // opaque storage ref — never used for URL construction
    captured_at: string;
  };
  link_type: "supporting_evidence" | "counter_evidence" | "citation" | "provenance" | "corroboration";
  credibility: {
    tier: "primary" | "secondary" | "tertiary" | "unverified";
    verified: boolean;
  };
  linked_object_summary: {
    entity_type: string;
    entity_ref: string;
    display_label: string | null;         // BFF-resolved — do not resolve from entity_ref
  };
  resolved_link: {
    availability: "available" | "unavailable" | "external";
    route_href: string | null;
    display_label: string;
    open_in_new_tab: boolean;
  };
  route_href: string;                     // BFF-provided canonical path to this evidence ref detail
}
```

### Get evidence reference detail

```
GET /api/v1/knowledge/evidence/{ref_id}
Path param: ref_id (required, format: evref-{UUID})
```

Expected response shape:

```typescript
interface EvidenceRefDetail {
  ref_id: string;
  source_document: {
    title: string;
    source_type: string;
    excerpt: string | null;              // plain text, max 500 chars — do not render as markdown
    source_ref: string;                  // opaque — never used for URL construction
    storage_preview: {
      available: boolean;
      preview_type: "text" | "image" | "pdf" | "unavailable";
      preview_token: string | null;      // short-lived BFF token; do not cache beyond the response
    };
    captured_at: string;
    captured_by: string;                 // BFF-resolved display name
  };
  link_type: "supporting_evidence" | "counter_evidence" | "citation" | "provenance" | "corroboration";
  credibility: {
    tier: "primary" | "secondary" | "tertiary" | "unverified";
    verified: boolean;
    last_verified_at: string | null;
    verification_method: string | null;
  };
  resolved_link: {
    availability: "available" | "unavailable" | "external";
    route_href: string | null;
    display_label: string;
    open_in_new_tab: boolean;
  };
  linked_decisions: LinkedDecision[];
  source_note_context: SourceNoteContext | null;
  source_memory_context: SourceMemoryContext | null;
  created_at: string;
  meta: {
    snapshot_at: string;
    surfaces: {
      evidence_ref_detail: "ok" | "degraded" | "unavailable";
      resolved_link: "ok" | "degraded" | "unavailable";
      linked_decisions: "ok" | "degraded" | "unavailable";
    };
  };
}

interface LinkedDecision {
  entity_type: string;
  entity_ref: string;
  display_label: string | null;         // BFF-resolved — do not reverse-resolve from entity_ref
  route_href: string | null;            // BFF-resolved canonical path — do not construct
  link_type: string;
  relationship_note: string | null;
}

interface SourceNoteContext {
  note_id: string;                      // format: note-{UUID}
  title: string | null;
  excerpt: string | null;
  route_href: string | null;
}

interface SourceMemoryContext {
  entry_id: string;                     // format: mem-{UUID}
  headline: string | null;
  knowledge_type: string | null;
  lifecycle_status: "active" | "archived" | "superseded" | null;
  route_href: string | null;
}
```

## Component Structure

### `EvidenceRefList.tsx`

- Route: `/knowledge/evidence`
- Fetches `GET /api/v1/knowledge/evidence` on mount; supports `linked_entity_type`, `linked_entity_ref`, `link_type`, `credibility_tier`, and `verified` filter params.
- Each row: `source_document.title`, `source_document.source_type`, `link_type` badge, `credibility.tier` badge, `credibility.verified` indicator, `linked_object_summary.display_label`, `resolved_link.display_label`.
- `source_document.source_ref` is opaque storage metadata — do not display it, parse it, or use it to construct any link.
- `linked_object_summary.display_label` is BFF-resolved — do not resolve from raw `entity_ref`.
- `resolved_link` is the only field from which the UI may derive a navigable link.
- Pagination via `next_page_token` cursor; do not pass a page integer.
- Row click navigates to `/knowledge/evidence/{ref_id}` using the BFF-provided `route_href`.
- When `meta.surfaces.evidence_refs_list` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never treat an empty `evidence_refs[]` array as authoritative when the surface is stale.

### `EvidenceRefDetail.tsx`

- Route: `/knowledge/evidence/:ref_id`
- Fetches `GET /api/v1/knowledge/evidence/{ref_id}` on mount.
- Renders `source_document.excerpt` as plain text — do not render as markdown.
- `source_document.storage_preview.preview_token` is short-lived — do not cache it beyond the response. Do not construct a preview URL from `source_ref` directly.
- `resolved_link` is the sole source for any navigable external or internal link. When `availability` is `unavailable`, show a degraded link indicator; when `external`, open in new tab (`open_in_new_tab: true`).
- `linked_decisions` panel: render `display_label` and `route_href` from BFF — do not reverse-resolve raw entity refs. When `meta.surfaces.linked_decisions` is `degraded`, show an inline partial-data indicator inside the panel rather than hiding it.
- `source_note_context`: when `null`, hide the panel entirely. When present, use `route_href` from the BFF to navigate to the source note.
- `source_memory_context`: when `null`, hide the panel entirely. When present, render `lifecycle_status` from the BFF — do not infer superseded state from `superseded_by` presence.
- `credibility.last_verified_at: null` means no verification event is on record — show an unverified indicator rather than hiding the field.
- When `meta.surfaces.evidence_ref_detail` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never collapse to an empty detail view.

## Degradation Handling

| `meta.surfaces.evidence_refs_list` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; evidence rows visible |
| `unavailable` | Replace list content with unavailable notice; never show "no evidence" as authoritative |

| `meta.surfaces.evidence_ref_detail` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; detail content visible |
| `unavailable` | Replace detail panel with unavailable notice; never collapse to empty view |

| `meta.surfaces.resolved_link` | Required behavior |
|---|---|
| `ok` | Render resolved link normally |
| `degraded` | Show link with staleness indicator |
| `unavailable` | Show degraded link indicator; `route_href` will be null |

| `meta.surfaces.linked_decisions` | Required behavior |
|---|---|
| `ok` | Render linked-decision panel normally |
| `degraded` | Inline partial-data indicator within the panel; do not hide the panel |
| `unavailable` | Show panel-level unavailable notice |

## Resolved Link Availability States

| `availability` | Required display |
|---|---|
| `available` | Active internal link using `route_href`; `open_in_new_tab` is `false` |
| `unavailable` | Degraded link indicator; `route_href` is null; do not hide the field |
| `external` | Active link opening in new tab; `open_in_new_tab` is `true`; `route_href` is the external canonical URI provided by the data plane |

Do not derive `availability` from `source_ref` suffix, MIME type, or guessed path conventions.

## Link Type Vocabulary

The taxonomy is backend-owned. Render badges or labels as provided; do not extend or reinterpret client-side.

| `link_type` | Meaning |
|---|---|
| `supporting_evidence` | Source document positively supports the linked decision or artifact |
| `counter_evidence` | Source document presents findings that oppose or constrain the linked object |
| `citation` | Source document is cited as a formal reference without directional claim |
| `provenance` | Source document is the origin event or artifact that generated the linked object |
| `corroboration` | Source document independently confirms a finding already linked from another ref |

## Credibility Tier Vocabulary

| `tier` | Meaning |
|---|---|
| `primary` | Direct observation or authoritative source |
| `secondary` | Derived or summarized |
| `tertiary` | Background or contextual |
| `unverified` | Has not been reviewed |

Do not infer `verified` from `tier` alone — use the explicit `verified` boolean from the BFF.
Do not gate any CTA, visibility, or sort order on `credibility.tier` without an explicit `allowedActions` flag from the BFF.

## State Requirements

Each data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `degraded`: staleness banner or inline partial-data indicator
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Do not construct any URL or link from raw `ref_id`, `source_ref`, `storage_ref`, or object names.
- Do not derive `link_type` or `credibility.tier` from source document MIME type, file extension, or path prefix.
- Do not reverse-resolve `linked_decisions[].display_label` or `route_href` from raw entity refs.
- Do not infer `source_note_context` or `source_memory_context` from raw refs or opaque string ids.
- Do not aggregate or filter evidence refs on the client side when server-side query parameters are available.
- Do not display an empty list or "no evidence" message as authoritative when `meta.surfaces.evidence_refs_list` is `degraded` or `unavailable`.
- If any required field is absent from the BFF response, write `.coordination/requests/KW-03-evidence-refs-bff-gap.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/KW-03-evidence-refs-ui-done.yaml` using the same pattern as `.coordination/requests/PKT-knowledge-workbench-ui-done.yaml`.

## References

- BFF contract: `docs/bff/KW-03-evidence-refs.md`
- Example payloads: `docs/examples/KW-03-evidence-refs.json`
- Packet family: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
- KW-01 anchor identity: `docs/bff/KW-01-institutional-memory.md` (for `mem-{UUID}` anchor format and lifecycle semantics)
- KW-02 source-note context: `docs/bff/KW-02-research-notes.md` (for `note-{UUID}` identity and note ownership contract)
- Evidence-link resolution precedent: `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` (CS-05)
