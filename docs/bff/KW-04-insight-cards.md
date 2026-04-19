# KW-04 Insight Cards BFF Contract

## Purpose

Provide the fourth browse module for the Knowledge Workbench. This contract defines the insight card aggregation endpoint, per-card detail route, card-surface read model, filter taxonomy, and aggregation provenance semantics for the Insight Cards module. It eliminates client-side synthesis of memory, notes, and evidence into insight cards.

Upstream dependencies:
- `KW-01` must be Lovable-ready. Insight aggregation consumes `entry_id` anchors from Institutional Memory as one of its upstream inputs.
- `KW-03` must be Lovable-ready. Insight cards reference `ref_id` values from Evidence Refs as supporting evidence. The KW-03 read model is the canonical source for per-card evidence display.

---

## Insight Card Identity

Every insight card has a stable `insight_id` of the format `ins-{UUID}`. This id is the canonical key used by KW-04 and referenced by KW-05 (Strategy Spec citation panels).

- `insight_id` must never be guessed, derived from summary text, or constructed from scope or tag fields.
- The BFF synthesizes the card from upstream memory, notes, and evidence at aggregation time. The frontend must not replicate this synthesis locally.

---

## Card Lifecycle

Insight cards have a simple lifecycle that the BFF resolves and the frontend renders without local inference.

| `status` | Meaning |
|---|---|
| `active` | Card is current and surfaced for browsing and drilldown |
| `superseded` | A newer insight supersedes this card; `superseded_by_id` is non-null |
| `archived` | Card has been administratively retired; no replacement |

Display rules:
- `superseded` and `archived` cards remain visible in the list when explicitly included via the `include_inactive` filter. They must not be silently hidden.
- When a card is `superseded`, the detail view must show a `superseded_by_id` reference and the BFF-resolved route to the replacement card.
- The frontend must not infer lifecycle status from timestamp recency, confidence score, or tag staleness.

---

## Confidence Scale

Each insight card carries a `confidence` value resolved by the BFF from the aggregation inputs.

```
confidence: {
  score: number (0.0–1.0, inclusive),
  label: "high | medium | low | insufficient_evidence",
  basis: "string (human-readable explanation of confidence basis, e.g., 'Supported by 4 primary evidence refs across 3 independent sources')"
}
```

- `label` is derived from `score` ranges locked by the BFF. The frontend must not re-derive `label` from `score` locally.
- `basis` is a BFF-authored explanation. The frontend renders it verbatim; it must not rephrase or abbreviate it.
- `insufficient_evidence` indicates that the aggregation ran but could not compute a meaningful confidence estimate; the card is still valid but must display an "unconfirmed" indicator.

---

## Filter Taxonomy

The BFF owns all filter vocabularies. The frontend must not hardcode tag labels, entity type names, or recency bucket definitions.

### Tag filter

Tags are backend-provided strings from the insight registry. The list route returns a `filter_metadata.tags[]` array containing all tags available for the current corpus, including counts. The frontend renders this array and must not invent or merge tags locally.

### Linked entity filter

`linked_entity_type` constrains cards to those whose aggregation inputs include a specific entity type. Allowed values are returned in `filter_metadata.linked_entity_types[]` and are a subset of:

| `linked_entity_type` | Upstream source |
|---|---|
| `memory_entry` | Institutional Memory (`KW-01`) |
| `research_note` | Research Notes (`KW-02`) |
| `evidence_ref` | Evidence Refs (`KW-03`) |
| `strategy_spec` | Strategy Spec (`KW-05`) |
| `experiment` | Research Workbench experiment |

Combining `linked_entity_type` and `linked_entity_ref` constrains results to cards linked to a specific upstream entity. `linked_entity_ref` requires `linked_entity_type`.

### Recency filter

`recency` is a backend-interpreted bucket. The BFF defines the bucket semantics; the frontend must not compute recency from `created_at` or `updated_at` locally.

| `recency` value | BFF interpretation |
|---|---|
| `7d` | Cards with aggregation activity within 7 days |
| `30d` | Cards with aggregation activity within 30 days |
| `90d` | Cards with aggregation activity within 90 days |
| `all` | No recency constraint (default) |

---

## Aggregation Provenance

Each insight card carries an `aggregation_provenance` object that explains which upstream inputs contributed to the card. The frontend renders this panel; it must not reconstruct provenance from raw linked-entity refs.

```
aggregation_provenance: {
  memory_entry_count: number,
  note_count: number,
  evidence_ref_count: number,
  primary_evidence_count: number,
  aggregated_at: string (ISO-8601),
  aggregation_version: string
}
```

- `primary_evidence_count` is the count of evidence refs with `credibility.tier = "primary"` that contributed to the card.
- `aggregation_version` is an opaque string indicating the version of the aggregation pipeline that produced the card. The frontend must not parse or compare it.

---

## Read Routes

### 1. List and Aggregate Insight Cards

**`GET /api/v1/knowledge/insights`**

**Query parameters:**
- `status` (optional): One of `active | superseded | archived | all`. Default `active`.
- `tag` (optional): Filter by a single backend-provided tag string. Must match a value from `filter_metadata.tags[].value`.
- `linked_entity_type` (optional): One of the linked entity type enum values above.
- `linked_entity_ref` (optional): Requires `linked_entity_type`. Filter to cards linked to this specific entity id.
- `recency` (optional): One of `7d | 30d | 90d | all`. Default `all`.
- `confidence_min` (optional): Minimum confidence `score` (0.0–1.0). Cards with `score` below this threshold are excluded.
- `page_token` (optional): Opaque cursor for keyset pagination.
- `page_size` (optional): Default 20, max 100.
- `include_inactive` (optional): Boolean. When `true`, include `superseded` and `archived` cards regardless of the `status` parameter. Default `false`.

**Response shape:**
```json
{
  "insight_cards": [
    {
      "insight_id": "string (format: ins-{UUID})",
      "summary": "string (plain text, max 300 chars)",
      "scope": "string (enum: global | persona | strategy | experiment | incident)",
      "scope_ref": "string | null (entity id when scope is not global)",
      "status": "active | superseded | archived",
      "superseded_by_id": "string (format: ins-{UUID}) | null",
      "confidence": {
        "score": "number (0.0–1.0)",
        "label": "high | medium | low | insufficient_evidence"
      },
      "tags": ["string"],
      "evidence_count": "number",
      "primary_evidence_count": "number",
      "aggregated_at": "string (ISO-8601)",
      "route_href": "string (/knowledge/insights/{insight_id})"
    }
  ],
  "filter_metadata": {
    "tags": [
      {
        "value": "string",
        "display_label": "string",
        "count": "number"
      }
    ],
    "linked_entity_types": [
      {
        "value": "string",
        "display_label": "string",
        "count": "number"
      }
    ],
    "recency_options": [
      {
        "value": "string",
        "display_label": "string"
      }
    ],
    "total_active_count": "number"
  },
  "pagination": {
    "page_size": 20,
    "next_page_token": "string | null",
    "has_more": "boolean"
  },
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "insight_cards": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `filter_metadata.tags`, `filter_metadata.linked_entity_types`, and `filter_metadata.recency_options` are the only valid sources for filter UI vocabulary. The frontend must not hardcode or extend these lists.
- `summary` is always plain text. The frontend must not render it as markdown.
- `scope_ref` is opaque when not null. The frontend must not resolve it to a display label locally; the card detail route provides BFF-resolved scope context.
- When `meta.surfaces.insight_cards` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never treat an empty `insight_cards[]` array as authoritative when the surface is stale.
- `filter_metadata.total_active_count` is the backend count of active cards before any filters are applied. The frontend must not compute this from the current page result set.

---

### 2. Get Insight Card Detail

**`GET /api/v1/knowledge/insights/{insight_id}`**

**Response shape:**
```json
{
  "insight_id": "string (format: ins-{UUID})",
  "summary": "string (plain text)",
  "scope": "string (enum: global | persona | strategy | experiment | incident)",
  "scope_context": {
    "scope_ref": "string | null",
    "display_label": "string | null",
    "route_href": "string | null"
  },
  "status": "active | superseded | archived",
  "superseded_by": {
    "insight_id": "string | null",
    "summary": "string | null",
    "route_href": "string | null"
  },
  "confidence": {
    "score": "number (0.0–1.0)",
    "label": "high | medium | low | insufficient_evidence",
    "basis": "string"
  },
  "tags": ["string"],
  "source_ref": "string (opaque internal aggregation ref — must not be used for UI construction)",
  "supporting_evidence_refs": [
    {
      "ref_id": "string (format: evref-{UUID})",
      "source_document_title": "string",
      "link_type": "supporting_evidence | counter_evidence | citation | provenance | corroboration",
      "credibility_tier": "primary | secondary | tertiary | unverified",
      "resolved_link": {
        "availability": "available | unavailable | external",
        "route_href": "string | null",
        "display_label": "string",
        "open_in_new_tab": "boolean"
      }
    }
  ],
  "linked_sources": [
    {
      "entity_type": "memory_entry | research_note | evidence_ref | strategy_spec | experiment",
      "entity_ref": "string",
      "display_label": "string",
      "route_href": "string | null",
      "relationship_note": "string | null"
    }
  ],
  "aggregation_provenance": {
    "memory_entry_count": "number",
    "note_count": "number",
    "evidence_ref_count": "number",
    "primary_evidence_count": "number",
    "aggregated_at": "string (ISO-8601)",
    "aggregation_version": "string"
  },
  "created_at": "string (ISO-8601)",
  "updated_at": "string (ISO-8601)",
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "insight_card_detail": "ok | degraded | unavailable",
      "supporting_evidence_refs": "ok | degraded | unavailable",
      "linked_sources": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `scope_context` is BFF-resolved. The frontend must not derive `display_label` or `route_href` from `scope_ref` locally.
- `source_ref` is an opaque internal aggregation ref. It must not be displayed, parsed, or used to construct any link.
- `supporting_evidence_refs[].resolved_link` is the only valid source for navigable evidence links. Follow the same CS-05 resolution rule as KW-03.
- `linked_sources` is a BFF-resolved drilldown panel. The frontend must not reverse-resolve `entity_ref` values into display labels or routes.
- `superseded_by` is non-null only when `status = "superseded"`. When non-null, the detail view must prominently display the supersession notice and provide the BFF-provided link to the replacement card.
- When `meta.surfaces.supporting_evidence_refs` is `degraded`, show an inline partial-data indicator within the evidence panel rather than hiding the panel.
- When `meta.surfaces.linked_sources` is `degraded`, show an inline partial-data indicator within the linked-sources panel.
- When `meta.surfaces.insight_card_detail` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never collapse to an empty detail view.

---

## Non-Goals — The Frontend Must Not

- Aggregate insight cards by joining or correlating `GET /api/v1/knowledge/memory`, `GET /api/v1/knowledge/notes`, and `GET /api/v1/knowledge/evidence` client-side.
- Derive `confidence.label` from `confidence.score` thresholds defined locally.
- Construct any URL or link from raw `insight_id`, `source_ref`, `entity_ref`, or `scope_ref` values.
- Filter, sort, or group cards using any logic not provided by the `filter_metadata` or `meta.surfaces` response fields.
- Compute `aggregation_provenance` counts from locally held entity lists.
- Infer card lifecycle status (`active | superseded | archived`) from timestamps, scores, or missing fields.
- Reverse-resolve `linked_sources[].display_label` or `linked_sources[].route_href` from raw `entity_ref` strings.
- Display `source_ref` to the user in any form.

---

## Design Rules

- **All synthesis is BFF-owned.** The insight card is the BFF's composed view over memory, notes, and evidence. The frontend renders what the BFF returns; it must not replicate the aggregation logic.
- **Filter vocabulary is backend-shaped.** `filter_metadata.tags`, `filter_metadata.linked_entity_types`, and `filter_metadata.recency_options` are the sole source for filter UI labels and values.
- **Pagination is keyset-based.** Use `page_token` cursors; do not pass `page` integers.
- **Surface health signals must come from `meta.surfaces`**, not from empty response arrays or HTTP 200 with zero results.
- **Evidence links follow the CS-05 precedent.** `supporting_evidence_refs[].resolved_link` is BFF-resolved; no client-side URL construction is permitted.
- **Confidence is a BFF statement.** The `confidence.label` and `confidence.basis` are BFF-owned; the frontend must never reclassify a card's confidence locally.
- **`source_ref` is opaque.** It must never appear in the UI, be parsed for metadata, or be used for any purpose other than being forwarded to the BFF in mutation requests when applicable.

---

## Relationship to Other Modules

- **KW-01**: Insight cards may include `entry_id` anchors from Institutional Memory in their `linked_sources`. The KW-01 lifecycle semantics apply to those anchors; the BFF resolves their current lifecycle state at read time.
- **KW-02**: Insight cards may include research-note inputs in their `linked_sources`. The KW-02 ownership and attachment contract governs those references.
- **KW-03**: Insight cards consume `ref_id` values from Evidence Refs as `supporting_evidence_refs`. The KW-03 read model is the canonical authority for evidence link resolution and credibility metadata.
- **KW-05**: Strategy spec citation panels may reference `insight_id` values. The KW-04 read model is the canonical source for those citations.
- **PKT-005**: All surface degradation must flow through the canonical SSE/degradation banner mechanism. `meta.surfaces.insight_cards` and `meta.surfaces.insight_card_detail` are the authoritative triggers.
