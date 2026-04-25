# PKT-013 Operator Home Dashboard — Frontend Change Spec

## Feature

- Feature ID: `PKT-013-operator-home`
- Screen ID: `screen-operator-home-dashboard`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Operator Home Dashboard** inside `front-ai-trading-system`. This screen gives operators one truthful landing page summarizing alerts, incidents, governance pressure, runtime coverage, and health state. All summary cards and escalation shortcuts must come from Pantheon BFF.

## Files to Create or Modify

```
src/pages/operator/OperatorHomeDashboard.tsx     — new operator-home screen
src/pages/operator/types.ts                      — add operator-home response types
src/lib/bffClient.ts                             — add operator-home fetch helper
```

## API Integration

Use the shared BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch operator home

```
GET /api/v1/operator/home
```

Expected response shape (see `docs/examples/PKT-013-operator-home.json` for a full example):

```typescript
interface OperatorHomeResponse {
  overall_status: "ok" | "degraded" | "unavailable";
  headline: string;
  message: string;
  safe_mode_state: {
    status: string | null;
    kill_switch_status: string | null;
    active: boolean | null;
    last_confirmed_at: string | null;
    last_triggered_at: string | null;
    secondary_path_available: boolean | null;
  };
  cards: Array<{
    card_id: "alerts" | "incidents" | "governance" | "runtime" | "health";
    label: string;
    status: "ok" | "degraded" | "unavailable";
    summary: string;
    details: Record<string, unknown>;
    target_refs: Array<{
      label: string;
      href: string;
      surface_id?: string;
    }>;
  }>;
  escalation_shortcuts: Array<{
    shortcut_id: string;
    label: string;
    reason: string;
    href: string;
    priority: "high" | "medium" | "low";
  }>;
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable"; source?: string }>;
  };
}
```

## Component Structure

### `OperatorHomeDashboard.tsx`

- Fetches `GET /api/v1/operator/home` on mount.
- Renders the summary header from `overall_status`, `headline`, `message`, and `safe_mode_state`.
- Renders exactly five cards from `cards[]` in backend-owned order.
- Renders `escalation_shortcuts[]` exactly in backend-owned order.
- Uses each card's `target_refs[]` for owner-screen navigation.
- Renders the shared degradation banner when any `meta.surfaces.*` entry is degraded or unavailable.

## Constraints

- Do not recreate the home dashboard by separately calling alerts, health, incidents, governance queues, runtime state, telemetry, or kill-switch endpoints.
- Do not reorder `cards[]` or `escalation_shortcuts[]`.
- Do not invent calm empty-state copy when `overall_status = "unavailable"` or upstream surfaces are degraded.
- Do not add approval, rollback, kill-switch activation, or runtime write CTAs.
- If any required field or `meta.surfaces.*` entry is missing, write `.coordination/requests/PKT-013-operator-home-bff-gap.yaml` and stop implementation.

## Degradation Handling

- `overall_status = "degraded"` keeps the dashboard visible and read-only.
- `overall_status = "unavailable"` renders the explicit unavailable state.
- `safe_mode_state` and `meta.surfaces.*` are backend-owned and must be rendered as supplied.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-013-operator-home-ui-done.yaml` and sync it back so Pantheon can review the return.

## References

- BFF contract: `docs/bff/PKT-013-operator-home.md`
- Screen spec: `docs/screens/PKT-013-operator-home.md`
- Example payload: `docs/examples/PKT-013-operator-home.json`
- Packet family: `docs/pantheon-handoffs/OC-002-operator-console-wave2/PACKET_FAMILY.md`
