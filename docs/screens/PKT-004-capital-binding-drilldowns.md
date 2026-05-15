# PKT-004 Module B — Capital / Binding Drilldowns

## Classification

- Workbench: Persona Workbench
- Module: `Module B — Capital / Binding Drilldowns`
- Feature ID: `PKT-004-capital-binding-drilldowns`
- Packet status: ready

## User Goal

Allow an operator to browse capital pools and persona-capital bindings, inspect individual pool and binding detail, and trace the relationship between capital allocation and persona deployment scope — without local data joining.

## Surface Inventory

| Surface | Screen ID | Endpoint | User action |
|---|---|---|---|
| `CP-01` Capital Pool List | `screen-capital-pool-list` | `GET /api/v1/capital-pools` | Browse capital pools with status and policy refs |
| `CP-02` Capital Pool Detail | `screen-capital-pool-detail` | `GET /api/v1/capital-pools/{pool_id}` | Inspect one pool with embedded binding refs |
| `CP-03` Binding List | `screen-binding-list` | `GET /api/v1/bindings` | Browse all persona-capital bindings with validity and scope |
| `CP-04` Binding Detail | `screen-binding-detail` | `GET /api/v1/bindings/{binding_id}` | Inspect one binding with persona and pool context |

## Page Sections Per Surface

### CP-01 Capital Pool List

- **Pool list**: rows showing `id`, `name`, `status`, `owner_id`, `owner_type`, `single_runtime_enforced`, `risk_policy_ref`.
- Clicking a row navigates to `CP-02 Capital Pool Detail`.
- **Loading, empty, and error states**: explicit and visually distinct.

### CP-02 Capital Pool Detail

- **Pool identity block**: `id`, `name`, `status`, `owner_id`, `owner_type`, `single_runtime_enforced`, `risk_policy_ref`.
- **Binding summary rail**: list of bindings referencing this pool (from embedded `bindings[]` or linked via `CP-03` filter).
- Navigation link to `CP-03 Binding List` filtered by `capital_pool_id`.

### CP-03 Binding List

- **Binding list**: rows showing `id`, `persona_id`, `capital_pool_id`, `role`, `validity`, `status`, `allowed_deployment_scope`.
- **Filter rail**: filter by `persona_id` or `capital_pool_id`. Filters passed as query parameters.
- Clicking a row navigates to `CP-04 Binding Detail`.

### CP-04 Binding Detail

- **Binding identity block**: `id`, `persona_id`, `capital_pool_id`, `role`, `validity`, `status`, `allowed_deployment_scope`.
- **Persona link**: navigation to `PS-02 Persona Detail` or `PM-01 Persona Management` for the binding's `persona_id`.
- **Pool link**: navigation to `CP-02 Capital Pool Detail` for the binding's `capital_pool_id`.

## Interaction Rules

- All data comes from BFF read routes listed above.
- No write actions are defined in this module. Binding mutations are out of scope.
- Filters are passed as query parameters to the BFF — no client-side filtering.
- `viewer` role tokens are rejected.

## Non-Blocking BFF Caveats

- `viewer` role is rejected on all CP surfaces.
- `meta.staleness` on list surfaces may be non-null when the backing store has not recently refreshed.

## Acceptance

- All four CP surfaces render with real BFF data and no mock rows.
- Filter parameters pass through to the BFF — no client-side filter logic.
- Navigation between pool and binding surfaces works using BFF-sourced IDs.
- Loading, empty, and error states are explicit and visually distinct.
