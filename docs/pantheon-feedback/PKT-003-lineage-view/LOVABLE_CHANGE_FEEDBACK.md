# PKT-003 Lineage View — Lovable Change Feedback

Reviewed the lineage-view implementation in `ajoe734/front-ai-trading-system` against the PKT-003 BFF contract, screen spec, and example payload.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Lineage View is implemented against the published PKT-003 contract. All three BFF endpoints are consumed through the existing BFF client. Acceptance criteria met in full.

> **BFF-gap note:** A `bff-gap` handoff was filed earlier (`.coordination/requests/PKT-003-lineage-view-bff-gap.yaml`) because the BFF returned raw `data`-keyed envelopes, missing node enrichment, and no per-artifact aggregation. Those gaps were resolved by BP5-SVC-010 ("Realize the lineage read model and performance service path"). The corrected BFF shapes match the example payload exactly. Implementation proceeded after BP5-SVC-010 landed.

## Verified Against Pantheon

- `GET /api/v1/lineage` is consumed through the BFF client with `artifact_id`, `page_token`, and `page_size` query params. No raw `fetch` or `axios` in component files.
- `GET /api/v1/lineage/graph` is called with `root_id` (from list-row click) and `depth` parameters. `root_type` is not exposed as a UI filter control per the v1 BFF contract constraint.
- `GET /api/v1/lineage/edges/{edge_id}` is called from graph-edge click only, passing the `edge_id` from `lineage_graph.edges[].id`. List-row click does not trigger the edge detail drawer.
- The Lineage List panel renders one row per `items[]` entry with `artifact_id`, `edge_count`, and `last_edge_at`. Pagination via `page_info.next_page_token` is handled with a "Load more" button.
- The Lineage Graph panel renders the directed graph from `nodes[]` and `edges[]` at the top level of the LN-03 response. Graph-edge click opens the `LineageEdgeDetail` drawer passing the `edge_id`.
- When `lineage_graph.edges[]` is empty for a given `root_id`, the screen displays "No lineage recorded for {artifact_id}" — no blank canvas.
- When `meta.staleness` is present on any response, a non-dismissable staleness banner is rendered.
- `LineageEdgeDetail` drawer fetches the flat-root edge object via LN-02 and renders `id`, `from_artifact_id`, `to_artifact_id`, `relationship`, and `created_at`. 404 on edge renders "Lineage edge not found".
- Missing required fields in any response are emitted as an explicit BFF-gap alert, not silently omitted or backfilled.
- All three surfaces handle loading, empty, degraded, and error states as explicit and visually distinct states.

## Notes

- The Lineage Graph panel uses the `depth` parameter as a user-selectable control (range 1–10) without client-side clamping — the BFF enforces the clamp per contract.
- `root_id` is held in local React state (`selectedArtifactId`). URL-addressable `?root_id=...` query-param wiring is deferred as a follow-up item; it is not implemented in this cycle.
- `root_type` is documented as a known v1 no-op in the component comment and is not surfaced as UI. A new packet revision is required before it can be exposed.
- This review included static code verification and cross-check against `docs/bff/PKT-003-lineage-view.md`, `docs/screens/PKT-003-lineage-view.md`, and `docs/examples/PKT-003-lineage-view.json`.

## Pantheon Follow-up

- No new Pantheon API gap requested in this cycle. The original bff-gap was resolved by BP5-SVC-010.
- The next Pantheon-owned steps are: (1) move the screen mount from `/lineage` to `/evolution/lineage` once the Evolution Workbench shell route is finalized, and (2) add `useSearchParams` wiring to sync `root_id` into the URL query param at that point.
