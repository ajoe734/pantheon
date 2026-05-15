# PKT-003 Inspiration Graph UI Decisions

- The screen remains bound to the composed EW-04 inspiration route instead of
  traversing raw lineage endpoints client-side.
- Invalid payloads are rejected before renderable state is committed so the page
  fails closed on malformed responses.
- `meta.surfaces.inspiration` continues to control the non-dismissable
  degradation states; the UI does not infer freshness from graph content.
- The graph stays read-only: edge selection opens the drawer, but no mutation
  actions are exposed.
- The route-live shell state is reflected by removing the stale `Soon` badge
  from the Inspiration navigation entry.
- This follow-up publishes the canonical frontend-feedback bundle so Pantheon
  can replay the packet review state from Git-visible artifacts.
