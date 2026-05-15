# PKT-003 Lineage View — Contract Lock

## Status

`delivered`

## Lock Date

2026-04-17

## Source Payload

`.coordination/requests/PKT-003-lineage-view-frontend-feedback.yaml`

## Version Lock

- Backend commit reference: `38aa953d93dc7dc490b30dde3d6df371877c5cab`
- BFF contract version: `pantheon-bff@38aa953d93dc7dc490b30dde3d6df371877c5cab`
- Reviewed front implementation anchor: `51a5cb91451dceb4b4e44961804ad5decf6ce359`
- Canonical front request-pair republish: `7309a518f3e64bf40bb4a33a371b8cc152655a7c`

## Delivered Endpoints

- `GET /api/v1/lineage`
- `GET /api/v1/lineage/edges/{edge_id}`
- `GET /api/v1/lineage/graph`

## Contract Paths

- `docs/bff/PKT-003-lineage-view.md`
- `docs/examples/PKT-003-lineage-view.json`
- `docs/screens/PKT-003-lineage-view.md`
- `docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md`

## Delivery Guarantees

- `GET /api/v1/lineage` is locked to the PKT-003 list shape:
  - top-level `items`
  - each item carries `artifact_id`, `edge_count`, and `last_edge_at`
  - `page_info.next_page_token`
  - `meta.snapshot_at`
- `GET /api/v1/lineage/edges/{edge_id}` is locked to the flat detail shape:
  - `id`
  - `from_artifact_id`
  - `to_artifact_id`
  - `relationship`
  - `created_at`
  - `meta.snapshot_at`
  - `404 OBJECT_NOT_FOUND` surfaces `Lineage edge not found`
- `GET /api/v1/lineage/graph` is locked to the graph shape:
  - top-level `nodes[]`
  - top-level `edges[]`
  - `meta.snapshot_at`
  - optional `meta.staleness` when the read surface is not fresh
  - `depth` is clamped by the BFF to `1..10`
- `root_type` remains a known v1 no-op. The BFF may accept it, but the UI must
  not expose it as a filter control until a later packet explicitly revises that
  boundary.
- Empty graph responses remain a real state, not a contract failure. The UI must
  render explicit `No lineage recorded for {artifact_id}` copy rather than a
  blank canvas.
- Any future missing required field is a fresh contract divergence and must
  publish a new `bff-gap` handoff instead of inferring local fallback state.

## Follow-up Scope

- Keep the current screen mounted at `/lineage` until the Evolution Workbench
  shell finalizes the `/evolution/lineage` route host
- Add `useSearchParams` `root_id` wiring only alongside that route migration
- Live runtime verification against a deployed Pantheon BFF remains a
  non-blocking follow-up for this packet
- No new endpoint, client-side shadow state, or alternate fetch path is
  authorized inside the current PKT-003 lineage packet
