# PKT-005 Global Degradation Banner — UI Decisions

- Banner state is derived exclusively from the current screen's BFF response `meta` fields (`meta.surfaces` and `meta.staleness`). No client-side shadow state, no separate health-check fetch, and no SSE-derived banner state.
- The five banner variants (`none`, `degraded`, `stale`, `partial`, `critical`) are resolved by a pure decision helper (`src/lib/degradationBanner.ts`) that is fully testable outside React.
- For PKT-002 Incident Home (split-read screen), expected surface keys `incident_list` and `kill_switch` are pre-seeded as `"unavailable"` in the merged surface map before either response arrives, so the banner can render a partial state immediately rather than waiting for both responses.
- The oldest `meta.staleness.last_known_at` across all split-read responses is used as the screen-level staleness indicator, consistent with the BFF contract merge rule.
- The STALE variant distinguishes itself from DEGRADED by checking `meta.staleness.served_from ∈ ["cache","reconstructed"]`, not by a special per-surface status value. This matches the decision tree in `docs/screens/PKT-005-degradation-banner.md`.
- Surface key names are humanised via `humanizeSurfaceKey` (underscore-split, title-case) for the PARTIAL variant surface list. No static lookup table is maintained.
- The banner is non-dismissable. It disappears automatically when the next BFF `meta` snapshot returns all surfaces to `ok`.
- "Refresh now" / "Refresh" re-fetches the screen's primary composed view. It does not reinitialise the page or navigate away.
- Static link targets for "Use admin CLI" and "View secondary control path guide" reference published Pantheon repo paths and do not require BFF calls.
- Missing required `meta.surfaces` keys are surfaced as inline BFF-gap alerts using `findMissingSurfaceFields`. No silent mock fallback is used.
