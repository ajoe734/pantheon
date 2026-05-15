# PKT-003 Evolution Center — Frontend Change Spec

## Feature

- Feature ID: `PKT-003-evolution-center`
- Screen ID: `screen-evolution-center`
- Workbench: Evolution Workbench
- Packet status: ready

## Summary

Build the **Evolution Center** screen inside `front-ai-trading-system`. This screen gives operators a consolidated view of evolution decisions, freeze orders, and rollback records so they can understand the current evolution state of the system without navigating individual incident or deployment records. All data authority must come from the Pantheon BFF — no local derivation or mock state.

## Files to Create or Modify

```
src/pages/evolution/EvolutionCenter.tsx              — new Evolution Center page
src/pages/evolution/EvolutionDecisionDetail.tsx       — new decision detail drawer component
src/pages/evolution/types.ts                          — add evolution-center types
src/lib/bffClient.ts                                  — add evolution-center fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

### Fetch evolution decisions list (EV-01)

```
GET /api/v1/evolution-decisions
Query params: action_type, risk_level, status, page_token, page_size
```

Expected response shape (see `docs/examples/PKT-003-evolution-center.json` for a full example):

```typescript
interface EvolutionDecisionListResponse {
  items: EvolutionDecisionSummary[];
  page_info: { next_page_token: string | null };
  meta: { snapshot_at: string };
}

interface EvolutionDecisionSummary {
  id: string;
  action_type: string;
  risk_level: "low" | "medium" | "high";
  status: string;
  incident_ref: string;
  artifact_id: string;
}
```

### Fetch evolution decision detail (EV-02)

```
GET /api/v1/evolution-decisions/{decision_id}
```

Expected response shape:

```typescript
interface EvolutionDecisionDetailResponse {
  id: string;
  action_type: string;
  risk_level: "low" | "medium" | "high";
  status: string;
  incident_ref: string;
  artifact_id: string;
  created_at: string;
  updated_at: string;
  notes: string;
  meta: { snapshot_at: string };
}
```

### Fetch freeze orders list (EV-03)

```
GET /api/v1/freeze-orders
Query params: status, scope
```

Expected response shape:

```typescript
interface FreezeOrderListResponse {
  items: FreezeOrderSummary[];
  meta: { snapshot_at: string };
}

interface FreezeOrderSummary {
  freeze_order_id: string;
  status: string;
  scope: string;
  issued_at: string;
}
```

Note: no status filter is applied by default — both active and lifted freeze orders are returned.

### Fetch rollbacks list (EV-04)

```
GET /api/v1/rollbacks
Query params: runtime_id, action_type
```

Expected response shape:

```typescript
interface RollbackListResponse {
  items: RollbackSummary[];
  meta: { snapshot_at: string };
}

interface RollbackSummary {
  rollback_id: string;
  action_type: string;
  runtime_id: string;
  executed_at: string;
}
```

Note: `time_range` is accepted by the BFF but not applied in v1 — do not expose it as a UI filter control.

## Component Structure

### `EvolutionCenter.tsx`

- Fetches all four list endpoints independently on mount. Panels must not block each other from rendering.
- **Evolution Decisions panel**: paginated list filtered by `action_type`, `risk_level`, or `status` passed as query params (no client-side filtering). Each row shows `id`, `action_type`, `risk_level`, `status`, `incident_ref`, and `artifact_id`. Row click opens the `EvolutionDecisionDetail` drawer.
- **Freeze Orders panel**: renders all freeze orders (active + lifted). Each row shows `freeze_order_id`, `status`, `scope`, and `issued_at`. Renders explicit empty state when no orders exist.
- **Rollbacks panel**: each row shows `rollback_id`, `action_type`, `runtime_id`, and `executed_at`. Renders explicit empty state when no rollbacks exist.
- **Staleness / degradation banner**: when `BFF_READ_SURFACE_STATE != fresh` (i.e., any panel response has staleness metadata), render a non-dismissable banner naming the affected surface.
- **Loading, empty, and error states**: explicit and visually distinct. No mock fallback for any panel.

### `EvolutionDecisionDetail.tsx`

- Receives `decision_id` as a prop; fetches `GET /api/v1/evolution-decisions/{decision_id}` on open.
- Renders all EV-02 fields: `id`, `action_type`, `risk_level`, `status`, `incident_ref`, `artifact_id`, `created_at`, `updated_at`, `notes`.
- No write actions — evolution decision mutations are not part of this screen.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Filter parameters (`action_type`, `risk_level`, `status`, `scope`) must be passed as query params to the BFF — do not filter client-side.
- Do not expose `time_range` as a rollback filter control — it is a v1 no-op in the BFF store.
- Only `operator`, `approver`, `admin`, and `reviewer` role tokens are accepted by the BFF. Surface a "permission required" state (not a data-loading error) if a viewer token is rejected.
- No write actions on this screen — evolution decision mutations belong to the Mutation Review screen (`PKT-003-mutation-review`), which is blocked pending EVO-004.
- If any required response field is absent, write `.coordination/requests/PKT-003-evolution-center-bff-gap.yaml` using `.coordination/requests/PKT-003-evolution-center-bff-gap.example.yaml` as the template and stop implementation.

## Degradation Handling

Each panel is independently fetched and independently degradable. When any panel's BFF response indicates staleness:

- Show a non-dismissable staleness banner identifying the affected panel.
- Do not collapse a panel that returned zero rows — render explicit empty-state copy instead.
- Do not cross-render one panel's data into another.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-003-evolution-center-ui-done.yaml` using `.coordination/requests/PKT-003-evolution-center-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-003-evolution-center.md`
- BFF contract: `docs/bff/PKT-003-evolution-center.md`
- Example payload: `docs/examples/PKT-003-evolution-center.json`
- Contract-ready: `.coordination/responses/PKT-003-evolution-center-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-003-evolution-center-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-003-evolution-center-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-003-evolution-center-ui-done.example.yaml`
