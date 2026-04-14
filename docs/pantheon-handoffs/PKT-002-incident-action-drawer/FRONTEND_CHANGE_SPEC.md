# PKT-002 Incident Action Drawer — Frontend Change Spec

## Feature

- Feature ID: `PKT-002-incident-action-drawer`
- Screen ID: `screen-operator-incident-action-drawer`
- Workbench: Operator Console
- Packet status: ready

## Summary

Build the **Incident Action Drawer** inside `front-ai-trading-system`. This drawer gives operators a dedicated emergency control surface to issue high-authority commands (Pause, RiskOff, LiquidateAll, HardRollback, IssueSafeMode) against an active incident. The drawer renders command receipts inline, degrades gracefully when the primary kill switch surface is unavailable, and surfaces a secondary control path for the safest subset of actions through the fallback routing path.

## Files to Create or Modify

```
src/components/operator/IncidentActionDrawer.tsx  — new drawer component
src/pages/operator/types.ts                        — add action-drawer types
src/lib/bffClient.ts                               — add kill-switch and command fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch kill switch status (fresh on drawer open)

```
GET /api/v1/kill-switch/status
```

Expected response shape (see `docs/examples/PKT-002-incident-action-drawer.json` for full examples):

```typescript
interface KillSwitchActionDrawerResponse {
  kill_switch: {
    status: "armed" | "triggered" | "cooling_down" | null;
    last_triggered_at: string | null;
    last_confirmed_at: string | null;
    active_commands: string[];
  };
  allowedActions: {
    canPause: boolean;
    canRiskOff: boolean;
    canLiquidateAll: boolean;
    canHardRollback: boolean;
    canIssueSafeMode: boolean;
    secondaryPathAvailable: boolean;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      kill_switch: "ok" | "degraded" | "unavailable";
      allowedActions: "ok" | "degraded" | "unavailable";
    };
    degradation?: {
      kill_switch_reason?: string;
      allowedActions_reason?: string;
    };
  };
}
```

### Issue emergency command

```
POST /api/v1/operator/commands
```

All five emergency actions use this route. The `command` field discriminates the action type. See `docs/bff/PKT-002-incident-action-drawer.md` for the full payload shape of each command.

Every command payload must include `audit_context.reason` (non-empty string).

Expected receipt response shape:

```typescript
interface CommandReceipt {
  receipt_id: string;
  command: string;
  status: "accepted" | "queued" | "failed";
  accepted_at: string;
  routing_path: "direct" | "fallback";
  expected_completion_at: string | null;
  error_message?: string;
}
```

## Component Structure

### `IncidentActionDrawer.tsx`

- Fetches `GET /api/v1/kill-switch/status` fresh when the drawer opens. Do not inherit stale state from the parent Incident Detail screen.
- **Kill Switch status header**: always visible at the top — `status`, `last_triggered_at`, `last_confirmed_at`, `active_commands`.
- **Emergency Action buttons** (primary path, when `meta.surfaces.kill_switch = ok`):
  - One button per `allowedActions` flag that is `true`: Pause Execution, Issue Risk-Off, Liquidate All, Hard Rollback, Issue Safe Mode.
  - Disabled buttons must be visually distinct from enabled buttons.
  - Each button requires a non-empty `audit_context.reason` text field before the submit button is enabled.
- **Secondary Control Path panel** (when `meta.surfaces.kill_switch = degraded` or `unavailable`):
  - Show last known kill switch state with `last_confirmed_at` timestamp.
  - Show copy: "Primary control surface is degraded. Commands will be routed through the fallback path. Confirm before proceeding."
  - Show only `PauseExecution` and `IssueRiskOff` buttons (the safest subset). These route as `routing_path: fallback`.
  - All other action buttons are disabled when the secondary control path is active.
- **Fully unavailable banner** (when `meta.surfaces.kill_switch = unavailable` **and** `allowedActions.secondaryPathAvailable = false`):
  - Render: "Emergency control surface is fully unavailable. Contact on-call directly."
  - All action buttons are disabled.
- **Command Receipt panel**: rendered inline after each `POST /api/v1/operator/commands` call:
  - Show: `receipt_id`, `command`, `status`, `accepted_at`, `routing_path`, `expected_completion_at`.
  - When `status = failed`: show `error_message` and disable further actions until the operator acknowledges.
  - After a successful command, the receipt replaces the action button area. A "Close drawer" action becomes available.
- **Loading and error states**: explicit. The drawer must not open without a valid kill switch status response or a degraded-state substitution.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Action button visibility and enabled/disabled state must come from `allowedActions` in the BFF response only. Do not derive eligibility locally.
- Every emergency command submission requires a non-empty `audit_context.reason`. Enforce this before enabling the submit button.
- If a required `allowedActions` field is absent from the BFF response, emit a `bff-gap` handoff using `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.example.yaml` as the template. Do not enable or disable actions based on assumption.
- After a non-2xx command response, render the error and retain the form for retry without reloading the drawer.

## Degradation Handling

| Condition | Behavior |
|---|---|
| `meta.surfaces.kill_switch = ok` | Render full primary action surface from `allowedActions` |
| `meta.surfaces.kill_switch = degraded` | Show secondary control path panel; restrict to PauseExecution and IssueRiskOff; disable all other actions |
| `meta.surfaces.kill_switch = unavailable` and `secondaryPathAvailable = true` | Show secondary control path panel with fallback routing |
| `meta.surfaces.kill_switch = unavailable` and `secondaryPathAvailable = false` | Show "Emergency control surface fully unavailable" banner; disable all actions |
| `meta.surfaces.allowedActions = degraded` | All CTAs disabled; show banner |
| `meta.surfaces.allowedActions = unavailable` | All CTAs disabled; show banner |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-002-incident-action-drawer-ui-done.yaml` using `.coordination/requests/PKT-002-incident-action-drawer-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-002-incident-action-drawer.md`
- BFF contract: `docs/bff/PKT-002-incident-action-drawer.md`
- Example payload: `docs/examples/PKT-002-incident-action-drawer.json`
- Contract-ready: `.coordination/responses/PKT-002-incident-action-drawer-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-002-incident-action-drawer-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-002-incident-action-drawer-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-002-incident-action-drawer-ui-done.example.yaml`
