# KW-02 Research Notes BFF Contract

## Purpose

Provide the second browse module for the Knowledge Workbench. This contract defines note ownership, the attachment taxonomy, referential integrity rules, and the create/list/detail routes for Research Notes. It eliminates client-invented taxonomy and owner inference from the frontend.

Upstream dependency: `KW-01` must be Lovable-ready. Notes may link to `entry_id` values as institutional-memory anchors.

---

## Attachment Taxonomy

Research notes have exactly one `attachment_type`. Supported values are backend-owned and must never be inferred by the frontend from path shapes or opaque ids.

| `attachment_type` | `attachment_ref` identity | Meaning |
|---|---|---|
| `research_ticket` | `ticket_id` (format: `tkt-{UUID}`) | Note is anchored to an open or closed research ticket |
| `persona` | `persona_id` | Note is authored against a specific persona's active model context |
| `strategy_spec` | `strategy_id` | Note is anchored to a specific strategy spec version |
| `free_standing` | `null` | Note is not anchored to any specific entity; visible across all contexts |

Rules:
- `attachment_ref` must match the identity format of the declared `attachment_type`.
- `free_standing` notes carry `attachment_ref: null`.
- Attachment type may not be changed after creation; a note must be re-created to change its target.
- The frontend must not synthesize an attachment type from URL segments, opaque string ids, or creator workspace context.

---

## Ownership Contract

Every note carries an `owner_ref` object that the BFF resolves at write time from the authenticated operator context.

```
owner_ref: {
  owner_type: "persona" | "operator",
  owner_id: string,
  display_name: string
}
```

- `owner_type: "persona"` — the note was authored on behalf of a persona model context (e.g., within a trainer or research session where a persona is the primary actor).
- `owner_type: "operator"` — the note was authored by a human operator directly.
- `owner_id` is the canonical persona or operator identifier. The frontend must not re-derive `owner_id` from session context or path conventions.
- `display_name` is BFF-resolved. The frontend must not resolve names from raw ids.

---

## Write Route

### Create Research Note

**`POST /api/v1/knowledge/notes`**

**Request body:**
```json
{
  "title": "string (optional, max 256 chars)",
  "body": "string (markdown supported, required)",
  "attachment_type": "research_ticket | persona | strategy_spec | free_standing",
  "attachment_ref": "string | null",
  "tags": ["string"],
  "linked_evidence_refs": ["string (ref_id)"],
  "linked_memory_anchors": ["string (entry_id, format: mem-{UUID})"]
}
```

Field rules:
- `body` is required and must not be empty.
- `attachment_ref` must be `null` when `attachment_type` is `free_standing`; must be non-null otherwise.
- `linked_evidence_refs` items reference `KW-03` evidence ref IDs. Unknown refs are accepted at write time but flagged as `unresolved` in subsequent detail responses.
- `linked_memory_anchors` items must be valid `entry_id` values from `KW-01`. Unknown anchors are rejected with `400`.
- `owner_ref` is server-assigned; the caller must not send it.

**Success response (201):**
```json
{
  "note_id": "string (format: note-{UUID})",
  "created_at": "string (ISO-8601)",
  "route_href": "/knowledge/notes/{note_id}"
}
```

**Error responses:**
- `400` — missing `body`, invalid `attachment_type`, `attachment_ref` mismatch, or unresolvable `linked_memory_anchors`.
- `422` — referential integrity failure (attachment target does not exist).

---

## Read Routes

### 1. List Research Notes

**`GET /api/v1/knowledge/notes`**

**Query parameters:**
- `owner_ref` (optional): Filter by `owner_id`. Accepts a single owner id.
- `attachment_type` (optional): One of `research_ticket | persona | strategy_spec | free_standing`.
- `attachment_ref` (optional): Requires `attachment_type` to be set. Filters to notes linked to the specific entity.
- `tags` (optional): Comma-separated tag values.
- `page_token` (optional): Opaque cursor for keyset pagination.
- `page_size` (optional): Default 20, max 100.

**Response shape:**
```json
{
  "notes": [
    {
      "note_id": "string (format: note-{UUID})",
      "title": "string | null",
      "excerpt": "string (first 280 chars of body, plain text)",
      "owner_ref": {
        "owner_type": "persona | operator",
        "owner_id": "string",
        "display_name": "string"
      },
      "attachment": {
        "type": "research_ticket | persona | strategy_spec | free_standing",
        "ref": "string | null",
        "display_label": "string | null"
      },
      "tags": ["string"],
      "created_at": "string (ISO-8601)",
      "updated_at": "string (ISO-8601)",
      "route_href": "string (/knowledge/notes/{note_id})"
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
      "research_note_list": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `attachment.display_label` is BFF-resolved from the attachment target (e.g., research ticket title, persona name). The frontend must not resolve labels from raw ids.
- `excerpt` is always plain text; the frontend must not render it as markdown.
- When `meta.surfaces.research_note_list` is `degraded` or `unavailable`, the frontend must show the canonical PKT-005 degradation banner. It must not treat an empty `notes[]` array as authoritative when the surface is stale.

---

### 2. Get Research Note Detail

**`GET /api/v1/knowledge/notes/{note_id}`**

**Response shape:**
```json
{
  "note_id": "string (format: note-{UUID})",
  "title": "string | null",
  "body": "string (markdown source)",
  "owner_ref": {
    "owner_type": "persona | operator",
    "owner_id": "string",
    "display_name": "string"
  },
  "attachment": {
    "type": "research_ticket | persona | strategy_spec | free_standing",
    "ref": "string | null",
    "display_label": "string | null",
    "route_href": "string | null"
  },
  "tags": ["string"],
  "linked_evidence_refs": [
    {
      "ref_id": "string",
      "resolution_state": "resolved | unresolved | unavailable",
      "display_label": "string | null",
      "route_href": "string | null"
    }
  ],
  "linked_memory_anchors": [
    {
      "entry_id": "string (format: mem-{UUID})",
      "headline": "string",
      "knowledge_type": "string (enum)",
      "lifecycle_status": "active | archived | superseded",
      "route_href": "string (/knowledge/memory/{entry_id})"
    }
  ],
  "created_at": "string (ISO-8601)",
  "updated_at": "string (ISO-8601)",
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "research_note_detail": "ok | degraded | unavailable",
      "evidence_links": "ok | degraded | unavailable",
      "memory_anchors": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `attachment.route_href` is BFF-resolved to the canonical surface for the attachment target. The frontend must not construct this URL from the raw `ref` value.
- `linked_evidence_refs[].resolution_state` drives link rendering. `unresolved` items must not show an active link. `unavailable` items show a degraded link indicator.
- `linked_memory_anchors[].lifecycle_status` comes from the BFF; the frontend must not derive superseded state from `superseded_by` presence alone.
- When `meta.surfaces.research_note_detail` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never collapse to an empty note body.
- When `meta.surfaces.evidence_links` or `meta.surfaces.memory_anchors` is `degraded`, show an inline partial-data indicator within the respective panel rather than hiding it entirely.

---

## Referential Integrity Rules

1. **Attachment target must exist at creation time.** If the `attachment_ref` does not resolve to a known entity, the BFF returns `422`.
2. **Memory anchor IDs must be valid `entry_id` values.** Unknown or malformed `entry_id` values are rejected with `400` at creation.
3. **Evidence ref IDs are accepted optimistically at creation** but flagged as `unresolved` in subsequent reads if the evidence store cannot confirm the ref.
4. **Orphaned notes.** If an attachment target is deleted or archived after note creation, the BFF retains the note but sets `attachment.display_label` to `null` and `attachment.route_href` to `null`. It does not cascade-delete the note.
5. **Tags are free-form strings.** There is no server-enforced tag vocabulary. The backend does not reject unknown tags.

---

## Non-Goals — The Frontend Must Not

- Infer `attachment_type` from URL path segments, opaque ids, or workspace context.
- Resolve `owner_ref.display_name` from raw `owner_id`.
- Construct `attachment.route_href` from raw `attachment_ref` values.
- Construct `linked_evidence_refs[].route_href` from raw `ref_id` values.
- Derive note lifecycle or archive state from timestamps alone.
- Filter or group notes by owner on the client side when server-side `owner_ref` filter is available.

---

## Design Rules

- **Attachment type is immutable.** No PATCH route exists for changing attachment target after creation. Re-creation is the correct path.
- **Owner is server-assigned.** No client-supplied `owner_ref` is accepted on write.
- **Pagination is keyset-based.** Do not pass `page` integer; use `page_token` cursors.
- **Surface health signals must come from `meta.surfaces`**, not from empty response arrays or HTTP 200 with zero results.
- **Evidence link resolution follows the CS-05 precedent**: the BFF resolves and status-marks each link; the frontend only renders the provided state.

---

## Relationship to Other Modules

- **KW-01**: Notes may link to `entry_id` values as `linked_memory_anchors`. The KW-01 identity contract (`mem-{UUID}` format, lifecycle semantics) is the authoritative anchor specification.
- **KW-03**: `linked_evidence_refs` items are placeholders for `KW-03` evidence ref objects. The full resolution contract is defined in `docs/bff/KW-03-evidence-refs.md` once that module is published.
- **KW-04**: Insight card aggregation consumes notes as a source. The `note_id` is the identity anchor that upstream aggregation must respect.
- **PKT-005**: All surface degradation must flow through the canonical SSE/degradation banner mechanism. `meta.surfaces.*` signals are the authoritative trigger.
