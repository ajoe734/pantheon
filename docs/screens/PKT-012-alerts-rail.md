# PKT-012 Operator Alerts Rail

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-alerts-rail`
- Feature ID: `PKT-012-alerts-rail`
- Packet status: ready

## User Goal

Give operators one chronological alert rail for active incidents, governance bottlenecks, kill-switch or safe-mode activity, and runtime anomalies without making the UI guess what deserves attention.

## Page Sections

- **Alert rail header**: renders `summary.total_active`, `summary.highest_severity`, and any degraded-state copy.
- **Alert list**: renders one row per `alerts[]` item in backend-owned order.
- **Alert category and severity chips**: render exactly from backend-supplied `category` and `severity`.
- **Owner-screen links**: use `target_ref` only.
- **Unavailable rail state**: when `meta.surfaces.alerts = unavailable`, show the explicit unavailable treatment instead of an empty list.

## Interaction Rules

- All production data comes from `GET /api/v1/operator/alerts`.
- The UI must not join incidents, governance queues, runtime state, telemetry summaries, or kill-switch state in the browser.
- The UI must not add alert acknowledgement or dismissal controls in this packet.
- Alert ordering, alert identity, severity taxonomy, and categories are backend-owned.
- Existing-owner navigation uses `target_ref` only.

## Acceptance

- The rail renders from one operator-owned alert feed.
- Alert rows appear in backend-owned chronological order.
- Severity and category chips use backend-supplied values only.
- The rail stays read-only.
- `meta.surfaces.alerts = unavailable` renders an explicit unavailable state.
- Any degraded contributing surface also triggers the shared global degradation banner.
