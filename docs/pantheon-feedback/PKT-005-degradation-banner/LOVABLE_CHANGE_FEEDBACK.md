# PKT-005 Global Degradation Banner — Lovable Change Feedback

Reviewed the Global Degradation Banner implementation in `ajoe734/front-ai-trading-system` at commit `7406990a8311ef6865491fcdb883b677a98ff6c9` against the PKT-005 BFF contract, screen spec, and example payloads.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Global Degradation Banner is implemented as a shared Operator Console primitive driven exclusively by `meta.staleness` and `meta.surfaces` fields from the existing BFF composed view responses. No dedicated health-check endpoint was added. All five banner variants are implemented and all three Operator Console screens are wired. All acceptance criteria are met.

## Verified Against Pantheon

- **`src/lib/degradationBanner.ts`** — pure decision helper implementing the exact decision tree from `docs/screens/PKT-005-degradation-banner.md`. Exports: `deriveDegradationBannerState`, `mergeBannerMeta`, `findMissingSurfaceFields`, `humanizeSurfaceKey`, `formatLastKnownAge`, `asBannerSurfaceStatus`.
- **`src/components/GlobalDegradationBanner.tsx`** — shared banner primitive rendered at the top of every Operator Console screen, below the navigation rail. Non-dismissable. Renders one of five visual variants: `none`, `degraded`, `stale`, `partial`, `critical`.
- **`src/components/GlobalDegradationBanner.test.tsx`** — six test cases covering all five banner decision-tree variants plus the PKT-002 split-read merge with oldest-staleness preservation.

### Variant coverage

- **none**: all `meta.surfaces[*].status == "ok"` and `meta.staleness == null` → banner returns null.
- **degraded**: one or more surfaces `degraded`, no surface `unavailable`, `served_from ∉ ["cache", "reconstructed"]` → "SYSTEM STATUS: SOME SERVICES DEGRADED".
- **stale**: `meta.staleness.served_from ∈ ["cache", "reconstructed"]`, no surface `unavailable` → "SYSTEM STATUS: LIMITED MONITORING" with humanised age from `meta.staleness.last_known_at`. If absent, renders "some time ago".
- **partial**: mixed map with at least one `unavailable` and at least one non-unavailable surface → "SYSTEM STATUS: PARTIAL DATA" listing per-surface humanised key names and status labels.
- **critical**: BFF request failed (`requestFailed=true`) or all surfaces `unavailable` → "SYSTEM STATUS: CONTROL PLANE UI DOWN" with secondary control path links.

### Screen integration

- **PKT-001 Deployment Review (`DeploymentReviewConsole.tsx`)**: banner state derived from the deployment plan list `meta` when the list view is active, or from the detail-panel `meta` (via `onMetaChange` / `onRequestFailureChange` props on `DeploymentPlanDetail`) when a specific plan is selected. Refresh triggers a partial re-fetch of the active view only.
- **PKT-002 Incident Home (`IncidentHome.tsx`)**: split-read merge implemented via `mergeBannerMeta`. Expected surface keys `incident_list` and `kill_switch` are pre-seeded as `unavailable`; each response updates its own key only. Oldest `last_known_at` staleness is preserved across the two independent responses. `findMissingSurfaceFields` is called on each response to detect missing surface keys and emit BFF-gap contract alerts.
- **PKT-002 Incident Detail (`IncidentDetail.tsx`)**: banner state derived from `GET /api/v1/operator/incident-response/{incident_id}` `meta`. Per-surface unavailable states remain visible on individual panels; the banner is the cross-screen summary only.
- **PKT-003 Post-Incident Review (`PostIncidentReviewConsole.tsx`)**: banner state derived from `GET /api/v1/operator/post-incident-review/{incident_id}` `meta`. Per-panel unavailable states are preserved; banner is supplementary.

### Constraint compliance

- No raw `fetch` or `axios` calls in component files. All BFF access is through the shared `operatorApi` BFF client.
- No dedicated `GET /api/v1/system/health` or equivalent health-check endpoint was added.
- Banner state is not derived from SSE event payloads. SSE may trigger a full BFF refetch; only the refreshed `meta` snapshot updates the banner.
- No additional surface status enums beyond `ok | degraded | unavailable`.
- Missing surface keys in BFF responses are detected via `findMissingSurfaceFields` and surfaced as inline "BFF contract gap detected" alerts.

### Decision tree compliance

The `deriveDegradationBannerState` function implements the decision tree exactly as published:

```
if requestFailed or all surfaces unavailable → critical
elif any surface unavailable → partial
elif meta.staleness.served_from ∈ ["cache","reconstructed"] and any surface degraded → stale
elif any surface degraded → degraded
else → none
```

## Notes

- The STALE variant requires at least one degraded surface combined with `meta.staleness.served_from ∈ ["cache","reconstructed"]`. A response where all surfaces are `ok` but `served_from = "cache"` would fall through to `none` — this is consistent with the decision tree (no degraded surface = no staleness signal to show).
- The PARTIAL variant includes all non-`ok` surfaces in its surface list, humanised via `humanizeSurfaceKey` (e.g., `runtime_binding` → "Runtime Binding").
- The "Refresh now" / "Refresh" button does a partial re-fetch of the current view — it does not reinitialise the full page or navigate away.
- "Use admin CLI" and "View secondary control path guide" are static links pointing to Pantheon repo paths; they do not require additional BFF calls.
- `npm run build`, targeted ESLint, and `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx` passed without errors.

## Pantheon Follow-up

- No Pantheon API gap is requested in this cycle. The `meta.surfaces` and `meta.staleness` fields are confirmed present in the PKT-005 contract-ready packet.
- No front-end rework is requested from this review. The remaining Pantheon-owned follow-up is to publish the normalized PKT-005 contract lock and delivery packet from a real Pantheon commit.
