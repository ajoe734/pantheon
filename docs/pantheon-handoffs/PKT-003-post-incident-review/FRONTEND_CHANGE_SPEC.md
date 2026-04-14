# PKT-003 Post-Incident Review Console — Frontend Change Spec

## Feature

- Feature ID: `PKT-003-post-incident-review`
- Screen ID: `screen-operator-post-incident-review`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Post-Incident Review Console** inside `front-ai-trading-system`. This screen gives operators a single surface to review a resolved incident end-to-end: the incident record, the postmortem findings, associated evolution decisions, artifact lineage, and telemetry performance — without joining surfaces client-side. All data authority must come from the Pantheon BFF composed view.

## Files to Create or Modify

```
src/pages/operator/PostIncidentReviewConsole.tsx     — new list + detail panel page
src/pages/operator/types.ts                          — add post-incident-review types
src/lib/bffClient.ts                                 — add post-incident-review fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch resolved incident list

```
GET /api/v1/incidents?status=resolved
Query params: status (comma-separated: open | resolved), page_token, page_size
```

Expected response shape (see `docs/examples/PKT-003-post-incident-review-console.json` for a full example):

```typescript
interface IncidentListResponse {
  items: IncidentSummary[];
  page_info: { next_page_token: string | null };
  meta: { snapshot_at: string };
}

interface IncidentSummary {
  incident_id: string;
  title: string;
  status: "open" | "resolved";
  artifact_id: string;
  resolved_at: string;
}
```

### Fetch post-incident review composed view

```
GET /api/v1/operator/post-incident-review/{incident_id}
Query params: snapshot=preferred
```

Expected response shape:

```typescript
interface PostIncidentReviewResponse {
  data: {
    incident: {
      incident_id: string;
      title: string;
      status: string;
      artifact_id: string;
      artifact_version: string;
      runtime_id: string;
      trace_id: string;
    };
    postmortem: {
      postmortem_id: string;
      status: string;
      root_cause: string;
      action_items: string[];
    } | null;
    evolution_decisions: EvolutionDecisionRef[];
    lineage_edges: LineageEdge[];
    telemetry_performance: {
      artifact_id: string;
      window: string;
      summary: {
        total_pnl: number;
        max_drawdown: number;
        sharpe_ratio: number;
      };
    } | null;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      postmortem: "ok" | "degraded" | "unavailable";
      evolution_decisions: "ok" | "degraded" | "unavailable";
      lineage: "ok" | "degraded" | "unavailable";
      telemetry_performance: "ok" | "degraded" | "unavailable";
    };
    staleness?: { reason: string; served_from: string };
  };
}

interface EvolutionDecisionRef {
  id: string;
  action_type: string;
  risk_level: "low" | "medium" | "high";
  status: string;
  incident_ref: string;
  artifact_id: string;
}

interface LineageEdge {
  id: string;
  from_artifact_id: string;
  to_artifact_id: string;
  relationship: string;
}
```

### Fetch postmortem index (navigation only)

```
GET /api/v1/postmortems
```

Used for navigation only — not the primary data source for the detail panel.

## Component Structure

### `PostIncidentReviewConsole.tsx`

- **Incident list panel**: fetches `GET /api/v1/incidents?status=resolved` on mount.
  - Renders one row per item with `incident_id`, `title`, `status`, `artifact_id`, and `resolved_at`.
  - Clicking a row opens the detail panel and fetches the composed view.
- **Post-Incident Review detail panel**: fetches `GET /api/v1/operator/post-incident-review/{incident_id}?snapshot=preferred` on row selection.
  - **Incident summary**: renders `incident_id`, `title`, `status`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id`.
  - **Postmortem panel**:
    - When `meta.surfaces.postmortem = ok`: render `postmortem_id`, `status`, `root_cause`, `action_items[]`.
    - When `meta.surfaces.postmortem = degraded`: render "Postmortem pending" panel with the `incident_id`. Do not show an empty state.
    - When `meta.surfaces.postmortem = unavailable`: render an explicit unavailable banner. Do not hide the panel.
  - **Evolution decisions panel**: renders `evolution_decisions[]` with `action_type`, `risk_level`, `status`, and `artifact_id` per row. Renders "No evolution decisions" when the list is empty.
  - **Lineage edges panel**: renders `lineage_edges[]` with `from_artifact_id`, `to_artifact_id`, and `relationship` per row. Renders "No lineage evidence" when empty or when `meta.surfaces.lineage = degraded`.
  - **Telemetry performance panel**: renders `telemetry_performance.summary` fields (`total_pnl`, `max_drawdown`, `sharpe_ratio`) and the `window`. Renders "No telemetry evidence" when `telemetry_performance` is null or `meta.surfaces.telemetry_performance = degraded`.
- **Degradation banner**: when any `meta.surfaces` entry is not `ok`, render a non-dismissable banner naming the affected panel. Do not hide content silently.
- **Staleness banner**: when `meta.staleness` is present on the detail response, render a non-dismissable banner.
- **Loading, empty, degraded, and error states**: explicit and visually distinct with no mock fallback.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- The detail panel must use `GET /api/v1/operator/post-incident-review/{incident_id}` as the primary source — do not re-fetch individual surfaces separately.
- `meta.surfaces` gating must come from the BFF response. Do not derive panel visibility locally.
- No write actions on this screen — all incident response write actions belong to `PKT-002`.
- If any `meta.surfaces` key is absent from the BFF response, write `.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml` using `.coordination/requests/PKT-003-post-incident-review-bff-gap.example.yaml` as the template and stop implementation.

## Degradation Handling

| Surface | `degraded` | `unavailable` |
|---|---|---|
| `postmortem` | Show "Postmortem pending" panel with `incident_id`; do not hide the panel or show empty | Show explicit unavailable banner; do not hide the panel |
| `evolution_decisions` | Show available items plus degradation banner | Show unavailable banner with no list content |
| `lineage` | Show "No lineage evidence yet" with a staleness note | Show "Lineage unavailable" banner |
| `telemetry_performance` | Show "No telemetry evidence yet" | Show "Telemetry performance unavailable" banner |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml` using `.coordination/requests/PKT-003-post-incident-review-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-003-post-incident-review-console.md`
- BFF contract: `docs/bff/PKT-003-post-incident-review-console.md`
- Example payload: `docs/examples/PKT-003-post-incident-review-console.json`
- Contract-ready: `.coordination/responses/PKT-003-post-incident-review-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-003-post-incident-review-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-003-post-incident-review-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-003-post-incident-review-ui-done.example.yaml`
