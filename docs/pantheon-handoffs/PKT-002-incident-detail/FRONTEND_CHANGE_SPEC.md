# PKT-002 Incident Detail — Frontend Change Spec

## Feature

- Feature ID: `PKT-002-incident-detail`
- Screen ID: `screen-operator-incident-detail`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Incident Detail** screen inside `front-ai-trading-system`. This screen gives operators a complete view of a single active incident — incident record, affected bindings, kill switch state, and available emergency actions — from a single composed BFF view. The UI must not join surfaces client-side. All data and CTA authority must come from Pantheon BFF.

## Files to Create or Modify

```
src/pages/operator/IncidentDetail.tsx         — new incident detail page
src/pages/operator/types.ts                   — add incident-detail types
src/lib/bffClient.ts                          — add incident-detail fetch call
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch incident response composed view

```
GET /api/v1/operator/incident-response/{incident_id}
Optional query param: snapshot=preferred
```

Expected response shape (see `docs/examples/PKT-002-incident-detail.json` for a full example):

```typescript
interface IncidentDetailResponse {
  data: {
    incident: {
      incident_id: string;
      title: string;
      severity: "sev1" | "sev2" | "sev3";
      status: "open" | "in_progress" | "resolved";
      artifact_id: string;
      artifact_version: string;
      runtime_id: string;
      trace_id: string;
      opened_at: string;
    };
    affected_bindings: AffectedBinding[];
    kill_switch: {
      status: "armed" | "triggered" | "cooling_down";
      last_triggered_at: string | null;
      last_confirmed_at: string;
      active_commands: string[];
    };
  };
  allowedActions: {
    canPause: boolean;
    canRiskOff: boolean;
    canLiquidateAll: boolean;
    canHardRollback: boolean;
    canIssueSafeMode: boolean;
    canOpenActionDrawer: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      incident: "ok" | "degraded" | "unavailable";
      affected_bindings: "ok" | "degraded" | "unavailable";
      kill_switch: "ok" | "degraded" | "unavailable";
      allowedActions: "ok" | "degraded" | "unavailable";
    };
    degradation?: {
      affected_bindings_reason?: string;
      kill_switch_reason?: string;
      allowedActions_reason?: string;
    };
    staleness?: { reason: string };
  };
}

interface AffectedBinding {
  binding_id: string;
  persona_id: string;
  capital_pool_id: string;
  stage: "paper" | "live";
  binding_status: string;
}
```

## Component Structure

### `IncidentDetail.tsx`

- Receives `incident_id` from the route.
- Fetches from `GET /api/v1/operator/incident-response/{incident_id}` on mount. Do not re-fetch individual surfaces separately.
- **Incident summary panel**: renders all fields from `data.incident`.
- **Affected bindings panel**:
  - When `meta.surfaces.affected_bindings = ok` and `data.affected_bindings` is empty: render "No affected bindings recorded".
  - When `meta.surfaces.affected_bindings = degraded`: render any available binding records followed by a named degradation notice: "Affected bindings data is partially unavailable — [meta.degradation.affected_bindings_reason]". Do not collapse a degraded read into the empty-success copy.
  - When `meta.surfaces.affected_bindings = ok` and the list is non-empty: render the list normally.
- **Kill Switch status panel**:
  - When `meta.surfaces.kill_switch = ok`: render `status`, `last_triggered_at`, `last_confirmed_at`, `active_commands`.
  - When `meta.surfaces.kill_switch = degraded`: render the last known state with a staleness note showing `last_confirmed_at`. Show the non-dismissable degradation banner.
  - When `meta.surfaces.kill_switch = unavailable`: render "Kill switch status unavailable". Do not assume any kill switch state.
- **Action entry strip**: read-only summary of allowed actions from `allowedActions`. Each allowed action shows its name and a short rationale. Includes an **Open Action Drawer** CTA, disabled when `allowedActions.canOpenActionDrawer = false`.
- **Degradation banner**: when any `meta.surfaces` entry is not `ok`, render a non-dismissable banner naming the degraded surface and disabling the relevant CTAs.
- **Loading, empty, degraded, and error states**: explicit and visually distinct with no mock fallback.
- 404 on `{incident_id}`: render "Incident not found" with the ID and a back action.
- If any expected `meta.surfaces` key is absent from the response, emit a `bff-gap` handoff.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Do not re-fetch individual surfaces (incidents, kill switch, bindings) separately — use the composed view only.
- CTA visibility for the action entry strip must come from `allowedActions` only. Do not derive eligibility locally.
- Never collapse `meta.surfaces.affected_bindings = degraded` into the same copy used for a true empty list.

## Degradation Handling

| Surface | `degraded` | `unavailable` |
|---|---|---|
| `incident` | Show banner; render partial data | Show banner; render unavailable state |
| `affected_bindings` | Show available records + explicit named degradation notice | Show banner; do not render as empty |
| `kill_switch` | Show last known state with staleness note | Show "Kill switch status unavailable"; assume no state |
| `allowedActions` | All CTAs disabled; show banner | All CTAs disabled; show banner |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-detail-ui-done.yaml` using `.coordination/requests/PKT-002-incident-detail-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-002-incident-detail.md`
- BFF contract: `docs/bff/PKT-002-incident-detail.md`
- Example payload: `docs/examples/PKT-002-incident-detail.json`
- Contract-ready: `.coordination/responses/PKT-002-incident-detail-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-002-incident-detail-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-002-incident-detail-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-002-incident-detail-ui-done.example.yaml`
