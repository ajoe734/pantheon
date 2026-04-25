# PKT-012 Operator Alerts Rail — Frontend Change Spec

## Feature

- Feature ID: `PKT-012-alerts-rail`
- Screen ID: `screen-operator-alerts-rail`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Operator Alerts Rail** inside `front-ai-trading-system`. This screen shows one operator-owned alert feed for incidents, governance queues, kill-switch state, and runtime anomalies. The UI must not reconstruct alert severity or alert identity client-side.

## Files to Create or Modify

```
src/pages/operator/OperatorAlertsRail.tsx        — new alerts-rail screen
src/pages/operator/types.ts                      — add alerts-rail response types
src/lib/bffClient.ts                             — add operator alerts fetch helper
```

## API Integration

Use the shared BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch alerts rail

```
GET /api/v1/operator/alerts
```

Expected response shape (see `docs/examples/PKT-012-alerts-rail.json` for a full example):

```typescript
interface OperatorAlertsResponse {
  alerts: Array<{
    alert_id: string;
    severity: "critical" | "high" | "medium" | "low";
    category: "incident" | "governance" | "kill_switch" | "runtime";
    raised_at: string;
    summary: string;
    target_ref: {
      surface_id: string;
      label: string;
      href: string;
      target_id?: string;
    };
  }>;
  summary: {
    total_active: number;
    highest_severity: "critical" | "high" | "medium" | "low" | null;
    by_severity: Record<"critical" | "high" | "medium" | "low", number>;
    by_category: Record<"incident" | "governance" | "kill_switch" | "runtime", number>;
  };
  meta: {
    snapshot_at: string;
    acknowledgement_supported: false;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable"; source?: string }>;
  };
}
```

## Component Structure

### `OperatorAlertsRail.tsx`

- Fetches `GET /api/v1/operator/alerts` on mount.
- Renders the alert feed header from `summary`.
- Renders one alert row per `alerts[]` entry in backend-owned order.
- Uses `target_ref` exactly as supplied for owner-screen navigation.
- Renders the shared degradation banner when any `meta.surfaces.*` entry is degraded or unavailable.

## Constraints

- Do not combine incidents, governance queues, kill-switch state, or runtime telemetry in the browser.
- Do not invent alert acknowledgement, dismissal, or snooze actions.
- Do not remap alert severities or categories.
- If any required field or `meta.surfaces.*` entry is missing, write `.coordination/requests/PKT-012-alerts-rail-bff-gap.yaml` and stop implementation.

## Degradation Handling

- `meta.surfaces.alerts = "degraded"` keeps the rail visible and read-only.
- `meta.surfaces.alerts = "unavailable"` renders the explicit unavailable state.
- `meta.acknowledgement_supported = false` means no acknowledgement CTA appears in this packet.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-012-alerts-rail-ui-done.yaml` and sync it back so Pantheon can review the return.

## References

- BFF contract: `docs/bff/PKT-012-alerts-rail.md`
- Screen spec: `docs/screens/PKT-012-alerts-rail.md`
- Example payload: `docs/examples/PKT-012-alerts-rail.json`
- Packet family: `docs/pantheon-handoffs/OC-002-operator-console-wave2/PACKET_FAMILY.md`
