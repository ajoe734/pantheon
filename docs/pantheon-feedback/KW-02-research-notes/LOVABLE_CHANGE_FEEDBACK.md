# KW-02 Research Notes — Lovable Change Feedback

**Source commit:** `e00d13dc220e4226f93c82e2c7dbc4a053601816`
**Status:** Complete

## What was built

Three surfaces implemented against the live Pantheon BFF route family:

- `GET /api/v1/knowledge/notes` → `ResearchNotesList`
- `GET /api/v1/knowledge/notes/{note_id}` → `ResearchNoteDetail`
- `POST /api/v1/knowledge/notes` → `CreateResearchNote`

## Files added

- `src/pages/knowledge/ResearchNoteTypes.ts` — contract types
- `src/pages/knowledge/ResearchNotesList.tsx` — list with filters & cursor pagination
- `src/pages/knowledge/ResearchNoteDetail.tsx` — detail with linked-evidence and memory-anchor panels
- `src/pages/knowledge/CreateResearchNote.tsx` — create form

## Files modified

- `src/lib/bffClient.ts` — added `researchNotesBffApi` namespace
- `src/App.tsx` — replaced legacy `KnowledgeNotesPages` placeholder with new live surfaces; added `/knowledge/notes/new` route

## Contract fidelity

- Owner identity from `owner_ref.display_name` (no client-side resolution).
- Attachment labels from `attachment.display_label` (no derivation from raw `attachment_ref`).
- Navigation uses `route_href` fields from BFF (not constructed).
- Evidence link `resolution_state` respected — `unresolved` items have no active link.
- Memory anchor `lifecycle_status` displayed as published (no superseded inference).
- Markdown body shown as preformatted text; `excerpt` rendered as plain text.

## Degradation behavior

Each surface state (`research_note_list`, `research_note_detail`, `evidence_links`, `memory_anchors`) renders the canonical PKT-005 amber/destructive banner. Empty arrays are not treated as authoritative under degraded/unavailable states.

## TypeScript build

`npx tsc --noEmit` passes with 0 errors.
