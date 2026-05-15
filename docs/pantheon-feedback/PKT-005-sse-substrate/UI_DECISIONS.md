# PKT-005 SSE Reconciliation Substrate — UI Decisions

## Decision 1: Multi-stream connection state aggregation

**Context:** `IncidentDetail` subscribes to three SSE streams simultaneously.

**Decision:** Use a three-slot state array; emit `connected` if any slot is connected, `reconnecting` if none are connected but at least one is reconnecting, otherwise `disconnected`.

**Rationale:** Shows the most optimistic truth — if any stream is live, the screen has real-time coverage.

---

## Decision 2: DeploymentReviewConsole runtime context via child callback

**Context:** The deployment plan list does not expose `runtime_id`. It is only available after a plan detail loads via `DeploymentPlanDetail`.

**Decision:** Added `onRuntimeBindingIdChange` to `DeploymentPlanDetailProps`. The parent subscribes to the runtime stream once the ID is available.

**Rationale:** Avoids duplicating the detail fetch in the parent and keeps the composed-view-first rule intact.

---

## Decision 3: IncidentActionDrawer kill-switch prop (not own SSE)

**Context:** `IncidentActionDrawer` must not open its own raw SSE connection.

**Decision:** Added `killSwitchActivated?: boolean` prop. `IncidentDetail` and `IncidentActionDrawerPage` both pass kill-switch SSE state down as a prop.

**Rationale:** Matches the spec requirement that the drawer consumes host-screen state, not a parallel raw connection.

---

## Decision 4: incident_updated applied to list state in PostIncidentReviewConsole

**Context:** PostIncidentReviewConsole fetches a list of resolved incidents; detail is loaded on select.

**Decision:** `incident_updated` events update the status field in the list state. No detail re-fetch is triggered.

**Rationale:** Minimal, incremental, idempotent — consistent with the substrate semantics.
