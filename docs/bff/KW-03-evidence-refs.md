# KW-03 Evidence Refs BFF Contract

**Status:** route-live — BFF implementation complete; frontend handoff bundle published at `docs/pantheon-handoffs/KW-03-evidence-refs/`

## Purpose

Provide the third browse module for the Knowledge Workbench. This contract defines the evidence reference read model, link-type taxonomy, credibility metadata, and the BFF-owned link resolution contract for Evidence Refs. It eliminates client-side URL construction from raw `ref_id` or `storage_ref` values.

Upstream dependencies:
- `KW-01` must be Lovable-ready. Evidence refs may carry `entry_id` anchors as downstream linked-decision targets.
- `KW-02` must be Lovable-ready. Evidence refs may appear as `linked_evidence_refs` on research notes; `note_id` is the stable source-context anchor used when the evidence originates from a note.

Precedent: `CS-05` (`services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md`) proves that BFF-owned per-link availability and resolved links can be returned without client-side URL construction. KW-03 follows the same resolution pattern applied to a cross-workbench evidence registry.

---

## Evidence Reference Identity

Every evidence reference has a stable `ref_id` of the format `evref-{UUID}`. This id is the canonical key used by KW-03, KW-04 (Insight Cards), and KW-05 (Strategy Spec citations).

- `ref_id` must never be guessed or constructed from `storage_ref`, file path, or object name.
- The BFF resolves the canonical target link at read time and returns it with an `availability` state.
- The frontend must not construct evidence URLs from any raw field.

---

## Link Taxonomy

Every evidence reference has a `link_type` that describes the relationship between the source document and the downstream object it supports. The taxonomy is backend-owned and must not be extended or reinterpreted by the frontend.

| `link_type` | Meaning |
|---|---|
| `supporting_evidence` | The source document positively supports the linked decision or artifact |
| `counter_evidence` | The source document presents findings that oppose or constrain the linked object |
| `citation` | The source document is cited as a formal reference without directional claim |
| `provenance` | The source document is the origin event or artifact that generated the linked object |
| `corroboration` | The source document independently confirms a finding already linked from another ref |

---

## Credibility Metadata

Each evidence reference carries a `credibility` object resolved by the BFF. The frontend renders the provided fields; it must not recalculate or override credibility signals.

```
credibility: {
  tier: "primary | secondary | tertiary | unverified",
  verified: boolean,
  last_verified_at: string (ISO-8601) | null,
  verification_method: string | null
}
```

- `tier` indicates the evidential weight. `primary` is direct observation or authoritative source; `secondary` is derived or summarized; `tertiary` is background or contextual; `unverified` has not been reviewed.
- `verified` is a boolean resolved at read time. The frontend must not infer `verified` from `tier` alone.
- When `last_verified_at` is null, no verification event is on record; the UI must show an appropriate unverified indicator, not hide the field.

---

## BFF-Owned Link Resolution

Each evidence reference exposes a `resolved_link` object that gives the frontend a ready-to-render target without any URL construction.

```
resolved_link: {
  availability: "available | unavailable | external",
  route_href: string | null,
  display_label: string,
  open_in_new_tab: boolean
}
```

- `availability: "available"` — the target is an internal Pantheon surface; `route_href` is a valid internal path.
- `availability: "unavailable"` — the source document could not be resolved (deleted, migrated, or access-controlled); `route_href` is null; show a degraded link indicator.
- `availability: "external"` — the source document lives outside Pantheon (an external paper, upstream report, or external data source); `route_href` is the external canonical URI provided by the data plane; `open_in_new_tab` is `true`.
- The frontend must not derive `availability` from `storage_ref` suffix, MIME type, or guessed path conventions.

---

## Read Routes

### 1. List Evidence References

**`GET /api/v1/knowledge/evidence`**

**Query parameters:**
- `linked_entity_type` (optional): Filter by downstream entity type. One of `memory_entry | research_note | insight_card | strategy_spec | experiment | artifact`.
- `linked_entity_ref` (optional): Requires `linked_entity_type`. Filter to refs linked to a specific entity id.
- `link_type` (optional): One of the link taxonomy values above.
- `credibility_tier` (optional): One of `primary | secondary | tertiary | unverified`.
- `verified` (optional): Boolean. `true` returns only verified refs; `false` returns only unverified.
- `page_token` (optional): Opaque cursor for keyset pagination.
- `page_size` (optional): Default 20, max 100.

**Response shape:**
```json
{
  "evidence_refs": [
    {
      "ref_id": "string (format: evref-{UUID})",
      "source_document": {
        "title": "string",
        "source_type": "string (enum: research_note | memory_entry | external_paper | experiment_artifact | incident_report | postmortem | audit_log)",
        "source_ref": "string (opaque storage ref — never used by frontend for URL construction)",
        "captured_at": "string (ISO-8601)"
      },
      "link_type": "supporting_evidence | counter_evidence | citation | provenance | corroboration",
      "credibility": {
        "tier": "primary | secondary | tertiary | unverified",
        "verified": "boolean"
      },
      "linked_object_summary": {
        "entity_type": "string",
        "entity_ref": "string",
        "display_label": "string | null"
      },
      "resolved_link": {
        "availability": "available | unavailable | external",
        "route_href": "string | null",
        "display_label": "string",
        "open_in_new_tab": "boolean"
      },
      "route_href": "string (/knowledge/evidence/{ref_id})"
    }
  ],
  "pagination": {
    "page_size": 20,
    "next_page_token": "string | null",
    "has_more": "boolean"
  },
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "evidence_refs_list": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `source_document.source_ref` is opaque storage metadata. The frontend must not parse it, display it, or use it to construct any link.
- `linked_object_summary.display_label` is BFF-resolved. The frontend must not resolve labels from raw `entity_ref`.
- `resolved_link` is the only field from which the frontend may derive a navigable link.
- When `meta.surfaces.evidence_refs_list` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never treat an empty `evidence_refs[]` array as authoritative when the surface is stale.

---

### 2. Get Evidence Reference Detail

**`GET /api/v1/knowledge/evidence/{ref_id}`**

**Response shape:**
```json
{
  "ref_id": "string (format: evref-{UUID})",
  "source_document": {
    "title": "string",
    "source_type": "string (enum: research_note | memory_entry | external_paper | experiment_artifact | incident_report | postmortem | audit_log)",
    "excerpt": "string | null (plain text, max 500 chars)",
    "source_ref": "string (opaque — never used for URL construction)",
    "storage_preview": {
      "available": "boolean",
      "preview_type": "text | image | pdf | unavailable",
      "preview_token": "string | null (resolved BFF preview token for preview_type=pdf or image; expires after TTL)"
    },
    "captured_at": "string (ISO-8601)",
    "captured_by": "string (display name, BFF-resolved)"
  },
  "link_type": "supporting_evidence | counter_evidence | citation | provenance | corroboration",
  "credibility": {
    "tier": "primary | secondary | tertiary | unverified",
    "verified": "boolean",
    "last_verified_at": "string (ISO-8601) | null",
    "verification_method": "string | null"
  },
  "resolved_link": {
    "availability": "available | unavailable | external",
    "route_href": "string | null",
    "display_label": "string",
    "open_in_new_tab": "boolean"
  },
  "linked_decisions": [
    {
      "entity_type": "string (enum)",
      "entity_ref": "string",
      "display_label": "string | null",
      "route_href": "string | null",
      "link_type": "string",
      "relationship_note": "string | null"
    }
  ],
  "source_note_context": {
    "note_id": "string (format: note-{UUID}) | null",
    "title": "string | null",
    "excerpt": "string | null",
    "route_href": "string | null"
  },
  "source_memory_context": {
    "entry_id": "string (format: mem-{UUID}) | null",
    "headline": "string | null",
    "knowledge_type": "string | null",
    "lifecycle_status": "active | archived | superseded | null",
    "route_href": "string | null"
  },
  "created_at": "string (ISO-8601)",
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "evidence_ref_detail": "ok | degraded | unavailable",
      "resolved_link": "ok | degraded | unavailable",
      "linked_decisions": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `source_document.excerpt` is always plain text. The frontend must not render it as markdown.
- `source_document.storage_preview.preview_token` is a short-lived token; the frontend must not cache it beyond the response. It must not construct the preview URL from `source_ref` directly.
- `linked_decisions` is a BFF-resolved panel. The frontend must not reverse-resolve raw entity ids into routes or display labels.
- `source_note_context` and `source_memory_context` are both nullable. When `null`, the evidence ref does not originate from a note or memory entry; the panel must be hidden, not shown as empty.
- When `meta.surfaces.linked_decisions` is `degraded`, show an inline partial-data indicator inside the linked-decision panel rather than hiding the panel entirely.
- When `meta.surfaces.evidence_ref_detail` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never collapse to an empty detail view.

---

## Non-Goals — The Frontend Must Not

- Construct any URL or link from raw `ref_id`, `source_ref`, `storage_ref`, or object names.
- Derive `link_type` or `credibility.tier` from source document MIME type, file extension, or path prefix.
- Reverse-resolve `linked_decisions[].display_label` or `route_href` from raw entity refs.
- Infer `source_note_context` or `source_memory_context` from raw refs or opaque string ids.
- Aggregate or filter evidence refs on the client side when server-side query parameters are available.
- Display an empty list or "no evidence" message as authoritative when `meta.surfaces.evidence_refs_list` is `degraded` or `unavailable`.

---

## Design Rules

- **All links are BFF-resolved.** The `resolved_link` object is the only valid source for navigable links. The frontend must never construct evidence URLs.
- **Pagination is keyset-based.** Use `page_token` cursors; do not pass `page` integers.
- **Surface health signals must come from `meta.surfaces`**, not from empty response arrays or HTTP 200 with zero results.
- **Evidence link resolution follows the CS-05 precedent**: the BFF resolves and status-marks each link; the frontend only renders the provided availability state.
- **`source_ref` is opaque storage metadata.** It must never appear in the UI, be parsed for URL segments, or be used for any purpose other than being forwarded to the BFF when the BFF requests it.
- **Credibility tier is informational only.** The frontend must not gate any CTA, visibility, or sort order on `credibility.tier` without an explicit `allowedActions` flag from the BFF.

---

## Relationship to Other Modules

- **KW-01**: Evidence refs may carry `source_memory_context` pointing to an `entry_id`. The KW-01 lifecycle semantics (`active | archived | superseded`) apply to that context field.
- **KW-02**: Evidence refs that originate from a research note carry `source_note_context` pointing to a `note_id`. The KW-02 ownership and attachment contract is the authority for note identity.
- **KW-04**: Insight card aggregation consumes evidence refs as one of its upstream inputs. The `ref_id` is the stable anchor that aggregation must respect.
- **KW-05**: Strategy spec citation panels reference `ref_id` values. The KW-03 read model is the canonical citation object for those panels.
- **PKT-005**: All surface degradation must flow through the canonical SSE/degradation banner mechanism. `meta.surfaces.*` signals are the authoritative trigger.
