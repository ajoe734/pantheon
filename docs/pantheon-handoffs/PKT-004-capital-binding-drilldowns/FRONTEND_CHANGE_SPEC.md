# PKT-004 Module B — Capital / Binding Drilldowns Frontend Change Spec

## Feature

- Feature ID: `PKT-004-capital-binding-drilldowns`
- Module: `Module B — Capital / Binding Drilldowns`
- Workbench: Persona Workbench
- Packet status: ready

## Summary

Build four Capital / Binding Drilldown surfaces in `front-ai-trading-system`: Capital Pool List, Capital Pool Detail, Binding List, and Binding Detail. All data must come from Pantheon BFF — no client-side joins.

## Files to Create or Modify

```
src/pages/persona/CapitalPoolList.tsx          — new capital pool list page (CP-01)
src/pages/persona/CapitalPoolDetail.tsx        — new capital pool detail page (CP-02)
src/pages/persona/BindingList.tsx              — new binding list page (CP-03)
src/pages/persona/BindingDetail.tsx            — new binding detail page (CP-04)
src/pages/persona/types.ts                     — add capital/binding drilldown types
src/lib/bffClient.ts                           — add capital/binding fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`.

| Surface | Endpoint |
|---|---|
| CP-01 Capital Pool List | `GET /api/v1/capital-pools` |
| CP-02 Capital Pool Detail | `GET /api/v1/capital-pools/{pool_id}` |
| CP-03 Binding List | `GET /api/v1/bindings` |
| CP-04 Binding Detail | `GET /api/v1/bindings/{binding_id}` |

See `docs/examples/PKT-004-capital-binding-drilldowns.json` for full example payloads.

## Component Structure

### `CapitalPoolList.tsx` (CP-01)

- Fetches from `GET /api/v1/capital-pools` on mount.
- Renders pool rows: `id`, `name`, `status`, `owner_id`, `single_runtime_enforced`, `risk_policy_ref`.
- Clicking a row navigates to `CapitalPoolDetail`.
- Renders loading, empty, and error states.

### `CapitalPoolDetail.tsx` (CP-02)

- Receives `pool_id` as a route param.
- Fetches from `GET /api/v1/capital-pools/{pool_id}` on mount.
- Renders pool identity block.
- Navigation link to `BindingList` filtered by `capital_pool_id`.

### `BindingList.tsx` (CP-03)

- Fetches from `GET /api/v1/bindings` on mount and on filter change.
- Supports `persona_id` and `capital_pool_id` filters passed as query params.
- Renders binding rows: `id`, `persona_id`, `capital_pool_id`, `role`, `validity`, `status`, `allowed_deployment_scope`.
- Clicking a row navigates to `BindingDetail`.

### `BindingDetail.tsx` (CP-04)

- Receives `binding_id` as a route param.
- Fetches from `GET /api/v1/bindings/{binding_id}` on mount.
- Renders binding identity block with navigation links to `PersonaManagement` and `CapitalPoolDetail`.

## Constraints

- Use the existing BFF client only.
- Do not add raw `fetch` or `axios` in component files.
- No write or command actions are defined in this module.
- Filters must be passed as query parameters — no client-side filtering.
- If a required response field is absent, write `.coordination/requests/PKT-004-capital-binding-drilldowns-bff-gap.yaml` and stop.

## Completion Handoff

When ready, write `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.yaml` using `.coordination/requests/PKT-004-capital-binding-drilldowns-ui-done.example.yaml` as the template. Sync back to GitHub.

## References

- BFF contract: `docs/bff/PKT-004-capital-binding-drilldowns.md`
- Screen spec: `docs/screens/PKT-004-capital-binding-drilldowns.md`
- Example payload: `docs/examples/PKT-004-capital-binding-drilldowns.json`
- Contract-ready: `.coordination/responses/PKT-004-capital-binding-drilldowns-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-004-capital-binding-drilldowns-lovable-ui-task.yaml`
