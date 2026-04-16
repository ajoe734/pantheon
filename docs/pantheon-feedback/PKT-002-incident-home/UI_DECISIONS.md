# PKT-002 Incident Home — UI Decisions

Feature ID: `PKT-002-incident-home`
Screen: `incident-home`
Workbench: `operator-console`

## Key UI Decisions

### 1. Kill Switch Badge on Control Rail

**Decision:** The kill switch status badge is always rendered on the operator console control rail,
not inline in the incident list.

**Rationale:** Kill switch state is a platform-level control surface. It must be visible regardless
of which incident the operator is viewing. Embedding it in the list would create inconsistent
visibility.

**Source:** `GET /api/v1/kill-switch/status` → `kill_switch.status`

### 2. Non-Dismissable Warning Banner for Kill Switch Degradation

**Decision:** When `meta.surfaces.kill_switch` is `degraded` or `unavailable`, display a
non-dismissable warning banner above the incident list.

**Rationale:** Kill switch degradation is a safety-critical signal. Operators must not be able to
accidentally dismiss it. The `non_dismissable` constraint is enforced at the component level.

### 3. Degradation Banner for Any Surface Degradation

**Decision:** Display a general degradation banner when any entry in `meta.surfaces` is `degraded`
or `unavailable`.

**Rationale:** The degradation banner provides a single summary indicator for partial data
reliability. This avoids per-field inline error states that would fragment the operator's attention.

### 4. No Local State Derivation

**Decision:** All status values (kill switch, degradation) come exclusively from BFF API responses.
No local derivation or caching.

**Rationale:** Local derivation creates divergence between what the operator sees and the actual
system state. All authority lives in the Pantheon BFF.

### 5. BFF Client Only

**Decision:** All API calls go through the existing `bffClient` in `src/lib/bffClient.ts`.

**Rationale:** Consistent error handling, auth, and retry logic. No raw fetch in component files.
