# PKT-013 Operator Home Dashboard

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-home-dashboard`
- Feature ID: `PKT-013-operator-home`
- Packet status: ready

## User Goal

Give operators one truthful home screen that summarizes alerts, incidents, governance pressure, runtime coverage, and health state without hiding degraded upstream conditions behind a calm empty dashboard.

## Page Sections

- **Home summary header**: renders `overall_status`, `headline`, `message`, and `safe_mode_state`.
- **Summary card stack**: renders `cards[]` in backend-owned order.
- **Escalation shortcuts rail**: renders `escalation_shortcuts[]` only from the backend-owned shortcut contract.
- **Existing-owner links**: render from each card's `target_refs[]`.
- **Unavailable home state**: when `meta.surfaces.operator_home = unavailable`, show the explicit unavailable treatment while preserving the backend-supplied card stack.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/home`.
- The UI must not recreate operator-home cards by separately calling alerts, health, incidents, governance queues, runtime state, telemetry, or kill-switch routes.
- `cards[]` stay in backend-owned order.
- `escalation_shortcuts[]` stay in backend-owned order and priority.
- `safe_mode_state` is backend-owned and must be rendered as-is.
- `target_refs[]` and `escalation_shortcuts[].href` are browser-ready owner-screen destinations supplied by Pantheon and must be rendered verbatim.
- This dashboard is read-only and must not add approval, rollback, kill-switch, or runtime mutation CTAs.

## Acceptance

- The home screen renders one backend-owned summary route.
- Exactly five cards render in the published order.
- Safe-mode state and overall status come directly from the route response.
- The screen distinguishes unavailable or degraded upstream state from a truly empty calm dashboard.
- Any degraded or unavailable card source also triggers the shared global degradation banner.
