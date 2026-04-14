# PKT-002 Incident Home — Frontend Change Spec

## Feature

- Feature ID: `PKT-002-incident-home`
- Screen ID: `screen-operator-incident-home`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Incident Home** screen inside `front-ai-trading-system`. This screen gives operators a single entry point for all active and recent incidents, with the kill switch status always visible as a control rail badge. All data and CTA authority must come from Pantheon BFF — no local derivation or mock state.

## Files to Create or Modify

```
src/pages/operator/IncidentHome.tsx           — new incident list page
src/pages/operator/types.ts                   — add incident-home types
src/lib/bffClient.ts                          — add incident-home fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch incident list

```
GET /api/v1/incidents
Query params: status (comma-separated: open | in_progress | resolved), page_token, page_size
```

Expected response shape (see `docs/examples/PKT-002-incident-home.json` for a full example):

```typescript
interface IncidentListResponse {
  items: IncidentSummary[];
  page_info: { next_page_token: string | null };
  meta: {
    snapshot_at: string;
    surfaces: {
      incident_list: "ok" | "degraded" | "unavailable";
    };
    degradation?: { reason: string };
  };
}

interface IncidentSummary {
  incident_id: string;
  title: string;
  severity: "sev1" | "sev2" | "sev3";
  status: "open" | "in_progress" | "resolved";
  artifact_id: string;
  opened_at: string;
}
```

### Fetch kill switch status

```
GET /api/v1/kill-switch/status
```

Expected response shape:

```typescript
interface KillSwitchStatusResponse {
  kill_switch: {
    status: "armed" | "triggered" | "cooling_down";
    last_triggered_at: string | null;
    last_confirmed_at: string;
    active_commands: string[];
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      kill_switch: "ok" | "degraded" | "unavailable";
    };
    degradation?: { reason: string };
  };
}
```

## Component Structure

### `IncidentHome.tsx`

- Fetches both `GET /api/v1/incidents` and `GET /api/v1/kill-switch/status` independently on mount. The kill switch fetch must not block incident list rendering.
- **Kill Switch Control Rail**: persistent badge at the top showing `kill_switch.status`.
  - When `meta.surfaces.kill_switch = ok`: render the status badge normally.
  - When `meta.surfaces.kill_switch = degraded`: render the badge with a staleness caveat showing `last_confirmed_at` timestamp. Do not hide the rail.
  - When `meta.surfaces.kill_switch = unavailable`: render "Kill switch status unavailable" banner. Do not assume any kill switch state.
- **Incident List panel**: paginated list filtered by `status=open,in_progress`. Each row shows `incident_id`, `title`, `severity`, `status`, `artifact_id`, and `opened_at`. Row click navigates to `screen-operator-incident-detail`.
- **Resolved Incidents tab**: secondary tab showing `status=resolved` incidents. Same row shape.
- **Degradation banner**: when any `meta.surfaces` entry is not `ok`, render a non-dismissable banner naming the affected surface.
- **Loading, empty, degraded, and error states**: explicit and visually distinct. No mock fallback.
- Status filter is passed as a query parameter — do not filter client-side.
- If any expected `meta.surfaces` key is absent from either BFF response, emit a `bff-gap` handoff using `.coordination/requests/PKT-002-incident-home-bff-gap.example.yaml` as the template.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Kill switch state must come from the BFF response. Do not derive state locally.
- Filters must be passed as query parameters to the BFF — do not filter client-side.
- Secondary control path copy is **not** part of this screen. It belongs to the Incident Action Drawer.

## Degradation Handling

| Surface | `degraded` | `unavailable` |
|---|---|---|
| `incident_list` | Show available items plus degradation banner | Show unavailable banner with no list content |
| `kill_switch` | Show badge with last-known state and `last_confirmed_at` staleness note | Show "Kill switch status unavailable" banner; do not assume any state |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-home-ui-done.yaml` using `.coordination/requests/PKT-002-incident-home-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-002-incident-home.md`
- BFF contract: `docs/bff/PKT-002-incident-home.md`
- Example payload: `docs/examples/PKT-002-incident-home.json`
- Contract-ready: `.coordination/responses/PKT-002-incident-home-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-002-incident-home-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-002-incident-home-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-002-incident-home-ui-done.example.yaml`
