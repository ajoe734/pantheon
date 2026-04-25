# PKT-011 Operator Health Status Board — Frontend Change Spec

## Feature

- Feature ID: `PKT-011-health-status-board`
- Screen ID: `screen-operator-health-status-board`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Operator Health Status Board** inside `front-ai-trading-system`. This screen gives operators one truthful health board for overall control-plane status, safe-mode state, grouped surface health, and secondary control path guidance. All health grouping must come from Pantheon BFF; the UI must not assemble the board from unrelated routes.

## Files to Create or Modify

```
src/pages/operator/OperatorHealthStatusBoard.tsx   — new health-status screen
src/pages/operator/types.ts                        — add health-status response types
src/lib/bffClient.ts                               — add health-status fetch helper
```

## API Integration

Use the shared BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch health-status board

```
GET /api/v1/operator/health-status
```

Expected response shape (see `docs/examples/PKT-011-health-status-board.json` for a full example):

```typescript
interface HealthStatusBoardResponse {
  overall_status: "ok" | "degraded" | "unavailable";
  headline: string;
  message: string;
  group_counts: {
    ok: number;
    degraded: number;
    unavailable: number;
  };
  safe_mode_state: {
    status: string | null;
    kill_switch_status: string | null;
    active: boolean | null;
    last_confirmed_at: string | null;
    last_triggered_at: string | null;
    secondary_path_available: boolean | null;
  };
  secondary_control_path: {
    mode: "hidden" | "advisory" | "recommended";
    reason: string | null;
    targets: Array<{
      operation: string;
      channel: string;
      command: string;
      api_path: string;
      required_role: string;
      requires_mfa: boolean;
    }>;
  };
  groups: HealthGroup[];
  meta: {
    snapshot_at: string;
    surfaces: Record<string, { status: "ok" | "degraded" | "unavailable"; source?: string }>;
  };
}

interface HealthGroup {
  group_id: "runtime" | "telemetry" | "incident" | "governance" | "kill_switch";
  label: string;
  status: "ok" | "degraded" | "unavailable";
  summary: string;
  details: Record<string, unknown>;
  surface_refs: Array<{
    surface_key: string;
    status: "ok" | "degraded" | "unavailable";
    source?: string;
    message?: string;
  }>;
  target_refs: Array<{
    label: string;
    href: string;
    min_role?: string;
  }>;
}
```

## Component Structure

### `OperatorHealthStatusBoard.tsx`

- Fetches `GET /api/v1/operator/health-status` on mount.
- Renders the summary header from `overall_status`, `headline`, `message`, and `safe_mode_state`.
- Renders one health card per `groups[]` entry in the order returned by the BFF.
- Shows the secondary control path panel only when `secondary_control_path.mode !== "hidden"`.
- Renders owner-screen links from `target_refs[]` only.
- Shows the shared degradation banner when any `meta.surfaces.*` entry is degraded or unavailable.

## Constraints

- Do not build this screen by combining `PKT-010`, `IN-01`, governance queues, or kill-switch status client-side.
- Do not invent additional health groups, health labels, or fallback targets.
- Do not add write CTAs for rollback, kill-switch activation, or approval actions.
- If any required field or `meta.surfaces.*` entry is missing, write `.coordination/requests/PKT-011-health-status-board-bff-gap.yaml` and stop implementation.

## Degradation Handling

- `overall_status = "degraded"` keeps the board visible and read-only.
- `overall_status = "unavailable"` renders the explicit unavailable state and the secondary control path prominently.
- `secondary_control_path.mode = "hidden"` suppresses the fallback panel.
- `secondary_control_path.mode = "advisory"` or `"recommended"` shows the fallback panel using the backend-supplied copy and targets only.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-011-health-status-board-ui-done.yaml` and sync it back so Pantheon can review the return.

## References

- BFF contract: `docs/bff/PKT-011-health-status-board.md`
- Screen spec: `docs/screens/PKT-011-health-status-board.md`
- Example payload: `docs/examples/PKT-011-health-status-board.json`
- Packet family: `docs/pantheon-handoffs/OC-002-operator-console-wave2/PACKET_FAMILY.md`
