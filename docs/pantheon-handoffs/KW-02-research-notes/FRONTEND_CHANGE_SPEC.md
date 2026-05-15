# KW-02 Research Notes — Frontend Change Spec

## Feature

- Feature ID: `KW-02-research-notes`
- Screen IDs: `screen-knowledge-notes-list`, `screen-knowledge-note-detail`
- Workbench: Knowledge Workbench
- Packet status: route-live — UI implementation may proceed against the live BFF routes
- Task: `KW-02-RESEARCH-NOTES-001`

## Readiness Gate

Pantheon has confirmed **all three** of the following routes are live and returning the published field shape:

1. `POST /api/v1/knowledge/notes` — creates a research note; validates `attachment_type`, `attachment_ref`, `linked_memory_anchors`; returns `note_id`, `created_at`, `route_href`; owner is server-assigned.
2. `GET /api/v1/knowledge/notes` — returns a paginated list with `owner_ref`, `attachment`, `tags`, `excerpt`, and `meta.surfaces.research_note_list`.
3. `GET /api/v1/knowledge/notes/{note_id}` — returns note body, `linked_evidence_refs` with resolution state, `linked_memory_anchors`, and per-panel surface state.

Build the production pages against these live surfaces. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/KW-02-research-notes-bff-gap.yaml` instead of inventing state or deriving owner/attachment semantics client-side.

## Summary

Build the **Research Notes** list, detail, and create screens inside `front-ai-trading-system`. This slice lets operators and persona-session actors write, browse, and inspect research notes attached to tickets, personas, strategy specs, or as free-standing notes. All data and CTA authority come from the Pantheon BFF — no client-side owner inference, no URL construction from raw ids, no attachment-type derivation from path shapes.

## Files to Create or Modify

```
src/pages/knowledge/ResearchNotesList.tsx     — new Research Notes list page
src/pages/knowledge/ResearchNoteDetail.tsx    — new Research Note detail page
src/pages/knowledge/CreateResearchNote.tsx    — new create-note form
src/pages/knowledge/ResearchNoteTypes.ts      — add note, attachment, and owner types
src/lib/bffClient.ts                          — add KW-02 note list, detail, and create calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### List research notes

```
GET /api/v1/knowledge/notes
```

Query parameters (all optional):

| Param | Type | Notes |
|---|---|---|
| `owner_ref` | string | Filter by `owner_id` |
| `attachment_type` | string | One of `research_ticket \| persona \| strategy_spec \| free_standing` |
| `attachment_ref` | string | Requires `attachment_type`; filters to notes linked to the specific entity |
| `tags` | string | Comma-separated tag values |
| `page_token` | string | Opaque keyset cursor |
| `page_size` | number | Default 20, max 100 |

Expected response shape (see `docs/examples/KW-02-research-notes.json` for full example):

```typescript
interface NoteListResponse {
  notes: NoteSummary[];
  pagination: {
    page_size: number;
    next_page_token: string | null;
    has_more: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: { research_note_list: "ok" | "degraded" | "unavailable" };
  };
}

interface NoteSummary {
  note_id: string;                   // format: note-{UUID}
  title: string | null;
  excerpt: string;                   // first 280 chars of body, plain text — do not render as markdown
  owner_ref: {
    owner_type: "persona" | "operator";
    owner_id: string;
    display_name: string;            // BFF-resolved — do not re-derive from owner_id
  };
  attachment: {
    type: "research_ticket" | "persona" | "strategy_spec" | "free_standing";
    ref: string | null;
    display_label: string | null;    // BFF-resolved — do not resolve from raw ref
  };
  tags: string[];
  created_at: string;
  updated_at: string;
  route_href: string;                // BFF-provided — use as-is
}
```

### Get research note detail

```
GET /api/v1/knowledge/notes/{note_id}
Path param: note_id (required)
```

Expected response shape:

```typescript
interface NoteDetail {
  note_id: string;
  title: string | null;
  body: string;                      // markdown source — safe to render as markdown
  owner_ref: {
    owner_type: "persona" | "operator";
    owner_id: string;
    display_name: string;
  };
  attachment: {
    type: "research_ticket" | "persona" | "strategy_spec" | "free_standing";
    ref: string | null;
    display_label: string | null;
    route_href: string | null;       // BFF-resolved canonical route — do not construct from ref
  };
  tags: string[];
  linked_evidence_refs: LinkedEvidenceRef[];
  linked_memory_anchors: LinkedMemoryAnchor[];
  created_at: string;
  updated_at: string;
  meta: {
    snapshot_at: string;
    surfaces: {
      research_note_detail: "ok" | "degraded" | "unavailable";
      evidence_links: "ok" | "degraded" | "unavailable";
      memory_anchors: "ok" | "degraded" | "unavailable";
    };
  };
}

interface LinkedEvidenceRef {
  ref_id: string;
  resolution_state: "resolved" | "unresolved" | "unavailable";
  display_label: string | null;
  route_href: string | null;         // null when unresolved or unavailable
}

interface LinkedMemoryAnchor {
  entry_id: string;                  // format: mem-{UUID}
  headline: string;
  knowledge_type: string;
  lifecycle_status: "active" | "archived" | "superseded";
  route_href: string;                // BFF-provided path
}
```

### Create research note

```
POST /api/v1/knowledge/notes
Content-Type: application/json
```

Request body:

```typescript
interface CreateNoteRequest {
  title?: string;                         // optional, max 256 chars
  body: string;                           // required, markdown supported
  attachment_type: "research_ticket" | "persona" | "strategy_spec" | "free_standing";
  attachment_ref: string | null;          // null when attachment_type is free_standing
  tags?: string[];
  linked_evidence_refs?: string[];        // ref_id values
  linked_memory_anchors?: string[];       // entry_id values (format: mem-{UUID})
  // owner_ref is server-assigned — do not send
}
```

Success response (201):

```typescript
interface CreateNoteResponse {
  note_id: string;
  created_at: string;
  route_href: string;
}
```

Error responses:

| Code | When |
|---|---|
| `400` | Missing `body`, invalid `attachment_type`, `attachment_ref` mismatch, or unresolvable `linked_memory_anchors` |
| `422` | Referential integrity failure — attachment target does not exist |

## Component Structure

### `ResearchNotesList.tsx`

- Route: `/knowledge/notes`
- Fetches `GET /api/v1/knowledge/notes` on mount; supports `owner_ref`, `attachment_type`, `attachment_ref`, and `tags` filter params.
- Each row: `title` (or excerpt fallback), `owner_ref.display_name`, `attachment.display_label`, `tags`, `created_at`.
- `excerpt` is always plain text — do not render as markdown.
- `attachment.display_label` is BFF-resolved — do not resolve from raw `attachment_ref`.
- Pagination via `next_page_token` cursor; do not pass a page integer.
- Row click navigates to `/knowledge/notes/{note_id}`.
- When `meta.surfaces.research_note_list` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Do not treat an empty `notes[]` as authoritative when the surface is stale.

### `ResearchNoteDetail.tsx`

- Route: `/knowledge/notes/:note_id`
- Fetches `GET /api/v1/knowledge/notes/{note_id}` on mount.
- Renders `body` as markdown.
- Renders `attachment.route_href` as BFF-provided — do not construct this from the raw `attachment_ref`.
- `linked_evidence_refs` panel: render `resolution_state` as the display authority. `unresolved` items must not show an active link. `unavailable` items show a degraded link indicator.
- `linked_memory_anchors` panel: render `lifecycle_status` as provided — do not infer superseded state from `superseded_by` presence alone.
- When `meta.surfaces.evidence_links` or `meta.surfaces.memory_anchors` is `degraded`, show an inline partial-data indicator within the respective panel rather than hiding it.
- When `meta.surfaces.research_note_detail` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Never collapse to an empty note body.

### `CreateResearchNote.tsx`

- Route: `/knowledge/notes/new` or as a modal/drawer.
- Submit to `POST /api/v1/knowledge/notes`.
- Attachment type selector must present the four backend-defined types as explicit choices: `research_ticket`, `persona`, `strategy_spec`, `free_standing`.
- When `free_standing` is selected, `attachment_ref` input is hidden and submitted as `null`.
- Do not set or send `owner_ref` — it is server-assigned.
- On `400` or `422`, show the BFF error message; do not invent client-side validation to preempt BFF rejection.
- On 201 success, navigate to the returned `route_href`.

## Degradation Handling

| `meta.surfaces.research_note_list` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; note rows visible |
| `unavailable` | Replace list content with unavailable notice; do not show "no notes" as authoritative |

| `meta.surfaces.research_note_detail` | Required behavior |
|---|---|
| `ok` | Normal display |
| `degraded` | Non-dismissable staleness banner; note body visible |
| `unavailable` | Replace detail panel with unavailable notice; never show empty note body |

| `meta.surfaces.evidence_links` | Required behavior |
|---|---|
| `ok` | Render evidence links normally |
| `degraded` | Inline partial-data indicator within the evidence panel; do not hide the panel |
| `unavailable` | Show panel-level unavailable notice |

| `meta.surfaces.memory_anchors` | Required behavior |
|---|---|
| `ok` | Render memory anchor links normally |
| `degraded` | Inline partial-data indicator within the anchors panel; do not hide the panel |
| `unavailable` | Show panel-level unavailable notice |

## Evidence Link Resolution States

| `resolution_state` | Required display |
|---|---|
| `resolved` | Active link using `route_href` and `display_label` |
| `unresolved` | Display `display_label` without an active link; indicate the ref could not be resolved |
| `unavailable` | Degraded link indicator; `route_href` is null |

## State Requirements

Each data panel must handle:

- `loading`: skeleton or spinner
- `empty`: explicit empty copy (no blank panels)
- `degraded`: staleness banner with available data
- `unavailable`: degradation placeholder
- `error`: error copy with retry option

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Do not infer `attachment_type` from URL path segments, opaque ids, or workspace context.
- Do not resolve `owner_ref.display_name` from raw `owner_id`.
- Do not construct `attachment.route_href` from raw `attachment_ref` values.
- Do not construct `linked_evidence_refs[].route_href` from raw `ref_id` values.
- Do not derive note lifecycle or archive state from timestamps alone.
- Do not filter or group notes by owner on the client side when the server-side `owner_ref` filter is available.
- Attachment type may not be changed after note creation; re-creation is the correct path.
- If any required field is absent from the BFF response, write `.coordination/requests/KW-02-research-notes-bff-gap.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/KW-02-research-notes-ui-done.yaml` using the same pattern as `.coordination/requests/PKT-knowledge-workbench-ui-done.yaml`.

## References

- BFF contract: `docs/bff/KW-02-research-notes.md`
- Example payloads: `docs/examples/KW-02-research-notes.json`
- Packet family: `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
- KW-01 anchor identity: `docs/bff/KW-01-institutional-memory.md` (for `mem-{UUID}` anchor format and lifecycle semantics)
- KW-03 evidence refs: `docs/bff/KW-03-evidence-refs.md` (for `evref-{UUID}` identity used in `linked_evidence_refs`)
