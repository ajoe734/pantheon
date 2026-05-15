# Review Report: PKT-003

**Task ID**: PKT-003
**Artifact**: `PKT-003-post-incident-evolution-packet-family` packet set
**Reviewer**: Codex
**Date**: 2026-04-14
**Status**: Changes requested

## Re-review Summary

The previous six blockers are resolved:

- Post-Incident Review now uses the nested `telemetry_performance.summary` shape in the screen spec, BFF contract, and example payload.
- Evolution Center now points the Freeze Orders panel at `GET /api/v1/freeze-orders` without an active-only filter.
- Lineage View now documents the LN-03 empty-state check against graph `edges`.
- Mutation Review now spells out the four unresolved `EVO-004` action paths: freeze, rollback, retrain, and redeploy.
- All three PKT-003 Lovable handoff bundles now exist under `docs/pantheon-handoffs/PKT-003-*` with `FRONTEND_CHANGE_SPEC.md`.
- Lineage View `.coordination` artifacts now key the empty-state rule off `lineage_graph.edges`.

One readiness blocker still remains in the Lineage View packet.

## Findings

### 1. Blocking: Lineage View still defines an impossible "list row -> edge detail drawer" interaction

The PKT-003 Lineage View packet still says the edge-detail drawer opens from a lineage list row selection, but LN-01 does not expose any `edge_id` the frontend could use to call `GET /api/v1/lineage/edges/{edge_id}`.

- The canonical screen spec says the lineage list rows only contain `artifact_id`, `edge_count`, and `last_edge_at`, while the edge-detail drawer still "opens on row selection" (`docs/screens/PKT-003-lineage-view.md:16-18`, `docs/screens/PKT-003-lineage-view.md:35-37`)
- The BFF contract for LN-01 requires only `artifact_id`, `edge_count`, and `last_edge_at`; it does not publish an `edge_id` or any other stable edge selector on the list surface (`docs/bff/PKT-003-lineage-view.md:14-23`)
- The example payload for `lineage_list.items[]` also contains only `artifact_id`, `edge_count`, and `last_edge_at` (`docs/examples/PKT-003-lineage-view.json:2-16`)
- The newly added frontend handoff bundle repeats the same impossible flow: the Lineage list rows render only those three fields, but clicking a row is still supposed to open `LineageEdgeDetail` and fetch `GET /api/v1/lineage/edges/{edge_id}` (`docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md:43-47`, `docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md:106-120`)

Why this blocks approval:
The packet still claims the Lineage View is implementation-ready, but the canonical interaction contract does not provide a usable identifier for the promised edge-detail flow. Downstream frontend work would have to invent an unapproved selector or guess a different trigger path, which means the packet is not actually ready.

## Recommendation

Do not approve `PKT-003` yet.

The next revision should:

1. Resolve the Lineage View interaction contract one way or the other:
   - either extend LN-01 to return a stable edge selector the frontend may legally use for the drawer, or
   - change the screen spec and handoff bundle so the edge-detail drawer is triggered from a surface that actually has an `edge_id` available, such as graph-edge selection rather than list-row selection.
2. Keep the canonical screen spec, BFF contract, example payload, `.coordination` YAML, and `docs/pantheon-handoffs/PKT-003-lineage-view/FRONTEND_CHANGE_SPEC.md` aligned on that same interaction.
3. Re-handoff PKT-003 for review once the Lineage View ready-state no longer depends on an unavailable identifier.

## Re-review approval (2026-04-14)

The Lineage View packet is now internally consistent on the edge-detail interaction:

- the screen spec keeps list rows limited to `artifact_id`, `edge_count`, and `last_edge_at`, and moves the edge drawer trigger to graph-edge selection using `lineage_graph.edges[].id`
- the frontend change spec matches that same contract: row click loads the graph, graph-edge click opens `LineageEdgeDetail`
- both `.coordination` response YAML files now repeat the same rule, so downstream frontend work no longer depends on inventing an unavailable `edge_id`

The earlier blocker is resolved, and the broader PKT-003 packet family still preserves the required ready-vs-blocked split:

- Post-Incident Review Console, Evolution Center, and Lineage View remain packet-ready
- Inspiration Graph and Mutation Review remain explicitly blocked behind the named BFF / `EVO-004` gaps rather than being misrepresented as ready

No blocking review findings remain.

### Approval recommendation

`PKT-003` is approved and can move to `review_approved`.
