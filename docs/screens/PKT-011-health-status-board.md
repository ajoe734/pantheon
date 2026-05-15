# PKT-011 Operator Health Status Board

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-health-status-board`
- Feature ID: `PKT-011-health-status-board`
- Packet status: ready

## User Goal

Give operators one truthful health board for overall control-plane status, safe-mode state, surface-group health, and fallback guidance so they do not have to infer system health from unrelated screens.

## Page Sections

- **Health summary header**: renders `headline`, `message`, `overall_status`, and the `safe_mode_state` summary.
- **Group health cards**: one card per `groups[]` entry for `runtime`, `telemetry`, `incident`, `governance`, and `kill_switch`.
- **Surface details rail**: each group card shows its `surface_refs[]` entries so degraded or unavailable sub-surfaces are explicit.
- **Existing-owner links**: renders `target_refs[]` links into the authoritative downstream screens.
- **Secondary control path panel**: renders from `secondary_control_path`; hidden only when `mode = hidden`.
- **Unavailable board state**: when `overall_status = unavailable`, the group grid remains truthful but the page must show the explicit unavailable-state treatment.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/health-status`.
- The UI must not synthesize health groups by separately calling runtime, telemetry, incident, governance, or kill-switch routes.
- The group order is backend-owned and follows the published taxonomy.
- `secondary_control_path.targets[]` render exactly as supplied; no fallback commands or internal endpoints may be invented locally.
- Existing-owner navigation uses `target_refs[]` only.
- This board is read-only. No rollback, kill-switch, or approval CTA is introduced here.

## Acceptance

- Overall status, safe-mode state, and group counts come directly from the BFF response.
- Exactly five group cards render in the backend-owned order.
- Group labels, summaries, and `surface_refs[]` are backend-shaped; no client-derived taxonomy is used.
- The secondary control path panel appears only when `mode != hidden`.
- `overall_status = unavailable` renders an explicit unavailable state.
- Any degraded or unavailable health group also triggers the shared global degradation banner.
