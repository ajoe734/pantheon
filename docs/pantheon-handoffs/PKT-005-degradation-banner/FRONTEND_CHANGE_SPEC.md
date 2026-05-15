# PKT-005 Global Degradation Banner — Frontend Change Spec

## Feature

- Feature ID: `PKT-005-degradation-banner`
- Screen ID: `surface-operator-global-degradation-banner`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the shared **Global Degradation Banner** inside `front-ai-trading-system`. This is a cross-cutting Operator Console primitive that derives screen health from the current BFF response `meta` only. It does not fetch a separate health endpoint, does not invent client-side shadow state, and does not use SSE payloads as the source of truth for banner state.

## Files to Create or Modify

```text
src/components/GlobalDegradationBanner.tsx          — shared banner primitive
src/components/GlobalDegradationBanner.test.tsx     — variant and decision-tree coverage
src/lib/degradationBanner.ts                        — pure decision helper for banner derivation
src/pages/operator/DeploymentReviewConsole.tsx      — mount banner for PKT-001
src/pages/operator/IncidentHome.tsx                 — mount banner and merge split-read meta for PKT-002 home
src/pages/operator/IncidentDetail.tsx               — mount banner for PKT-002 detail
src/pages/operator/PostIncidentReviewConsole.tsx    — mount banner for PKT-003
src/lib/bffClient.ts                                — no new endpoint; reuse existing screen fetches only
```

## API Integration

Use the existing BFF client only. Do not add raw `fetch` or `axios` calls in component files.

### No dedicated endpoint

The banner does **not** have its own BFF route. Derive banner state from these existing reads:

```text
GET /api/v1/operator/deployment-review/{plan_id}
GET /api/v1/operator/incident-response/{incident_id}
GET /api/v1/operator/post-incident-review/{incident_id}
GET /api/v1/incidents
GET /api/v1/kill-switch/status
```

### Required response fields

Every composed view consumed by the banner must provide:

```typescript
interface BannerMeta {
  snapshot_at: string | null;
  staleness: null | {
    served_from: "primary" | "replica" | "cache" | "reconstructed" | "none";
    last_known_at?: string;
    max_age_minutes?: number;
  };
  surfaces: Record<string, { status: "ok" | "degraded" | "unavailable" }>;
}
```

## Banner Decision Rules

Implement the decision tree exactly as published in `docs/screens/PKT-005-degradation-banner.md`.

### Variants

- `none`: all `meta.surfaces[*].status == "ok"` and `meta.staleness == null`
- `degraded`: one or more surfaces are `degraded`, and no surface is `unavailable`
- `stale`: `meta.staleness.served_from ∈ ["cache", "reconstructed"]`, no surface is `unavailable`
- `partial`: a mixed surface map that includes at least one `unavailable` surface and at least one `ok` or `degraded` surface
- `critical`: the BFF request failed entirely, or all surfaces are `unavailable`

### Rendering rules

- The banner is non-dismissable.
- It sits at the top of each Operator Console screen, below the global navigation rail.
- It disappears automatically when the next fresh BFF `meta` snapshot returns all surfaces to `ok`.
- The STALE variant shows humanised age from `meta.staleness.last_known_at`. If absent, render "some time ago".
- The PARTIAL variant lists each degraded or unavailable surface using humanised key names (`runtime_binding` → `Runtime Binding`).
- The CRITICAL variant is a client-constructed state for request failure or all-unavailable surfaces. Do not synthesize success when the request throws.

## Screen Integration

### PKT-001 Deployment Review

- Read banner state directly from `GET /api/v1/operator/deployment-review/{plan_id}` `meta`.
- Do not issue a second fetch when only the banner needs to update.

### PKT-002 Incident Home (split-read)

- Merge `meta.surfaces` from `GET /api/v1/incidents` and `GET /api/v1/kill-switch/status` into one screen-level surface map.
- Treat any expected surface key that has not returned yet as `unavailable`.
- For `meta.staleness`, use the non-null object with the oldest `last_known_at`.
- If `incident_list` or `kill_switch` is absent from its response `meta.surfaces`, emit a `bff-gap` handoff.

### PKT-002 Incident Detail

- Read banner state from `GET /api/v1/operator/incident-response/{incident_id}` `meta`.
- The banner does not replace the screen's explicit degraded or unavailable panel states.

### PKT-003 Post-Incident Review

- Read banner state from `GET /api/v1/operator/post-incident-review/{incident_id}` `meta`.
- Keep per-panel unavailable states visible; the banner is only the cross-screen summary.

## Constraints

- Use the current screen response `meta` as the only source of truth for banner state.
- Do not add a dedicated BFF health-check fetch.
- Do not derive banner state from SSE events directly. SSE may trigger a full refetch; only the refreshed BFF `meta` may update the banner.
- Do not invent additional surface enums beyond `ok | degraded | unavailable`.
- If any required `meta.surfaces` key is absent from a response, write `.coordination/requests/PKT-005-degradation-banner-bff-gap.yaml` using `.coordination/requests/PKT-005-degradation-banner-bff-gap.example.yaml` and stop implementation.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml` using `.coordination/requests/PKT-005-degradation-banner-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-005-degradation-banner.md`
- BFF contract: `docs/bff/PKT-005-degradation-banner.md`
- Example payload: `docs/examples/PKT-005-degradation-banner.json`
- Contract-ready: `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-005-degradation-banner-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-005-degradation-banner-ui-done.example.yaml`
