# PKT-002 Incident Action Drawer

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-incident-action-drawer`
- Feature ID: `PKT-002-incident-action-drawer`
- Packet status: ready

## User Goal

Give an operator a dedicated emergency control surface to issue and confirm high-authority commands (Pause, RiskOff, LiquidateAll, HardRollback, IssueSafeMode) against an active incident. The drawer must render command receipts inline after each action and degrade gracefully when the primary kill switch surface is unavailable by surfacing a secondary control path.

## Page Sections

- **Kill Switch status header**: current kill switch state from `GET /api/v1/kill-switch/status` — `status`, `last_triggered_at`, `last_confirmed_at`, `active_commands[]`. Always visible at the top of the drawer.
- **Emergency Action buttons**: one button per allowed action, derived from `allowedActions`:
  - `Pause Execution` — visible and enabled when `allowedActions.canPause = true`
  - `Issue Risk-Off` — visible and enabled when `allowedActions.canRiskOff = true`
  - `Liquidate All` — visible and enabled when `allowedActions.canLiquidateAll = true`
  - `Hard Rollback` — visible and enabled when `allowedActions.canHardRollback = true`
  - `Issue Safe Mode` — visible and enabled when `allowedActions.canIssueSafeMode = true`
  - Each button requires a mandatory `audit_context.reason` field before submission.
- **Command Receipt panel**: after each `POST /api/v1/operator/commands` call, the receipt is rendered inline:
  - `receipt_id`
  - `command`
  - `status` (`accepted`, `queued`, `failed`)
  - `accepted_at`
  - `routing_path` (`direct` or `fallback`)
  - `expected_completion_at` (nullable)
  - If `status = failed`, render the `error_message` and disable further actions until the operator acknowledges.
- **Secondary Control Path panel**: rendered when `meta.surfaces.kill_switch = degraded` or `unavailable`. Shows:
  - The last known kill switch state with `last_confirmed_at` timestamp.
  - Copy: "Primary control surface is degraded. Commands will be routed through the fallback path. Confirm before proceeding."
  - A reduced emergency form with only `PauseExecution` and `IssueRiskOff` available (the safest subset). These route as `routing_path: fallback` in the command receipt.
  - All other action buttons are disabled when the secondary control path is active.
- **Degradation banner**: when `meta.surfaces.kill_switch = unavailable` and the secondary path is also unavailable, render: "Emergency control surface is fully unavailable. Contact on-call directly." All action buttons are disabled.
- **Loading and error states**: explicit. The drawer must not open without a valid kill switch status response or a degraded-state substitution.

## Interaction Rules

- All production data comes from Pantheon BFF routes only.
- Kill switch status is fetched fresh when the drawer opens. Do not inherit stale state from the parent Incident Detail screen.
- Action buttons derive their visibility and enabled/disabled state from `allowedActions` in the kill switch status response. No local eligibility logic.
- Every emergency action submission requires a non-empty `audit_context.reason` string. The UI must enforce this before enabling the submit button.
- After a successful command, the receipt panel replaces the action button area. A "Close drawer" action becomes available.
- If `POST /api/v1/operator/commands` returns a non-2xx response, render the error and retain the form for retry without reloading the drawer.
- If a required `allowedActions` field is absent from the BFF response, emit a `bff-gap` handoff instead of enabling or disabling actions based on assumption.

## Acceptance

- Kill switch status header renders from `GET /api/v1/kill-switch/status` with no mock state.
- Action buttons render and disable based on `allowedActions` from the BFF response only.
- Each button requires a non-empty `audit_context.reason` before submission is enabled.
- Command receipt renders inline after each successful command with all required fields.
- Secondary control path panel renders when `meta.surfaces.kill_switch = degraded` or `unavailable`, showing the last known state, the fallback routing copy, and only the safe action subset.
- "Emergency control surface fully unavailable" banner renders when both primary and secondary paths are unavailable.
- Front-end emits a `bff-gap` handoff if any `allowedActions` field is absent from the BFF response.
