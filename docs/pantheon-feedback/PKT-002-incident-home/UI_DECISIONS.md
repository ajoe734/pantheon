# PKT-002 Incident Home — UI Decisions

Feature ID: `PKT-002-incident-home`
Screen: `incident-home`
Workbench: `operator-console`

## Key UI Decisions

### 1. Split the screen into two authoritative reads

**Decision:** `IncidentHome.tsx` performs one read for the incident list and one read for the
kill-switch rail, then merges the two metas only for banner presentation.

**Rationale:** The packet defines two distinct BFF surfaces. Keeping them separate preserves
contract boundaries while still allowing a single degradation summary at the page level.

### 2. Keep the kill-switch rail outside the incident table

**Decision:** The kill-switch state is rendered as a dedicated control-rail card in the right-hand
column, not as a table column or a per-row badge.

**Rationale:** Kill-switch status is platform-wide control state. Rendering it separately avoids
implying that the state belongs to a single incident row.

### 3. Use explicit unavailable and degraded states

**Decision:** When `meta.surfaces.kill_switch` is `unavailable`, the UI renders a destructive
unknown-state alert. When it is `degraded`, the UI renders the last known state plus a
non-dismissable warning.

**Rationale:** The packet distinguishes safety-critical uncertainty from stale-but-usable data.
Collapsing those states would mislead operators.

### 4. Treat missing envelope fields as a contract gap

**Decision:** The screen validates required list and kill-switch fields before rendering the success
state and shows a `bff-gap` alert when required fields are absent.

**Rationale:** The Lovable task explicitly forbids inventing fields. Contract violations must be
surfaced for Pantheon follow-up instead of papered over in the UI.

### 5. Route all reads through the shared BFF client

**Decision:** The screen uses `operatorApi.listIncidentHome()` and
`operatorApi.getIncidentHomeKillSwitchStatus()` from `src/lib/bffClient.ts`.

**Rationale:** This keeps auth, base URL resolution, and error handling centralized and satisfies
the no-raw-fetch constraint from the handoff packet.
