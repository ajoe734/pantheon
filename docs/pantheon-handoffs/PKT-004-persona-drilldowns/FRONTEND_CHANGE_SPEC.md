# PKT-004 Module A — Persona Drilldowns Frontend Change Spec

## Feature

- Feature ID: `PKT-004-persona-drilldowns`
- Module: `Module A — Persona Drilldowns`
- Workbench: Persona Workbench
- Packet status: ready (catalog packetization; standalone Persona Workbench IA deferred to Wave 2)

## Summary

Build six Persona Drilldown surfaces in `front-ai-trading-system`: Persona Catalog, Persona Detail, Session List, Session Detail, Teaching History, and Capability Snapshot. All data must come from Pantheon BFF — no client-side joins.

## Files to Create or Modify

```
src/pages/persona/PersonaCatalog.tsx           — new persona list page (PS-01)
src/pages/persona/PersonaDetail.tsx            — new persona detail page (PS-02)
src/pages/persona/PersonaSessionList.tsx       — new session list page (PS-03)
src/pages/persona/SessionDetail.tsx            — new session detail page (PS-04)
src/pages/persona/PersonaTeachingHistory.tsx   — new teaching history page (PS-05)
src/pages/persona/PersonaCapabilities.tsx      — new capability snapshot page (PS-06)
src/pages/persona/types.ts                     — add persona drilldown types
src/lib/bffClient.ts                           — add persona drilldown fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` calls in component files.

| Surface | Endpoint |
|---|---|
| PS-01 Persona Catalog | `GET /api/v1/personas` |
| PS-02 Persona Detail | `GET /api/v1/personas/{persona_id}` |
| PS-03 Session List | `GET /api/v1/personas/{persona_id}/sessions` |
| PS-04 Session Detail | `GET /api/v1/sessions/{session_id}` |
| PS-05 Teaching History | `GET /api/v1/personas/{persona_id}/teaching` |
| PS-06 Capability Snapshot | `GET /api/v1/personas/{persona_id}/capabilities` |

See `docs/examples/PKT-004-persona-drilldowns.json` for full example payloads.

## Component Structure

### `PersonaCatalog.tsx` (PS-01)

- Fetches from `GET /api/v1/personas` on mount and on filter change.
- Renders a list of personas: `id`, `name`, `lifecycle_state`, `mandate`, `strategy_family`, `last_active_at`.
- Supports `lifecycle_state` filter passed as a query param — no client-side filter logic.
- Clicking a row navigates to `PersonaDetail` or `PersonaManagement`.
- Renders loading, empty, and error states as distinct visual states.

### `PersonaDetail.tsx` (PS-02)

- Receives `persona_id` as a route param.
- Fetches from `GET /api/v1/personas/{persona_id}` on mount.
- Renders persona identity block and embedded bindings summary.
- Navigation links to Session List, Teaching History, and Capability Snapshot.

### `PersonaSessionList.tsx` (PS-03)

- Fetches from `GET /api/v1/personas/{persona_id}/sessions` on mount.
- Renders session rows: `id`, `status`, `started_at`, `last_heartbeat_at`, `tools_enabled`, `pool_scope`.
- Clicking a row navigates to `SessionDetail`.

### `SessionDetail.tsx` (PS-04)

- Receives `session_id` as a route param.
- Fetches from `GET /api/v1/sessions/{session_id}` on mount.
- Renders session header and inline capability snapshot.

### `PersonaTeachingHistory.tsx` (PS-05)

- Fetches from `GET /api/v1/personas/{persona_id}/teaching` on mount.
- Renders teaching session rows with expandable outcomes drawer.

### `PersonaCapabilities.tsx` (PS-06)

- Fetches from `GET /api/v1/personas/{persona_id}/capabilities` on mount.
- Renders snapshot header, effective tools, skills, workflows, and restrictions.

## Constraints

- Use the existing BFF client only.
- Do not add raw `fetch` or `axios` in component files.
- Do not import or use any demo provider or mock data layer.
- Filters must be passed as query parameters to the BFF — do not filter client-side.
- If a required response field is absent, write `.coordination/requests/PKT-004-persona-drilldowns-bff-gap.yaml` and stop.
- `viewer` role tokens are rejected — ensure the auth token passed is `operator`, `approver`, `admin`, or `reviewer`.

## Completion Handoff

When ready, write `.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml` using `.coordination/requests/PKT-004-persona-drilldowns-ui-done.example.yaml` as the template. Sync back to GitHub.

## References

- BFF contract: `docs/bff/PKT-004-persona-drilldowns.md`
- Screen spec: `docs/screens/PKT-004-persona-drilldowns.md`
- Example payload: `docs/examples/PKT-004-persona-drilldowns.json`
- Contract-ready: `.coordination/responses/PKT-004-persona-drilldowns-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-004-persona-drilldowns-lovable-ui-task.yaml`
