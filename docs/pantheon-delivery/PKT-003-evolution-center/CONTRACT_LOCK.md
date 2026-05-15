# PKT-003 Evolution Center — Contract Lock

## Lock Date

2026-04-17

## Lock Scope

This file records the current Pantheon BFF contract state for
`PKT-003-evolution-center` after the Pantheon follow-up review of the completed
UI handoff from `ajoe734/front-ai-trading-system`.

Canonical contract references:

- `docs/bff/PKT-003-evolution-center.md`
- `docs/examples/PKT-003-evolution-center.json`
- `docs/screens/PKT-003-evolution-center.md`

## Endpoint 1: `GET /api/v1/evolution-decisions` (EV-01)

### Locked response shape

```typescript
interface EvolutionDecisionListResponse {
  items: EvolutionDecisionSummary[];
  page_info: { next_page_token: string | null };
  meta: { snapshot_at: string; staleness?: EvolutionStaleness | null };
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

### Current Pantheon implementation lock

- Route: `services/control-plane/bff/main.py` `list_evolution_decisions()`
- Projection: `services/control-plane/bff/main.py`
  `_project_evolution_decision_contract()`
- Source data: `services/control-plane/bff/read_store.py`
  `list_evolution_decisions()`

### Locked behavior

- Returns top-level `items`
- Returns `page_info.next_page_token`
- Returns `meta.snapshot_at`
- Includes `meta.staleness` only when `BFF_READ_SURFACE_STATE != fresh`
- Applies `action_type`, `risk_level`, and `status` server-side

### Status

`aligned`

## Endpoint 2: `GET /api/v1/evolution-decisions/{decision_id}` (EV-02)

### Locked response shape

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
  meta: { snapshot_at: string; staleness?: EvolutionStaleness | null };
}
```

### Current Pantheon implementation lock

- Route: `services/control-plane/bff/main.py` `get_evolution_decision()`
- Projection: `services/control-plane/bff/main.py`
  `_project_evolution_decision_contract()`
- Source data: `services/control-plane/bff/read_store.py`
  `get_evolution_decision_by_id()`

### Locked behavior

- Returns the decision fields at the response root, not under `data`
- Returns `updated_at` and `notes`
- Returns `meta.snapshot_at`
- Includes `meta.staleness` only when `BFF_READ_SURFACE_STATE != fresh`
- Returns `404 OBJECT_NOT_FOUND` when the decision does not exist

### Status

`aligned`

## Endpoint 3: `GET /api/v1/freeze-orders` (EV-03)

### Locked response shape

```typescript
interface FreezeOrderListResponse {
  items: FreezeOrderSummary[];
  meta: { snapshot_at: string; staleness?: EvolutionStaleness | null };
}

interface FreezeOrderSummary {
  freeze_order_id: string;
  status: string;
  scope: string;
  issued_at: string;
}
```

### Current Pantheon implementation lock

- Route: `services/control-plane/bff/main.py` `list_freeze_orders()`
- Projection: `services/control-plane/bff/main.py`
  `_project_freeze_order_contract()`
- Source data: `services/control-plane/bff/read_store.py`
  `list_freeze_orders()`

### Locked behavior

- Returns top-level `items`
- Returns `meta.snapshot_at`
- Projects `freeze_order_id` from the source `id` field when needed
- Projects `issued_at` from the source `created_at` field when needed
- Applies `status` and `scope` server-side

### Status

`aligned`

## Endpoint 4: `GET /api/v1/rollbacks` (EV-04)

### Locked response shape

```typescript
interface RollbackListResponse {
  items: RollbackSummary[];
  meta: { snapshot_at: string; staleness?: EvolutionStaleness | null };
}

interface RollbackSummary {
  rollback_id: string;
  action_type: string;
  runtime_id: string;
  executed_at: string;
}
```

### Current Pantheon implementation lock

- Route: `services/control-plane/bff/main.py` `list_rollbacks()`
- Projection: `services/control-plane/bff/main.py`
  `_project_rollback_contract()`
- Source data: `services/control-plane/bff/read_store.py`
  `list_all_rollbacks()`

### Locked behavior

- Returns top-level `items`
- Returns `meta.snapshot_at`
- Projects `rollback_id` from the source `id` field when needed
- Projects `executed_at` from the source `initiated_at` field when needed
- Applies `runtime_id` and `action_type` server-side
- Accepts `time_range`, but the v1 store still treats it as deferred and the UI
  must continue not to expose it

### Status

`aligned`

## Role And Degradation Lock

- Accepted read roles remain `operator`, `approver`, `admin`, and `reviewer`
- `viewer` tokens remain rejected with `403 INSUFFICIENT_ROLE`
- When `BFF_READ_SURFACE_STATE != fresh`, the route metadata may include
  `meta.staleness`, which is the canonical signal for the UI stale-read banner

## Verification Evidence

- `pytest -q services/control-plane/bff/test_evolution_center_contract.py`
- Targeted FastAPI `TestClient` probe against the four EV routes with seeded
  read-store data
- `npm run build` in `../front-ai-trading-system`

## Next Step

Pantheon follow-up for the current PKT-003 Evolution Center UI cycle is
complete. No new endpoint, no shadow state, and no additional Pantheon-owned
contract expansion is authorized for this packet.
